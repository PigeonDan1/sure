import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { isAbsolute, join, resolve } from "node:path";
import { createNodeRequestDispatcher } from "@earendil-works/sure-core/node";
import type { ExecutionRequest, ExecutionRequestDispatcher } from "@earendil-works/sure-core";
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

/**
 * Build an opt-in dispatcher for one host-allowlisted local Python operation.
 * It consumes the already planned request and delegates process behavior to the
 * existing backend adapter; it never parses skill metadata or decides outcomes.
 */
export function createHarnessRequestDispatcher(options: HarnessRequestDispatcherOptions): ExecutionRequestDispatcher {
	const resolveRuntime = options.resolveRuntime ?? ((packageDir) => resolveHarnessPython(packageDir, { activate: false }));
	const executeBackend = options.executeBackend ?? runHarnessBackend;
	return createNodeRequestDispatcher({
		ctx: options.ctx,
		allowedOperations: options.allowedOperations,
		timeoutMs: options.timeoutMs,
		availableCapabilityIds: new Set(["sure.execution.harness-python"]),
		now: options.now,
		resolveRuntime: (context) => {
			const runtime = resolveRuntime(context.packageDir);
			if (!runtime.ok || runtime.contract === undefined) {
				return { ok: false, error: runtime.error ?? "HARNESS_RUNTIME_NOT_READY" };
			}
			const contract = runtime.contract;
			return {
				ok: true,
				contract: {
					runtime_id: contract.runtime_id,
					executable: contract.python_executable,
					details: {
						python_executable: contract.python_executable,
						python_abi: contract.python_abi,
						python_version: contract.python_version,
						lock_sha256: contract.lock_sha256,
						harness_version: contract.harness_version,
						manifest_path: contract.manifest_path,
						runtime_root: contract.runtime_root,
					},
					host: contract,
				},
			};
		},
		environment:
			options.environment === undefined
				? undefined
				: (runtime, request) =>
						options.environment?.(runtime.host as HarnessRuntimeContract, request) ??
						harnessRuntimeEnv(runtime.host as HarnessRuntimeContract),
		executeBackend: (backend) => {
			return executeBackend({
				ctx: backend.ctx,
				script: backend.script,
				args: backend.args,
				timeoutMs: backend.timeoutMs,
				environment: backend.environment === undefined ? undefined : () => backend.environment ?? {},
				includeSpawnErrorInStderr: backend.includeSpawnErrorInStderr,
			});
		},
		includeSpawnErrorInStderr: options.includeSpawnErrorInStderr,
	});
}
