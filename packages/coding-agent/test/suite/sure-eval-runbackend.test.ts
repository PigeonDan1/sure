import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { runBackend } from "../../../../sure/skills/sure_eval/hooks/checkpoints.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// sure_eval skill package root (repo-relative from the test file).
const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_eval");

// Minimal SureHookContext mock. runDir is a temp dir we populate with artifacts.
function makeCtx(runDir: string): SureHookContext {
	return {
		point: "post_tool_result",
		run: { id: "test-eval-b1", command: "/sure_eval", status: "running" } as never,
		skill: { name: "sure_eval", command: "/sure_eval" } as never,
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
describe("sure_eval runBackend — gate script invocation path (B1 regression)", () => {
	it("injects --run-dir when the caller passes only --produces (gate scripts declare --run-dir required)", () => {
		const runDir = resolve(__dirname, "tmp-b1");
		mkdirSync(join(runDir, "artifacts"), { recursive: true });
		// A compliant submit_result with vc_submit — vc_check.py must PASS, not crash.
		writeFileSync(
			join(runDir, "artifacts", "submit_result.json"),
			JSON.stringify({ execution_path: "vc_submit", vc_available: true }, null, 2),
			"utf-8",
		);
		const produces = join(runDir, "artifacts", "submit_result.json");

		// Exactly how runGateScript calls runBackend: ["--produces", <abs>], no --run-dir.
		const r = runBackend(makeCtx(runDir), "vc_check.py", ["--produces", produces]);

		expect(r.status).toBe(0); // 0 = script ran and passed. Non-null argparse error = the B1 bug.
		expect(r.ok).toBe(true);
		expect(r.stderr).not.toContain("required: --run-dir");
	});

	it("resolves a relative --produces value under <runDir>/artifacts/", () => {
		const runDir = resolve(__dirname, "tmp-b1-rel");
		mkdirSync(join(runDir, "artifacts"), { recursive: true });
		writeFileSync(
			join(runDir, "artifacts", "submit_result.json"),
			JSON.stringify({ execution_path: "vc_submit", vc_available: true }, null, 2),
			"utf-8",
		);
		// Relative produces path — runBackend must resolve it under artifacts/.
		const r = runBackend(makeCtx(runDir), "vc_check.py", ["--produces", "submit_result.json"]);
		expect(r.status).toBe(0);
		expect(r.ok).toBe(true);
	});
});

// Regression guard for the script_routing whitelist naming. check_script_routing.py
// + script_routing.schema.json must accept the SHORT contract step names
// (prepare_dataset / materialize_templates / wait_for_predictions / validate_predictions
// / evaluate_predictions / refresh_report) — these are the authoritative names used by
// the contract doc AND the produces exemplar (scripts/templates/main_agent_script_routing.json).
// Previously the whitelist used file-derived names (prepare_sure_dataset, ...) which would
// reject an agent faithfully following its own exemplar — blocking every real eval at
// script_routing. submit_vc_run was also wrongly in the whitelist (it is a separate gate
// unit, not a script_routing step). Fixed: whitelist = short names, no submit_vc_run,
// and the script field is now cross-checked against the expected file + existence on disk.
describe("sure_eval check_script_routing whitelist naming (regression)", () => {
	function runGate(runDir: string, produces: string) {
		return runBackend(makeCtx(runDir), "check_script_routing.py", ["--produces", produces]);
	}

	it("passes for the produces exemplar (short contract names map to real scripts)", () => {
		const runDir = resolve(__dirname, "tmp-sr-ok");
		mkdirSync(join(runDir, "artifacts"), { recursive: true });
		// The exemplar uses short names that map to real files under scripts/.
		const exemplar = JSON.parse(
			readFileSync(join(PACKAGE_DIR, "scripts", "templates", "main_agent_script_routing.json"), "utf-8"),
		);
		writeFileSync(join(runDir, "artifacts", "script_routing.json"), JSON.stringify(exemplar), "utf-8");
		const r = runGate(runDir, join(runDir, "artifacts", "script_routing.json"));
		expect(r.status).toBe(0);
		expect(r.ok).toBe(true);
	});

	it("rejects the old file-derived step name with a repair pointing at the short name", () => {
		const runDir = resolve(__dirname, "tmp-sr-oldname");
		mkdirSync(join(runDir, "artifacts"), { recursive: true });
		writeFileSync(
			join(runDir, "artifacts", "script_routing.json"),
			JSON.stringify({
				steps: [{ name: "prepare_sure_dataset", script: "scripts/prepare_sure_dataset.py" }],
			}),
			"utf-8",
		);
		const r = runGate(runDir, join(runDir, "artifacts", "script_routing.json"));
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("prepare_sure_dataset");
		expect(r.stderr).toContain("prepare_dataset"); // the correct short name is named
	});

	it("rejects submit_vc_run as a script_routing step (it is a separate gate unit)", () => {
		const runDir = resolve(__dirname, "tmp-sr-vc");
		mkdirSync(join(runDir, "artifacts"), { recursive: true });
		writeFileSync(
			join(runDir, "artifacts", "script_routing.json"),
			JSON.stringify({
				steps: [{ name: "submit_vc_run", script: "scripts/vc_check.py" }],
			}),
			"utf-8",
		);
		const r = runGate(runDir, join(runDir, "artifacts", "script_routing.json"));
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("submit_vc_run");
	});

	it("rejects a whitelisted name whose script path does not exist on disk", () => {
		const runDir = resolve(__dirname, "tmp-sr-missing");
		mkdirSync(join(runDir, "artifacts"), { recursive: true });
		writeFileSync(
			join(runDir, "artifacts", "script_routing.json"),
			JSON.stringify({
				steps: [{ name: "prepare_dataset", script: "scripts/nonexistent.py" }],
			}),
			"utf-8",
		);
		const r = runGate(runDir, join(runDir, "artifacts", "script_routing.json"));
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("does not match the expected");
	});
});
