import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { CheckpointData, RunCheckpoint } from "../../../../sure/skills/sure_infer/hooks/checkpoints.ts";
import { advance, bumpRetry } from "../../../../sure/skills/sure_infer/hooks/checkpoints.ts";
import { countersFor, preFinish, preToolCall } from "../../../../sure/skills/sure_infer/hooks/index.ts";
import {
	findUnit,
	LAST_UNIT,
	MAIN_FLOW_UNITS,
	TOTAL_UNITS,
	type Unit,
} from "../../../../sure/skills/sure_infer/hooks/state-machine.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// sure_infer skill package root (repo-relative from the test file).
const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_infer");
const SCRIPTS_DIR = join(PACKAGE_DIR, "scripts");

const UNIT_IDS = ["dataset_scope", "execute_inference", "extract_lessons", "run_report"];
const UP_TO_EXECUTE = ["dataset_scope", "execute_inference"];
const DATASET = "aishell1__v1.0.2__asr";

type StatePatchForTest = {
	counters?: { completed_units?: number; total_units?: number; gate_blocks?: number };
	message?: string;
	checkpoint?: { data: CheckpointData };
};

function statePatch(result: { state_patch?: unknown }): StatePatchForTest {
	return (result.state_patch ?? {}) as StatePatchForTest;
}

function freshCtx(
	name: string,
	point: SureHookContext["point"] = "post_tool_result",
): { ctx: SureHookContext; runDir: string } {
	const runDir = resolve(__dirname, "tmp-infer-sm", name);
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	const ctx: SureHookContext = {
		point,
		run: { id: "test-infer-sm", command: "/sure_infer", status: "running" } as never,
		skill: { name: "sure_infer", command: "/sure_infer" } as never,
		cwd: PACKAGE_DIR,
		packageDir: PACKAGE_DIR,
		runDir,
		args: "",
	};
	return { ctx, runDir };
}

function seedCheckpoint(runDir: string, data: CheckpointData): void {
	writeFileSync(join(runDir, "state.json"), JSON.stringify({ checkpoint: { data } }, null, 2), "utf-8");
}

function writeArtifact(runDir: string, produces: string, value: unknown): void {
	writeFileSync(join(runDir, "artifacts", produces), JSON.stringify(value, null, 2), "utf-8");
}

// The product tree check_run_report.py --profile infer looks for under
// execution_result.product_dir: completed status rows, protocol.yaml, one
// non-empty prediction file and one reference projection per dataset.
function seedInferenceProduct(runDir: string): string {
	const root = join(runDir, "model-infer-run");
	mkdirSync(join(root, "predictions"), { recursive: true });
	mkdirSync(join(root, "references", "sure_benchmark", "jsonl"), { recursive: true });
	writeFileSync(join(root, "predictions", `${DATASET}.txt`), "utt\t文本\n", "utf-8");
	writeFileSync(
		join(root, "prediction_generation_status.json"),
		JSON.stringify(
			{
				schema: "sure.eval.prediction_generation_status.v2",
				datasets: [{ dataset: DATASET, status: "completed", num_expected_samples: 1, num_generated_samples: 1 }],
			},
			null,
			2,
		),
		"utf-8",
	);
	writeFileSync(join(root, "protocol.yaml"), "schema: sure.eval.inference_protocol.v1\n", "utf-8");
	writeFileSync(
		join(root, "references", "sure_benchmark", "jsonl", `${DATASET}.jsonl`),
		`${JSON.stringify({ key: "utt", text: "文本" })}\n`,
		"utf-8",
	);
	return root;
}

function succeededExecutionResult(productDir: string): Record<string, unknown> {
	return {
		job_status: "succeeded",
		exit_code: 0,
		execution_path: "local_docker",
		product_dir: productDir,
		datasets: [{ dataset: DATASET, expected: 1, generated: 1, valid: 1 }],
	};
}

// Every field run_report.schema.json requires (validateProduces takes the union
// of the unit's requiredFields and the schema's required list), so only the
// python gate can reject what follows.
function runReport(overrides: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		run_id: "test-infer-sm",
		timestamp: "2026-07-12T00:00:00Z",
		task_type: "evaluate_existing_model",
		goal: "inference run",
		selected_datasets: [DATASET],
		executed_steps: ["execute_inference"],
		status: "success",
		report_persisted: true,
		execution_path_actual: "local_docker",
		...overrides,
	};
}

