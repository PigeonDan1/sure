import type { JsonValue, WorkflowDefinition, WorkflowUnit } from "../../packages/sure-core/src/index.ts";
import { APPROVE_UNITS, AUDIT_UNITS, DECISION_UNITS } from "../skills/sure_approve/hooks/state-machine.ts";
import { MAIN_FLOW_UNITS as EVAL_UNITS } from "../skills/sure_eval/hooks/state-machine.ts";
import { MODEL_FEED_UNITS } from "../skills/sure_feed/hooks/state-machine.ts";
import { MAIN_FLOW_UNITS as INFER_UNITS } from "../skills/sure_infer/hooks/state-machine.ts";
import { MODEL_TOOL_UNITS } from "../skills/sure_onboard/hooks/state-machine.ts";
import { TRANS_UNITS } from "../skills/sure_trans/hooks/state-machine.ts";

export type LegacySkillId = "sure_feed" | "sure_onboard" | "sure_infer" | "sure_eval" | "sure_trans" | "sure_approve";

interface LegacyUnit {
	id: string;
	label: string;
	kind?: "linear" | "gate";
	produces: string;
	schemaRef?: string;
	requiredFields?: readonly string[];
	allowedValues?: Readonly<Record<string, readonly unknown[]>>;
	forbiddenFields?: readonly string[];
	gateScript?: string;
	gateCheck?: (artifact: unknown) => unknown;
	helperScripts?: readonly string[];
	ownedScripts?: readonly string[];
	gateInputs?: readonly string[];
}

const TRANS_CHECK_KINDS: Readonly<Record<string, string>> = {
	load_trans_input: "input",
	inspect_dependencies: "dependencies",
	detect_framework: "framework",
	prepare_fixture: "fixture",
	stage_model_payload: "model_payload",
	generate_adapter: "adapter",
	build_adapter_image: "adapter_image",
	package_container: "registry",
	write_runtime_inventory: "runtime_inventory",
	verdict: "verdict",
	finalize_model_bundle: "deployment_ready",
};

const TRANS_VALIDATE_KINDS: Readonly<Record<string, string>> = {
	validate_original_inference: "original_inference",
	validate_import: "import",
	validate_load: "load",
	validate_infer: "infer",
	validate_contract: "contract",
	validate_mcp: "mcp",
	validate_equivalence: "equivalence",
};

function asJsonValue(value: unknown): JsonValue | undefined {
	if (value === null || typeof value === "string" || typeof value === "boolean") return value;
	if (typeof value === "number") return Number.isFinite(value) ? value : undefined;
	if (Array.isArray(value)) {
		const parsed = value.map(asJsonValue);
		return parsed.every((entry): entry is JsonValue => entry !== undefined) ? parsed : undefined;
	}
	if (typeof value === "object" && value !== null) {
		const output: Record<string, JsonValue> = {};
		for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
			const parsed = asJsonValue(entry);
			if (parsed === undefined) return undefined;
			output[key] = parsed;
		}
		return output;
	}
	return undefined;
}

function jsonValues(values: readonly unknown[] | undefined): readonly JsonValue[] | undefined {
	if (!values) return undefined;
	const parsed = values.map(asJsonValue);
	return parsed.every((entry): entry is JsonValue => entry !== undefined) ? parsed : undefined;
}

function scriptArgs(skillId: LegacySkillId, unit: LegacyUnit): readonly string[] | undefined {
	if (skillId === "sure_infer" && unit.id === "run_report") return ["--profile", "infer"];
	if (skillId === "sure_eval" && unit.id === "run_report") return ["--profile", "eval"];
	if (skillId === "sure_trans") {
		const kind = TRANS_CHECK_KINDS[unit.id] ?? TRANS_VALIDATE_KINDS[unit.id];
		return kind ? ["--kind", kind] : undefined;
	}
	if (skillId === "sure_approve") {
		if (unit.id === "classify_producer") return ["--kind", "producer"];
		if (unit.id === "audit_integrity") return ["--kind", "integrity"];
		if (unit.id === "seal_candidate") return ["--kind", "manifest"];
		if (unit.id === "prepare_review") return ["--kind", "review"];
	}
	return undefined;
}

