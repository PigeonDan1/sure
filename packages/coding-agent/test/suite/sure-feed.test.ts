import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { advance, type CheckpointData } from "../../../../sure/skills/sure_feed/hooks/checkpoints.ts";
import { postToolResult } from "../../../../sure/skills/sure_feed/hooks/index.ts";
import {
	FIRST_UNIT,
	findUnit,
	LAST_UNIT,
	MODEL_FEED_UNITS,
	nextUnit,
} from "../../../../sure/skills/sure_feed/hooks/state-machine.ts";
import { validateProduces } from "../../../../sure/skills/sure_feed/hooks/validate.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// sure_feed skill package root (repo-relative from the test file).
const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_feed");
const SCRIPTS_DIR = join(PACKAGE_DIR, "scripts");

// Minimal SureHookContext mock. runDir is a temp dir we populate with artifacts.
function makeCtx(runDir: string): SureHookContext {
	return {
		point: "post_tool_result",
		run: { id: "test-feed", command: "/sure_feed", status: "running" } as never,
		skill: { name: "sure_feed", command: "/sure_feed" } as never,
		cwd: PACKAGE_DIR,
		packageDir: PACKAGE_DIR,
		runDir,
		args: "",
	};
}

function writeArtifact(runDir: string, produces: string, value: unknown): void {
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	writeFileSync(join(runDir, "artifacts", produces), JSON.stringify(value, null, 2), "utf-8");
}

// Run a gate script the same way the hook does (spawnSync, exit 0 = pass).
function runGate(script: string, runDir: string, produces: string): { ok: boolean; stderr: string } {
	const r = spawnSync(
		"python3",
		[join(SCRIPTS_DIR, script), "--run-dir", runDir, "--produces", join(runDir, "artifacts", produces)],
		{ cwd: PACKAGE_DIR, encoding: "utf-8", timeout: 30_000 },
	);
	return { ok: r.status === 0, stderr: r.stderr ?? "" };
}

const cleanups: Array<() => void> = [];

afterEach(() => {
	while (cleanups.length) {
		const clean = cleanups.pop();
		if (clean) clean();
	}
});

describe("sure_feed state machine", () => {
	it("declares the 6 units in scan→match→collect→convert→rank→emit order", () => {
		const ids = MODEL_FEED_UNITS.map((u) => u.id);
		expect(ids).toEqual([
			"scan_modelscope",
			"match_task",
			"collect_metadata",
			"convert_to_oref",
			"rank_and_select",
			"emit_handoff_manifest",
		]);
		// Linear/gate kinds: match_task and rank_and_select are gates.
		const gates = MODEL_FEED_UNITS.filter((u) => u.kind === "gate").map((u) => u.id);
		expect(gates).toEqual(["match_task", "rank_and_select"]);
		expect(MODEL_FEED_UNITS.length).toBe(6);
		expect(FIRST_UNIT.id).toBe("scan_modelscope");
		expect(LAST_UNIT.id).toBe("emit_handoff_manifest");
	});

	it("advances strictly one step (no skipping)", () => {
		const completed: CheckpointData = {
			currentUnit: "match_task",
			completedUnits: ["scan_modelscope"],
			retries: {},
		};
		const next = advance(findUnit("match_task")!, completed);
		expect(next?.data.currentUnit).toBe("collect_metadata");
		expect(next?.data.completedUnits).toContain("match_task");

		// nextUnit must not jump past the immediate neighbour.
		expect(nextUnit("scan_modelscope")?.id).toBe("match_task");
		expect(nextUnit("convert_to_oref")?.id).toBe("rank_and_select");
	});

	it("clears the retry counter for a unit once it advances", () => {
		const completed: CheckpointData = {
			currentUnit: "match_task",
			completedUnits: ["scan_modelscope"],
			retries: { match_task: 2 },
		};
		const next = advance(findUnit("match_task")!, completed);
		expect(next?.data.retries.match_task).toBeUndefined();
	});
});

