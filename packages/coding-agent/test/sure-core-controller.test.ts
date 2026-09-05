import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { coreDefinitionForLegacy } from "../../../sure/core/legacy-core-adapter.ts";
import { PiSureController, type SureHookDispatcher, translatePiEvent } from "../src/core/sure/controller.ts";
import type { SureHookContext, SureSkillPackage } from "../src/core/sure/types.ts";

const roots: string[] = [];

function skillPackage(): SureSkillPackage {
	return {
		manifest: { name: "sure_infer", command: "sure_infer", prompt: "test" },
		manifestPath: "/repo/sure.skill.json",
		packageDir: "/repo",
		promptPath: "/repo/SKILL.md",
		prompt: "test",
		source: "repository",
		sourceRoot: "/repo",
	};
}

function context(runDir: string): Omit<SureHookContext, "point"> {
	return {
		run: {
			runId: "controller-run",
			skillName: "sure_infer",
			command: "sure_infer",
			status: "running",
			cwd: runDir,
			packageDir: "/repo",
			runDir,
			args: "",
			startedAt: "2026-09-06T00:00:00.000Z",
			updatedAt: "2026-09-06T00:00:00.000Z",
		},
		skill: skillPackage().manifest,
		cwd: runDir,
		packageDir: "/repo",
		runDir,
		args: "",
		event: { toolName: "bash", toolCallId: "call-1", input: { command: "echo ok" } },
	};
}

function dispatcher(result: unknown): SureHookDispatcher {
	return {
		run: async () => ({ ok: true, state_patch: result }),
	};
}

afterEach(() => {
	for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe("Pi/Core SURE controller boundary", () => {
	it("translates Pi tool events without discarding legacy payload fields", () => {
		const event = { toolName: "bash", toolCallId: "call-1", isError: false, input: { command: "echo ok" } };
		expect(translatePiEvent("pre_tool_call", event)).toEqual({
			point: "pre_tool_call",
			kind: "tool_call",
			tool_name: "bash",
			tool_call_id: "call-1",
			is_error: false,
			payload: event,
		});
	});

	it("rejects a Pi hook that jumps over a canonical unit", async () => {
		const root = mkdtempSync(join(tmpdir(), "sure-controller-"));
		roots.push(root);
		mkdirSync(join(root, "artifacts"), { recursive: true });
		const definition = coreDefinitionForLegacy("sure_infer");
		const first = definition.branches[0]?.units[0];
		const third = definition.branches[0]?.units[2];
		if (!first || !third) throw new Error("inference definition is incomplete");
		writeFileSync(
			join(root, "state.json"),
			JSON.stringify({ checkpoint: { data: { currentUnit: first.id, completedUnits: [], retries: {} } } }),
		);
		const controller = new PiSureController(
			skillPackage(),
			dispatcher({
				checkpoint: {
					id: "main_flow",
					data: {
						currentUnit: third.id,
						completedUnits: [first.id, definition.branches[0]?.units[1]?.id],
						retries: {},
					},
				},
			}),
		);
		const result = await controller.run("post_tool_result", context(root));
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("illegal checkpoint transition");
	});

	it("accepts a legal one-step Pi hook transition", async () => {
		const root = mkdtempSync(join(tmpdir(), "sure-controller-"));
		roots.push(root);
		const definition = coreDefinitionForLegacy("sure_infer");
		const first = definition.branches[0]?.units[0];
		const second = definition.branches[0]?.units[1];
		if (!first || !second) throw new Error("inference definition is incomplete");
		writeFileSync(
			join(root, "state.json"),
			JSON.stringify({ checkpoint: { data: { currentUnit: first.id, completedUnits: [], retries: {} } } }),
		);
		const controller = new PiSureController(
			skillPackage(),
			dispatcher({
				checkpoint: {
					id: "main_flow",
					data: { currentUnit: second.id, completedUnits: [first.id], retries: {} },
				},
			}),
		);
		const result = await controller.run("post_tool_result", context(root));
		expect(result.ok).toBe(true);
	});
});
