import { writeFileSync, mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, describe, expect, it } from "vitest";
import { finalEvaluationGate, incompleteReportError } from "../../../../sure/skills/sure_eval/hooks/index.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

const temporaryRuns: string[] = [];

function contextFor(runDir: string): SureHookContext {
	return {
		point: "pre_finish",
		run: { id: "eval-terminal", command: "/sure_eval", status: "running" } as never,
		skill: { name: "sure_eval", command: "/sure_eval" } as never,
		cwd: runDir,
		packageDir: runDir,
		runDir,
		args: "",
	};
}

function writeEvalReport(runDir: string, value: unknown): void {
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	writeFileSync(join(runDir, "artifacts", "eval_run_report.json"), JSON.stringify(value), "utf8");
}

afterEach(() => {
	for (const path of temporaryRuns.splice(0)) rmSync(path, { recursive: true, force: true });
});

describe("sure_eval incomplete terminal contract", () => {
	const report = {
		schema: "sure.eval.run_report.v1",
		status: "incomplete",
		error_code: "INPUT_EVIDENCE_MISSING",
		evaluation_only: true,
		inference_executed: false,
		old_evaluation_reused: false,
		append_attempted: false,
		source_identity: { protocol_id: "standard_system" },
	};

	it("accepts an explicit incomplete report that proves no side effects", () => {
		expect(incompleteReportError(report, "incomplete")).toBeUndefined();
	});

	it("rejects an incomplete report that claims an append", () => {
		expect(incompleteReportError({ ...report, append_attempted: true }, "incomplete")).toContain(
			"append_attempted=false",
		);
	});

	it("rechecks a success-shaped evaluation report with the final phase", () => {
		const runDir = mkdtempSync(join(tmpdir(), "sure-eval-terminal-"));
		temporaryRuns.push(runDir);
		writeEvalReport(runDir, { schema: "sure.eval.run_report.v1", status: "success" });
		const calls: Array<{ script: string; args: string[] }> = [];
		const result = finalEvaluationGate(contextFor(runDir), (_ctx, script, args) => {
			calls.push({ script, args });
			return { ok: true };
		});
		expect(result).toEqual({ ok: true });
		expect(calls).toEqual([
			{
				script: "check_eval_run_report.py",
				args: ["--produces", join(runDir, "artifacts", "eval_run_report.json"), "--phase", "final"],
			},
		]);
	});

	it("blocks an invalid evaluation report before invoking the backend", () => {
		const runDir = mkdtempSync(join(tmpdir(), "sure-eval-terminal-"));
		temporaryRuns.push(runDir);
		mkdirSync(join(runDir, "artifacts"), { recursive: true });
		writeFileSync(join(runDir, "artifacts", "eval_run_report.json"), "{broken", "utf8");
		let invoked = false;
		const result = finalEvaluationGate(contextFor(runDir), () => {
			invoked = true;
			return { ok: true };
		});
		expect(result).toMatchObject({ ok: false, reason: "evaluation report is not valid JSON" });
		expect(invoked).toBe(false);
	});

	it("leaves an explicit non-success report to the incomplete terminal contract", () => {
		const runDir = mkdtempSync(join(tmpdir(), "sure-eval-terminal-"));
		temporaryRuns.push(runDir);
		writeEvalReport(runDir, { schema: "sure.eval.run_report.v1", status: "incomplete" });
		expect(finalEvaluationGate(contextFor(runDir), () => ({ ok: false }))).toBeUndefined();
	});
});