describe("sure_infer state machine shape", () => {
	it("has exactly four units in order", () => {
		expect(MAIN_FLOW_UNITS.map((unit) => unit.id)).toEqual(UNIT_IDS);
		expect(TOTAL_UNITS).toBe(4);
	});

	// Gate units must have NO in-process gateCheck: the python gateScript is the
	// single authoritative semantic checker, and it has to exist under scripts/
	// so runGateScript can spawn it.
	it.each([
		["execute_inference", "check_execution_result.py"],
		["extract_lessons", "check_memory_extraction.py"],
		["run_report", "check_run_report.py"],
	])("%s delegates semantics to its python gateScript (no in-process gateCheck)", (unitId, script) => {
		const unit = findUnit(unitId)!;
		expect(unit.kind).toBe("gate");
		expect(unit.gateScript).toBe(script);
		expect(unit.gateCheck).toBeUndefined();
		expect(existsSync(join(SCRIPTS_DIR, script))).toBe(true);
	});

	it("those are all the gate units", () => {
		const gates = MAIN_FLOW_UNITS.filter((unit) => unit.kind === "gate").map((unit) => unit.id);
		expect(gates).toEqual(["execute_inference", "extract_lessons", "run_report"]);
	});

	it("run_report runs check_run_report.py with --profile infer", () => {
		const { ctx } = freshCtx("gate-args");
		expect(findUnit("run_report")!.gateScriptArgs?.(ctx)).toEqual(["--profile", "infer"]);
	});
});

