import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { isAbsolute, join, resolve } from "node:path";
import {
	canonicalJsonDigest,
	type CapabilityEvidence,
	type CapabilityRequirement,
	type ExecutionRequest,
	type ExecutionRequestDispatcher,
	type JsonValue,
} from "@earendil-works/sure-core";
import {
	harnessRuntimeEnv,
	resolveHarnessPython,
	type HarnessRuntimeContract,
	type HarnessRuntimeResolution,
} from "./resolve.ts";

/** The small host context needed to invoke a bundled SURE backend script. */
export interface HarnessBackendContext {
	readonly packageDir: string;
	readonly runDir: string;
}

export interface HarnessBackendResult {
	ok: boolean;
	stdout: string;
	stderr: string;
	status: number | null;
}

export interface HarnessBackendInvocation {
	scriptPath: string;
	args: string[];
}

export interface HarnessBackendExecutionOptions {
	ctx: HarnessBackendContext;
	script: string;
	args: readonly string[];
	/** Preserve each skill's existing wall-clock budget. */
	timeoutMs?: number;
	/** Extend the locked runtime environment for a skill-specific policy. */
	environment?: (runtime: HarnessRuntimeContract) => NodeJS.ProcessEnv;
	/** Trans historically surfaced a spawn error when stderr was empty. */
	includeSpawnErrorInStderr?: boolean;
}

/**
 * Build the exact argv used by legacy checkpoint runners.
 *
 * This function is intentionally pure: it does not resolve a runtime, touch the
 * filesystem, or infer workflow state. Hosts can therefore replace the local
 * process adapter without changing the request presented to an executor.
 */
export function prepareHarnessBackendInvocation(
	ctx: HarnessBackendContext,
	script: string,
	args: readonly string[],
): HarnessBackendInvocation {
	const scriptPath = join(ctx.packageDir, "scripts", script);
	const inputArgs = [...args];
	const produces = inputArgs.find((arg) => arg === "--produces");
	const runDir = inputArgs.find((arg) => arg === "--run-dir");
	// Gate scripts require --run-dir. Keep an explicitly supplied flag exactly
	// as-is for compatibility; otherwise prepend the active run directory.
	const finalArgs = runDir === undefined ? ["--run-dir", ctx.runDir, ...inputArgs] : inputArgs;
	// Legacy callers sometimes pass a relative produces name. Resolve it under
	// the run artifact root while retaining every other argument byte-for-byte.
	if (produces !== undefined) {
		const index = finalArgs.indexOf("--produces");
		const value = finalArgs[index + 1];
		if (typeof value === "string" && !isAbsolute(value)) {
			finalArgs[index + 1] = join(ctx.runDir, "artifacts", value);
		}
	}
	return { scriptPath, args: finalArgs };
}

/**
 * Execute one bundled backend through the locked SURE runtime.
 *
 * The default remains the historical local Python process. The adapter's
 * narrow options are the seam for a future host-owned dispatcher; no workflow
 * transition or PASS decision is made here.
 */
export function runHarnessBackend(options: HarnessBackendExecutionOptions): HarnessBackendResult {
	const invocation = prepareHarnessBackendInvocation(options.ctx, options.script, options.args);
	if (!existsSync(invocation.scriptPath)) {
		return {
			ok: false,
			status: null,
			stdout: "",
			stderr: `Backend script not found: scripts/${options.script}. Bundle the Python backend into the skill package.`,
		};
	}
	const runtime = resolveHarnessPython(options.ctx.packageDir);
	if (!runtime.ok || runtime.contract === undefined) {
		return {
			ok: false,
			status: null,
			stdout: "",
			stderr: runtime.error ?? "HARNESS_RUNTIME_NOT_READY",
		};
	}
	const environment = options.environment?.(runtime.contract) ?? {
		...process.env,
		...harnessRuntimeEnv(runtime.contract),
	};
	const result = spawnSync(runtime.contract.python_executable, [invocation.scriptPath, ...invocation.args], {
		cwd: options.ctx.packageDir,
		encoding: "utf-8",
		timeout: options.timeoutMs ?? 300_000,
		env: environment,
	});
	return {
		ok: result.status === 0,
		stdout: result.stdout ?? "",
		stderr:
			result.stderr ??
			(options.includeSpawnErrorInStderr && result.error
				? `scripts/${options.script} did not complete: ${result.error.message}`
				: ""),
		status: result.status,
	};
}

