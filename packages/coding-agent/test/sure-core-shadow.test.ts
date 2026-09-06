import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { CANONICAL_SKILLS } from "../../../sure/canonical/skills/index.ts";
import {
	coreDefinitionForLegacy,
	type LegacySkillId,
	legacyUnitCounts,
} from "../../../sure/core/legacy-core-adapter.ts";
import {
	type RunCheckpoint as ApproveCheckpoint,
	type CheckpointData as ApproveData,
	advance as approveAdvance,
	bumpRetry as approveBumpRetry,
	initialCheckpoint as approveInitialCheckpoint,
} from "../../../sure/skills/sure_approve/hooks/checkpoints.ts";
import {
	APPROVE_UNITS,
	type Unit as ApproveUnit,
	AUDIT_UNITS,
	DECISION_UNITS,
} from "../../../sure/skills/sure_approve/hooks/state-machine.ts";
import {
	type RunCheckpoint as EvalCheckpoint,
	type CheckpointData as EvalData,
	advance as evalAdvance,
	bumpRetry as evalBumpRetry,
} from "../../../sure/skills/sure_eval/hooks/checkpoints.ts";
import { MAIN_FLOW_UNITS as EVAL_UNITS } from "../../../sure/skills/sure_eval/hooks/state-machine.ts";
import {
	type RunCheckpoint as FeedCheckpoint,
	type CheckpointData as FeedData,
	advance as feedAdvance,
	bumpRetry as feedBumpRetry,
} from "../../../sure/skills/sure_feed/hooks/checkpoints.ts";
import { MODEL_FEED_UNITS } from "../../../sure/skills/sure_feed/hooks/state-machine.ts";
import {
	type RunCheckpoint as InferCheckpoint,
	type CheckpointData as InferData,
	advance as inferAdvance,
	bumpRetry as inferBumpRetry,
} from "../../../sure/skills/sure_infer/hooks/checkpoints.ts";
import { MAIN_FLOW_UNITS as INFER_UNITS } from "../../../sure/skills/sure_infer/hooks/state-machine.ts";
import {
	type RunCheckpoint as OnboardCheckpoint,
	type CheckpointData as OnboardData,
	advance as onboardAdvance,
	bumpRetry as onboardBumpRetry,
} from "../../../sure/skills/sure_onboard/hooks/checkpoints.ts";
import { MODEL_TOOL_UNITS } from "../../../sure/skills/sure_onboard/hooks/state-machine.ts";
import {
	type RunCheckpoint as TransCheckpoint,
	type CheckpointData as TransData,
	advance as transAdvance,
	bumpRetry as transBumpRetry,
} from "../../../sure/skills/sure_trans/hooks/checkpoints.ts";
import { TRANS_UNITS } from "../../../sure/skills/sure_trans/hooks/state-machine.ts";
import { applyValidation, initialCheckpoint, type WorkflowCheckpointData } from "../../sure-core/src/index.ts";

interface LegacyState {
	currentUnit: string;
	completedUnits: string[];
	retries: Record<string, number>;
	blocks?: number;
	failedArtifactDigests?: Record<string, string>;
	mode?: string;
}

interface LegacyFlow {
	skillId: "sure_feed" | "sure_onboard" | "sure_infer" | "sure_eval" | "sure_trans";
	units: readonly { id: string }[];
	maxRetries: number;
	initial: LegacyState;
	advance: (state: LegacyState) => LegacyState;
	bump: (state: LegacyState, digest: string) => LegacyState;
}

interface HostParityFixture {
	schema: string;
	recipe: string[];
	traces: Array<{ skill_id: LegacySkillId; branch_id: string }>;
}

interface HostParityFlow {
	skillId: LegacySkillId;
	branchId: string;
	units: readonly { id: string }[];
	maxRetries: number;
	initial: LegacyState;
	advance: (state: LegacyState) => LegacyState;
	bump: (state: LegacyState, digest: string) => LegacyState;
}

const HOST_PARITY_FIXTURE = JSON.parse(
	readFileSync(new URL("../../../sure/canonical/fixtures/host-parity-traces.json", import.meta.url), "utf8"),
) as HostParityFixture;

