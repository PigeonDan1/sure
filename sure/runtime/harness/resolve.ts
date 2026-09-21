import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
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
		// Every caller decodes the child's stdout/stderr as UTF-8, but nothing told
		// the child to encode that way: on a host whose code page is not UTF-8
		// (cp936 is the common one) a gate refusal naming a non-ASCII path came
		// back as U+FFFD. PYTHONIOENCODING covers exactly the stdio streams;
		// PYTHONUTF8 would also switch the default encoding of the child's open(),
		// which is a far wider change than this needs.
		PYTHONIOENCODING: "utf-8",
	};
}

export function activateHarnessRuntime(contract: HarnessRuntimeContract): void {
Object.assign(process.env, harnessRuntimeEnv(contract));
}

export const UV_INSTALL_HINT =
	process.platform === "win32"
		? 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
		: "curl -LsSf https://astral.sh/uv/install.sh | sh";

function pinnedPythonVersion(repoRoot: string): string {
	// The ABI pin lives in one place; reading it here keeps resolve.ts from
	// becoming a second copy that drifts.
	try {
		const spec: unknown = JSON.parse(
			readFileSync(resolve(repoRoot, "sure/runtime/harness/runtime.json"), "utf-8"),
		);
		if (isRecord(spec) && typeof spec.python === "string" && spec.python !== "") {
			return spec.python;
		}
	} catch {
		// fall through to the pin below
	}
	return "3.11";
}

/** How to launch bootstrap.py: an explicit interpreter, or uv with its own Python. */
export function harnessBootstrapCommand(repoRoot: string): { command: string; args: string[] } {
	const explicit = process.env.SURE_HARNESS_BOOTSTRAP_PYTHON?.trim();
	if (explicit) {
		return { command: explicit, args: [] };
	}
	const uv = process.env.SURE_UV_BIN?.trim() || "uv";
	return { command: uv, args: ["run", "--no-project", "--python", pinnedPythonVersion(repoRoot)] };
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
	const { command, args } = harnessBootstrapCommand(repoRoot);
	const completed = spawnSync(command, [...args, bootstrap, "--json"], {
		cwd: repoRoot,
		encoding: "utf-8",
		timeout: 900_000,
		env: process.env,
	});
	if (completed.error) {
		// ENOENT here means the launcher itself is missing, which on a fresh PC is
		// almost always uv. Saying "bootstrap exited null" sent people reading logs.
		// An explicit interpreter override is the user's own path, so installing uv
		// would not fix it: name what is missing and stop there.
		const overridden = Boolean(process.env.SURE_HARNESS_BOOTSTRAP_PYTHON?.trim());
		const reason =
			(completed.error as NodeJS.ErrnoException).code === "ENOENT"
				? overridden
					? `${command} is not installed. SURE_HARNESS_BOOTSTRAP_PYTHON points at it.`
					: `${command} is not installed. Install uv:\n  ${UV_INSTALL_HINT}`
				: completed.error.message;
		const failure = { ok: false, error: `HARNESS_RUNTIME_NOT_READY: ${reason}` };
		resolvedByRepo.set(repoRoot, failure);
		return failure;
	}
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
