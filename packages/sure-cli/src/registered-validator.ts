import { createHash } from "node:crypto";
import { lstatSync, readFileSync, realpathSync } from "node:fs";
import { join } from "node:path";
import type {
	ArtifactRef,
	CoreRunRecord,
	ExecutionAdmissionTrace,
	ExecutionReceipt,
	ExecutionRequest,
	JsonValue,
} from "@earendil-works/sure-core";
import { canonicalJsonDigest, validateExecutionAdmissionReceiptBinding } from "@earendil-works/sure-core";
import { resolveSemanticBackendOperation, verifyPortableRuntime } from "@earendil-works/sure-core/evaluation";
import { type ExecutorRunResult, executeRequest } from "./executor.ts";

const DIGEST_PATTERN = /^sha256:[0-9a-f]{64}$/;

export interface SkillRuntimeBinding {
	host: "pi" | "portable";
	skill_id: string;
	workflow_digest: string;
	validator_registry_digest: string;
	semantic_backend_registry_digest: string;
	semantic_runtime_digest: string;
	executor_registry_digest: string;
	core_package_version: string;
}

export interface RegisteredValidatorDescriptor {
	id: string;
	skill_id?: string;
	branch_id?: string;
	unit_id?: string;
	backend_operation_id?: string;
	script_args?: readonly string[];
}

export interface PersistedValidatorDocument {
	path: string;
	digest: string;
}

export interface ValidatorEvidenceEntry {
	validator_id: string;
	backend_operation_id?: string;
	verdict: "PASS" | "FAIL" | "NOT_EXECUTED";
	artifact_digest: string;
	reason_code: string;
	diagnostics: readonly string[];
	runtime_digest?: string;
	backend_registry_digest?: string;
	backend_bundle_digest?: string;
	backend_resource_digest?: string;
	request_path?: string;
	request_digest?: string;
	admission_path?: string;
	admission_digest?: string;
	receipt_path?: string;
	receipt_digest?: string;
}

export interface RegisteredValidationEvidence {
	schema: "sure.validator.evidence.v1";
	source: "surectl";
	registry_digest: string;
	runtime_digest?: string;
	validators: readonly ValidatorEvidenceEntry[];
}

export interface RegisteredValidationResult {
	verdict: "PASS" | "FAIL" | "NOT_EXECUTED";
	reason: string;
	evidence: RegisteredValidationEvidence;
}

export interface RegisteredValidationOptions {
	runtime_root?: string;
	runtime_binding: SkillRuntimeBinding;
	run: CoreRunRecord;
	branch_id: string;
	unit_id: string;
	attempt: number;
	artifact: ArtifactRef;
	validators: readonly RegisteredValidatorDescriptor[];
	validator_registry_digest: string;
	python_executable: string;
	package_dir: string;
	workspace_root: string;
	artifacts_root: string;
	artifacts_resolved_root: string;
	reference_snapshot_digest: string;
	policy_digest: string;
	forbidden_output_roots: readonly string[];
	invocation_id: string;
	created_at: string;
	base_environment?: NodeJS.ProcessEnv;
	persist_request(key: string, request: ExecutionRequest): PersistedValidatorDocument;
	persist_receipt(key: string, receipt: ExecutionReceipt): PersistedValidatorDocument;
	persist_admission_trace?(key: string, trace: ExecutionAdmissionTrace): PersistedValidatorDocument;
}

function object(value: unknown, label: string): Record<string, unknown> {
	if (typeof value !== "object" || value === null || Array.isArray(value))
		throw new Error(`${label} must be an object.`);
	return value as Record<string, unknown>;
}

function stringValue(value: unknown, label: string): string {
	if (typeof value !== "string" || value.trim() === "") throw new Error(`${label} must be a non-empty string.`);
	return value;
}

function digestValue(value: unknown, label: string): string {
	const digest = stringValue(value, label);
	if (!DIGEST_PATTERN.test(digest)) throw new Error(`${label} must be a canonical SHA-256 digest.`);
	return digest;
}

