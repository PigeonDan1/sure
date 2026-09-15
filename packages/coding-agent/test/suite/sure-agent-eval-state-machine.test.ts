import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { CheckpointData, RunCheckpoint } from "../../../../sure/skills/sure_agent_eval/hooks/checkpoints.ts";
import { advance, bumpRetry } from "../../../../sure/skills/sure_agent_eval/hooks/checkpoints.ts";
import { countersFor, preFinish, preStart, preToolCall } from "../../../../sure/skills/sure_agent_eval/hooks/index.ts";
import {
	findUnit,
	LAST_UNIT,
	MAIN_FLOW_UNITS,
	TOTAL_UNITS,
	type Unit,
} from "../../../../sure/skills/sure_agent_eval/hooks/state-machine.ts";
import { discoverSureSkillPackages, SURE_COMMANDS } from "../../src/core/sure/manifest.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// sure_agent_eval skill package root (repo-relative from the test file).
const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_agent_eval");
const SCRIPTS_DIR = join(PACKAGE_DIR, "scripts");
const REPO_ROOT = resolve(__dirname, "../../../..");

const UNIT_IDS = ["resolve_agent", "run_agent", "evaluate", "run_report"];
const UP_TO_EVALUATE = ["resolve_agent", "run_agent", "evaluate"];
const AGENT_NAME = "demo_s2tt_agent";

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
	const runDir = resolve(__dirname, "tmp-agent-eval-sm", name);
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	const ctx: SureHookContext = {
		point,
		run: { id: "test-agent-eval-sm", command: "/sure_agent_eval", status: "running" } as never,
		skill: { name: "sure_agent_eval", command: "/sure_agent_eval" } as never,
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

// check_agent_run_report.py cross-checks the run report against the resolved
// spec: agent name equality and run_dir == spec.runtime.product_dir.
function seedResolvedSpec(runDir: string, productDir: string): void {
	writeArtifact(runDir, "agent_spec_resolved.json", {
		schema: "sure.agent_eval.spec_resolved.v1",
		agent: { name: AGENT_NAME, task: "s2tt", input: "speech", output: "text" },
		stages: [],
		datasets: [],
		metrics: ["bleu"],
		runtime: { run_id: "test-agent-eval-sm", product_dir: productDir },
	});
}

function seedSuccessEvalReport(runDir: string): void {
	writeArtifact(runDir, "eval_run_report.json", { schema: "sure.agent_eval.eval_run_report.v1", status: "success" });
}

// Every field run_report.schema.json requires (validateProduces takes the union
// of the unit's requiredFields and the schema's required list), so only the
// python gate can reject what follows.
function runReport(productDir: string, overrides: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		run_id: "test-agent-eval-sm",
		timestamp: "2026-09-10T00:00:00Z",
		task_type: "evaluate_agent",
		goal: "agent evaluation run",
		agent_name: AGENT_NAME,
		selected_datasets: ["mini_s2tt_zh2en__unversioned"],
		executed_steps: ["run_agent", "evaluate"],
		status: "success",
		report_persisted: true,
		execution_path_actual: "local",
		run_dir: productDir,
		...overrides,
	};
}

describe("sure_agent_eval command registration", () => {
	it("manifest registers /sure_agent_eval", () => {
		expect(SURE_COMMANDS).toContain("sure_agent_eval");
	});

	it("ships a generic S2TT example", () => {
		const example = readFileSync(join(PACKAGE_DIR, "examples/agent_s2tt_example.yaml"), "utf-8");
		expect(example).toContain("task: s2tt");
		expect(example).toContain("input: speech");
		expect(example).toContain("output: text");
	});

	it("repository discovery finds the package with no diagnostics", () => {
		const discovered = discoverSureSkillPackages(REPO_ROOT);
		expect(discovered.diagnostics).toEqual([]);
		const commands = discovered.packages.map((skillPackage) => skillPackage.manifest.command);
		expect(commands).toContain("sure_agent_eval");
	});
});

describe("sure_agent_eval state machine shape", () => {
	it("has exactly four units in order", () => {
		expect(MAIN_FLOW_UNITS.map((unit) => unit.id)).toEqual(UNIT_IDS);
		expect(TOTAL_UNITS).toBe(4);
	});

	it("resolve_agent is the linear pre-resolved unit owning resolve_agent.py", () => {
		const unit = findUnit("resolve_agent")!;
		expect(unit.kind).toBe("linear");
		expect(unit.produces).toBe("agent_spec_resolved.json");
		expect(unit.helperScripts).toEqual(["resolve_agent.py"]);
		expect(unit.gateScript).toBeUndefined();
	});

	// Gate units must have NO in-process gateCheck: the python gateScript is the
	// single authoritative semantic checker, and it has to exist under scripts/
	// so runGateScript can spawn it.
	it.each([
		["run_agent", "check_agent_execution.py"],
		["evaluate", "check_agent_eval_report.py"],
		["run_report", "check_agent_run_report.py"],
	])("%s delegates semantics to its python gateScript (no in-process gateCheck)", (unitId, script) => {
		const unit = findUnit(unitId)!;
		expect(unit.kind).toBe("gate");
		expect(unit.gateScript).toBe(script);
		expect(unit.gateCheck).toBeUndefined();
		expect(existsSync(join(SCRIPTS_DIR, script))).toBe(true);
	});

	it("those are all the gate units", () => {
		const gates = MAIN_FLOW_UNITS.filter((unit) => unit.kind === "gate").map((unit) => unit.id);
		expect(gates).toEqual(["run_agent", "evaluate", "run_report"]);
	});

	it("each gate unit owns exactly its runnable backend helper", () => {
		expect(findUnit("run_agent")!.helperScripts).toEqual(["agent_runner.py"]);
		expect(findUnit("evaluate")!.helperScripts).toEqual(["run_agent_eval.py"]);
		expect(findUnit("run_report")!.helperScripts).toBeUndefined();
	});
});