function normalizeUnit(skillId: LegacySkillId, legacy: LegacyUnit): WorkflowUnit {
	const kind = legacy.kind ?? "gate";
	const args = scriptArgs(skillId, legacy);
	const allowedValues =
		legacy.allowedValues === undefined
			? undefined
			: Object.fromEntries(
					Object.entries(legacy.allowedValues).flatMap(([key, values]) => {
						const parsed = jsonValues(values);
						return parsed === undefined ? [] : [[key, parsed]];
					}),
				);
	return {
		id: legacy.id,
		label: legacy.label,
		kind,
		produces: legacy.produces,
		...(legacy.schemaRef === undefined ? {} : { schema_ref: legacy.schemaRef }),
		...(legacy.requiredFields === undefined ? {} : { required_fields: legacy.requiredFields }),
		...(legacy.forbiddenFields === undefined ? {} : { forbidden_fields: legacy.forbiddenFields }),
		...(allowedValues === undefined ? {} : { allowed_values: allowedValues }),
		...(kind !== "gate"
			? {}
			: {
					gate: {
						validator_id: legacy.gateScript
							? "python-script"
							: legacy.gateCheck
								? "legacy-in-process"
								: "structural",
						...(legacy.gateScript !== undefined && legacy.gateCheck !== undefined
							? { auxiliary_validator_ids: ["legacy-in-process"] }
							: {}),
						...(legacy.gateScript === undefined ? {} : { script_id: legacy.gateScript }),
						...(args === undefined ? {} : { script_args: args }),
						...(legacy.gateInputs === undefined ? {} : { gate_inputs: legacy.gateInputs }),
					},
				}),
		...(legacy.helperScripts === undefined ? {} : { helper_scripts: legacy.helperScripts }),
		...(legacy.ownedScripts === undefined ? {} : { owned_scripts: legacy.ownedScripts }),
	};
}

function makeDefinition(
	skillId: LegacySkillId,
	units: readonly LegacyUnit[],
	maxRetries: number,
	branchId = "main",
): WorkflowDefinition {
	const normalized = units.map((unit) => normalizeUnit(skillId, unit));
	const first = normalized[0];
	const last = normalized[normalized.length - 1];
	if (!first || !last) throw new Error(`Legacy skill ${skillId} has no units.`);
	return {
		schema: "sure.workflow.definition.v1",
		workflow_id: skillId,
		version: "legacy-v1",
		checkpoint_id: "main_flow",
		checkpoint_label:
			skillId === "sure_feed"
				? "SURE model-feed state machine"
				: skillId === "sure_onboard"
					? "SURE model-tool state machine"
					: skillId === "sure_infer"
						? "SURE inference state machine"
						: skillId === "sure_eval"
							? "SURE evaluation state machine"
							: skillId === "sure_trans"
								? "SURE model transformation state machine"
								: "SURE state machine",
		branches: [{ id: branchId, units: normalized, initial_unit_id: first.id, terminal_unit_id: last.id }],
		default_branch_id: branchId,
		retry_policy: {
			default_max_retries: maxRetries,
			...(skillId !== "sure_approve" ? { exempt_from_exhaustion: ["extract_lessons"] } : {}),
		},
	};
}

function approveDefinition(): WorkflowDefinition {
	const audit = AUDIT_UNITS.map((unit) => normalizeUnit("sure_approve", unit));
	const decision = DECISION_UNITS.map((unit) => normalizeUnit("sure_approve", unit));
	const firstAudit = audit[0];
	const lastAudit = audit.at(-1);
	const firstDecision = decision[0];
	const lastDecision = decision.at(-1);
	if (!firstAudit || !lastAudit || !firstDecision || !lastDecision) throw new Error("Approval workflow has no units.");
	return {
		schema: "sure.workflow.definition.v1",
		workflow_id: "sure_approve",
		version: "legacy-v1",
		checkpoint_id: "approval_flow",
		checkpoint_label: "SURE approval state machine",
		branches: [
			{ id: "audit", units: audit, initial_unit_id: firstAudit.id, terminal_unit_id: lastAudit.id },
			{ id: "approve", units: decision, initial_unit_id: firstDecision.id, terminal_unit_id: lastDecision.id },
		],
		default_branch_id: "audit",
		retry_policy: { default_max_retries: 3 },
	};
}

export const LEGACY_WORKFLOW_DEFINITIONS: Readonly<Record<LegacySkillId, WorkflowDefinition>> = {
	sure_feed: makeDefinition("sure_feed", MODEL_FEED_UNITS, 3),
	sure_onboard: makeDefinition("sure_onboard", MODEL_TOOL_UNITS, 3),
	sure_infer: makeDefinition("sure_infer", INFER_UNITS, 2),
	sure_eval: makeDefinition("sure_eval", EVAL_UNITS, 2),
	sure_trans: makeDefinition("sure_trans", TRANS_UNITS, 3),
	sure_approve: approveDefinition(),
};

export function coreDefinitionForLegacy(skillId: LegacySkillId): WorkflowDefinition {
	return LEGACY_WORKFLOW_DEFINITIONS[skillId];
}

export function legacyUnitCounts(): Readonly<Record<LegacySkillId, number>> {
	return Object.fromEntries(
		(Object.keys(LEGACY_WORKFLOW_DEFINITIONS) as LegacySkillId[]).map((skillId) => [
			skillId,
			LEGACY_WORKFLOW_DEFINITIONS[skillId].branches.reduce((sum, branch) => sum + branch.units.length, 0),
		]),
	) as Readonly<Record<LegacySkillId, number>>;
}

export { APPROVE_UNITS };