/** Load the immutable runtime identities carried next to a generated skill definition. */
export function loadSkillRuntimeBinding(
	packageDir: string,
	expected: { skill_id: string; workflow_digest: string; validator_registry_digest: string; core_version: string },
): SkillRuntimeBinding {
	const path = join(packageDir, "generation.lock.json");
	const stat = lstatSync(path);
	if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`Skill generation lock is not a regular file: ${path}`);
	const raw = object(JSON.parse(readFileSync(path, "utf8")) as unknown, "skill generation lock");
	if (raw.schema !== "sure.skill.generation.lock.v1") throw new Error("Unsupported skill generation lock schema.");
	if (raw.host !== "pi" && raw.host !== "portable") throw new Error("Skill generation lock host is invalid.");
	const binding: SkillRuntimeBinding = {
		host: raw.host,
		skill_id: stringValue(raw.skill_id, "skill_id"),
		workflow_digest: digestValue(raw.workflow_digest, "workflow_digest"),
		validator_registry_digest: digestValue(raw.validator_registry_digest, "validator_registry_digest"),
		semantic_backend_registry_digest: digestValue(
			raw.semantic_backend_registry_digest,
			"semantic_backend_registry_digest",
		),
		semantic_runtime_digest: digestValue(raw.semantic_runtime_digest, "semantic_runtime_digest"),
		executor_registry_digest: digestValue(raw.executor_registry_digest, "executor_registry_digest"),
		core_package_version: stringValue(raw.core_package_version, "core_package_version"),
	};
	if (binding.skill_id !== expected.skill_id)
		throw new Error("Skill generation lock does not match the selected skill.");
	if (binding.workflow_digest !== expected.workflow_digest) {
		throw new Error("Skill generation lock does not match the run workflow digest.");
	}
	if (binding.validator_registry_digest !== expected.validator_registry_digest) {
		throw new Error("Skill generation lock does not match the run validator registry.");
	}
	if (binding.core_package_version !== expected.core_version) {
		throw new Error("Skill generation lock does not match the active Core version.");
	}
	return binding;
}

function sha256(path: string): string {
	return `sha256:${createHash("sha256").update(readFileSync(path)).digest("hex")}`;
}

export function artifactRef(
	path: string,
	unitId: string,
	runDir: string,
	options: {
		artifact_id?: string;
		origin?: ArtifactRef["origin"];
		source_root?: string;
		reference_snapshot_digest?: string;
	} = {},
): ArtifactRef {
	const stat = lstatSync(path);
	if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`Validator artifact is not a regular file: ${path}`);
	return {
		artifact_id: options.artifact_id ?? `unit-${unitId}`.slice(0, 128),
		path,
		resolved_path: realpathSync.native(path),
		sha256: sha256(path),
		size: stat.size,
		media_type: "application/json",
		origin: options.origin ?? "local_staging",
		source_root: options.source_root ?? runDir,
		...(options.reference_snapshot_digest === undefined
			? {}
			: { reference_snapshot_digest: options.reference_snapshot_digest }),
	};
}

function unavailableEntries(
	validators: readonly RegisteredValidatorDescriptor[],
	artifactDigest: string,
	reason: string,
	runtimeDigest?: string,
): ValidatorEvidenceEntry[] {
	return validators.map((validator) => ({
		validator_id: validator.id,
		...(validator.backend_operation_id === undefined ? {} : { backend_operation_id: validator.backend_operation_id }),
		verdict: "NOT_EXECUTED",
		artifact_digest: artifactDigest,
		reason_code: "CAPABILITY_MISSING",
		diagnostics: [reason],
		...(runtimeDigest === undefined ? {} : { runtime_digest: runtimeDigest }),
	}));
}

export function unavailableRegisteredValidation(
	registryDigest: string,
	validators: readonly RegisteredValidatorDescriptor[],
	artifactDigest: string,
	reason: string,
	runtimeDigest?: string,
): RegisteredValidationResult {
	const entries = unavailableEntries(validators, artifactDigest, reason, runtimeDigest);
	return {
		verdict: "NOT_EXECUTED",
		reason,
		evidence: {
			schema: "sure.validator.evidence.v1",
			source: "surectl",
			registry_digest: registryDigest,
			...(runtimeDigest === undefined ? {} : { runtime_digest: runtimeDigest }),
			validators: entries,
		},
	};
}

function aggregate(entries: readonly ValidatorEvidenceEntry[]): RegisteredValidationResult["verdict"] {
	if (entries.length === 0) return "NOT_EXECUTED";
	if (entries.some((entry) => entry.verdict === "NOT_EXECUTED")) return "NOT_EXECUTED";
	if (entries.some((entry) => entry.verdict === "FAIL")) return "FAIL";
	return "PASS";
}

function diagnostics(result: ExecutorRunResult): string[] {
	const messages = [...result.request_validation.errors, ...(result.receipt_validation?.errors ?? [])];
	for (const diagnostic of result.receipt?.diagnostics ?? []) {
		if (typeof diagnostic.message === "string") messages.push(diagnostic.message);
		else if (typeof diagnostic.text === "string") messages.push(diagnostic.text);
		else messages.push(JSON.stringify(diagnostic));
	}
	return [...new Set(messages.filter((message) => message.trim() !== ""))];
}