function project(data: LegacyState | WorkflowCheckpointData): LegacyState {
	return {
		currentUnit: data.currentUnit,
		completedUnits: [...data.completedUnits],
		retries: { ...data.retries },
		...(data.blocks === undefined ? {} : { blocks: data.blocks }),
		failedArtifactDigests: { ...(data.failedArtifactDigests ?? {}) },
		...(data.mode === undefined ? {} : { mode: data.mode }),
	};
}

function feedData(state: LegacyState): FeedData {
	return {
		currentUnit: state.currentUnit,
		completedUnits: state.completedUnits,
		retries: state.retries,
		blocks: state.blocks,
		failedArtifactDigests: state.failedArtifactDigests ?? {},
	};
}

function onboardData(state: LegacyState): OnboardData {
	return feedData(state);
}

function transData(state: LegacyState): TransData {
	return feedData(state);
}

function inferData(state: LegacyState): InferData {
	return {
		currentUnit: state.currentUnit,
		completedUnits: state.completedUnits,
		retries: state.retries,
		blocks: state.blocks,
		failedArtifactDigests: state.failedArtifactDigests,
	};
}

function evalData(state: LegacyState): EvalData {
	return inferData(state);
}

function standardFlow(
	skillId: LegacyFlow["skillId"],
	units: readonly { id: string }[],
	maxRetries: number,
	advanceImpl: (
		unit: never,
		data: never,
	) => FeedCheckpoint | OnboardCheckpoint | InferCheckpoint | EvalCheckpoint | TransCheckpoint | undefined,
	bumpImpl: (
		unit: never,
		data: never,
		digest?: string,
	) => FeedCheckpoint | OnboardCheckpoint | InferCheckpoint | EvalCheckpoint | TransCheckpoint,
	unitAt: (id: string) => never,
	toData: (state: LegacyState) => never,
): LegacyFlow {
	const initial: LegacyState = {
		currentUnit: units[0]?.id ?? "",
		completedUnits: [],
		retries: {},
		failedArtifactDigests: {},
	};
	return {
		skillId,
		units,
		maxRetries,
		initial,
		advance: (state) => {
			const result = advanceImpl(unitAt(state.currentUnit), toData(state));
			if (!result) throw new Error(`Legacy ${skillId} advance unexpectedly returned undefined.`);
			return project(result.data);
		},
		bump: (state, digest) => project(bumpImpl(unitAt(state.currentUnit), toData(state), digest).data),
	};
}

const FLOWS: LegacyFlow[] = [
	standardFlow(
		"sure_feed",
		MODEL_FEED_UNITS,
		3,
		feedAdvance as unknown as (unit: never, data: never) => FeedCheckpoint,
		feedBumpRetry as unknown as (unit: never, data: never, digest?: string) => FeedCheckpoint,
		(id) => MODEL_FEED_UNITS.find((unit) => unit.id === id) as never,
		(state) => feedData(state) as never,
	),
	standardFlow(
		"sure_onboard",
		MODEL_TOOL_UNITS,
		3,
		onboardAdvance as unknown as (unit: never, data: never) => OnboardCheckpoint,
		onboardBumpRetry as unknown as (unit: never, data: never, digest?: string) => OnboardCheckpoint,
		(id) => MODEL_TOOL_UNITS.find((unit) => unit.id === id) as never,
		(state) => onboardData(state) as never,
	),
	standardFlow(
		"sure_infer",
		INFER_UNITS,
		2,
		inferAdvance as unknown as (unit: never, data: never) => InferCheckpoint,
		inferBumpRetry as unknown as (unit: never, data: never, digest?: string) => InferCheckpoint,
		(id) => INFER_UNITS.find((unit) => unit.id === id) as never,
		(state) => inferData(state) as never,
	),
	standardFlow(
		"sure_eval",
		EVAL_UNITS,
		2,
		evalAdvance as unknown as (unit: never, data: never) => EvalCheckpoint,
		evalBumpRetry as unknown as (unit: never, data: never, digest?: string) => EvalCheckpoint,
		(id) => EVAL_UNITS.find((unit) => unit.id === id) as never,
		(state) => evalData(state) as never,
	),
	standardFlow(
		"sure_trans",
		TRANS_UNITS,
		3,
		transAdvance as unknown as (unit: never, data: never) => TransCheckpoint,
		transBumpRetry as unknown as (unit: never, data: never, digest?: string) => TransCheckpoint,
		(id) => TRANS_UNITS.find((unit) => unit.id === id) as never,
		(state) => transData(state) as never,
	),
];

