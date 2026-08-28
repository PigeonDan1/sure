import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { CheckpointData } from "../../../../sure/skills/sure_trans/hooks/checkpoints.ts";
import { postToolResult } from "../../../../sure/skills/sure_trans/hooks/index.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_trans");

type StatePatchForTest = {
	counters?: { gate_blocks?: number };
	message?: string;
	checkpoint?: { data: CheckpointData };
};

function statePatch(result: { state_patch?: unknown }): StatePatchForTest {
	return (result.state_patch ?? {}) as StatePatchForTest;
}

function freshCtx(name: string, args = ""): { ctx: SureHookContext; runDir: string } {
	const runDir = resolve(__dirname, "tmp-trans-retry", name);
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	const ctx: SureHookContext = {
		point: "post_tool_result",
		run: { id: "test-trans-retry", command: "/sure_trans", status: "running" } as never,
		skill: { name: "sure_trans", command: "/sure_trans" } as never,
		cwd: PACKAGE_DIR,
		packageDir: PACKAGE_DIR,
		runDir,
		args,
		event: { isError: false },
	} as SureHookContext;
	return { ctx, runDir };
}

function seed(runDir: string, retries: number): void {
	const data: CheckpointData = {
		currentUnit: "validate_contract",
		completedUnits: [],
		retries: { validate_contract: retries },
		failedArtifactDigests: {},
	};
	writeFileSync(join(runDir, "state.json"), JSON.stringify({ checkpoint: { data } }, null, 2), "utf-8");
	writeFileSync(
		join(runDir, "artifacts", "contract_result.json"),
		JSON.stringify({ status: "failed", run_command: ["true"] }, null, 2),
		"utf-8",
	);
}

// A gate script is an executor: every run resubmits a vc job. Once the unit has
// spent its retries the run has to stop spending them, otherwise each further
// tool result buys another job and the message just counts higher.
describe("a gate that has run out of retries", () => {
	it("does not run the gate script again", () => {
		const { ctx, runDir } = freshCtx("exhausted");
		seed(runDir, 3);

		const result = postToolResult(ctx);

		expect(result.ok).toBe(false);
		expect(result.repair).toContain("no retries left");
	});

	it("leaves the retry counter where it was instead of counting higher", () => {
		const { ctx, runDir } = freshCtx("no-further-bump");
		seed(runDir, 3);

		const patch = statePatch(postToolResult(ctx));

		expect(patch.checkpoint?.data.retries.validate_contract).toBe(3);
	});

	it("honors a larger max_retries from the slash command before stopping", () => {
		const { ctx, runDir } = freshCtx("raised-ceiling", "max_retries=5");
		seed(runDir, 3);

		const result = postToolResult(ctx);

		expect(result.repair ?? "").not.toContain("no retries left");
	});
});