function executionVerdict(
	result: ExecutorRunResult,
	admissionErrors: readonly string[] = [],
): ValidatorEvidenceEntry["verdict"] {
	if (admissionErrors.length > 0) return "NOT_EXECUTED";
	if (!result.receipt || !result.receipt_validation?.valid || !result.capability.admitted) return "NOT_EXECUTED";
	if (result.receipt.lifecycle === "SUCCEEDED") return "PASS";
	if (["FAILED", "PARTIAL", "CANCELLED"].includes(result.receipt.lifecycle)) return "FAIL";
	return "NOT_EXECUTED";
}

function safeKey(value: string): string {
	return value.replaceAll(/[^A-Za-z0-9._-]+/g, "_").slice(0, 80);
}

function requestFor(
	options: RegisteredValidationOptions,
	validator: RegisteredValidatorDescriptor,
	operation: ReturnType<typeof resolveSemanticBackendOperation>,
	index: number,
): ExecutionRequest {
	const semantic = {
		schema: "sure.semantic.validation.request.v1",
		run_id: options.run.runId,
		branch_id: options.branch_id,
		unit_id: options.unit_id,
		attempt: options.attempt,
		validator_id: validator.id,
		backend_operation_id: operation.operation_id,
		artifact_digest: options.artifact.sha256,
		workflow_digest: options.run.workflowDigest,
		validator_registry_digest: options.validator_registry_digest,
		runtime_digest: options.runtime_binding.semantic_runtime_digest,
		backend_registry_digest: operation.registry_digest,
		...(operation.bundle_digest === undefined ? {} : { backend_bundle_digest: operation.bundle_digest }),
		backend_resource_digest: operation.resource_digest,
		script_args: [...(validator.script_args ?? [])],
		policy_digest: options.policy_digest,
		...(options.run.policySnapshotDigest === undefined
			? {}
			: { policy_snapshot_digest: options.run.policySnapshotDigest }),
	};
	const semanticRequestDigest = canonicalJsonDigest(semantic as unknown as JsonValue);
	return {
		schema: "sure.execution_request.v1",
		request_id: `validation-${options.invocation_id}-${index + 1}`,
		semantic_request_digest: semanticRequestDigest,
		run_id: options.run.runId,
		unit_id: options.unit_id,
		attempt: options.attempt,
		operation: "validation",
		subject: {
			bundle_manifest_path: options.artifact.path,
			bundle_digest: options.artifact.sha256,
			runtime_identity_digest: options.runtime_binding.semantic_runtime_digest,
		},
		inputs: [options.artifact],
		entrypoint: {
			executable: options.python_executable,
			argv: [
				operation.path,
				"--run-dir",
				options.run.runDir,
				"--produces",
				options.artifact.path,
				...(validator.script_args ?? []),
			],
			working_directory: options.package_dir,
		},
		runtime_requirements: {
			executor_kind: "python",
			harness_python_executable: options.python_executable,
			semantic_backend_operation_id: operation.operation_id,
			semantic_backend_registry_digest: operation.registry_digest,
			...(operation.bundle_digest === undefined ? {} : { semantic_backend_bundle_digest: operation.bundle_digest }),
			semantic_backend_resource_digest: operation.resource_digest,
			portable_runtime_digest: options.runtime_binding.semantic_runtime_digest,
		},
		capability_requirements: [
			{
				capability_id: "sure.execution.harness-python",
				capability_class: "execution_capability",
				required: true,
			},
		],
		reference_snapshot_digest: options.reference_snapshot_digest,
		output_root: {
			path: options.artifacts_root,
			resolved_path: options.artifacts_resolved_root,
			scope_id: options.run.runId,
			policy_digest: options.policy_digest,
			writable: true,
		},
		policy_digest: options.policy_digest,
		...(options.run.policySnapshotDigest === undefined
			? {}
			: { policy_snapshot_digest: options.run.policySnapshotDigest }),
		created_at: options.created_at,
	};
}

export interface SemanticRuntimeEnvironmentOptions {
	base_environment?: NodeJS.ProcessEnv;
	run: CoreRunRecord;
	workspace_root: string;
	policy_digest: string;
}