function approvalTraceFlow(mode: "audit" | "approve", units: readonly ApproveUnit[]): HostParityFlow {
	function checkpoint(state: LegacyState): ApproveCheckpoint {
		return {
			...approveInitialCheckpoint(mode),
			data: {
				mode,
				currentUnit: state.currentUnit,
				completedUnits: [...state.completedUnits],
				retries: { ...state.retries },
				...(state.blocks === undefined ? {} : { blocks: state.blocks }),
				failedArtifactDigests: { ...(state.failedArtifactDigests ?? {}) },
			},
		};
	}
	return {
		skillId: "sure_approve",
		branchId: mode,
		units,
		maxRetries: 3,
		initial: project(approveInitialCheckpoint(mode).data),
		advance: (state) => project(approveAdvance(checkpoint(state)).data),
		bump: (state, digest) => project(approveBumpRetry(checkpoint(state), digest).data),
	};
}

const HOST_PARITY_FLOWS: HostParityFlow[] = [
	...FLOWS.map((flow) => ({ ...flow, branchId: "main" })),
	approvalTraceFlow("audit", AUDIT_UNITS),
	approvalTraceFlow("approve", DECISION_UNITS),
];

function compareCanonicalTrace(flow: HostParityFlow): void {
	const definition = coreDefinitionForLegacy(flow.skillId);
	const initialOptions =
		flow.skillId === "sure_approve"
			? { id: "approval_flow", label: "SURE approval state machine", mode: flow.branchId }
			: undefined;
	let core = initialCheckpoint(definition, flow.branchId, initialOptions);
	let legacy = project(flow.initial);

	const missing = applyValidation(definition, core, { kind: "missing", reason: "fixture artifact missing" });
	expect(missing.action, `${flow.skillId}/${flow.branchId}/missing`).toBe("missing");
	expect(project(missing.checkpoint.data)).toEqual(legacy);
	core = missing.checkpoint;

	const firstDigest = "fixture:failure-1";
	legacy = flow.bump(legacy, firstDigest);
	let failed = applyValidation(definition, core, {
		kind: "fail",
		reason: "fixture failure",
		artifact_digest: firstDigest,
	});
	expect(failed.action, `${flow.skillId}/${flow.branchId}/retry-1`).toBe("retry");
	expect(project(failed.checkpoint.data)).toEqual(legacy);
	core = failed.checkpoint;

	const unchanged = applyValidation(definition, core, {
		kind: "fail",
		reason: "fixture bytes unchanged",
		artifact_digest: firstDigest,
	});
	expect(unchanged.action, `${flow.skillId}/${flow.branchId}/unchanged`).toBe("unchanged");
	expect(project(unchanged.checkpoint.data)).toEqual(legacy);
	core = unchanged.checkpoint;

	for (let attempt = 2; attempt <= flow.maxRetries; attempt += 1) {
		const digest = `fixture:failure-${attempt}`;
		legacy = flow.bump(legacy, digest);
		failed = applyValidation(definition, core, {
			kind: "fail",
			reason: `fixture failure ${attempt}`,
			artifact_digest: digest,
		});
		expect(failed.action, `${flow.skillId}/${flow.branchId}/retry-${attempt}`).toBe(
			attempt === flow.maxRetries ? "exhausted" : "retry",
		);
		expect(project(failed.checkpoint.data)).toEqual(legacy);
		core = failed.checkpoint;
	}

	legacy = flow.advance(legacy);
	let passed = applyValidation(definition, core, { kind: "pass" });
	expect(project(passed.checkpoint.data), `${flow.skillId}/${flow.branchId}/repaired`).toEqual(legacy);
	core = passed.checkpoint;

	for (const unit of flow.units.slice(1)) {
		legacy = flow.advance(legacy);
		passed = applyValidation(definition, core, { kind: "pass" });
		expect(passed.accepted, `${flow.skillId}/${flow.branchId}/${unit.id}`).toBe(true);
		expect(project(passed.checkpoint.data), `${flow.skillId}/${flow.branchId}/${unit.id}`).toEqual(legacy);
		core = passed.checkpoint;
	}

	expect(core.resumable, `${flow.skillId}/${flow.branchId}/terminal`).toBe(false);
	expect(core.data.completedUnits).toEqual(flow.units.map((unit) => unit.id));
	expect(core.data.blocks).toBe(flow.maxRetries);
}

