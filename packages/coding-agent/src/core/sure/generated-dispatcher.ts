import { createHash } from "node:crypto";
import { lstatSync, readFileSync } from "node:fs";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import type { ExecutionRequest, ExecutionRequestDispatcher, JsonValue } from "@earendil-works/sure-core";
import { canonicalJsonDigest } from "@earendil-works/sure-core";
import { loadSemanticBackendManifest } from "@earendil-works/sure-core/evaluation";
import {
	createNodeRequestDispatcher,
	type NodeRequestDispatcherBackendOptions,
	type NodeRequestDispatcherOptions,
} from "@earendil-works/sure-core/node";
import { verifyPiGeneratedPackageBinding } from "./execution-provenance.ts";
import type { SureHookContext } from "./types.ts";

export interface PiGeneratedDispatcherOptions {
	/** Explicit operation ids; defaults to the onboarding import operation. */
	readonly operation_ids?: readonly string[];
	/** Host-owned runtime resolver; omission is a fail-closed unavailable runtime. */
	readonly resolveRuntime?: NodeRequestDispatcherOptions["resolveRuntime"];
	/** Host-owned execution callback; omission is a fail-closed unavailable executor. */
	readonly executeBackend?: NodeRequestDispatcherOptions["executeBackend"];
	readonly environment?: NodeRequestDispatcherOptions["environment"];
	readonly includeSpawnErrorInStderr?: NodeRequestDispatcherOptions["includeSpawnErrorInStderr"];
}

export interface PiGeneratedDispatcherEnabledOptIn {
	readonly enabled: true;
	/** Host-owned label used to identify this explicit configuration in logs. */
	readonly configuration_id: string;
	/** The only operation ids this host is willing to dispatch. */
	readonly operation_ids: readonly string[];
	/** Required host adapters; omission is not a valid production opt-in. */
	readonly resolveRuntime: NonNullable<PiGeneratedDispatcherOptions["resolveRuntime"]>;
	readonly executeBackend: NonNullable<PiGeneratedDispatcherOptions["executeBackend"]>;
	readonly environment?: PiGeneratedDispatcherOptions["environment"];
	readonly includeSpawnErrorInStderr?: PiGeneratedDispatcherOptions["includeSpawnErrorInStderr"];
}

export type PiGeneratedDispatcherOptIn = { readonly enabled: false } | PiGeneratedDispatcherEnabledOptIn;

export type PiGeneratedDispatcherResolver = (request: ExecutionRequest) => ExecutionRequestDispatcher | undefined;

/**
 * Host-owned, explicit opt-in binding.  The digest covers configuration
 * intent (id and sorted allowlist), while generated package/runtime digests
 * remain the execution trust boundary.  This helper never reads environment
 * variables or agent state and returns undefined for the disabled state.
 */
export interface PiGeneratedDispatcherOptInBinding {
	readonly schema: "sure.pi.generated-dispatcher.opt-in.v1";
	readonly configuration_id: string;
	readonly configuration_digest: string;
	readonly operation_ids: readonly string[];
	readonly resolverForContext: (context: Omit<SureHookContext, "point">) => PiGeneratedDispatcherResolver;
}

const SAFE_OPT_IN_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

function requireSafeOptInId(value: unknown, field: string): string {
	if (typeof value !== "string" || !SAFE_OPT_IN_ID.test(value)) {
		throw new Error(`generated dispatcher opt-in ${field} must be a safe non-empty identifier`);
	}
	return value;
}

/**
 * Construct a host configuration that can be passed to
 * `executionDispatcherForRequestForContext`.  It is deliberately separate
 * from the default extension: callers must opt in in code with `enabled: true`.
 */