/** Build the isolated environment shared by registered validators and operations. */
export function semanticRuntimeEnvironment(
	options: SemanticRuntimeEnvironmentOptions,
	runtimeRoot: string,
): NodeJS.ProcessEnv {
	const environment: NodeJS.ProcessEnv = {
		...(options.base_environment ?? process.env),
		PYTHONHASHSEED: "0",
		PYTHONDONTWRITEBYTECODE: "1",
		PYTHONNOUSERSITE: "1",
		PYTHONPATH: runtimeRoot,
		SURE_REPOSITORY_ROOT: options.workspace_root,
		SURE_RUNTIME_SUPPORT_ROOT: runtimeRoot,
		SURE_SEMANTIC_BACKEND_MANIFEST: join(runtimeRoot, "semantic-backends.json"),
		SURE_SEMANTIC_BACKEND_ROOT: join(runtimeRoot, "backends"),
		SURE_POLICY_DIGEST: options.policy_digest,
	};
	delete environment.SURE_SITE_POLICY_SNAPSHOT;
	delete environment.SURE_POLICY_SNAPSHOT_DIGEST;
	if (options.run.policySnapshotPath !== undefined) {
		environment.SURE_SITE_POLICY_SNAPSHOT = options.run.policySnapshotPath;
		if (options.run.policySnapshotDigest !== undefined) {
			environment.SURE_POLICY_SNAPSHOT_DIGEST = options.run.policySnapshotDigest;
		}
		delete environment.SURE_SITE_POLICY;
	}
	delete environment.PYTHONHOME;
	delete environment.PYTHONSTARTUP;
	delete environment.SURE_CANONICAL_SKILLS_ROOT;
	delete environment.SURE_LEGACY_SKILLS_ROOT;
	return environment;
}