describe("sure_feed validateProduces (three-gate tiers)", () => {
	it("treats a missing artifact as stay-on-unit (not a hard failure)", () => {
		const ctx = makeCtx(resolve(__dirname, "tmp-missing"));
		const unit = findUnit("scan_modelscope")!;
		const result = validateProduces(ctx, unit, undefined);
		expect(result.ok).toBe(false);
		expect(result.missing).toBe(true);
	});

	it("rejects a produces missing a required field (anti step-skip)", () => {
		const ctx = makeCtx(resolve(__dirname, "tmp-reqfield"));
		const unit = findUnit("scan_modelscope")!;
		// candidates field absent.
		const result = validateProduces(ctx, unit, { scan_summary: "x" });
		expect(result.ok).toBe(false);
		expect(result.reason).toContain("missing field candidates");
	});

	it("rejects a produces carrying a forbidden later-unit field (anti step-merge)", () => {
		const ctx = makeCtx(resolve(__dirname, "tmp-forbidden"));
		const unit = findUnit("scan_modelscope")!; // forbiddenFields: selected, handoff_manifest_path
		const result = validateProduces(ctx, unit, {
			candidates: [],
			selected: [{ model_id: "m1" }], // belongs to rank_select_result
		});
		expect(result.ok).toBe(false);
		expect(result.reason).toContain("forbidden field selected");
	});

	it("accepts a compliant scan_result", () => {
		const ctx = makeCtx(resolve(__dirname, "tmp-ok"));
		const unit = findUnit("scan_modelscope")!;
		const result = validateProduces(ctx, unit, {
			source: "modelscope",
			candidates: [{ model_id: "m1", repo: "https://modelscope.cn/x/m1" }],
		});
		expect(result.ok).toBe(true);
	});
});

// Repair-message quality: validate.ts must surface the CORRECT schema (expected
// type, enum, declared fields) inline so the agent can self-repair without
// re-reading SKILL.md. This is the explicit quality bar for schema-managing hooks.
describe("sure_feed validateProduces repair message quality", () => {
	it("missing-field repair names the field, its expected shape, AND the full schema summary", () => {
		const ctx = makeCtx(resolve(__dirname, "tmp-rq-missing"));
		const unit = findUnit("scan_modelscope")!; // schema: scan_result.schema.json
		const result = validateProduces(ctx, unit, { source: "modelscope" }); // candidates absent
		expect(result.ok).toBe(false);
		expect(result.reason).toContain("missing field candidates");
		expect(result.repair).toContain("candidates");
		expect(result.repair).toContain("Expected candidates:");
		expect(result.repair).toContain("array"); // expected type from schema
		expect(result.repair).toContain("Full expected shape:"); // full schema summary
	});

	it("value-out-of-domain repair names the field, the allowed enum, AND the bad value", () => {
		const ctx = makeCtx(resolve(__dirname, "tmp-rq-enum"));
		const unit = findUnit("scan_modelscope")!; // schema enum: source ∈ [modelscope, huggingface]
		const result = validateProduces(ctx, unit, {
			source: "github", // illegal — schema enum must reject this
			candidates: [{ model_id: "m1" }],
		});
		expect(result.ok).toBe(false);
		expect(result.reason).toContain("value out of domain");
		expect(result.repair).toContain("source");
		expect(result.repair).toContain("modelscope");
		expect(result.repair).toContain("huggingface");
		expect(result.repair).toContain("github");
	});

	it("additionalProperties:false repair lists the undeclared field AND the declared set", () => {
		const ctx = makeCtx(resolve(__dirname, "tmp-rq-extra"));
		const unit = findUnit("scan_modelscope")!; // schema sets additionalProperties:false
		const result = validateProduces(ctx, unit, {
			source: "modelscope",
			candidates: [],
			bogus_later_field: "leak", // not declared — must be caught
		});
		expect(result.ok).toBe(false);
		expect(result.reason).toContain("additional properties");
		expect(result.repair).toContain("bogus_later_field");
		expect(result.repair).toContain("additionalProperties:false");
		expect(result.repair).toContain("source"); // declared set listed
		expect(result.repair).toContain("candidates");
	});
});

