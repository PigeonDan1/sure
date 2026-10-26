import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { materializeDatasetScope, onResume, postToolResult } from "../../../../sure/skills/sure_eval/hooks/index.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_eval");
const DATASETS = ["fleurs-test_ar__v1.0.2__asr", "other__v1.0.1__asr"];
const runDirs: string[] = [];

function createContext(datasets: unknown = DATASETS): SureHookContext {
	const runDir = mkdtempSync(join(tmpdir(), "sure-eval-scope-"));
	runDirs.push(runDir);
	mkdirSync(join(runDir, "artifacts"));
	writeFileSync(
		join(runDir, "artifacts", "prediction_source_resolved.json"),
		JSON.stringify({ schema: "sure.reval.approved_prediction_source.v2", datasets }),
	);
	return {
		point: "post_tool_result",
		run: { runId: "test-eval-scope", status: "running" },
		skill: { name: "sure_eval", command: "/sure_eval" },
		cwd: PACKAGE_DIR,
		packageDir: PACKAGE_DIR,
		runDir,
		args: "",
		event: { toolName: "read", isError: false },
	} as SureHookContext;
}

afterEach(() => {
	while (runDirs.length > 0) {
		rmSync(runDirs.pop()!, { recursive: true, force: true });
	}
});

describe("sure_eval deterministic dataset scope", () => {
	it("selects the entire source set and preserves an existing valid decision", () => {
		const ctx = createContext();
		const decisionPath = join(ctx.runDir, "artifacts", "dataset_decision.json");
		expect(materializeDatasetScope(ctx).ok).toBe(true);
		const decision = JSON.parse(readFileSync(decisionPath, "utf-8"));
		expect(decision).toMatchObject({
			run_id: "test-eval-scope",
			selected_datasets: DATASETS,
			skipped_datasets: [],
			auto_selected: true,
		});
		writeFileSync(decisionPath, JSON.stringify({ ...decision, selection_basis: ["existing decision"] }));
		expect(materializeDatasetScope(ctx).ok).toBe(true);
		expect(JSON.parse(readFileSync(decisionPath, "utf-8")).selection_basis).toEqual(["existing decision"]);
	});

	it("rejects malformed source ids and never fabricates a scope", () => {
		const ctx = createContext([DATASETS[0], DATASETS[0]]);
		const result = materializeDatasetScope(ctx);
		expect(result.ok).toBe(false);
		expect(existsSync(join(ctx.runDir, "artifacts", "dataset_decision.json"))).toBe(false);
	});

	it("rejects existing partial selection without overwriting it", () => {
		const ctx = createContext();
		const decisionPath = join(ctx.runDir, "artifacts", "dataset_decision.json");
		const partial = { selection_basis: ["subset"], selected_datasets: [DATASETS[0]], skipped_datasets: [] };
		writeFileSync(decisionPath, JSON.stringify(partial));
		expect(materializeDatasetScope(ctx).repair).toContain("entire resolved prediction source dataset set");
		expect(JSON.parse(readFileSync(decisionPath, "utf-8"))).toEqual(partial);
	});

	it("does not overwrite an unreadable existing decision", () => {
		const ctx = createContext();
		const decisionPath = join(ctx.runDir, "artifacts", "dataset_decision.json");
		writeFileSync(decisionPath, "{broken");
		expect(materializeDatasetScope(ctx).repair).toContain("not valid JSON");
		expect(readFileSync(decisionPath, "utf-8")).toBe("{broken");
	});

	it("advances an older run from dataset_scope after a successful tool result", () => {
		const ctx = createContext();
		writeFileSync(
			join(ctx.runDir, "state.json"),
			JSON.stringify({
				checkpoint: {
					data: { currentUnit: "dataset_scope", completedUnits: [], retries: {}, failedArtifactDigests: {} },
				},
			}),
		);
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		expect(result.state_patch).toMatchObject({
			phase: { id: "execute_evaluation" },
			counters: { completed_units: 1 },
			checkpoint: { data: { currentUnit: "execute_evaluation", completedUnits: ["dataset_scope"] } },
		});
		expect(existsSync(join(ctx.runDir, "artifacts", "dataset_decision.json"))).toBe(true);
	});

	it("advances an older run immediately on resume without a tool call", () => {
		const ctx = createContext();
		writeFileSync(
			join(ctx.runDir, "state.json"),
			JSON.stringify({
				checkpoint: {
					data: { currentUnit: "dataset_scope", completedUnits: [], retries: {}, failedArtifactDigests: {} },
				},
			}),
		);
		const result = onResume(ctx);
		expect(result.ok).toBe(true);
		expect(result.state_patch).toMatchObject({
			checkpoint: { data: { currentUnit: "execute_evaluation", completedUnits: ["dataset_scope"] } },
		});
		expect(existsSync(join(ctx.runDir, "artifacts", "dataset_decision.json"))).toBe(true);
	});

	it("does not repeat a completed dataset_scope on resume", () => {
		const ctx = createContext();
		writeFileSync(
			join(ctx.runDir, "state.json"),
			JSON.stringify({
				checkpoint: { data: { currentUnit: "execute_evaluation", completedUnits: ["dataset_scope"] } },
			}),
		);
		expect(onResume(ctx)).toEqual({ ok: true });
		expect(existsSync(join(ctx.runDir, "artifacts", "dataset_decision.json"))).toBe(false);
	});

	it("refuses to advance an older run with an invalid prediction source", () => {
		const ctx = createContext([]);
		const result = onResume(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("nonempty, unique set");
		expect(existsSync(join(ctx.runDir, "artifacts", "dataset_decision.json"))).toBe(false);
	});
});
