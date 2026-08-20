import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

export interface HarnessRuntimeContract {
runtime_id: string;
python_executable: string;
python_abi: string;
python_version: string;
lock_sha256: string;
harness_version: string;
manifest_path: string;
runtime_root: string;
install_log?: string;
}

export interface HarnessRuntimeResolution {
ok: boolean;
contract?: HarnessRuntimeContract;
error?: string;
}

const resolvedByRepo = new Map<string, HarnessRuntimeResolution>();

function isRecord(value: unknown): value is Record<string, unknown> {
return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseContract(value: unknown): HarnessRuntimeContract | undefined {
if (!isRecord(value)) {
return undefined;
}
for (const key of [
"runtime_id",
"python_executable",
"python_abi",
"python_version",
"lock_sha256",
"harness_version",
"manifest_path",
"runtime_root",
]) {
if (typeof value[key] !== "string" || value[key] === "") {
return undefined;
}
}
return value as unknown as HarnessRuntimeContract;
}

export function repoRootForPackage(packageDir: string): string {
return resolve(packageDir, "../../..");
}

export function harnessRuntimeEnv(contract: HarnessRuntimeContract): NodeJS.ProcessEnv {
return {
HARNESS_PYTHON_BIN: contract.python_executable,
SURE_EVAL_HARNESS_PYTHON_BIN: contract.python_executable,
SURE_HARNESS_RUNTIME_ID: contract.runtime_id,
SURE_HARNESS_LOCK_SHA256: contract.lock_sha256,
		SURE_HARNESS_MANIFEST_PATH: contract.manifest_path,
		SURE_HARNESS_RUNTIME_ROOT: contract.runtime_root,
	};
}

export function activateHarnessRuntime(contract: HarnessRuntimeContract): void {
Object.assign(process.env, harnessRuntimeEnv(contract));
}

export function resolveHarnessPython(packageDir: string): HarnessRuntimeResolution {
const repoRoot = repoRootForPackage(packageDir);
const cached = resolvedByRepo.get(repoRoot);
if (cached?.ok && cached.contract && existsSync(cached.contract.python_executable)) {
activateHarnessRuntime(cached.contract);
return cached;
}
const bootstrap = resolve(repoRoot, "sure/runtime/harness/bootstrap.py");
if (!existsSync(bootstrap)) {
return { ok: false, error: `HARNESS_RUNTIME_NOT_READY: bootstrap is missing: ${bootstrap}` };
}
const bootstrapPython = process.env.SURE_HARNESS_BOOTSTRAP_PYTHON?.trim() || "python3";
const completed = spawnSync(bootstrapPython, [bootstrap, "--json"], {
cwd: repoRoot,
encoding: "utf-8",
timeout: 900_000,
env: process.env,
});
if (completed.status !== 0) {
const detail = completed.stderr?.trim() || completed.stdout?.trim() || `bootstrap exited ${completed.status}`;
const failure = { ok: false, error: detail };
resolvedByRepo.set(repoRoot, failure);
return failure;
}
try {
const contract = parseContract(JSON.parse(completed.stdout));
if (!contract || !existsSync(contract.python_executable)) {
throw new Error("bootstrap returned an incomplete runtime contract");
}
const success = { ok: true, contract };
activateHarnessRuntime(contract);
resolvedByRepo.set(repoRoot, success);
return success;
} catch (error) {
const detail = error instanceof Error ? error.message : String(error);
const failure = { ok: false, error: `HARNESS_RUNTIME_NOT_READY: ${detail}` };
resolvedByRepo.set(repoRoot, failure);
return failure;
}
}