// Regression guard for the preFinish terminal-gate backstop. run_report is a
// gate-with-script (check_run_report.py) with NO in-process gateCheck, so a
// preFinish that only re-ran `LAST_UNIT.gateCheck` would let a report mutated
// between postToolResult and sure_finish sail through. preFinish must re-run
// the python gate (mirrors the sure_onboard verdict backstop).
describe("sure_infer preFinish terminal-gate backstop (regression)", () => {
	it("LAST_UNIT (run_report) has a gateScript and no gateCheck — so the backstop MUST call runGateScript", () => {
		expect(LAST_UNIT.id).toBe("run_report");
		expect(LAST_UNIT.gateScript).toBe("check_run_report.py");
		expect(LAST_UNIT.gateCheck).toBeUndefined();
		expect(existsSync(join(SCRIPTS_DIR, "check_run_report.py"))).toBe(true);
	});

	it("rejects a tampered run_report (report_persisted=false) at finish — backstop re-runs the python gate", () => {
		const { ctx, runDir } = freshCtx("tampered", "pre_finish");
		seedCheckpoint(runDir, {
			currentUnit: "run_report",
			completedUnits: [...UP_TO_EXECUTE, "extract_lessons"],
			retries: {},
		});
		// Schema-complete, so validateProduces passes and only check_run_report.py can catch it.
		writeArtifact(runDir, "main_agent_run_report.json", runReport({ report_persisted: false }));
		const result = preFinish(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("report_persisted");
	});

	it("accepts a compliant run_report at finish", () => {
		const { ctx, runDir } = freshCtx("clean", "pre_finish");
		const productDir = seedInferenceProduct(runDir);
		seedCheckpoint(runDir, {
			currentUnit: "run_report",
			completedUnits: [...UP_TO_EXECUTE, "extract_lessons"],
			retries: {},
		});
		writeArtifact(runDir, "execution_result.json", succeededExecutionResult(productDir));
		writeArtifact(runDir, "main_agent_run_report.json", runReport());
		const result = preFinish(ctx);
		expect(result.ok, result.repair).toBe(true);
	});

	it("does not double-count run_report when finish runs after the terminal checkpoint", () => {
		const { ctx, runDir } = freshCtx("terminal-already-counted", "pre_finish");
		const productDir = seedInferenceProduct(runDir);
		seedCheckpoint(runDir, { currentUnit: "run_report", completedUnits: UNIT_IDS, retries: {} });
		writeArtifact(runDir, "execution_result.json", succeededExecutionResult(productDir));
		writeArtifact(runDir, "main_agent_run_report.json", runReport());
		const result = preFinish(ctx);
		const patch = statePatch(result);
		expect(result.ok, result.repair).toBe(true);
		expect(patch.counters?.completed_units).toBe(4);
		expect(patch.counters?.total_units).toBe(4);
	});
});

describe("sure_infer countersFor", () => {
	it("keeps gate_blocks consistent with the retry ledger", () => {
		const data: CheckpointData = {
			currentUnit: "run_report",
			completedUnits: [],
			retries: { dataset_scope: 4, execute_inference: 2 },
		};
		expect(countersFor(data, 0).gate_blocks).toBe(6);
	});

	it("keeps counting blocks after the blocked unit passes", () => {
		const unit = findUnit("dataset_scope");
		expect(unit).toBeDefined();
		let data: CheckpointData = { currentUnit: "dataset_scope", completedUnits: [], retries: {} };
		data = bumpRetry(unit as Unit, data).data;
		data = bumpRetry(unit as Unit, data).data;
		expect(countersFor(data, 0).gate_blocks).toBe(2);

		// advance() clears the unit's retry entry, which is right for the retry
		// budget and wrong for a run-long tally: a run that was blocked twice and
		// then finished used to report zero blocks.
		data = (advance(unit as Unit, data) as RunCheckpoint).data;
		expect(countersFor(data, 0).gate_blocks).toBe(2);
	});
});

// Spec §4.1: extract_lessons declares helperScripts (build_run_digest.py, for the
// --out preview only) and preToolCall must honour them the way sure_onboard
// does: allowed only while that unit is current, never from another unit. Gate
// scripts of completed units stay allowed; run_infer.py carries no
// state-machine position and is allowed from every unit.
describe("sure_infer preToolCall script whitelist", () => {
	function toolCtx(name: string, currentUnit: string, completedUnits: string[], command: string): SureHookContext {
		const { ctx, runDir } = freshCtx(name, "pre_tool_call");
		seedCheckpoint(runDir, { currentUnit, completedUnits, retries: {} });
		ctx.event = { toolName: "bash", input: { command } };
		return ctx;
	}

	it("extract_lessons declares build_run_digest.py as its only helper script", () => {
		const unit = findUnit("extract_lessons");
		expect(unit).toBeDefined();
		expect(unit?.helperScripts).toEqual(["build_run_digest.py"]);
		expect(unit?.gateScript).toBe("check_memory_extraction.py");
	});

	it("allows the digest preview helper while extract_lessons is current", () => {
		const ctx = toolCtx(
			"helper-allowed",
			"extract_lessons",
			UP_TO_EXECUTE,
			"python3 scripts/build_run_digest.py --run-dir .sure/runs/x --repo-root . --out artifacts/run_digest.preview.json",
		);
		expect(preToolCall(ctx).ok).toBe(true);
	});

	it("still allows the unit's own gate script", () => {
		const ctx = toolCtx(
			"gate-allowed",
			"extract_lessons",
			UP_TO_EXECUTE,
			"python3 scripts/check_memory_extraction.py --run-dir .sure/runs/x --produces artifacts/extraction_declaration.json",
		);
		expect(preToolCall(ctx).ok).toBe(true);
	});

	it("rejects the helper from an earlier unit", () => {
		const ctx = toolCtx(
			"helper-too-early",
			"execute_inference",
			["dataset_scope"],
			"python3 scripts/build_run_digest.py --run-dir .sure/runs/x --repo-root .",
		);
		const result = preToolCall(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain('is not permitted from unit "execute_inference"');
	});

	it("rejects the helper from a later unit even though extract_lessons is completed", () => {
		const ctx = toolCtx(
			"helper-too-late",
			"run_report",
			[...UP_TO_EXECUTE, "extract_lessons"],
			"python3 scripts/build_run_digest.py --run-dir .sure/runs/x --repo-root .",
		);
		const result = preToolCall(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain('is not permitted from unit "run_report"');
	});

	it.each(UNIT_IDS)("allows run_infer.py from unit %s", (unitId) => {
		const completed = UNIT_IDS.slice(0, UNIT_IDS.indexOf(unitId));
		const ctx = toolCtx(
			`run-infer-${unitId}`,
			unitId,
			completed,
			"python3 scripts/run_infer.py --run-dir .sure/runs/x",
		);
		expect(preToolCall(ctx).ok).toBe(true);
	});
});
