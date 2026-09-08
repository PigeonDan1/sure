import { isAbsolute, resolve } from "node:path";
import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type {
	CapabilityEvidence,
	CapabilityRequirement,
	CapabilityStatus,
	ExecutionRequest,
	JsonValue,
} from "../contracts/types.ts";
import type { ExecutionRequestDispatcher } from "../execution/types.ts";

/** Host-owned roots used while binding a canonical request to one local entrypoint. */
export interface NodeRequestDispatcherContext {
	readonly packageDir: string;
	readonly runDir: string;
}

/** Runtime identity needed to bind request.entrypoint.executable. */
export interface NodeRequestDispatcherRuntime {
	readonly runtime_id: string;
	readonly executable: string;
	readonly details?: Record<string, JsonValue>;
	/** Optional host-owned opaque contract retained for the execute callback. */
	readonly host?: unknown;
}

export interface NodeRequestDispatcherRuntimeResolution {
	readonly ok: boolean;
	readonly contract?: NodeRequestDispatcherRuntime;
	readonly error?: string;
}

export interface NodeRequestDispatcherBackendOptions {
	readonly ctx: NodeRequestDispatcherContext;
	readonly request: ExecutionRequest;
	readonly runtime: NodeRequestDispatcherRuntime;
	readonly script: string;
	readonly args: readonly string[];
	readonly timeoutMs?: number;
	readonly environment?: NodeJS.ProcessEnv;
	readonly includeSpawnErrorInStderr?: boolean;
}

export interface NodeRequestDispatcherResult {
	ok: boolean;
	stdout: string;
	stderr: string;
	status: number | null;
}

export interface NodeRequestDispatcherOptions {
	/** Host-owned package/run roots used to bind the request entrypoint. */
	readonly ctx: NodeRequestDispatcherContext;
	/** Immutable operation id -> direct script mapping admitted by the host. */
	readonly allowedOperations: ReadonlyMap<string, string>;
	/** Preserve an operation-specific legacy wall-clock budget. */
	readonly timeoutMs?: number | ((request: ExecutionRequest) => number);
	/** Resolve the already selected runtime; this callback may inspect only host state. */
	readonly resolveRuntime: (ctx: NodeRequestDispatcherContext) => NodeRequestDispatcherRuntimeResolution;
	/** Execute a bound script; receipt construction remains outside this adapter. */
	readonly executeBackend: (options: NodeRequestDispatcherBackendOptions) => NodeRequestDispatcherResult;
	/** Capability ids this runtime probe can positively establish. */
	readonly availableCapabilityIds?: ReadonlySet<string>;
	readonly environment?: (runtime: NodeRequestDispatcherRuntime, request: ExecutionRequest) => NodeJS.ProcessEnv;
	readonly includeSpawnErrorInStderr?: boolean;
	readonly now?: () => string;
}

interface BoundRequest {
	operationId: string;
	script: string;
	args: string[];
}

function resolveRuntime(
	options: NodeRequestDispatcherOptions,
	context: NodeRequestDispatcherContext,
): NodeRequestDispatcherRuntimeResolution {
	const resolution = options.resolveRuntime(context);
	if (!resolution.ok || resolution.contract === undefined) return resolution;
	const contract = resolution.contract;
	if (
		typeof contract.runtime_id !== "string" ||
		contract.runtime_id.trim() === "" ||
		typeof contract.executable !== "string" ||
		!isAbsolute(contract.executable)
	) {
		return { ok: false, error: "HOST_RUNTIME_CONTRACT_INVALID" };
	}
	return resolution;
}

function requestOperationId(request: ExecutionRequest): string {
	const operationId = request.runtime_requirements.semantic_backend_operation_id;
	if (typeof operationId !== "string" || operationId.trim() === "") {
		throw new Error("execution request has no semantic backend operation id");
	}
	return operationId;
}