export function runRegisteredValidators(options: RegisteredValidationOptions): RegisteredValidationResult {
	if (!options.runtime_root) {
		const reason = "portable semantic validator runtime was not provided";
		return unavailableRegisteredValidation(
			options.validator_registry_digest,
			options.validators,
			options.artifact.sha256,
			reason,
		);
	}
	let verification: ReturnType<typeof verifyPortableRuntime>;
	try {
		verification = verifyPortableRuntime(options.runtime_root, {
			expected_runtime_digest: options.runtime_binding.semantic_runtime_digest,
			expected_core_package_version: options.runtime_binding.core_package_version,
			expected_semantic_backend_registry_digest: options.runtime_binding.semantic_backend_registry_digest,
			expected_executor_registry_digest: options.runtime_binding.executor_registry_digest,
		});
	} catch (error) {
		const reason = error instanceof Error ? error.message : String(error);
		return unavailableRegisteredValidation(
			options.validator_registry_digest,
			options.validators,
			options.artifact.sha256,
			reason,
		);
	}
	const entries: ValidatorEvidenceEntry[] = [];
	const environment = semanticRuntimeEnvironment(options, verification.root);
	for (const [index, validator] of options.validators.entries()) {
		if (!validator.backend_operation_id) {
			entries.push(
				...unavailableEntries(
					[validator],
					options.artifact.sha256,
					`validator ${validator.id} has no registered backend operation`,
					verification.lock.runtime_digest,
				),
			);
			continue;
		}
		let operation: ReturnType<typeof resolveSemanticBackendOperation>;
		try {
			operation = resolveSemanticBackendOperation(verification.root, validator.backend_operation_id, {
				manifestPath: join(verification.root, "semantic-backends.json"),
				expectedRegistryDigest: verification.lock.semantic_backend_registry_digest,
				environment,
			});
			if (operation.kind !== "validate") throw new Error(`${operation.operation_id} is not a validate operation`);
			if (!operation.consumer_skill_ids.includes(options.runtime_binding.skill_id)) {
				throw new Error(
					`${options.runtime_binding.skill_id} is not an admitted consumer of ${operation.operation_id}`,
				);
			}
		} catch (error) {
			entries.push(
				...unavailableEntries(
					[validator],
					options.artifact.sha256,
					error instanceof Error ? error.message : String(error),
					verification.lock.runtime_digest,
				),
			);
			continue;
		}
		if (
			operation.requires_policy_snapshot &&
			(options.run.policySnapshotPath === undefined || options.run.policySnapshotDigest === undefined)
		) {
			entries.push({
				validator_id: validator.id,
				backend_operation_id: operation.operation_id,
				verdict: "NOT_EXECUTED",
				artifact_digest: options.artifact.sha256,
				reason_code: "CAPABILITY_MISSING",
				diagnostics: [`${operation.operation_id} requires an immutable site-policy snapshot bound to the run`],
				runtime_digest: verification.lock.runtime_digest,
				backend_registry_digest: operation.registry_digest,
				...(operation.bundle_digest === undefined ? {} : { backend_bundle_digest: operation.bundle_digest }),
				backend_resource_digest: operation.resource_digest,
			});
			continue;
		}
		const request = requestFor(options, validator, operation, index);
		const key = `${index + 1}-${safeKey(validator.id)}`;
		const persistedRequest = options.persist_request(key, request);
		const result = executeRequest(request, {
			kind: "python",
			executor_digest: options.run.executorDigest ?? options.runtime_binding.executor_registry_digest,
			executor_version: options.runtime_binding.core_package_version,
			working_directory: options.package_dir,
			allowed_output_roots: [options.run.runDir, ...(options.run.outputDir ? [options.run.outputDir] : [])],
			forbidden_output_roots: options.forbidden_output_roots,
			timeout_ms: operation.timeout_ms,
			environment,
		});
		const persistedReceipt = result.receipt ? options.persist_receipt(key, result.receipt) : undefined;
		const persistedAdmission = options.persist_admission_trace?.(key, result.admission_trace);
		const admissionErrors = validateExecutionAdmissionReceiptBinding(request, result.admission_trace, {
			receipt: result.receipt,
			receipt_valid: result.receipt_validation?.valid,
			capability_admitted: result.capability.admitted,
		});
		const verdict = executionVerdict(result, admissionErrors);
		const reasonCode =
			admissionErrors.length > 0
				? "INVALID_CONTRACT"
				: verdict === "PASS"
					? "VALIDATION_PASSED"
					: verdict === "FAIL"
						? "VALIDATION_FAILED"
						: result.outcome.reason_code;
		entries.push({
			validator_id: validator.id,
			backend_operation_id: operation.operation_id,
			verdict,
			artifact_digest: options.artifact.sha256,
			reason_code: reasonCode,
			diagnostics: [...diagnostics(result), ...admissionErrors],
			runtime_digest: verification.lock.runtime_digest,
			backend_registry_digest: operation.registry_digest,
			...(operation.bundle_digest === undefined ? {} : { backend_bundle_digest: operation.bundle_digest }),
			backend_resource_digest: operation.resource_digest,
			request_path: persistedRequest.path,
			request_digest: persistedRequest.digest,
			...(persistedAdmission === undefined
				? {}
				: { admission_path: persistedAdmission.path, admission_digest: persistedAdmission.digest }),
			...(persistedReceipt === undefined
				? {}
				: { receipt_path: persistedReceipt.path, receipt_digest: persistedReceipt.digest }),
		});
	}
	try {
		const currentArtifact = artifactRef(options.artifact.path, options.unit_id, options.run.runDir);
		if (
			currentArtifact.sha256 !== options.artifact.sha256 ||
			currentArtifact.size !== options.artifact.size ||
			currentArtifact.resolved_path !== options.artifact.resolved_path
		) {
			throw new Error("validator input artifact changed during validation");
		}
	} catch (error) {
		const reason = error instanceof Error ? error.message : String(error);
		for (const entry of entries) {
			entry.verdict = "NOT_EXECUTED";
			entry.reason_code = "DIGEST_MISMATCH";
			entry.diagnostics = [...entry.diagnostics, reason];
		}
	}
	try {
		verifyPortableRuntime(verification.root, {
			expected_runtime_digest: verification.lock.runtime_digest,
			expected_core_package_version: verification.lock.core_package_version,
			expected_semantic_backend_registry_digest: verification.lock.semantic_backend_registry_digest,
			expected_executor_registry_digest: verification.lock.executor_registry_digest,
		});
	} catch (error) {
		const reason = `portable runtime changed during validation: ${error instanceof Error ? error.message : String(error)}`;
		for (const entry of entries) {
			entry.verdict = "NOT_EXECUTED";
			entry.reason_code = "DIGEST_MISMATCH";
			entry.diagnostics = [...entry.diagnostics, reason];
		}
	}
	const verdict = aggregate(entries);
	const reason =
		verdict === "PASS"
			? "registered validators passed"
			: verdict === "FAIL"
				? "registered validator failed"
				: "registered validator capability was not executed";
	return {
		verdict,
		reason,
		evidence: {
			schema: "sure.validator.evidence.v1",
			source: "surectl",
			registry_digest: options.validator_registry_digest,
			runtime_digest: verification.lock.runtime_digest,
			validators: entries,
		},
	};
}