export function createPiGeneratedDispatcherOptIn(
	config: PiGeneratedDispatcherOptIn,
): PiGeneratedDispatcherOptInBinding | undefined {
	if (config.enabled === false) return undefined;
	const configurationId = requireSafeOptInId(config.configuration_id, "configuration_id");
	if (!Array.isArray(config.operation_ids) || config.operation_ids.length === 0) {
		throw new Error("generated dispatcher opt-in operation_ids must be a non-empty array");
	}
	const operationIds = [...config.operation_ids].map((value) => requireSafeOptInId(value, "operation_id")).sort();
	if (new Set(operationIds).size !== operationIds.length) {
		throw new Error("generated dispatcher opt-in operation_ids must be unique");
	}
	if (typeof config.resolveRuntime !== "function") {
		throw new Error("generated dispatcher opt-in requires a host runtime resolver");
	}
	if (typeof config.executeBackend !== "function") {
		throw new Error("generated dispatcher opt-in requires a host executor");
	}
	const configurationDigest = canonicalJsonDigest({
		schema: "sure.pi.generated-dispatcher.opt-in.v1",
		configuration_id: configurationId,
		enabled: true,
		operation_ids: operationIds,
	} as unknown as JsonValue);
	const binding: PiGeneratedDispatcherOptInBinding = {
		schema: "sure.pi.generated-dispatcher.opt-in.v1",
		configuration_id: configurationId,
		configuration_digest: configurationDigest,
		operation_ids: Object.freeze(operationIds),
		resolverForContext(context) {
			return createPiGeneratedLocalRequestDispatcherResolver(context, {
				operation_ids: operationIds,
				resolveRuntime: config.resolveRuntime,
				executeBackend: config.executeBackend,
				...(config.environment === undefined ? {} : { environment: config.environment }),
				...(config.includeSpawnErrorInStderr === undefined
					? {}
					: { includeSpawnErrorInStderr: config.includeSpawnErrorInStderr }),
			});
		},
	};
	return Object.freeze(binding);
}

interface LockedOperation {
	entrypoint: string;
	accepted_digests: readonly string[];
	script: string;
	timeout_ms: number;
}

interface LockedPackage {
	generation: NonNullable<ReturnType<typeof verifyPiGeneratedPackageBinding>>;
	operations: ReadonlyMap<string, LockedOperation>;
}

function digestFile(path: string): string {
	return `sha256:${createHash("sha256").update(readFileSync(path)).digest("hex")}`;
}

function contained(root: string, candidate: string): boolean {
	const relation = relative(resolve(root), resolve(candidate));
	return relation === "" || (relation !== ".." && !relation.startsWith(`..${sep}`) && !isAbsolute(relation));
}

function verifiedEntrypoint(packageDir: string, entrypoint: string, acceptedDigests: readonly string[]): string {
	if (!entrypoint.startsWith("scripts/") || entrypoint.includes("..")) {
		throw new Error(`generated dispatcher entrypoint is outside scripts/: ${entrypoint}`);
	}
	const script = entrypoint.slice("scripts/".length);
	if (script.trim() === "" || script.includes("/")) {
		throw new Error(`generated dispatcher entrypoint is not a direct script: ${entrypoint}`);
	}
	const path = join(packageDir, "scripts", script);
	if (!contained(join(packageDir, "scripts"), path)) {
		throw new Error(`generated dispatcher entrypoint escaped package scripts: ${entrypoint}`);
	}
	const stat = lstatSync(path);
	if (!stat.isFile() || stat.isSymbolicLink()) {
		throw new Error(`generated dispatcher entrypoint is not a regular file: ${entrypoint}`);
	}
	const actualDigest = digestFile(path);
	if (!acceptedDigests.includes(actualDigest)) {
		throw new Error(`generated dispatcher entrypoint digest mismatch: ${entrypoint}`);
	}
	return script;
}

function lockedOperations(
	context: Omit<SureHookContext, "point">,
	options: PiGeneratedDispatcherOptions,
): LockedPackage {
	const generation = verifyPiGeneratedPackageBinding(context);
	if (generation === undefined) throw new Error("generated dispatcher requires a Pi generation lock");
	const manifestPath = join(resolve(context.packageDir), "semantic-backends.json");
	const manifest = loadSemanticBackendManifest(context.packageDir, {
		manifestPath,
		expectedRegistryDigest: generation.semantic_backend_registry_digest,
	});
	const operationIds = options.operation_ids ?? ["sure.onboard.execute_import"];
	if (operationIds.length === 0) throw new Error("generated dispatcher requires at least one operation id");
	const locked = new Map<string, LockedOperation>();
	for (const operationId of operationIds) {
		if (locked.has(operationId)) throw new Error(`generated dispatcher operation is duplicated: ${operationId}`);
		const declaration = manifest.bundles
			.flatMap((bundle) =>
				bundle.operations
					.filter((operation) => operation.operation_id === operationId)
					.map((operation) => ({ bundle, operation })),
			)
			.at(0);
		if (declaration === undefined)
			throw new Error(`generated dispatcher operation is not registered: ${operationId}`);
		const { operation } = declaration;
		if (operation.kind !== "execute")
			throw new Error(`generated dispatcher operation is not executable: ${operationId}`);
		if (!operation.consumer_skill_ids.includes(context.skill.name)) {
			throw new Error(`generated dispatcher operation is not admitted for ${context.skill.name}: ${operationId}`);
		}
		if (!Number.isSafeInteger(operation.timeout_ms) || operation.timeout_ms <= 0) {
			throw new Error(`generated dispatcher operation timeout is invalid: ${operationId}`);
		}
		const acceptedDigests = [operation.canonical_resource_digest, operation.legacy_resource_digest].filter(
			(value): value is string => value !== undefined,
		);
		if (acceptedDigests.length === 0) throw new Error(`generated dispatcher has no resource digest: ${operationId}`);
		const script = verifiedEntrypoint(context.packageDir, operation.entrypoint, acceptedDigests);
		locked.set(operationId, {
			entrypoint: operation.entrypoint,
			accepted_digests: Object.freeze([...acceptedDigests]),
			script,
			timeout_ms: operation.timeout_ms,
		});
	}
	return { generation, operations: locked };
}

