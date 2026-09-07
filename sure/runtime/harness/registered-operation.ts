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
	createOperationExecutionEvidence,
	type OperationExecutionEvidence,
	validatePolicySnapshot,
} from "@earendil-works/sure-core";
import {
	loadSemanticBackendManifest,
	repositoryRootForPackage,
	resolveSemanticBackendOperation,
} from "@earendil-works/sure-core/evaluation";

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
	execute(): PiBackendProcessResult;
}

export interface PiRegisteredOperationResult extends PiBackendProcessResult {
	evidence?: OperationExecutionEvidence;
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
	readonly reasonCode: "CAPABILITY_MISSING" | "INVALID_CONTRACT";

	constructor(
		message: string,
		reasonCode: "CAPABILITY_MISSING" | "INVALID_CONTRACT",
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