function bindRequest(
	request: ExecutionRequest,
	options: NodeRequestDispatcherOptions,
	runtime: NodeRequestDispatcherRuntime,
): BoundRequest {
	const operationId = requestOperationId(request);
	const script = options.allowedOperations.get(operationId);
	if (script === undefined || script.trim() === "" || isAbsolute(script) || script.includes("..")) {
		throw new Error(`host dispatcher operation is not allowlisted: ${operationId}`);
	}
	const argvScript = request.entrypoint.argv[0];
	const expectedScript = resolve(options.ctx.packageDir, "scripts", script);
	if (argvScript === undefined || resolve(argvScript) !== expectedScript) {
		throw new Error(`execution request entrypoint does not match the allowlisted script for ${operationId}`);
	}
	const runDirIndex = request.entrypoint.argv.indexOf("--run-dir");
	if (runDirIndex < 0 || request.entrypoint.argv[runDirIndex + 1] !== options.ctx.runDir) {
		throw new Error("execution request must bind the host run directory explicitly");
	}
	const producesIndex = request.entrypoint.argv.indexOf("--produces");
	if (
		producesIndex >= 0 &&
		request.entrypoint.argv[producesIndex + 1] !== undefined &&
		!isAbsolute(request.entrypoint.argv[producesIndex + 1])
	) {
		throw new Error("execution request must bind an absolute produces path");
	}
	if (
		request.entrypoint.working_directory !== undefined &&
		resolve(request.entrypoint.working_directory) !== resolve(options.ctx.packageDir)
	) {
		throw new Error("execution request working directory is outside the host package root");
	}
	if (resolve(request.entrypoint.executable) !== resolve(runtime.executable)) {
		throw new Error("execution request executable does not match the locked harness runtime");
	}
	return { operationId, script, args: request.entrypoint.argv.slice(1) };
}

function capabilityEvidence(
	requirement: CapabilityRequirement,
	status: CapabilityStatus,
	observedAt: string,
	details: Record<string, JsonValue>,
): CapabilityEvidence {
	const base: CapabilityEvidence = {
		capability_id: requirement.capability_id,
		capability_class: requirement.capability_class,
		status,
		source: "host_probe",
		observed_at: observedAt,
		details,
	};
	return { ...base, evidence_digest: canonicalJsonDigest(base as unknown as JsonValue) };
}

function runtimeDetails(runtime: NodeRequestDispatcherRuntime, operationId?: string): Record<string, JsonValue> {
	return {
		...(runtime.details ?? {}),
		runtime_id: runtime.runtime_id,
		executable: runtime.executable,
		...(operationId === undefined ? {} : { semantic_backend_operation_id: operationId }),
	};
}

/**
 * Construct the common host-side request binder used by Pi and future hosts.
 * It never reads skill instructions, mutates workflow state, or creates a
 * receipt.  A host must supply both runtime resolution and process execution.
 */
export function createNodeRequestDispatcher(options: NodeRequestDispatcherOptions): ExecutionRequestDispatcher {
	const availableCapabilityIds = new Set(options.availableCapabilityIds ?? []);
	const allowedOperations = new Map(options.allowedOperations);
	const boundOptions = { ...options, allowedOperations };
	const now = options.now ?? (() => new Date().toISOString());
	// Resolve roots once so a caller cannot alter the dispatcher after
	// construction. The per-request checks below still revalidate every field.
	const packageDir = resolve(options.ctx.packageDir);
	const runDir = resolve(options.ctx.runDir);
	const context = Object.freeze({ packageDir, runDir });

	return {
		probe(request, requirements) {
			const runtime = resolveRuntime(options, context);
			if (!runtime.ok || runtime.contract === undefined) {
				return requirements.map((requirement) =>
					capabilityEvidence(
						requirement,
						availableCapabilityIds.has(requirement.capability_id) ? "MISSING" : "UNKNOWN",
						now(),
						{ reason: runtime.error ?? "HOST_RUNTIME_NOT_READY" },
					),
				);
			}
			const contract = runtime.contract;
			const bound = bindRequest(request, boundOptions, contract);
			return requirements.map((requirement) =>
				capabilityEvidence(
					requirement,
					availableCapabilityIds.has(requirement.capability_id) ? "AVAILABLE" : "UNKNOWN",
					now(),
					runtimeDetails(contract, bound.operationId),
				),
			);
		},
		execute(request) {
			const runtime = resolveRuntime(options, context);
			if (!runtime.ok || runtime.contract === undefined) {
				return {
					ok: false,
					stdout: "",
					stderr: runtime.error ?? "HOST_RUNTIME_NOT_READY",
					status: null,
				};
			}
			try {
				const contract = runtime.contract;
				const bound = bindRequest(request, boundOptions, contract);
				return options.executeBackend({
					ctx: context,
					request,
					runtime: contract,
					script: bound.script,
					args: bound.args,
					timeoutMs: typeof options.timeoutMs === "function" ? options.timeoutMs(request) : options.timeoutMs,
					environment: options.environment?.(contract, request),
					includeSpawnErrorInStderr: options.includeSpawnErrorInStderr,
				});
			} catch (error) {
				return {
					ok: false,
					stdout: "",
					stderr: error instanceof Error ? error.message : String(error),
					status: null,
				};
			}
		},
	};
}
