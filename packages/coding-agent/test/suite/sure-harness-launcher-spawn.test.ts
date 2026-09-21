import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The launcher is a bare command name, and on Windows only cross-spawn can find
// a `uv.cmd`/`uv.bat` shim. Which implementation runs is observable on any host
// by forcing process.platform and letting node:child_process report a runtime
// that is ready: if the Windows path still goes through node:child_process the
// resolution succeeds, which is exactly the bug.
const spies = vi.hoisted(() => ({ nodeSpawnSync: vi.fn() }));

vi.mock("node:child_process", async (importOriginal) => {
	const actual = await importOriginal<typeof import("node:child_process")>();
	return { ...actual, spawnSync: spies.nodeSpawnSync };
});

// resolveHarnessPython derives the repository root from the skill package.
const SKILL_PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_infer");
// Nothing on PATH answers to this, so the real cross-spawn always fails here.
const MISSING_LAUNCHER = "sure-launcher-not-installed";

function readyRuntime() {
	return {
		status: 0,
		signal: null,
		pid: 1,
		output: [],
		stderr: "",
		stdout: JSON.stringify({
			runtime_id: "test-runtime",
			python_executable: process.execPath,
			python_abi: "cp311",
			python_version: "3.11.0",
			lock_sha256: "0000",
			harness_version: "v1",
			manifest_path: "manifest.json",
			runtime_root: "runtime",
		}),
	};
}

async function freshResolveHarnessPython() {
	vi.resetModules();
	return (await import("../../../../sure/runtime/harness/resolve.ts")).resolveHarnessPython;
}

describe("harness launcher spawn", () => {
	const SAVED_ENV = [
		"SURE_UV_BIN",
		"SURE_HARNESS_BOOTSTRAP_PYTHON",
		"HARNESS_PYTHON_BIN",
		"SURE_EVAL_HARNESS_PYTHON_BIN",
		"SURE_HARNESS_RUNTIME_ID",
		"SURE_HARNESS_LOCK_SHA256",
		"SURE_HARNESS_MANIFEST_PATH",
		"SURE_HARNESS_RUNTIME_ROOT",
	] as const;
	let previousEnv: Record<string, string | undefined>;
	let platformDescriptor: PropertyDescriptor | undefined;

	beforeEach(() => {
		previousEnv = Object.fromEntries(SAVED_ENV.map((key) => [key, process.env[key]]));
		delete process.env.SURE_HARNESS_BOOTSTRAP_PYTHON;
		process.env.SURE_UV_BIN = MISSING_LAUNCHER;
		platformDescriptor = Object.getOwnPropertyDescriptor(process, "platform");
		spies.nodeSpawnSync.mockReset().mockReturnValue(readyRuntime());
	});

	afterEach(() => {
		if (platformDescriptor) Object.defineProperty(process, "platform", platformDescriptor);
		for (const [key, value] of Object.entries(previousEnv)) {
			if (value === undefined) delete process.env[key];
			else process.env[key] = value;
		}
	});

	it("does not spawn the launcher through node:child_process on Windows", async () => {
		Object.defineProperty(process, "platform", { configurable: true, value: "win32" });
		const resolveHarnessPython = await freshResolveHarnessPython();

		const resolution = resolveHarnessPython(SKILL_PACKAGE_DIR);

		expect(spies.nodeSpawnSync).not.toHaveBeenCalled();
		expect(resolution.ok).toBe(false);
		expect(resolution.error).toContain(`${MISSING_LAUNCHER} is not installed`);
	});

	it("keeps spawning the launcher through node:child_process elsewhere", async () => {
		Object.defineProperty(process, "platform", { configurable: true, value: "linux" });
		const resolveHarnessPython = await freshResolveHarnessPython();

		const resolution = resolveHarnessPython(SKILL_PACKAGE_DIR);

		expect(spies.nodeSpawnSync).toHaveBeenCalledWith(
			MISSING_LAUNCHER,
			expect.arrayContaining(["run"]),
			expect.anything(),
		);
		expect(resolution.ok).toBe(true);
	});
});
