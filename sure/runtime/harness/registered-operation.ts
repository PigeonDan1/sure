import { createHash } from "node:crypto";
import {
	closeSync,
	constants,
	existsSync,
	fstatSync,
	lstatSync,
	openSync,
	readFileSync,
	realpathSync,
} from "node:fs";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import {
	canonicalJsonDigest,
	createBoundExecutionReceipt,
	createExecutionAdmissionTrace,
	createOperationExecutionEvidence,
	inspectExecutionArtifact,
	projectExecutionEvidence,
	createOutcome,
	EXECUTOR_KINDS,
	EXECUTOR_TRUST_LEVELS,
	type OperationExecutionEvidence,
	validateExecutionAdmissionReceiptBinding,
	validateExecutionReceipt,
	validateExecutionRequest,
	validatePolicySnapshot,
} from "@earendil-works/sure-core";
import type {
	ArtifactRef,
	CapabilityEvidence,
	CapabilityRequirement,
	ExecutionInputBindingResolver,
	ExecutionLifecycle,
	ExecutionOperation,
	ExecutionReceipt,
	ExecutionRequest,
	ExecutionProvenancePublisher,
	PublishedExecutionProvenance,
	PublishedExecutionRequest,
	ExecutorIdentity,
	JsonValue,
} from "@earendil-works/sure-core";
import {
	createRegisteredOperationRequest,
	loadSemanticBackendManifest,
	registeredOperationCapabilityRequirements,
	repositoryRootForPackage,
	resolveSemanticBackendOperation,
	type ResolvedSemanticBackend,
} from "@earendil-works/sure-core/evaluation";
import { resolveHarnessPython } from "./resolve.ts";

export interface PiBackendProcessResult {
	ok: boolean;
	stdout: string;
	stderr: string;
	status: number | null;
}

export interface PiRegisteredOperationOptions {
	ctx: SureHookContext;
	unit_id: string;
	attempt: number;
	operation_id: string;
	script_id: string;
	artifact_input_path: string;
	artifact_output_path?: string;
	/** Canonical operation kind used when the host issues a provenance request. */
	request_operation?: ExecutionOperation;
	/** Arguments after the stable --run-dir/--produces prefix. */
	script_args?: readonly string[];
	/** Resolved context for operations with a conditional input contract. */
	input_context?: Readonly<Record<string, unknown>>;
	input_context_digest?: string;
	input_resolver?: ExecutionInputBindingResolver;
	execute(): PiBackendProcessResult;
}

export interface PiRegisteredOperationResult extends PiBackendProcessResult {
	evidence?: OperationExecutionEvidence;
}

interface PiHostSession {
	readonly invocation_id: string;
	readonly request_id: string;
	readonly receipt_id: string;
	readonly started_at: string;
	readonly run_id: string;
	readonly run_dir: string;
	readonly package_dir: string;
	readonly artifacts_root: string;
	readonly artifacts_resolved_root: string;
	readonly branch_id: string;
	readonly workflow_digest: string;
	readonly validator_registry_digest: string;
	readonly semantic_runtime_digest: string;
	readonly semantic_backend_registry_digest: string;
	readonly executor_registry_digest: string;
	readonly core_package_version: string;
	readonly reference_snapshot_digest: string;
	readonly policy_digest: string;
	readonly policy_snapshot_digest?: string;
	readonly executor: ExecutorIdentity;
	readonly forbidden_output_roots: readonly string[];
	readonly python_executable?: string;
	readonly publisher: ExecutionProvenancePublisher;
	readonly capability_evidence_for?: (
		requirements: readonly CapabilityRequirement[],
	) => readonly CapabilityEvidence[];
}

export interface PiRegisteredValidatorOptions {
	ctx: SureHookContext;
	unit_id: string;
	attempt: number;
	operation_id: string;
	validator_id?: string;
	script_id: string;
	artifact_path: string;
	execute(resolved_path: string): PiBackendProcessResult;
}

export interface PiRegisteredValidatorEvidence {
	schema: "sure.validator.evidence.v1";
	source: "pi_hook";
	registry_digest: string;
	validators: readonly [
		{
			validator_id: string;
			backend_operation_id: string;
			verdict: "PASS" | "FAIL" | "NOT_EXECUTED";
			artifact_digest: string;
			reason_code: string;
			diagnostics: readonly string[];
			backend_resource_digest?: string;
			backend_bundle_digest?: string;
			unit_id: string;
			attempt: number;
		},
	];
}

export interface PiRegisteredValidatorResult extends PiBackendProcessResult {
	verdict: "PASS" | "FAIL" | "NOT_EXECUTED";
	evidence?: PiRegisteredValidatorEvidence;
}

interface RegisteredImplementation {
	artifact_mode: "preexisting" | "mutating" | "producing";
	requires_policy_snapshot: boolean;
	registry_digest: string;
	bundle_digest?: string;
	resource_digest: string;
	backend: ResolvedSemanticBackend;
}

interface RegisteredValidatorImplementation {
	registry_digest: string;
	bundle_digest?: string;
	resource_digest: string;
	requires_policy_snapshot: boolean;
	path: string;
}

type ValidatorEvidenceIdentity = {
	registry_digest: string;
	bundle_digest?: string;
	resource_digest?: string;
};

interface RegistryBinding {
	manifestPath: string;
	environment: NodeJS.ProcessEnv;
	expectedRegistryDigest?: string;
	repositoryRoot: string;
	distributionLocked: boolean;
}

const MAX_DIAGNOSTIC_LENGTH = 4096;
const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/;

class PiOperationUnavailableError extends Error {
	readonly reasonCode: "CAPABILITY_MISSING" | "INVALID_CONTRACT" | "PATH_OUT_OF_SCOPE";

	constructor(
		message: string,
		reasonCode: "CAPABILITY_MISSING" | "INVALID_CONTRACT" | "PATH_OUT_OF_SCOPE",
	) {
		super(message);
		this.name = "PiOperationUnavailableError";
		this.reasonCode = reasonCode;
	}
}

function contained(root: string, candidate: string): boolean {
	const rel = relative(root, candidate);
	return rel === "" || (rel !== ".." && !rel.startsWith(`..${sep}`) && !isAbsolute(rel));
}

