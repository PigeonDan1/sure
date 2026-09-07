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

interface RegisteredImplementation {
	artifact_mode: "preexisting" | "mutating" | "producing";
	requires_policy_snapshot: boolean;
	registry_digest: string;
	bundle_digest?: string;
	resource_digest: string;
}

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
	const repositoryRoot = repositoryRootForPackage(ctx.packageDir, {});
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