function comparePassAndRetry(flow: LegacyFlow): void {
	const definition = coreDefinitionForLegacy(flow.skillId);
	let legacy = project(flow.initial);
	let core = initialCheckpoint(definition);
	for (const unit of flow.units) {
		const legacyNext = flow.advance(legacy);
		const coreNext = applyValidation(definition, core, { kind: "pass", artifact_digest: `pass:${unit.id}` });
		expect(coreNext.accepted, `${flow.skillId}/${unit.id}`).toBe(true);
		expect(project(coreNext.checkpoint.data), `${flow.skillId}/${unit.id}`).toEqual(legacyNext);
		legacy = legacyNext;
		core = coreNext.checkpoint;
	}
	expect(core.resumable).toBe(false);

	legacy = project(flow.initial);
	core = initialCheckpoint(definition);
	const first = flow.units[0];
	if (!first) throw new Error(`${flow.skillId} has no initial unit.`);
	const firstRetry = flow.bump(legacy, "digest:1");
	const coreFirstRetry = applyValidation(definition, core, {
		kind: "fail",
		reason: "legacy differential failure",
		artifact_digest: "digest:1",
	});
	expect(project(coreFirstRetry.checkpoint.data), `${flow.skillId}/retry-1`).toEqual(firstRetry);
	expect(coreFirstRetry.retry_consumed).toBe(true);

	const unchanged = applyValidation(definition, coreFirstRetry.checkpoint, {
		kind: "fail",
		reason: "same bytes",
		artifact_digest: "digest:1",
	});
	expect(unchanged.action).toBe("unchanged");
	expect(project(unchanged.checkpoint.data)).toEqual(firstRetry);

	const secondLegacy = flow.bump(firstRetry, "digest:2");
	const secondCore = applyValidation(definition, coreFirstRetry.checkpoint, {
		kind: "fail",
		reason: "changed bytes",
		artifact_digest: "digest:2",
	});
	expect(project(secondCore.checkpoint.data), `${flow.skillId}/retry-2`).toEqual(secondLegacy);
}

describe("legacy workflow shadow adapter", () => {
	it("uses each canonical workflow object as the Pi compatibility authority", () => {
		for (const skill of CANONICAL_SKILLS) {
			expect(coreDefinitionForLegacy(skill.skill_id as LegacySkillId)).toBe(skill.workflow);
		}
	});

	it("preserves unit order, retry defaults, and terminal boundaries", () => {
		expect(legacyUnitCounts()).toEqual({
			sure_feed: MODEL_FEED_UNITS.length,
			sure_onboard: MODEL_TOOL_UNITS.length,
			sure_infer: INFER_UNITS.length,
			sure_eval: EVAL_UNITS.length,
			sure_trans: TRANS_UNITS.length,
			sure_approve: APPROVE_UNITS.length,
		});
		for (const flow of FLOWS) {
			const definition = coreDefinitionForLegacy(flow.skillId);
			const branch = definition.branches[0];
			expect(branch.units.map((unit) => unit.id)).toEqual(flow.units.map((unit) => unit.id));
			expect(definition.retry_policy.default_max_retries).toBe(flow.maxRetries);
			expect(branch.initial_unit_id).toBe(flow.units[0]?.id);
			expect(branch.terminal_unit_id).toBe(flow.units.at(-1)?.id);
			expect(new Set(branch.units.map((unit) => unit.id)).size).toBe(branch.units.length);
		}
		for (const skillId of ["sure_feed", "sure_onboard", "sure_infer", "sure_eval", "sure_trans"] as const) {
			expect(coreDefinitionForLegacy(skillId).retry_policy.exempt_from_exhaustion).toContain("extract_lessons");
		}
	});

	it.each(FLOWS.map((flow) => [flow.skillId, flow] as const))("matches old pass/retry trace for %s", (_id, flow) => {
		comparePassAndRetry(flow);
	});

	it("matches the canonical missing/retry/exhaustion/repair trace on every Pi branch", () => {
		expect(HOST_PARITY_FIXTURE.schema).toBe("sure.host_parity.traces.v1");
		expect(HOST_PARITY_FIXTURE.recipe).toEqual([
			"missing",
			"fail_new",
			"fail_unchanged",
			"fail_until_exhausted",
			"pass_current",
			"pass_remaining",
		]);
		expect(HOST_PARITY_FLOWS.map((flow) => ({ skill_id: flow.skillId, branch_id: flow.branchId }))).toEqual(
			HOST_PARITY_FIXTURE.traces,
		);
		for (const flow of HOST_PARITY_FLOWS) compareCanonicalTrace(flow);
	});

	it("retains gate scripts, helper/owned scripts, and trans semantic marker", () => {
		const trans = coreDefinitionForLegacy("sure_trans").branches[0].units;
		const adapter = trans.find((unit) => unit.id === "generate_adapter");
		expect(adapter?.gate?.script_id).toBe("check_artifact.py");
		expect(adapter?.gate?.script_args).toEqual(["--kind", "adapter"]);
		expect(adapter?.gate?.validator_id).toBe("python-script");
		expect(adapter?.gate?.auxiliary_validator_ids).toEqual(["legacy-in-process"]);
		expect(adapter?.owned_scripts).toEqual(["scaffold_adapter.py"]);
		const extraction = trans.find((unit) => unit.id === "extract_lessons");
		expect(extraction?.owned_scripts).toEqual(["build_run_digest.py"]);
		expect(extraction?.gate?.gate_inputs).toEqual(["candidates", "memory_evidence"]);
	});

	it("keeps legacy checkpoint identity while correcting infer/eval display labels", () => {
		const infer = initialCheckpoint(coreDefinitionForLegacy("sure_infer"));
		const evalCheckpoint = initialCheckpoint(coreDefinitionForLegacy("sure_eval"));
		expect(infer.id).toBe("main_flow");
		expect(evalCheckpoint.id).toBe("main_flow");
		expect(infer.label).toBe("SURE inference state machine");
		expect(evalCheckpoint.label).toBe("SURE evaluation state machine");
	});
});