/**
 * Build an explicit host-only resolver from a verified generated Pi package.
 * Unsupported request operation ids return undefined and therefore retain the
 * legacy callback path; they never broaden the allowlist or read agent state.
 */
export function createPiGeneratedLocalRequestDispatcherResolver(
	context: Omit<SureHookContext, "point">,
	options: PiGeneratedDispatcherOptions = {},
): PiGeneratedDispatcherResolver {
	const lockedPackage = lockedOperations(context, options);
	const locked = lockedPackage.operations;
	const resolveRuntime =
		options.resolveRuntime ??
		(() => ({ ok: false, error: "generated dispatcher runtime adapter is not configured" }));
	const executeBackend =
		options.executeBackend ??
		((_backend: NodeRequestDispatcherBackendOptions) => ({
			ok: false,
			stdout: "",
			stderr: "generated dispatcher executor is not configured",
			status: null,
		}));
	const dispatcher = createNodeRequestDispatcher({
		ctx: { packageDir: resolve(context.packageDir), runDir: resolve(context.runDir) },
		allowedOperations: new Map([...locked].map(([operationId, operation]) => [operationId, operation.script])),
		timeoutMs: (request) => {
			const operationId = request.runtime_requirements.semantic_backend_operation_id;
			if (typeof operationId !== "string") throw new Error("generated dispatcher request has no operation id");
			const operation = locked.get(operationId);
			if (operation === undefined)
				throw new Error(`generated dispatcher operation is not allowlisted: ${operationId}`);
			return operation.timeout_ms;
		},
		availableCapabilityIds: new Set(["sure.execution.harness-python"]),
		environment: options.environment,
		includeSpawnErrorInStderr: options.includeSpawnErrorInStderr,
		resolveRuntime,
		executeBackend,
	});
	const assertPackageCurrent = (request: ExecutionRequest): LockedOperation => {
		const currentGeneration = verifyPiGeneratedPackageBinding(context);
		if (currentGeneration === undefined) throw new Error("generated dispatcher generation lock disappeared");
		if (
			currentGeneration.semantic_backend_registry_digest !==
			lockedPackage.generation.semantic_backend_registry_digest
		) {
			throw new Error("generated dispatcher semantic backend registry drifted after resolver creation");
		}
		const operationId = request.runtime_requirements.semantic_backend_operation_id;
		if (typeof operationId !== "string") throw new Error("generated dispatcher request has no operation id");
		const operation = locked.get(operationId);
		if (operation === undefined) throw new Error(`generated dispatcher operation is not allowlisted: ${operationId}`);
		const script = verifiedEntrypoint(context.packageDir, operation.entrypoint, operation.accepted_digests);
		if (script !== operation.script)
			throw new Error(`generated dispatcher entrypoint changed: ${operation.entrypoint}`);
		return operation;
	};
	const guardedDispatcher: ExecutionRequestDispatcher = {
		probe(request, requirements) {
			assertPackageCurrent(request);
			return dispatcher.probe(request, requirements);
		},
		execute(request) {
			assertPackageCurrent(request);
			return dispatcher.execute(request);
		},
	};
	return (request) => {
		const operationId = request.runtime_requirements.semantic_backend_operation_id;
		return typeof operationId === "string" && locked.has(operationId) ? guardedDispatcher : undefined;
	};
}
