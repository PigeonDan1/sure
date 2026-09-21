import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { type HarnessRuntimeContract, harnessRuntimeEnv } from "../../../../sure/runtime/harness/resolve.ts";
import { runBackend } from "../../../../sure/skills/sure_infer/hooks/checkpoints.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// sure_infer skill package root (repo-relative from the test file).
const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_infer");

// U+00E9. Encodable in latin-1 as the single byte 0xE9, which is not valid
// UTF-8 on its own — so a child that encodes with the wrong codec produces
// exactly the U+FFFD the user sees, on every platform.
const NON_ASCII = "é";
const REPLACEMENT = "�";

function makeCtx(runDir: string): SureHookContext {
	return {
		point: "post_tool_result",
		run: { id: "test-child-encoding", command: "/sure_infer", status: "running" } as never,
		skill: { name: "sure_infer", command: "/sure_infer" } as never,
		cwd: PACKAGE_DIR,
		packageDir: PACKAGE_DIR,
		runDir,
		args: "",
	};
}

describe("Harness Runtime child stdio encoding", () => {
	let previous: string | undefined;

	beforeEach(() => {
		previous = process.env.PYTHONIOENCODING;
		// Force a non-UTF-8 ambient encoding so the round trip is exercised on
		// Linux and macOS too, where the locale is UTF-8 and the defect would
		// otherwise be invisible. This stands in for cp936 on a Chinese Windows
		// host: any codec that is not UTF-8 reproduces it.
		process.env.PYTHONIOENCODING = "latin-1";
	});

	afterEach(() => {
		if (previous === undefined) {
			delete process.env.PYTHONIOENCODING;
		} else {
			process.env.PYTHONIOENCODING = previous;
		}
	});

	it("keeps a non-ASCII path readable in a gate refusal spawned through runBackend", () => {
		// check_assessment.py echoes the --produces path into stderr when the
		// artifact is missing. Nothing is created: the path only has to not exist.
		const runDir = join(tmpdir(), `sure-child-encoding-${process.pid}`);
		const produces = join(runDir, "artifacts", `assessment_report_${NON_ASCII}.json`);

		const r = runBackend(makeCtx(runDir), "check_assessment.py", ["--produces", produces]);

		// The gate refused, and the refusal is legible.
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("assessment_report.json not found at");
		expect(r.stderr).not.toContain(REPLACEMENT);
		expect(r.stderr).toContain(NON_ASCII);
	});

	it("pins the child's stdio encoding to UTF-8", () => {
		const contract: HarnessRuntimeContract = {
			runtime_id: "harness-test",
			python_executable: "/nonexistent/python",
			python_abi: "cp311",
			python_version: "3.11",
			lock_sha256: "a".repeat(64),
			harness_version: "test",
			manifest_path: "/nonexistent/runtime-manifest.json",
			runtime_root: "/nonexistent",
		};
		expect(harnessRuntimeEnv(contract).PYTHONIOENCODING).toBe("utf-8");
	});
});