describe("sure_agent_eval preStart parameter validation", () => {
	// These rejections must fire before resolveHarnessPython so they hold even
	// where the locked Harness Runtime is not materialized.
	it("rejects a missing agent/datasets/metrics parameter", () => {
		const { ctx } = freshCtx("prestart-missing", "pre_start");
		ctx.args = "datasets=/data/x metrics=bleu";
		const result = preStart(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("Missing required /sure_agent_eval parameter(s): agent");
	});

	it("rejects deprecated untrusted parameters", () => {
		const { ctx } = freshCtx("prestart-deprecated", "pre_start");
		ctx.args = "agent=a.yaml datasets=/data/x metrics=bleu model=qwen3_asr";
		const result = preStart(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("does not accept model");
	});
});

// Regression guard for the preFinish terminal-gate backstop. run_report is a
// gate-with-script (check_agent_run_report.py) with NO in-process gateCheck, so
// a preFinish that only re-ran `LAST_UNIT.gateCheck` would let a report mutated
// between postToolResult and sure_finish sail through. preFinish must re-run
// the python gate.
describe("sure_agent_eval preFinish terminal-gate backstop (regression)", () => {
	it("LAST_UNIT (run_report) has a gateScript and no gateCheck — so the backstop MUST call runGateScript", () => {
		expect(LAST_UNIT.id).toBe("run_report");
		expect(LAST_UNIT.gateScript).toBe("check_agent_run_report.py");
		expect(LAST_UNIT.gateCheck).toBeUndefined();
		expect(existsSync(join(SCRIPTS_DIR, "check_agent_run_report.py"))).toBe(true);
	});

	it("rejects a tampered run_report (report_persisted=false) at finish — backstop re-runs the python gate", () => {
		const { ctx, runDir } = freshCtx("tampered", "pre_finish");
		const productDir = join(runDir, "agent-eval-run");
		seedCheckpoint(runDir, { currentUnit: "run_report", completedUnits: UP_TO_EVALUATE, retries: {} });
		seedResolvedSpec(runDir, productDir);
		seedSuccessEvalReport(runDir);
		// Schema-complete, so validateProduces passes and only check_agent_run_report.py can catch it.
		writeArtifact(runDir, "main_agent_run_report.json", runReport(productDir, { report_persisted: false }));
		const result = preFinish(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("report_persisted");
	});

	it("rejects a run_report whose agent_name drifts from the resolved spec", () => {
		const { ctx, runDir } = freshCtx("identity-drift", "pre_finish");
		const productDir = join(runDir, "agent-eval-run");
		seedCheckpoint(runDir, { currentUnit: "run_report", completedUnits: UP_TO_EVALUATE, retries: {} });
		seedResolvedSpec(runDir, productDir);
		seedSuccessEvalReport(runDir);
		writeArtifact(runDir, "main_agent_run_report.json", runReport(productDir, { agent_name: "other_agent" }));
		const result = preFinish(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("agent_name");
	});

	it("accepts a compliant run_report at finish", () => {
		const { ctx, runDir } = freshCtx("clean", "pre_finish");
		const productDir = join(runDir, "agent-eval-run");
		seedCheckpoint(runDir, { currentUnit: "run_report", completedUnits: UP_TO_EVALUATE, retries: {} });
		seedResolvedSpec(runDir, productDir);
		seedSuccessEvalReport(runDir);
		writeArtifact(runDir, "main_agent_run_report.json", runReport(productDir));
		const result = preFinish(ctx);
		expect(result.ok, result.repair).toBe(true);
	});

	it("does not double-count run_report when finish runs after the terminal checkpoint", () => {
		const { ctx, runDir } = freshCtx("terminal-already-counted", "pre_finish");
		const productDir = join(runDir, "agent-eval-run");
		seedCheckpoint(runDir, { currentUnit: "run_report", completedUnits: UNIT_IDS, retries: {} });
		seedResolvedSpec(runDir, productDir);
		seedSuccessEvalReport(runDir);
		writeArtifact(runDir, "main_agent_run_report.json", runReport(productDir));
		const result = preFinish(ctx);
		const patch = statePatch(result);
		expect(result.ok, result.repair).toBe(true);
		expect(patch.counters?.completed_units).toBe(4);
		expect(patch.counters?.total_units).toBe(4);
	});
});

describe("sure_agent_eval countersFor", () => {
	it("keeps gate_blocks consistent with the retry ledger", () => {
		const data: CheckpointData = {
			currentUnit: "run_report",
			completedUnits: [],
			retries: { run_agent: 4, evaluate: 2 },
		};
		expect(countersFor(data, 0).gate_blocks).toBe(6);
	});

	it("keeps counting blocks after the blocked unit passes", () => {
		const unit = findUnit("run_agent");
		expect(unit).toBeDefined();
		let data: CheckpointData = { currentUnit: "run_agent", completedUnits: ["resolve_agent"], retries: {} };
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

// preToolCall: the skill's own scripts belong to their owning unit; the
// sure_infer backend is import-only (any direct call is refused) and the raw
// inference surface stays off-limits from every unit.
describe("sure_agent_eval preToolCall script whitelist", () => {
	function toolCtx(name: string, currentUnit: string, completedUnits: string[], command: string): SureHookContext {
		const { ctx, runDir } = freshCtx(name, "pre_tool_call");
		seedCheckpoint(runDir, { currentUnit, completedUnits, retries: {} });
		ctx.event = { toolName: "bash", input: { command } };
		return ctx;
	}

	it("allows agent_runner.py only while run_agent is current", () => {
		const allowed = toolCtx(
			"runner-allowed",
			"run_agent",
			["resolve_agent"],
			"python3 scripts/agent_runner.py --run-dir .sure/runs/x",
		);
		expect(preToolCall(allowed).ok).toBe(true);

		const tooLate = toolCtx(
			"runner-too-late",
			"evaluate",
			["resolve_agent", "run_agent"],
			"python3 scripts/agent_runner.py --run-dir .sure/runs/x",
		);
		const result = preToolCall(tooLate);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain('is not permitted from unit "evaluate"');
	});

	it("allows run_agent_eval.py only while evaluate is current", () => {
		const allowed = toolCtx(
			"eval-allowed",
			"evaluate",
			["resolve_agent", "run_agent"],
			"python3 scripts/run_agent_eval.py --run-dir .sure/runs/x",
		);
		expect(preToolCall(allowed).ok).toBe(true);

		const tooEarly = toolCtx(
			"eval-too-early",
			"run_agent",
			["resolve_agent"],
			"python3 scripts/run_agent_eval.py --run-dir .sure/runs/x",
		);
		const result = preToolCall(tooEarly);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain('is not permitted from unit "run_agent"');
	});

	it("run_report owns no runnable backend scripts", () => {
		const ctx = toolCtx(
			"report-no-backend",
			"run_report",
			UP_TO_EVALUATE,
			"python3 scripts/run_agent_eval.py --run-dir .sure/runs/x",
		);
		const result = preToolCall(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("This unit owns no runnable backend scripts.");
	});

	it("still allows the current unit's own gate script", () => {
		const ctx = toolCtx(
			"gate-allowed",
			"evaluate",
			["resolve_agent", "run_agent"],
			"python3 scripts/check_agent_eval_report.py --run-dir .sure/runs/x --produces artifacts/eval_run_report.json",
		);
		expect(preToolCall(ctx).ok).toBe(true);
	});

	it("allows gate scripts of completed units", () => {
		const ctx = toolCtx(
			"completed-gate-allowed",
			"evaluate",
			["resolve_agent", "run_agent"],
			"python3 scripts/check_agent_execution.py --run-dir .sure/runs/x --produces artifacts/execution_result.json",
		);
		expect(preToolCall(ctx).ok).toBe(true);
	});

	it("allows resolve_agent.py while resolve_agent is current", () => {
		const ctx = toolCtx(
			"resolve-helper",
			"resolve_agent",
			[],
			"python3 scripts/resolve_agent.py --agent a.yaml --datasets /data/x --metrics bleu",
		);
		expect(preToolCall(ctx).ok).toBe(true);
	});

	it.each(UNIT_IDS)("rejects the sure_infer backend from unit %s", (unitId) => {
		const completed = UNIT_IDS.slice(0, UNIT_IDS.indexOf(unitId));
		const ctx = toolCtx(
			`infer-backend-${unitId}`,
			unitId,
			completed,
			"python3 sure/skills/sure_infer/scripts/run_eval.py --run-dir .sure/runs/x",
		);
		const result = preToolCall(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("direct call");
	});

	it.each(UNIT_IDS)("rejects the raw inference surface from unit %s", (unitId) => {
		const completed = UNIT_IDS.slice(0, UNIT_IDS.indexOf(unitId));
		const ctx = toolCtx(
			`infer-surface-${unitId}`,
			unitId,
			completed,
			"python3 scripts/generate_predictions_via_server.py --model qwen3_asr",
		);
		const result = preToolCall(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("inference surface");
	});
});