function regularFileBytes(path: string, root: string): Buffer {
	const lexical = resolve(path);
	const lexicalRoot = resolve(root);
	if (!contained(lexicalRoot, lexical)) throw new Error("operation artifact is outside the run artifact root");
	const stat = lstatSync(lexical);
	if (!stat.isFile() || stat.isSymbolicLink()) throw new Error("operation artifact is not a regular file");
	const resolvedPath = resolve(realpathSync.native(lexical));
	const resolvedRoot = resolve(realpathSync.native(lexicalRoot));
	if (!contained(resolvedRoot, resolvedPath)) throw new Error("operation artifact resolves outside the run artifact root");
	let descriptor: number | undefined;
	try {
		descriptor = openSync(lexical, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
		const opened = fstatSync(descriptor);
		if (!opened.isFile()) throw new Error("operation artifact is not a regular file");
		return readFileSync(descriptor);
	} finally {
		if (descriptor !== undefined) closeSync(descriptor);
	}
}

function regularFileDigest(path: string, root: string): string {
	return `sha256:${createHash("sha256").update(regularFileBytes(path, root)).digest("hex")}`;
}

function fileDigest(path: string): string {
	const stat = lstatSync(path);
	if (!stat.isFile() || stat.isSymbolicLink()) throw new Error("operation entrypoint is not a regular file");
	return `sha256:${createHash("sha256").update(readFileSync(path)).digest("hex")}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function generationRegistryDigest(ctx: SureHookContext): string | undefined {
	const generationLockPath = join(ctx.packageDir, "generation.lock.json");
	if (!existsSync(generationLockPath)) return undefined;
	const lock = JSON.parse(readFileSync(generationLockPath, "utf8")) as unknown;
	if (
		!isRecord(lock) ||
		lock.schema !== "sure.skill.generation.lock.v1" ||
		lock.host !== "pi" ||
		lock.skill_id !== ctx.skill.name ||
		!SHA256_DIGEST.test(String(lock.semantic_backend_registry_digest ?? ""))
	) {
		throw new Error("registered operation Pi generation lock is invalid");
	}
	return String(lock.semantic_backend_registry_digest);
}

function registryBinding(ctx: SureHookContext): RegistryBinding {
	const repositoryRoot = repositoryRootForPackage(
		ctx.packageDir,
		ctx.repoRoot === undefined ? {} : { ...process.env, SURE_REPOSITORY_ROOT: ctx.repoRoot },
	);
	const environment = { ...process.env, SURE_REPOSITORY_ROOT: repositoryRoot };
	const lockedRegistryDigest = generationRegistryDigest(ctx);
	const canonicalManifest = join(
		repositoryRoot,
		"sure",
		"canonical",
		"shared",
		"evaluation",
		"backend-manifest.json",
	);
	if (existsSync(canonicalManifest)) {
		return {
			manifestPath: canonicalManifest,
			environment,
			repositoryRoot,
			distributionLocked: lockedRegistryDigest !== undefined,
			...(lockedRegistryDigest === undefined ? {} : { expectedRegistryDigest: lockedRegistryDigest }),
		};
	}

	const packageManifest = join(ctx.packageDir, "semantic-backends.json");
	if (lockedRegistryDigest === undefined || !existsSync(packageManifest)) {
		throw new Error("registered operation has no canonical registry or locked Pi distribution registry");
	}
	return {
		manifestPath: packageManifest,
		environment,
		expectedRegistryDigest: lockedRegistryDigest,
		repositoryRoot,
		distributionLocked: true,
	};
}

function implementationSource(
	ctx: SureHookContext,
	bundle: ReturnType<typeof loadSemanticBackendManifest>["bundles"][number],
	registry: RegistryBinding,
	operation: ReturnType<typeof loadSemanticBackendManifest>["bundles"][number]["operations"][number],
	actualDigest: string,
): "canonical" | "legacy" | undefined {
	if (registry.distributionLocked) {
		// Generated Pi packages carry a locked copy of the compatibility tree at
		// their package root. Select the verified registry tier by the bytes that
		// are actually about to run; this keeps legacy-compatible facades valid
		// while still allowing a future package to carry canonical bytes.
		if (operation.canonical_resource_digest === actualDigest) return "canonical";
		if (operation.legacy_resource_digest === actualDigest) return "legacy";
		return undefined;
	}
	const canonicalRoot =
		bundle.canonical_root_kind === "repository"
			? join(registry.repositoryRoot, bundle.canonical_root)
			: join(
					registry.repositoryRoot,
					"sure",
					"canonical",
					"skills",
					bundle.canonical_root.replace(/^skills\//, ""),
				);
	const legacyRoot =
		bundle.legacy_root_kind === "repository"
			? join(registry.repositoryRoot, bundle.legacy_root)
			: join(
					registry.repositoryRoot,
					"sure",
					"skills",
					bundle.legacy_root.replace(/^skills\//, ""),
				);
	const packageDir = resolve(ctx.packageDir);
	if (packageDir === resolve(canonicalRoot)) return "canonical";
	if (packageDir === resolve(legacyRoot)) return "legacy";
	throw new Error("registered operation package is neither a canonical/legacy root nor a locked Pi distribution");
}

function validatorImplementationSource(
	ctx: SureHookContext,
	registry: RegistryBinding,
	scriptId: string,
	packageEntrypoint: string,
): "canonical" | "legacy" | undefined {
	const packageRoot = resolve(ctx.packageDir);
	const canonicalSkillRoot = join(
		registry.repositoryRoot,
		"sure",
		"canonical",
		"skills",
		ctx.skill.name.replaceAll("_", "-"),
	);
	const legacySkillRoot = join(registry.repositoryRoot, "sure", "skills", ctx.skill.name);
	if (packageRoot === resolve(canonicalSkillRoot)) return "canonical";
	if (packageRoot === resolve(legacySkillRoot)) return "legacy";
	// Generated Pi packages are separate from both source trees.  Their
	// validator facade is generated from the canonical skill, so identify the
	// tier by comparing its bytes to the source facade before resolving the
	// immutable shared backend.
	const actualDigest = fileDigest(packageEntrypoint);
	const canonicalFacade = join(canonicalSkillRoot, "scripts", scriptId);
	if (existsSync(canonicalFacade) && fileDigest(canonicalFacade) === actualDigest) return "canonical";
	const legacyFacade = join(legacySkillRoot, "scripts", scriptId);
	if (existsSync(legacyFacade) && fileDigest(legacyFacade) === actualDigest) return "legacy";
	return undefined;
}

function registeredImplementation(options: PiRegisteredOperationOptions): RegisteredImplementation {
	const registry = registryBinding(options.ctx);
	const manifest = loadSemanticBackendManifest(options.ctx.packageDir, registry);
	const declaration = manifest.bundles
		.flatMap((bundle) =>
			bundle.operations
				.filter((operation) => operation.operation_id === options.operation_id)
				.map((operation) => ({ bundle, operation })),
		)
		.at(0);
	if (!declaration) throw new Error(`registered operation is unavailable: ${options.operation_id}`);
	if (declaration.operation.kind !== "execute") {
		throw new Error(`registered operation is not executable: ${options.operation_id}`);
	}
	if (!declaration.operation.consumer_skill_ids.includes(options.ctx.skill.name)) {
		throw new Error(`${options.ctx.skill.name} is not an admitted consumer of ${options.operation_id}`);
	}
	if (declaration.operation.artifact_mode === undefined) {
		throw new Error(`registered operation does not declare artifact_mode: ${options.operation_id}`);
	}
	const expectedEntrypoint = `scripts/${options.script_id}`;
	if (declaration.operation.entrypoint !== expectedEntrypoint) {
		throw new Error(
			`registered operation entrypoint ${declaration.operation.entrypoint} does not match ${expectedEntrypoint}`,
		);
	}

	const packageEntrypoint = join(options.ctx.packageDir, expectedEntrypoint);
	const actualDigest = fileDigest(packageEntrypoint);
	const acceptedDigests = [
		declaration.operation.legacy_resource_digest,
		declaration.operation.canonical_resource_digest,
	].filter((value): value is string => value !== undefined);
	if (acceptedDigests.length === 0 || !acceptedDigests.includes(actualDigest)) {
		throw new Error(`registered operation entrypoint digest mismatch: ${options.operation_id}`);
	}

	// Resolve as well as inspect the declaration. This verifies the locked
	// canonical/package tree before the Pi compatibility entrypoint can run.
	const requiredSource = implementationSource(
		options.ctx,
		declaration.bundle,
		registry,
		declaration.operation,
		actualDigest,
	);
	const resolved = resolveSemanticBackendOperation(options.ctx.packageDir, options.operation_id, {
		...registry,
		...(requiredSource === undefined ? {} : { requiredSource }),
	});
	if (resolved.artifact_mode !== declaration.operation.artifact_mode) {
		throw new Error(`registered operation artifact_mode resolution mismatch: ${options.operation_id}`);
	}
	if (resolved.resource_digest !== actualDigest) {
		throw new Error(`registered operation resolved resource does not match executed bytes: ${options.operation_id}`);
	}
	return {
		artifact_mode: declaration.operation.artifact_mode,
		requires_policy_snapshot: resolved.requires_policy_snapshot,
		registry_digest: manifest.registry_digest,
		...(resolved.bundle_digest === undefined ? {} : { bundle_digest: resolved.bundle_digest }),
		resource_digest: actualDigest,
		backend: resolved,
	};
}

/**
 * Resolve a validator through the same immutable registry used by execute
 * operations.  The package entrypoint may be a generated/legacy facade (and
 * therefore has a different digest); the resolved canonical or legacy backend
 * is the authority, while the facade remains the Pi-compatible launch path.
 */
function registeredValidatorImplementation(
	options: PiRegisteredValidatorOptions,
): RegisteredValidatorImplementation {
	const registry = registryBinding(options.ctx);
	const manifest = loadSemanticBackendManifest(options.ctx.packageDir, registry);
	const declaration = manifest.bundles
		.flatMap((bundle) =>
			bundle.operations
				.filter((operation) => operation.operation_id === options.operation_id)
				.map((operation) => ({ bundle, operation })),
		)
		.at(0);
	if (!declaration) throw new Error(`registered validator is unavailable: ${options.operation_id}`);
	if (declaration.operation.kind !== "validate") {
		throw new Error(`registered backend is not a validator: ${options.operation_id}`);
	}
	if (!declaration.operation.consumer_skill_ids.includes(options.ctx.skill.name)) {
		throw new Error(`${options.ctx.skill.name} is not an admitted consumer of ${options.operation_id}`);
	}
	const expectedEntrypoint = `scripts/${options.script_id}`;
	if (declaration.operation.entrypoint !== expectedEntrypoint) {
		throw new Error(
			`registered validator entrypoint ${declaration.operation.entrypoint} does not match ${expectedEntrypoint}`,
		);
	}
	// A generated Pi skill launches a thin facade from its own package. Verify
	// that it is a regular, contained file and identify the source tier from its
	// exact bytes. The immutable digest check below then applies to the resolved
	// canonical/legacy backend bytes as well.
	const packageEntrypoint = join(options.ctx.packageDir, expectedEntrypoint);
	regularFileBytes(packageEntrypoint, options.ctx.packageDir);
	const requiredSource = validatorImplementationSource(
		options.ctx,
		registry,
		options.script_id,
		packageEntrypoint,
	);
	if (registry.distributionLocked && requiredSource === undefined) {
		throw new Error(`registered validator facade digest is not a canonical or legacy source: ${options.operation_id}`);
	}
	const resolved = resolveSemanticBackendOperation(options.ctx.packageDir, options.operation_id, {
		...registry,
		...(requiredSource === undefined ? {} : { requiredSource }),
	});
	if (resolved.kind !== "validate") {
		throw new Error(`resolved backend is not a validator: ${options.operation_id}`);
	}
	if (!resolved.consumer_skill_ids.includes(options.ctx.skill.name)) {
		throw new Error(`${options.ctx.skill.name} is not an admitted consumer of ${options.operation_id}`);
	}
	return {
		registry_digest: manifest.registry_digest,
		...(resolved.bundle_digest === undefined ? {} : { bundle_digest: resolved.bundle_digest }),
		resource_digest: resolved.resource_digest,
		requires_policy_snapshot: resolved.requires_policy_snapshot,
		path: resolved.path,
	};
}

function requireBoundPolicySnapshot(ctx: SureHookContext): void {
	const snapshotPath = ctx.run.policySnapshotPath;
	const snapshotDigest = ctx.run.policySnapshotDigest;
	if (snapshotPath === undefined || snapshotDigest === undefined) {
		throw new PiOperationUnavailableError(
			"registered operation requires an immutable site-policy snapshot bound to the run",
			"CAPABILITY_MISSING",
		);
	}
	try {
		const bytes = regularFileBytes(snapshotPath, join(ctx.runDir, "artifacts"));
		const snapshot = validatePolicySnapshot(JSON.parse(bytes.toString("utf8")) as unknown);
		if (snapshot.snapshot_digest !== snapshotDigest) {
			throw new Error("run policy snapshot digest does not match the validated snapshot");
		}
		if (ctx.run.policyDigest !== undefined && snapshot.policy_digest !== ctx.run.policyDigest) {
			throw new Error("run policy digest does not match the validated snapshot");
		}
	} catch (error) {
		if (error instanceof PiOperationUnavailableError) throw error;
		throw new PiOperationUnavailableError(
			error instanceof Error ? error.message : String(error),
			"INVALID_CONTRACT",
		);
	}
}

function diagnostic(result: PiBackendProcessResult): string[] {
	const detail = result.stderr.trim() || result.stdout.trim();
	return detail === "" ? [] : [detail.slice(0, MAX_DIAGNOSTIC_LENGTH)];
}

function unavailable(
	options: PiRegisteredOperationOptions,
	inputDigest: string | undefined,
	error: unknown,
): PiRegisteredOperationResult {
	const message = error instanceof Error ? error.message : String(error);
	const reasonCode = error instanceof PiOperationUnavailableError ? error.reasonCode : "INVALID_CONTRACT";
	return {
		ok: false,
		stdout: "",
		stderr: message,
		status: null,
		...(inputDigest === undefined
			? {}
			: {
					evidence: createOperationExecutionEvidence({
						source: "pi_hook",
						operation_id: options.operation_id,
						verdict: "NOT_EXECUTED",
						reason_code: reasonCode,
						diagnostics: [message],
						artifact_input_digest: inputDigest,
						artifact_input_path: resolve(options.artifact_input_path),
						artifact_output_path: resolve(options.artifact_output_path ?? options.artifact_input_path),
						branch_id: "main",
						unit_id: options.unit_id,
						attempt: options.attempt,
					}),
				}),
	};
}

function validHostSession(value: unknown): value is PiHostSession {
	if (!isRecord(value)) return false;
	for (const field of [
		"invocation_id",
		"request_id",
		"receipt_id",
		"started_at",
		"run_id",
		"run_dir",
		"package_dir",
		"artifacts_root",
		"artifacts_resolved_root",
		"branch_id",
		"workflow_digest",
		"validator_registry_digest",
		"semantic_runtime_digest",
		"semantic_backend_registry_digest",
		"executor_registry_digest",
		"core_package_version",
		"reference_snapshot_digest",
		"policy_digest",
	] as const) {
		if (typeof value[field] !== "string" || value[field].trim() === "") return false;
	}
	if (!isRecord(value.executor) || typeof value.publisher !== "object" || value.publisher === null) return false;
	if (
		typeof value.executor.executor_id !== "string" ||
		value.executor.executor_id.trim() === "" ||
		typeof value.executor.version !== "string" ||
		value.executor.version.trim() === "" ||
		!SHA256_DIGEST.test(String(value.executor.digest)) ||
		!EXECUTOR_KINDS.includes(value.executor.kind as (typeof EXECUTOR_KINDS)[number]) ||
		!EXECUTOR_TRUST_LEVELS.includes(value.executor.trust_level as (typeof EXECUTOR_TRUST_LEVELS)[number])
	)
		return false;
	if (
		!Array.isArray(value.forbidden_output_roots) ||
		value.forbidden_output_roots.some((root) => typeof root !== "string" || !isAbsolute(root))
	)
		return false;
	if (value.python_executable !== undefined && (typeof value.python_executable !== "string" || !isAbsolute(value.python_executable)))
		return false;
	const publisher = value.publisher as Record<string, unknown>;
	if (typeof publisher.publishRequest !== "function" || typeof publisher.publishCompletion !== "function") return false;
	return true;
}

function issueHostSession(options: PiRegisteredOperationOptions): PiHostSession {
	const issuer = options.ctx.executionProvenance;
	if (issuer === undefined) {
		throw new PiOperationUnavailableError(
			"registered operation has no host-issued provenance session",
			"CAPABILITY_MISSING",
		);
	}
	const raw = issuer.issue({ unit_id: options.unit_id, attempt: options.attempt, operation_id: options.operation_id });
	if (!validHostSession(raw)) {
		throw new PiOperationUnavailableError(
			"host-issued provenance session is incomplete or malformed",
			"INVALID_CONTRACT",
		);
	}
	const session = raw;
	if (session.run_id !== options.ctx.run.runId) {
		throw new PiOperationUnavailableError("host provenance session run_id does not match the Pi run", "INVALID_CONTRACT");
	}
	if (resolve(session.run_dir) !== resolve(options.ctx.runDir)) {
		throw new PiOperationUnavailableError("host provenance session run_dir does not match the Pi run", "INVALID_CONTRACT");
	}
	if (resolve(session.package_dir) !== resolve(options.ctx.packageDir)) {
		throw new PiOperationUnavailableError("host provenance session package_dir does not match the Pi skill", "INVALID_CONTRACT");
	}
	if (!SHA256_DIGEST.test(session.workflow_digest) || !SHA256_DIGEST.test(session.validator_registry_digest)) {
		throw new PiOperationUnavailableError("host provenance session workflow binding is invalid", "INVALID_CONTRACT");
	}
	if (!SHA256_DIGEST.test(session.semantic_runtime_digest) || !SHA256_DIGEST.test(session.semantic_backend_registry_digest)) {
		throw new PiOperationUnavailableError("host provenance session runtime binding is invalid", "INVALID_CONTRACT");
	}
	if (!SHA256_DIGEST.test(session.executor_registry_digest) || !SHA256_DIGEST.test(session.reference_snapshot_digest)) {
		throw new PiOperationUnavailableError("host provenance session executor/reference binding is invalid", "INVALID_CONTRACT");
	}
	if (!SHA256_DIGEST.test(session.policy_digest)) {
		throw new PiOperationUnavailableError("host provenance session policy binding is invalid", "INVALID_CONTRACT");
	}
	if (session.policy_snapshot_digest !== undefined && !SHA256_DIGEST.test(session.policy_snapshot_digest)) {
		throw new PiOperationUnavailableError("host provenance session policy snapshot digest is invalid", "INVALID_CONTRACT");
	}
	if (!isAbsolute(session.artifacts_root) || !isAbsolute(session.artifacts_resolved_root)) {
		throw new PiOperationUnavailableError("host provenance session artifact roots must be absolute", "INVALID_CONTRACT");
	}
	if (!contained(resolve(options.ctx.runDir), resolve(session.artifacts_root))) {
		throw new PiOperationUnavailableError("host provenance artifact root escaped the Pi run", "PATH_OUT_OF_SCOPE");
	}
	return session;
}

function hostArtifactRef(
	path: string,
	root: string,
	artifactId: string,
	origin: ArtifactRef["origin"],
): ArtifactRef {
	const lexical = resolve(path);
	const lexicalRoot = resolve(root);
	if (!contained(lexicalRoot, lexical)) throw new Error("execution artifact is outside the host artifact root");
	const rootStat = lstatSync(lexicalRoot);
	if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) throw new Error("host artifact root is not a regular directory");
	const stat = lstatSync(lexical);
	if (stat.isSymbolicLink()) throw new Error("execution artifact is a symlink");
	const resolvedRoot = resolve(realpathSync.native(lexicalRoot));
	const resolvedPath = resolve(realpathSync.native(lexical));
	if (!contained(resolvedRoot, resolvedPath)) throw new Error("execution artifact resolves outside the host artifact root");
	const inspected = inspectExecutionArtifact(lexical);
	return {
		artifact_id: artifactId,
		path: lexical,
		resolved_path: resolvedPath,
		sha256: inspected.sha256,
		size: inspected.size,
		media_type: inspected.media_type,
		origin,
		source_root: lexicalRoot,
		kind: inspected.kind,
		digest_kind: inspected.digest_kind,
	};
}

function hostCapabilityEvidence(
	session: PiHostSession,
	requirements: readonly CapabilityRequirement[],
	result: PiBackendProcessResult,
	observedAt: string,
	pythonExecutable: string | undefined,
): CapabilityEvidence[] {
	if (session.capability_evidence_for !== undefined) {
		return [...session.capability_evidence_for(requirements)].map((item) => ({ ...item }));
	}
	return requirements.map((requirement) => {
		// A callback's exit code is not evidence of arbitrary hardware.  The
		// bridge can only establish that the bound harness runtime was selected;
		// every other execution capability needs an explicit host probe.
		const runtimeAvailable =
			requirement.capability_id === "sure.execution.harness-python" &&
			pythonExecutable !== undefined &&
			pythonExecutable.trim() !== "" &&
			result.status !== null;
		const status: CapabilityEvidence["status"] = runtimeAvailable ? "AVAILABLE" : "MISSING";
		const base: CapabilityEvidence = {
			capability_id: requirement.capability_id,
			capability_class: requirement.capability_class,
			status,
			source: "executor",
			observed_at: observedAt,
			details: {
				execution_surface: "pi_hook",
				process_started: result.status !== null,
				runtime_bound: pythonExecutable !== undefined && pythonExecutable.trim() !== "",
			},
		};
		return { ...base, evidence_digest: canonicalJsonDigest(base as unknown as JsonValue) };
	});
}

function hostLifecycle(result: PiBackendProcessResult, outputPresent: boolean): ExecutionLifecycle {
	if (result.status === null) return "NOT_STARTED";
	if (result.ok && result.status === 0) return outputPresent ? "SUCCEEDED" : "PARTIAL";
	return "FAILED";
}

function hostDiagnosticRecords(messages: readonly string[]): readonly Record<string, JsonValue>[] {
	return messages
		.filter((message) => message.trim() !== "")
		.map((message, index) => ({ code: index === 0 ? "PI_EXECUTION" : "PI_EXECUTION_DIAGNOSTIC", message }));
}

function hostOutputPath(
	options: PiRegisteredOperationOptions,
	implementation: RegisteredImplementation,
	artifactsRoot: string,
): string {
	if (implementation.backend.output_contract === undefined) {
		return resolve(options.artifact_output_path ?? options.artifact_input_path);
	}
	const declared = implementation.backend.output_contract.outputs[0];
	if (declared === undefined) throw new Error(`${options.operation_id} output contract has no output`);
	return resolve(options.artifact_output_path ?? join(artifactsRoot, declared.path));
}

function hostPublicationFailure(
	options: PiRegisteredOperationOptions,
	implementation: RegisteredImplementation,
	inputDigest: string,
	result: PiBackendProcessResult,
	publishedRequest: PublishedExecutionRequest,
	branchId: string,
	runtimeDigest: string,
	outputPath: string,
	error: unknown,
): PiRegisteredOperationResult {
	const message = error instanceof Error ? error.message : String(error);
	const diagnostics = [...diagnostic(result), message, "execution may have occurred before provenance publication failed"];
	return {
		...result,
		ok: false,
		stderr: result.stderr.trim() === "" ? diagnostics.join("; ") : result.stderr,
		evidence: createOperationExecutionEvidence({
			source: "pi_hook",
			operation_id: options.operation_id,
			artifact_mode: implementation.artifact_mode,
			verdict: "NOT_EXECUTED",
			reason_code: "INVALID_CONTRACT",
			diagnostics,
			artifact_input_digest: inputDigest,
			artifact_input_path: resolve(options.artifact_input_path),
			artifact_output_path: outputPath,
			runtime_digest: runtimeDigest,
			backend_registry_digest: implementation.registry_digest,
			...(implementation.bundle_digest === undefined ? {} : { backend_bundle_digest: implementation.bundle_digest }),
			backend_resource_digest: implementation.resource_digest,
			request_path: publishedRequest.documents.latest.path,
			request_digest: publishedRequest.documents.latest.digest,
			branch_id: branchId,
			unit_id: options.unit_id,
			attempt: options.attempt,
			outcome: {
				validator_verdict: "NOT_EXECUTED",
				workflow_disposition: "BLOCK",
				outcome: "NOT_EXECUTED",
				reason_code: "INVALID_CONTRACT",
			},
		}),
	};
}

function runHostRegisteredOperation(
	options: PiRegisteredOperationOptions,
	inputDigest: string,
	implementation: RegisteredImplementation,
): PiRegisteredOperationResult {
	if (options.request_operation === undefined) {
		return unavailable(
			options,
			inputDigest,
			new PiOperationUnavailableError(
				"registered operation has no execution_request_operation binding",
				"INVALID_CONTRACT",
			),
		);
	}
	let session: PiHostSession;
	try {
		session = issueHostSession(options);
		if (implementation.registry_digest !== session.semantic_backend_registry_digest) {
			throw new PiOperationUnavailableError(
				"host provenance semantic backend registry does not match the admitted operation",
				"INVALID_CONTRACT",
			);
		}
	} catch (error) {
		return unavailable(options, inputDigest, error);
	}

	let pythonExecutable = session.python_executable;
	if (pythonExecutable === undefined || pythonExecutable.trim() === "") {
		const runtime = resolveHarnessPython(options.ctx.packageDir, { activate: false });
		if (!runtime.ok || runtime.contract === undefined) {
			return unavailable(
				options,
				inputDigest,
				new PiOperationUnavailableError(
					runtime.error ?? "SURE harness Python runtime is unavailable",
					"CAPABILITY_MISSING",
				),
			);
		}
		pythonExecutable = runtime.contract.python_executable;
	}

	let request: ExecutionRequest;
	let publishedRequest: PublishedExecutionRequest;
	let outputPath: string;
	try {
		outputPath = hostOutputPath(options, implementation, session.artifacts_root);
		if (!contained(resolve(session.artifacts_root), outputPath)) {
			throw new PiOperationUnavailableError(
				"registered operation output path is outside the host artifact root",
				"PATH_OUT_OF_SCOPE",
			);
		}
		const inputArtifact = hostArtifactRef(
			options.artifact_input_path,
			session.artifacts_root,
			"operation-input",
			"local_staging",
		);
		const facadeBackend: ResolvedSemanticBackend = {
			...implementation.backend,
			path: resolve(join(options.ctx.packageDir, "scripts", options.script_id)),
		};
		request = createRegisteredOperationRequest({
			request_id: session.request_id,
			run_id: session.run_id,
			run_dir: session.run_dir,
			workflow_digest: session.workflow_digest,
			policy_snapshot_digest: session.policy_snapshot_digest,
			branch_id: session.branch_id,
			unit_id: options.unit_id,
			attempt: options.attempt,
			request_operation: options.request_operation,
			backend: facadeBackend,
			script_args: options.script_args ?? [],
			artifact: inputArtifact,
			input_context: options.input_context,
			input_context_digest: options.input_context_digest,
			input_resolver: options.input_resolver,
			output_path: outputPath,
			python_executable: pythonExecutable,
			package_dir: session.package_dir,
			artifacts_root: session.artifacts_root,
			artifacts_resolved_root: session.artifacts_resolved_root,
			semantic_runtime_digest: session.semantic_runtime_digest,
			reference_snapshot_digest: session.reference_snapshot_digest,
			policy_digest: session.policy_digest,
			created_at: session.started_at,
		});
		const requestValidation = validateExecutionRequest(request, {
			allowed_output_roots: [session.run_dir],
			forbidden_output_roots: session.forbidden_output_roots,
		});
		if (!requestValidation.valid) {
			throw new PiOperationUnavailableError(
				`host execution request failed Core validation: ${requestValidation.errors.join("; ")}`,
				requestValidation.outcome.reason_code === "PATH_OUT_OF_SCOPE" ? "PATH_OUT_OF_SCOPE" : "INVALID_CONTRACT",
			);
		}
		publishedRequest = session.publisher.publishRequest(request);
	} catch (error) {
		return unavailable(options, inputDigest, error);
	}

	let result: PiBackendProcessResult;
	try {
		result = options.execute();
		if (!isRecord(result) || typeof result.ok !== "boolean" || typeof result.stdout !== "string" || typeof result.stderr !== "string") {
			throw new Error("registered operation callback returned an invalid process result");
		}
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		result = { ok: false, stdout: "", stderr: message, status: 1 };
	}

	try {
		let outputArtifact: ArtifactRef | undefined;
		let outputDiagnostic: string | undefined;
		try {
			const outputId = implementation.backend.output_contract?.outputs[0]?.artifact_id ?? "operation-output";
			outputArtifact = hostArtifactRef(outputPath, session.artifacts_root, outputId, "generated");
		} catch (error) {
			outputDiagnostic = error instanceof Error ? error.message : String(error);
		}
		const lifecycle = hostLifecycle(result, outputArtifact !== undefined);
		const finishedAt = new Date().toISOString();
		const requirements = registeredOperationCapabilityRequirements(implementation.backend);
		const capabilityEvidence = hostCapabilityEvidence(session, requirements, result, finishedAt, pythonExecutable);
		const receiptDiagnostics = [
			...diagnostic(result),
			...(outputDiagnostic === undefined || outputArtifact !== undefined ? [] : [outputDiagnostic]),
		];
		const receipt = createBoundExecutionReceipt(request, {
			receipt_id: session.receipt_id,
			executor: session.executor,
			lifecycle,
			capability_evidence: capabilityEvidence,
			outputs: outputArtifact === undefined ? [] : [outputArtifact],
			started_at: session.started_at,
			finished_at: lifecycle === "NOT_STARTED" ? undefined : finishedAt,
			...(result.status === null ? {} : { exit_code: result.status }),
			...(receiptDiagnostics.length === 0 ? {} : { diagnostics: hostDiagnosticRecords(receiptDiagnostics) }),
		});
		const boundary = {
			allowed_output_roots: [session.run_dir],
			forbidden_output_roots: session.forbidden_output_roots,
		};
		const receiptValidation = validateExecutionReceipt(request, receipt, boundary);
		const outcome = receiptValidation.outcome;
		const admission = createExecutionAdmissionTrace(request, finishedAt, outcome, {
			probe_invoked: false,
			execute_invoked: result.status !== null,
			receipt,
			receipt_valid: receiptValidation.valid,
		});
		const admissionErrors = validateExecutionAdmissionReceiptBinding(request, admission, {
			receipt,
			receipt_valid: receiptValidation.valid,
			capability_admitted: receiptValidation.capability.admitted,
		});
		let provenance: PublishedExecutionProvenance;
		try {
			provenance = session.publisher.publishCompletion({
				request,
				receipt,
				admission,
				validation_options: {
					require_receipt: true,
					allowed_output_roots: [session.run_dir],
					forbidden_output_roots: session.forbidden_output_roots,
				},
				legacy_views: {
					execution_surface: "pi_hook",
					execution_result: result.stderr.trim() || result.stdout.trim() || null,
				},
			});
		} catch (error) {
			return hostPublicationFailure(
				options,
				implementation,
				inputDigest,
				result,
				publishedRequest,
				session.branch_id,
				session.semantic_runtime_digest,
				outputPath,
				error,
			);
		}
		const historyFailure =
			!provenance.validation.valid && provenance.validation.outcome.reason_code !== "CAPABILITY_MISSING";
		const contractErrors = [
			...admissionErrors,
			...(historyFailure ? provenance.validation.errors : []),
		];
		const validReceipt = receiptValidation.valid && provenance.validation.valid;
		const missingSuccessfulOutput = result.ok && result.status === 0 && outputArtifact === undefined;
		const projection =
			contractErrors.length > 0
				? { verdict: "NOT_EXECUTED" as const, reason_code: "INVALID_CONTRACT" as const }
				: projectExecutionEvidence({
						lifecycle,
						receipt_valid: validReceipt,
						capability_admitted: receiptValidation.capability.admitted,
						missing_output: missingSuccessfulOutput,
						outcome_reason_code: outcome.reason_code,
					});
		const finalOutcome =
			contractErrors.length > 0
				? createOutcome({
						validatorVerdict: "NOT_EXECUTED",
						workflowDisposition: "BLOCK",
						reasonCode: "INVALID_CONTRACT",
						diagnostics: contractErrors.map((message) => ({ code: "EXECUTION_PROVENANCE", message })),
					})
				: outcome;
		const diagnostics = [
			...diagnostic(result),
			...(outputDiagnostic === undefined || outputArtifact !== undefined ? [] : [outputDiagnostic]),
			...receiptValidation.errors,
			...admissionErrors,
			...(historyFailure ? provenance.validation.errors : []),
			...(missingSuccessfulOutput ? [`${options.operation_id} did not bind the gate artifact output`] : []),
		];
		return {
			...result,
			ok: projection.verdict === "PASS",
			...(missingSuccessfulOutput && result.stderr.trim() === ""
				? { stderr: `${options.operation_id} did not bind the gate artifact output` }
				: {}),
			evidence: createOperationExecutionEvidence({
				source: "pi_hook",
				operation_id: options.operation_id,
				artifact_mode: implementation.artifact_mode,
				verdict: projection.verdict,
				reason_code: projection.reason_code,
				diagnostics,
				artifact_input_digest: inputDigest,
				artifact_input_path: resolve(options.artifact_input_path),
				...(outputArtifact === undefined ? {} : { artifact_output_digest: outputArtifact.sha256 }),
				artifact_output_path: outputPath,
				runtime_digest: session.semantic_runtime_digest,
				backend_registry_digest: implementation.registry_digest,
				...(implementation.bundle_digest === undefined ? {} : { backend_bundle_digest: implementation.bundle_digest }),
				backend_resource_digest: implementation.resource_digest,
				...(request.input_binding === undefined
					? {}
					: {
							input_contract_digest: request.input_binding.contract_digest,
							input_selector_id: request.input_binding.selector_id,
							input_context_digest: request.input_binding.context_digest,
							input_binding_digest: request.input_binding.binding_digest,
						}),
				request_path: provenance.documents.request.latest.path,
				request_digest: provenance.documents.request.latest.digest,
				admission_path: provenance.documents.admission.latest.path,
				admission_digest: provenance.documents.admission.latest.digest,
				...(provenance.documents.receipt === undefined
					? {}
					: {
							receipt_path: provenance.documents.receipt.latest.path,
							receipt_digest: provenance.documents.receipt.latest.digest,
					}),
				contract_path: provenance.documents.contract.latest.path,
				contract_digest: provenance.documents.contract.latest.digest,
				execution_history_digest: provenance.history_digest,
				branch_id: session.branch_id,
				unit_id: options.unit_id,
				attempt: options.attempt,
				outcome: finalOutcome,
			}),
		};
	} catch (error) {
		return hostPublicationFailure(
			options,
			implementation,
			inputDigest,
			result,
			publishedRequest,
			session.branch_id,
			session.semantic_runtime_digest,
			outputPath,
			error,
		);
	}
}

function validatorUnavailable(
	options: PiRegisteredValidatorOptions,
	artifactDigest: string | undefined,
	implementation: ValidatorEvidenceIdentity | undefined,
	error: unknown,
): PiRegisteredValidatorResult {
	const message = error instanceof Error ? error.message : String(error);
	const reasonCode = error instanceof PiOperationUnavailableError ? error.reasonCode : "INVALID_CONTRACT";
	return {
		ok: false,
		stdout: "",
		stderr: message,
		status: null,
		verdict: "NOT_EXECUTED",
		...(artifactDigest === undefined || implementation === undefined
			? {}
			: {
					evidence: {
						schema: "sure.validator.evidence.v1",
						source: "pi_hook",
						registry_digest: implementation.registry_digest,
						validators: [
							{
								validator_id: options.validator_id ?? options.operation_id,
								backend_operation_id: options.operation_id,
								verdict: "NOT_EXECUTED",
								artifact_digest: artifactDigest,
								reason_code: reasonCode,
								diagnostics: [message],
									...(implementation.resource_digest === undefined
										? {}
										: { backend_resource_digest: implementation.resource_digest }),
								...(implementation.bundle_digest === undefined
									? {}
									: { backend_bundle_digest: implementation.bundle_digest }),
								unit_id: options.unit_id,
								attempt: options.attempt,
							},
						],
					},
				}),
	};
}

/**
 * Pi compatibility adapter for a registered legacy gate runner. It preserves
 * the existing subprocess callback and adds a Core-defined evidence envelope.
 * It deliberately does not invent an execution request or receipt; absence of
 * those fields keeps this projection below trusted/formal execution assurance.
 */
export function runPiRegisteredOperation(options: PiRegisteredOperationOptions): PiRegisteredOperationResult {
	const artifactRoot = join(options.ctx.runDir, "artifacts");
	let inputDigest: string | undefined;
	try {
		inputDigest = regularFileDigest(options.artifact_input_path, artifactRoot);
		const implementation = registeredImplementation(options);
		if (implementation.requires_policy_snapshot) {
			requireBoundPolicySnapshot(options.ctx);
		}
		if (options.ctx.executionProvenance !== undefined) {
			return runHostRegisteredOperation(options, inputDigest, implementation);
		}
		const result = options.execute();
		const outputPath = options.artifact_output_path ?? options.artifact_input_path;
		let outputDigest: string | undefined;
		try {
			outputDigest = regularFileDigest(outputPath, artifactRoot);
		} catch {
			outputDigest = undefined;
		}
		const processStarted = result.status !== null;
		const missingSuccessfulOutput = result.ok && result.status === 0 && outputDigest === undefined;
		const passed = result.ok && result.status === 0 && !missingSuccessfulOutput;
		const verdict = passed ? "PASS" : missingSuccessfulOutput || !processStarted ? "NOT_EXECUTED" : "FAIL";
		const reasonCode = passed
			? "EXECUTION_SUCCEEDED"
			: missingSuccessfulOutput
				? "INVALID_CONTRACT"
				: processStarted
					? "EXECUTION_FAILED"
					: "CAPABILITY_MISSING";
		const diagnostics = [
			...diagnostic(result),
			...(missingSuccessfulOutput ? ["registered operation did not leave a regular gate artifact"] : []),
		];
		const evidence = createOperationExecutionEvidence({
			source: "pi_hook",
			operation_id: options.operation_id,
			artifact_mode: implementation.artifact_mode,
			verdict,
			reason_code: reasonCode,
			diagnostics,
			artifact_input_digest: inputDigest,
			artifact_input_path: resolve(options.artifact_input_path),
			...(outputDigest === undefined ? {} : { artifact_output_digest: outputDigest }),
			artifact_output_path: resolve(outputPath),
			backend_registry_digest: implementation.registry_digest,
			...(implementation.bundle_digest === undefined
				? {}
				: { backend_bundle_digest: implementation.bundle_digest }),
			backend_resource_digest: implementation.resource_digest,
			branch_id: "main",
			unit_id: options.unit_id,
			attempt: options.attempt,
		});
		return {
			...result,
			ok: passed,
			...(missingSuccessfulOutput && result.stderr.trim() === ""
				? { stderr: "registered operation did not leave a regular gate artifact" }
				: {}),
			evidence,
		};
	} catch (error) {
		return unavailable(options, inputDigest, error);
	}
}

/**
 * Pi adapter for a deterministic, read-only semantic validator.  A validator
 * is deliberately separate from `runPiRegisteredOperation`: a successful
 * executor is only `VALIDATION_PENDING`, and this adapter is the point at
 * which the independent checker can produce PASS/FAIL evidence.
 */
export function runPiRegisteredValidator(options: PiRegisteredValidatorOptions): PiRegisteredValidatorResult {
	const artifactRoot = join(options.ctx.runDir, "artifacts");
	let artifactDigest: string | undefined;
	let implementation: RegisteredValidatorImplementation | undefined;
	let evidenceIdentity: ValidatorEvidenceIdentity | undefined;
	try {
		artifactDigest = regularFileDigest(options.artifact_path, artifactRoot);
		implementation = registeredValidatorImplementation(options);
		if (implementation.requires_policy_snapshot) {
			requireBoundPolicySnapshot(options.ctx);
		}
		const beforePath = realpathSync.native(resolve(options.artifact_path));
		const result = options.execute(implementation.path);
		let afterDigest: string | undefined;
		let changed = false;
		try {
			afterDigest = regularFileDigest(options.artifact_path, artifactRoot);
			changed =
				afterDigest !== artifactDigest || realpathSync.native(resolve(options.artifact_path)) !== beforePath;
		} catch {
			changed = true;
		}
		const processStarted = result.status !== null;
		const passed = result.ok && result.status === 0 && !changed;
		const verdict: PiRegisteredValidatorResult["verdict"] = passed
			? "PASS"
			: processStarted && !changed
				? "FAIL"
				: "NOT_EXECUTED";
		const reasonCode = passed
			? "VALIDATION_PASSED"
			: changed
				? "INVALID_CONTRACT"
				: processStarted
					? "VALIDATION_FAILED"
					: "CAPABILITY_MISSING";
		const diagnostics = [
			...diagnostic(result),
			...(changed ? ["semantic validator changed or removed its input artifact"] : []),
		];
		const evidence: PiRegisteredValidatorEvidence = {
			schema: "sure.validator.evidence.v1",
			source: "pi_hook",
			registry_digest: implementation.registry_digest,
			validators: [
				{
					validator_id: options.validator_id ?? options.operation_id,
					backend_operation_id: options.operation_id,
					verdict,
					artifact_digest: afterDigest ?? artifactDigest,
					reason_code: reasonCode,
					diagnostics,
					backend_resource_digest: implementation.resource_digest,
					...(implementation.bundle_digest === undefined
						? {}
						: { backend_bundle_digest: implementation.bundle_digest }),
					unit_id: options.unit_id,
					attempt: options.attempt,
				},
			],
		};
		return { ...result, ok: passed, verdict, evidence };
	} catch (error) {
		if (implementation === undefined) {
			try {
				const registry = registryBinding(options.ctx);
				const manifest = loadSemanticBackendManifest(options.ctx.packageDir, registry);
				const declaration = manifest.bundles
					.flatMap((bundle) => bundle.operations.filter((operation) => operation.operation_id === options.operation_id))
					.at(0);
				if (declaration) {
					evidenceIdentity = {
						registry_digest: manifest.registry_digest,
						...(declaration.canonical_resource_digest === undefined
							? {}
							: { resource_digest: declaration.canonical_resource_digest }),
					};
				}
			} catch {
				// Keep the fail-closed result even when the registry itself is unavailable.
			}
		}
		return validatorUnavailable(options, artifactDigest, implementation ?? evidenceIdentity, error);
	}
}
