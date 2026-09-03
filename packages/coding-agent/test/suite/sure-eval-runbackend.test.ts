import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { runBackend } from "../../../../sure/skills/sure_infer/hooks/checkpoints.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// sure_infer skill package root (repo-relative from the test file).
const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_infer");

// Minimal SureHookContext mock. runDir is a temp dir we populate with artifacts.
function makeCtx(runDir: string): SureHookContext {
	return {
		point: "post_tool_result",
		run: { id: "test-eval-b1", command: "/sure_infer", status: "running" } as never,
		skill: { name: "sure_infer", command: "/sure_infer" } as never,
		cwd: PACKAGE_DIR,
		packageDir: PACKAGE_DIR,
		runDir,
		args: "",
	};
}

// This test exercises the REAL hook→gateScript path that runGateScript uses:
// it calls runBackend with ONLY ["--produces", <abs>] (no --run-dir) and relies
// on runBackend to inject --run-dir. Regression guard for the bug where the
// presence of --produces suppressed the --run-dir injection, crashing every
// gate script on argparse "required: --run-dir".
describe("sure_infer runBackend — gate script invocation path (B1 regression)", () => {
	it("injects --run-dir when the caller passes only --produces (gate scripts declare --run-dir required)", () => {
		const runDir = resolve(__dirname, "tmp-b1");
		mkdirSync(join(runDir, "artifacts"), { recursive: true });
		// A compliant assessment_report (check_assessment.py passes on these two keys).
		writeFileSync(
			join(runDir, "artifacts", "assessment_report.json"),
			JSON.stringify({ anomaly_detected: false, user_confirmed: true }, null, 2),
			"utf-8",
		);
		const produces = join(runDir, "artifacts", "assessment_report.json");

		// Exactly how runGateScript calls runBackend: ["--produces", <abs>], no --run-dir.
		const r = runBackend(makeCtx(runDir), "check_assessment.py", ["--produces", produces]);

		expect(r.status).toBe(0); // 0 = script ran and passed. Non-null argparse error = the B1 bug.
		expect(r.ok).toBe(true);
		expect(r.stderr).not.toContain("required: --run-dir");
	});

	it("resolves a relative --produces value under <runDir>/artifacts/", () => {
		const runDir = resolve(__dirname, "tmp-b1-rel");
		mkdirSync(join(runDir, "artifacts"), { recursive: true });
		writeFileSync(
			join(runDir, "artifacts", "assessment_report.json"),
			JSON.stringify({ anomaly_detected: false, user_confirmed: true }, null, 2),
			"utf-8",
		);
		// Relative produces path — runBackend must resolve it under artifacts/.
		const r = runBackend(makeCtx(runDir), "check_assessment.py", ["--produces", "assessment_report.json"]);
		expect(r.status).toBe(0);
		expect(r.ok).toBe(true);
	});
});
