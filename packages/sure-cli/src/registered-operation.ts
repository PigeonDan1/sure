import { randomUUID } from "node:crypto";
import { join, resolve } from "node:path";
import type {
	ArtifactRef,
	CapabilityRequirement,
	CoreOutcome,
	CoreRunRecord,
	ExecutionOperation,
	ExecutionReceipt,
	ExecutionRequest,
	JsonValue,
} from "@earendil-works/sure-core";
import { canonicalJsonDigest, createOutcome, executionOutputContractDigest } from "@earendil-works/sure-core";
import { resolveSemanticBackendOperation, verifyPortableRuntime } from "@earendil-works/sure-core/evaluation";
import { type ExecutorRunResult, executeRequest } from "./executor.ts";
import {
	type PersistedValidatorDocument,
	type SkillRuntimeBinding,
	semanticRuntimeEnvironment,
} from "./registered-validator.ts";

export interface RegisteredOperationEvidence {
	schema: "sure.operation.execution.v1";
	source: "surectl";
	operation_id: string;
	verdict: "PASS" | "FAIL" | "NOT_EXECUTED";
	reason_code: string;
	diagnostics: readonly string[];
	artifact_input_digest: string;
	/** Input anchor used by a producer operation; omitted for legacy requests. */
	artifact_input_path?: string;
	/** Digest observed after the executor completed, when it emitted the gate artifact. */
	artifact_output_digest?: string;
	/** Declared output path for a producer/mutating contract. */
	artifact_output_path?: string;
	runtime_digest?: string;
	backend_registry_digest?: string;
	backend_bundle_digest?: string;
	backend_resource_digest?: string;
	request_path?: string;
	request_digest?: string;
	receipt_path?: string;
	receipt_digest?: string;
}

export interface RegisteredOperationResult {
	outcome: CoreOutcome;
	evidence: RegisteredOperationEvidence;
	request?: ExecutionRequest;
	receipt?: ExecutionReceipt;
	execution?: ExecutorRunResult;
}

export interface RegisteredOperationOptions {
	runtime_root?: string;
	runtime_binding: SkillRuntimeBinding;
	run: CoreRunRecord;
	branch_id: string;
	unit_id: string;
	attempt: number;
	operation_id: string;
	request_operation: ExecutionOperation;
	script_args: readonly string[];
	artifact: ArtifactRef;
	/** Optional output path for a producer; defaults to the contract's first output. */
	output_path?: string;
	python_executable: string;
	package_dir: string;
	workspace_root: string;
	artifacts_root: string;
	artifacts_resolved_root: string;
	reference_snapshot_digest: string;
	policy_digest: string;
	forbidden_output_roots: readonly string[];
	created_at: string;
	base_environment?: NodeJS.ProcessEnv;
	persist_request(request: ExecutionRequest): PersistedValidatorDocument;
	persist_receipt(receipt: ExecutionReceipt): PersistedValidatorDocument;
}

export interface RegisteredOperationSemanticBinding {
	run_id: string;
	branch_id: string;
	unit_id: string;
	attempt: number;
	operation_id: string;
	request_operation: ExecutionOperation;
	artifact_input_digest: string;
	artifact_input_path?: string;
	workflow_digest?: string;
	runtime_digest: string;
	backend_registry_digest: string;
	backend_bundle_digest?: string;
	backend_resource_digest: string;
	reference_snapshot_digest: string;
	script_args: readonly string[];
	policy_digest: string;
	artifact_output_path?: string;
	output_contract_digest?: string;
	capability_requirements_digest?: string;
}

export function registeredOperationSemanticDigest(binding: RegisteredOperationSemanticBinding): string {
	return canonicalJsonDigest({ schema: "sure.semantic.operation.request.v1", ...binding } as unknown as JsonValue);
}

/** Return the canonical capability set required by a registered operation. */
export function registeredOperationCapabilityRequirements(
	operation: ReturnType<typeof resolveSemanticBackendOperation>,
): readonly CapabilityRequirement[] {
	const requirements = new Map<string, CapabilityRequirement>();
	// Registered operations always execute through the SURE Python bridge.
	requirements.set("sure.execution.harness-python", {
		capability_id: "sure.execution.harness-python",
		capability_class: "execution_capability",
		required: true,
	});
	for (const requirement of operation.capability_requirements ?? []) {
		if (requirement.capability_id === "sure.execution.harness-python") continue;
		requirements.set(requirement.capability_id, { ...requirement });
	}
	return [...requirements.values()];
}