describe("sure_feed gate scripts (real python3 spawnSync)", () => {
	const tmpRoot = resolve(__dirname, "tmp-gate");

	function freshRunDir(name: string): string {
		const dir = join(tmpRoot, name);
		mkdirSync(join(dir, "artifacts"), { recursive: true });
		return dir;
	}

	it("check_match_task passes when every matched candidate has match_source", () => {
		const runDir = freshRunDir("match-pass");
		writeArtifact(runDir, "match_task_result.json", {
			candidates: [
				{ model_id: "m1", match: { matched: true, match_source: "tasks", task_type: "asr" } },
				{ model_id: "m2", match: { matched: true, match_source: "custom_tag", task_type: "asr" } },
			],
		});
		const r = runGate("check_match_task.py", runDir, "match_task_result.json");
		expect(r.ok).toBe(true);
	});

	it("check_match_task fails when a matched candidate omits match_source", () => {
		const runDir = freshRunDir("match-fail");
		writeArtifact(runDir, "match_task_result.json", {
			candidates: [{ model_id: "m1", match: { matched: true } }],
		});
		const r = runGate("check_match_task.py", runDir, "match_task_result.json");
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("match_source");
	});

	it("check_rank_select passes for a non-empty selection with repo + score", () => {
		const runDir = freshRunDir("rank-pass");
		writeArtifact(runDir, "rank_select_result.json", {
			selected: [{ model_id: "m1", repo: "https://modelscope.cn/x/m1", score: 1.5, rank_reason: "top" }],
		});
		const r = runGate("check_rank_select.py", runDir, "rank_select_result.json");
		expect(r.ok).toBe(true);
	});

	it("check_rank_select fails when a selected candidate has no repo (handoff needs it)", () => {
		const runDir = freshRunDir("rank-fail");
		writeArtifact(runDir, "rank_select_result.json", {
			selected: [{ model_id: "m1", score: 1.5 }],
		});
		const r = runGate("check_rank_select.py", runDir, "rank_select_result.json");
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("repo");
	});

	it("check_rank_select repair names the score domain (≥ 0) and the bad value", () => {
		const runDir = freshRunDir("rank-score");
		writeArtifact(runDir, "rank_select_result.json", {
			selected: [{ model_id: "m1", repo: "https://modelscope.cn/x/m1", score: -3 }],
		});
		const r = runGate("check_rank_select.py", runDir, "rank_select_result.json");
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("score");
		expect(r.stderr).toContain(">="); // domain: score ≥ 0
		expect(r.stderr).toContain("-3"); // the offending value is echoed back
	});
});

// End-to-end hook pipeline: drives the REAL postToolResult through
// validateProduces + in-process gateCheck + runGateScript (which calls runBackend
// → python3 scripts/check_match_task.py --produces <abs>, with runBackend
// injecting --run-dir). This is the path that was broken before the B1 fix
// (--run-dir was never injected → argparse crash → gate always failed).
describe("sure_feed postToolResult end-to-end (real hook → gate script → advance)", () => {
	function freshCtx(name: string): { ctx: SureHookContext; runDir: string } {
		const runDir = resolve(__dirname, "tmp-e2e", name);
		mkdirSync(join(runDir, "artifacts"), { recursive: true });
		const ctx: SureHookContext = {
			point: "post_tool_result",
			run: { id: "test-e2e", command: "/sure_feed", status: "running" } as never,
			skill: { name: "sure_feed", command: "/sure_feed" } as never,
			cwd: PACKAGE_DIR,
			packageDir: PACKAGE_DIR,
			runDir,
			args: "",
		};
		return { ctx, runDir };
	}

	it("advances from match_task → collect_metadata when the gate passes (gate script actually runs)", () => {
		const { ctx, runDir } = freshCtx("advance-pass");
		// Seed the checkpoint at match_task (scan_modelscope already done).
		writeFileSync(
			join(runDir, "state.json"),
			JSON.stringify(
				{ checkpoint: { data: { currentUnit: "match_task", completedUnits: ["scan_modelscope"], retries: {} } } },
				null,
				2,
			),
			"utf-8",
		);
		writeArtifact(runDir, "match_task_result.json", {
			candidates: [{ model_id: "m1", match: { matched: true, match_source: "tasks", task_type: "asr" } }],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		// Must have advanced past match_task to collect_metadata.
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("collect_metadata");
		expect(checkpoint?.data.completedUnits).toContain("match_task");
		expect(checkpoint?.data.retries.match_task).toBeUndefined();
	});

	it("blocks (no advance) when the gate script rejects the artifact", () => {
		const { ctx, runDir } = freshCtx("advance-block");
		writeFileSync(
			join(runDir, "state.json"),
			JSON.stringify(
				{ checkpoint: { data: { currentUnit: "match_task", completedUnits: ["scan_modelscope"], retries: {} } } },
				null,
				2,
			),
			"utf-8",
		);
		// matched:true but no match_source → gate script rejects.
		writeArtifact(runDir, "match_task_result.json", {
			candidates: [{ model_id: "m1", match: { matched: true } }],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("match_source");
		// Still on match_task (did not advance).
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("match_task");
	});

	it("stays on the unit (no block, no advance) when the artifact is not yet produced", () => {
		const { ctx, runDir } = freshCtx("advance-missing");
		writeFileSync(
			join(runDir, "state.json"),
			JSON.stringify(
				{ checkpoint: { data: { currentUnit: "match_task", completedUnits: ["scan_modelscope"], retries: {} } } },
				null,
				2,
			),
			"utf-8",
		);
		// No match_task_result.json written.
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
	});
});