function approveState(data: ApproveData): LegacyState {
	return project(data);
}

function approveCheckpoint(data: ApproveCheckpoint): LegacyState {
	return approveState(data.data);
}

function compareApproveBranch(mode: "audit" | "approve", units: readonly ApproveUnit[]): void {
	const definition = coreDefinitionForLegacy("sure_approve");
	let legacy = approveInitialCheckpoint(mode);
	let core = initialCheckpoint(definition, mode, { id: "approval_flow", label: "SURE approval state machine", mode });
	for (const unit of units) {
		const oldNext = approveAdvance(legacy);
		const coreNext = applyValidation(definition, core, { kind: "pass", artifact_digest: `approve:${unit.id}` });
		expect(coreNext.accepted).toBe(true);
		expect(project(coreNext.checkpoint.data), `${mode}/${unit.id}`).toEqual(approveCheckpoint(oldNext));
		legacy = oldNext;
		core = coreNext.checkpoint;
	}
	expect(core.resumable).toBe(false);

	legacy = approveInitialCheckpoint(mode);
	core = initialCheckpoint(definition, mode, { id: "approval_flow", label: "SURE approval state machine", mode });
	const oldRetry = approveBumpRetry(legacy, "approval:digest:1");
	const coreRetry = applyValidation(definition, core, {
		kind: "fail",
		reason: "approval differential failure",
		artifact_digest: "approval:digest:1",
	});
	expect(project(coreRetry.checkpoint.data), `${mode}/retry`).toEqual(approveCheckpoint(oldRetry));
	const unchanged = applyValidation(definition, coreRetry.checkpoint, {
		kind: "fail",
		reason: "same approval bytes",
		artifact_digest: "approval:digest:1",
	});
	expect(unchanged.action).toBe("unchanged");
	expect(project(unchanged.checkpoint.data)).toEqual(approveCheckpoint(oldRetry));
}

describe("approval workflow shadow adapter", () => {
	it("matches both audit and approve branches", () => {
		compareApproveBranch("audit", AUDIT_UNITS);
		compareApproveBranch("approve", DECISION_UNITS);
		const definition = coreDefinitionForLegacy("sure_approve");
		expect(definition.branches.map((branch) => branch.id)).toEqual(["audit", "approve"]);
		expect(definition.branches[0]?.units.map((unit) => unit.id)).toEqual(AUDIT_UNITS.map((unit) => unit.id));
		expect(definition.branches[1]?.units.map((unit) => unit.id)).toEqual(DECISION_UNITS.map((unit) => unit.id));
	});
});