export function registeredOperationCapabilityRequirementsDigest(
	operation: ReturnType<typeof resolveSemanticBackendOperation>,
): string | undefined {
	return operation.capability_requirements === undefined
		? undefined
		: canonicalJsonDigest(registeredOperationCapabilityRequirements(operation) as unknown as JsonValue);
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

function unavailable(
	options: RegisteredOperationOptions,
	reason: string,
	runtimeDigest?: string,
): RegisteredOperationResult {
	const outcome = createOutcome({
		validatorVerdict: "NOT_EXECUTED",
		workflowDisposition: "WAIT",
		reasonCode: "CAPABILITY_MISSING",
	});
	return {
		outcome,
		evidence: {
			schema: "sure.operation.execution.v1",
			source: "surectl",
			operation_id: options.operation_id,
			verdict: "NOT_EXECUTED",
			reason_code: "CAPABILITY_MISSING",
			diagnostics: [reason],
			artifact_input_digest: options.artifact.sha256,
			...(runtimeDigest === undefined ? {} : { runtime_digest: runtimeDigest }),
		},
	};
}

function requestFor(
	options: RegisteredOperationOptions,
	operation: ReturnType<typeof resolveSemanticBackendOperation>,
): ExecutionRequest {
	const contract = operation.output_contract;
	if (contract !== undefined && contract.outputs.length !== 1) {
		throw new Error(`${operation.operation_id} currently requires exactly one declared output`);
	}
	const declaredOutputPath =
		contract === undefined ? undefined : join(options.artifacts_root, contract.outputs[0]?.path ?? "");
	if (
		contract !== undefined &&
		options.output_path !== undefined &&
		declaredOutputPath !== undefined &&
		resolve(options.output_path) !== resolve(declaredOutputPath)
	) {
		throw new Error(`${operation.operation_id} output_path does not match its output contract`);
	}
	const outputPath = contract === undefined ? options.artifact.path : (options.output_path ?? declaredOutputPath!);
	const contractDigest = contract === undefined ? undefined : executionOutputContractDigest(contract);
	const declaredCapabilities = registeredOperationCapabilityRequirements(operation);
	const capabilityRequirementsDigest = registeredOperationCapabilityRequirementsDigest(operation);
	const binding: RegisteredOperationSemanticBinding = {
		run_id: options.run.runId,
		branch_id: options.branch_id,
		unit_id: options.unit_id,
		attempt: options.attempt,
		operation_id: operation.operation_id,
		request_operation: options.request_operation,
		artifact_input_digest: options.artifact.sha256,
		workflow_digest: options.run.workflowDigest,
		runtime_digest: options.runtime_binding.semantic_runtime_digest,
		backend_registry_digest: operation.registry_digest,
		...(operation.bundle_digest === undefined ? {} : { backend_bundle_digest: operation.bundle_digest }),
		backend_resource_digest: operation.resource_digest,
		reference_snapshot_digest: options.reference_snapshot_digest,
		script_args: [...options.script_args],
		policy_digest: options.policy_digest,
		...(contract === undefined ? {} : { artifact_output_path: outputPath, output_contract_digest: contractDigest }),
		...(contract === undefined ? {} : { artifact_input_path: options.artifact.path }),
		...(capabilityRequirementsDigest === undefined
			? {}
			: { capability_requirements_digest: capabilityRequirementsDigest }),
	};
	return {
		schema: "sure.execution_request.v1",
		request_id: `operation-${randomUUID().replaceAll("-", "").slice(0, 20)}`,
		semantic_request_digest: registeredOperationSemanticDigest(binding),
		run_id: options.run.runId,
		unit_id: options.unit_id,
		attempt: options.attempt,
		operation: options.request_operation,
		subject: {
			bundle_manifest_path: options.artifact.path,
			bundle_digest: options.artifact.sha256,
			runtime_identity_digest: options.runtime_binding.semantic_runtime_digest,
		},
		inputs: [options.artifact],
		entrypoint: {
			executable: options.python_executable,
			argv: [operation.path, "--run-dir", options.run.runDir, "--produces", outputPath, ...options.script_args],
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
			workflow_digest: options.run.workflowDigest ?? "",
			branch_id: options.branch_id,
			script_args: [...options.script_args],
			artifact_input_digest: options.artifact.sha256,
			...(contract === undefined ? {} : { artifact_input_path: options.artifact.path }),
			...(contract === undefined
				? {}
				: { artifact_output_path: outputPath, output_contract_digest: contractDigest }),
			...(capabilityRequirementsDigest === undefined
				? {}
				: { capability_requirements_digest: capabilityRequirementsDigest }),
		},
		capability_requirements: [...declaredCapabilities],
		reference_snapshot_digest: options.reference_snapshot_digest,
		output_root: {
			path: options.artifacts_root,
			resolved_path: options.artifacts_resolved_root,
			scope_id: options.run.runId,
			policy_digest: options.policy_digest,
			writable: true,
		},
		policy_digest: options.policy_digest,
		created_at: options.created_at,
		...(contract === undefined ? {} : { output_contract: contract }),
	};
}

export function runRegisteredOperation(options: RegisteredOperationOptions): RegisteredOperationResult {
	if (!options.runtime_root) return unavailable(options, "portable semantic operation runtime was not provided");
	let verification: ReturnType<typeof verifyPortableRuntime>;
	try {
		verification = verifyPortableRuntime(options.runtime_root, {
			expected_runtime_digest: options.runtime_binding.semantic_runtime_digest,
			expected_core_package_version: options.runtime_binding.core_package_version,
			expected_semantic_backend_registry_digest: options.runtime_binding.semantic_backend_registry_digest,
			expected_executor_registry_digest: options.runtime_binding.executor_registry_digest,
		});
	} catch (error) {
		return unavailable(options, error instanceof Error ? error.message : String(error));
	}
	const environment = semanticRuntimeEnvironment(options, verification.root);
	let operation: ReturnType<typeof resolveSemanticBackendOperation>;
	try {
		operation = resolveSemanticBackendOperation(verification.root, options.operation_id, {
			manifestPath: join(verification.root, "semantic-backends.json"),
			expectedRegistryDigest: verification.lock.semantic_backend_registry_digest,
			environment,
		});
		if (operation.kind !== "execute") throw new Error(`${operation.operation_id} is not an execute operation`);
		if (!operation.consumer_skill_ids.includes(options.runtime_binding.skill_id)) {
			throw new Error(
				`${options.runtime_binding.skill_id} is not an admitted consumer of ${operation.operation_id}`,
			);
		}
		if (operation.artifact_mode === "producing" && operation.output_contract === undefined) {
			throw new Error(
				`${operation.operation_id} declares a producing artifact; an explicit output contract is required before gate binding`,
			);
		}
	} catch (error) {
		return unavailable(
			options,
			error instanceof Error ? error.message : String(error),
			verification.lock.runtime_digest,
		);
	}
	if (
		operation.requires_policy_snapshot &&
		(options.run.policySnapshotPath === undefined || options.run.policySnapshotDigest === undefined)
	) {
		return unavailable(
			options,
			`${operation.operation_id} requires an immutable site-policy snapshot bound to the run`,
			verification.lock.runtime_digest,
		);
	}
	let request: ExecutionRequest;
	try {
		request = requestFor(options, operation);
	} catch (error) {
		return unavailable(
			options,
			error instanceof Error ? error.message : String(error),
			verification.lock.runtime_digest,
		);
	}
	const persistedRequest = options.persist_request(request);
	const execution = executeRequest(request, {
		kind: "python",
		executor_digest: options.run.executorDigest ?? options.runtime_binding.executor_registry_digest,
		executor_version: options.runtime_binding.core_package_version,
		working_directory: options.package_dir,
		allowed_output_roots: [options.run.runDir, ...(options.run.outputDir ? [options.run.outputDir] : [])],
		forbidden_output_roots: options.forbidden_output_roots,
		timeout_ms: operation.timeout_ms,
		environment,
		output_paths: [
			operation.output_contract === undefined
				? options.artifact.path
				: (options.output_path ?? join(options.artifacts_root, operation.output_contract.outputs[0]?.path ?? "")),
		],
	});
	const persistedReceipt = execution.receipt ? options.persist_receipt(execution.receipt) : undefined;
	const outputPath =
		operation.output_contract === undefined
			? options.artifact.path
			: (options.output_path ?? resolve(options.artifacts_root, operation.output_contract.outputs[0]?.path ?? ""));
	const outputArtifact = execution.receipt?.outputs.find(
		(candidate) => resolve(candidate.path) === resolve(outputPath),
	);
	const validReceipt = execution.receipt !== undefined && execution.receipt_validation?.valid === true;
	const verdict =
		!validReceipt || !execution.capability.admitted
			? "NOT_EXECUTED"
			: execution.receipt?.lifecycle === "SUCCEEDED"
				? "PASS"
				: execution.receipt?.lifecycle === "NOT_STARTED"
					? "NOT_EXECUTED"
					: "FAIL";
	return {
		outcome: execution.outcome,
		request,
		receipt: execution.receipt,
		execution,
		evidence: {
			schema: "sure.operation.execution.v1",
			source: "surectl",
			operation_id: operation.operation_id,
			verdict,
			reason_code:
				verdict === "PASS" ? "EXECUTION_SUCCEEDED" : verdict === "FAIL" ? "EXECUTION_FAILED" : "CAPABILITY_MISSING",
			diagnostics: diagnostics(execution),
			artifact_input_digest: options.artifact.sha256,
			...(operation.output_contract === undefined ? {} : { artifact_input_path: options.artifact.path }),
			...(operation.output_contract === undefined ? {} : { artifact_output_path: outputPath }),
			...(outputArtifact === undefined ? {} : { artifact_output_digest: outputArtifact.sha256 }),
			runtime_digest: verification.lock.runtime_digest,
			backend_registry_digest: operation.registry_digest,
			...(operation.bundle_digest === undefined ? {} : { backend_bundle_digest: operation.bundle_digest }),
			backend_resource_digest: operation.resource_digest,
			request_path: persistedRequest.path,
			request_digest: persistedRequest.digest,
			...(persistedReceipt === undefined
				? {}
				: { receipt_path: persistedReceipt.path, receipt_digest: persistedReceipt.digest }),
		},
	};
}