export interface HarnessRequestDispatcherOptions {
	/** Host-owned package/run roots used to bind the request entrypoint. */
	readonly ctx: HarnessBackendContext;
	/** Immutable operation id -> script mapping admitted by the host registry. */
	readonly allowedOperations: ReadonlyMap<string, string>;
	/** Preserve the operation-specific legacy wall-clock budget. */
	readonly timeoutMs?: number | ((request: ExecutionRequest) => number);
	/** Reproduce a skill's explicit environment policy without reading agent state. */
	readonly environment?: (runtime: HarnessRuntimeContract, request: ExecutionRequest) => NodeJS.ProcessEnv;
	readonly includeSpawnErrorInStderr?: boolean;
	readonly now?: () => string;
	/** Injectable only for deterministic host tests; production resolves the locked runtime. */
	readonly resolveRuntime?: (packageDir: string) => HarnessRuntimeResolution;
	/** Injectable only for differential tests; production uses runHarnessBackend. */
	readonly executeBackend?: (options: HarnessBackendExecutionOptions) => HarnessBackendResult;
}

interface BoundHarnessRequest {
	operationId: string;
	script: string;
	args: string[];
}

function requestOperationId(request: ExecutionRequest): string {
	const operationId = request.runtime_requirements.semantic_backend_operation_id;
	if (typeof operationId !== "string" || operationId.trim() === "") {
		throw new Error("execution request has no semantic backend operation id");
	}
	return operationId;
}

function bindHarnessRequest(
	request: ExecutionRequest,
	options: HarnessRequestDispatcherOptions,
	runtime: HarnessRuntimeContract,
): BoundHarnessRequest {
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
	if (producesIndex >= 0 && request.entrypoint.argv[producesIndex + 1] !== undefined && !isAbsolute(request.entrypoint.argv[producesIndex + 1])) {
		throw new Error("execution request must bind an absolute produces path");
	}
	if (request.entrypoint.working_directory !== undefined && resolve(request.entrypoint.working_directory) !== resolve(options.ctx.packageDir)) {
		throw new Error("execution request working directory is outside the host package root");
	}
	if (resolve(request.entrypoint.executable) !== resolve(runtime.python_executable)) {
		throw new Error("execution request executable does not match the locked harness runtime");
	}
	return { operationId, script, args: request.entrypoint.argv.slice(1) };
}

function capabilityEvidence(
	requirement: CapabilityRequirement,
	status: CapabilityEvidence["status"],
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

/**
 * Build an opt-in dispatcher for one host-allowlisted local Python operation.
 * It consumes the already planned request and delegates process behavior to the
 * existing backend adapter; it never parses skill metadata or decides outcomes.
 */
export function createHarnessRequestDispatcher(options: HarnessRequestDispatcherOptions): ExecutionRequestDispatcher {
	const resolveRuntime = options.resolveRuntime ?? ((packageDir) => resolveHarnessPython(packageDir, { activate: false }));
	const executeBackend = options.executeBackend ?? runHarnessBackend;
	const now = options.now ?? (() => new Date().toISOString());
	const allowedOperations = new Map(options.allowedOperations);
	const boundOptions = { ...options, allowedOperations };

	return {
		probe(request, requirements) {
			const runtime = resolveRuntime(options.ctx.packageDir);
			if (!runtime.ok || runtime.contract === undefined) {
				return requirements.map((requirement) =>
					capabilityEvidence(requirement, requirement.capability_id === "sure.execution.harness-python" ? "MISSING" : "UNKNOWN", now(), {
						reason: runtime.error ?? "HARNESS_RUNTIME_NOT_READY",
					}),
				);
			}
			const contract = runtime.contract;
			const bound = bindHarnessRequest(request, boundOptions, contract);
			return requirements.map((requirement) =>
				capabilityEvidence(
					requirement,
					requirement.capability_id === "sure.execution.harness-python" ? "AVAILABLE" : "UNKNOWN",
					now(),
					{
						runtime_id: contract.runtime_id,
						python_executable: contract.python_executable,
						semantic_backend_operation_id: bound.operationId,
					},
				),
			);
		},
		execute(request) {
			const runtime = resolveRuntime(options.ctx.packageDir);
			if (!runtime.ok || runtime.contract === undefined) {
				return {
					ok: false,
					stdout: "",
					stderr: runtime.error ?? "HARNESS_RUNTIME_NOT_READY",
					status: null,
				};
			}
			try {
				const bound = bindHarnessRequest(request, boundOptions, runtime.contract);
				const result = executeBackend({
					ctx: options.ctx,
					script: bound.script,
					args: bound.args,
					timeoutMs: typeof options.timeoutMs === "function" ? options.timeoutMs(request) : options.timeoutMs,
					environment:
						options.environment === undefined
							? undefined
							: (resolvedRuntime) => options.environment?.(resolvedRuntime, request) ?? harnessRuntimeEnv(resolvedRuntime),
					includeSpawnErrorInStderr: options.includeSpawnErrorInStderr,
				});
				return result;
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
