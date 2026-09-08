import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { applyValidation, initialCheckpoint, type WorkflowCheckpoint } from "../../sure-core/src/index.ts";
import { PiSureController, type SureHookDispatcher, translatePiEvent } from "../src/core/sure/controller.ts";
import { type SureWorkflowId, workflowDefinitionForSkill } from "../src/core/sure/generated-workflows.ts";
import type { SureHookContext, SureSkillPackage } from "../src/core/sure/types.ts";

const roots: string[] = [];

interface HostParityFixture {
	schema: string;
	traces: Array<{ skill_id: SureWorkflowId; branch_id: string }>;
}

const HOST_PARITY_FIXTURE = JSON.parse(
	readFileSync(new URL("../../../sure/canonical/fixtures/host-parity-traces.json", import.meta.url), "utf8"),
) as HostParityFixture;

function skillPackage(name: SureWorkflowId = "sure_infer"): SureSkillPackage {
	return {
		manifest: { name, command: name, prompt: "test" },
		manifestPath: "/repo/sure.skill.json",
		packageDir: "/repo",
		promptPath: "/repo/SKILL.md",
		prompt: "test",
		source: "repository",
		sourceRoot: "/repo",
	};
}

function context(runDir: string, skillName: SureWorkflowId = "sure_infer"): Omit<SureHookContext, "point"> {
	return {
		run: {
			runId: "controller-run",
			skillName,
			command: skillName,
			status: "running",
			cwd: runDir,
			packageDir: "/repo",
			runDir,
			args: "",
			startedAt: "2026-09-06T00:00:00.000Z",
			updatedAt: "2026-09-06T00:00:00.000Z",
		},
		skill: skillPackage(skillName).manifest,
		cwd: runDir,
		packageDir: "/repo",
		runDir,
		args: "",
		event: { toolName: "bash", toolCallId: "call-1", input: { command: "echo ok" } },
	};
}

function checkpointPatch(checkpoint: WorkflowCheckpoint): Record<string, unknown> {
	return {
		checkpoint: {
			id: checkpoint.id,
			label: checkpoint.label,
			resumable: checkpoint.resumable,
			resume_hint: checkpoint.resume_hint,
			data: checkpoint.data,
		},
	};
}

async function acceptedByPiController(
	root: string,
	skillId: SureWorkflowId,
	before: WorkflowCheckpoint,
	after: WorkflowCheckpoint,
	step: string,
): Promise<void> {
	writeFileSync(join(root, "state.json"), JSON.stringify(checkpointPatch(before)));
	const controller = new PiSureController(skillPackage(skillId), dispatcher(checkpointPatch(after)));
	const result = await controller.run("post_tool_result", context(root, skillId));
	expect(result.ok, `${skillId}/${before.branch_id}/${step}: ${result.repair ?? result.message ?? "rejected"}`).toBe(
		true,
	);
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

	it("does not accept a caller-supplied provenance issuer", async () => {
		const root = mkdtempSync(join(tmpdir(), "sure-controller-context-"));
		roots.push(root);
		let received: Omit<SureHookContext, "point"> | undefined;
		const callerIssuer = { issue: () => ({ forged: true }) };
		const dispatcher: SureHookDispatcher = {
			run: async (_point, context) => {
				received = context;
				return { ok: true };
			},
		};
		const controller = new PiSureController(skillPackage(), dispatcher);
		await controller.run("post_tool_result", {
			...context(root),
			executionProvenance: callerIssuer,
		});
		expect(received?.executionProvenance).toBeUndefined();
		expect(Object.keys(received ?? {})).not.toContain("executionProvenance");
	});

	it("rejects a Pi hook that jumps over a canonical unit", async () => {
		const root = mkdtempSync(join(tmpdir(), "sure-controller-"));
		roots.push(root);
		mkdirSync(join(root, "artifacts"), { recursive: true });
		const definition = workflowDefinitionForSkill("sure_infer");
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
		const definition = workflowDefinitionForSkill("sure_infer");
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

	it("accepts the canonical host-parity trace on every Pi workflow branch", async () => {
		expect(HOST_PARITY_FIXTURE.schema).toBe("sure.host_parity.traces.v1");
		for (const trace of HOST_PARITY_FIXTURE.traces) {
			const root = mkdtempSync(join(tmpdir(), `sure-controller-${trace.skill_id}-`));
			roots.push(root);
			const definition = workflowDefinitionForSkill(trace.skill_id);
			const branch = definition.branches.find((candidate) => candidate.id === trace.branch_id);
			if (!branch) throw new Error(`Missing fixture branch ${trace.skill_id}/${trace.branch_id}`);
			const initialOptions =
				trace.skill_id === "sure_approve"
					? { id: "approval_flow", label: "SURE approval state machine", mode: trace.branch_id }
					: undefined;
			let checkpoint = initialCheckpoint(definition, trace.branch_id, initialOptions);

			const applyAndAudit = async (
				signal:
					| { kind: "missing"; reason: string }
					| { kind: "fail"; reason: string; artifact_digest: string }
					| { kind: "pass" },
				step: string,
			): Promise<void> => {
				const transition = applyValidation(definition, checkpoint, signal);
				await acceptedByPiController(root, trace.skill_id, checkpoint, transition.checkpoint, step);
				checkpoint = transition.checkpoint;
			};

			await applyAndAudit({ kind: "missing", reason: "fixture artifact missing" }, "missing");
			await applyAndAudit(
				{ kind: "fail", reason: "fixture failure", artifact_digest: "fixture:failure-1" },
				"retry-1",
			);
			await applyAndAudit(
				{ kind: "fail", reason: "fixture bytes unchanged", artifact_digest: "fixture:failure-1" },
				"unchanged",
			);
			for (let attempt = 2; attempt <= definition.retry_policy.default_max_retries; attempt += 1) {
				await applyAndAudit(
					{
						kind: "fail",
						reason: `fixture failure ${attempt}`,
						artifact_digest: `fixture:failure-${attempt}`,
					},
					`retry-${attempt}`,
				);
			}
			await applyAndAudit({ kind: "pass" }, "repair-pass");
			for (const unit of branch.units.slice(1)) await applyAndAudit({ kind: "pass" }, `pass-${unit.id}`);
			expect(checkpoint.resumable, `${trace.skill_id}/${trace.branch_id}/terminal`).toBe(false);
			expect(checkpoint.data.completedUnits).toEqual(branch.units.map((unit) => unit.id));
		}
	});
});
