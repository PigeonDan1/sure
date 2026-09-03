import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import type { GateResult } from "./checkpoints.ts";

// SURE-EVAL state machine: score an inference bundle with the evaluation
// engine, without running the model.
//
// Each unit maps to a structured-output artifact. Linear units are LLM
// self-driven (the hook advances the checkpoint once the produces artifact is
// compliant); gate units are hook-enforced (post_tool_result runs
// validateProduces + a Python semantic gate script and blocks on failure). See
// SKILL.md for the unit contract.
//
// Gate-check split principle (no redundancy, no drift):
//   - validateProduces (checkpoints/validate.ts) owns STRUCTURE: required
//     fields, type, enum (allowedValues merged with schema.enum), and
//     additionalProperties:false (forbidden later-unit fields). Every unit.
//   - The Python gateScript owns SEMANTICS for gate units: cross-field
//     conditions, filesystem cross-checks. One authoritative checker per
//     concern — no duplicated constant lists, no === true vs truthy drift.
//
// The evaluation itself is one deterministic backend call
// (../sure_infer/scripts/run_eval.py); check_eval_run_report.py validates the
// terminal report it writes against the resolved prediction source.

export type UnitKind = "linear" | "gate";

export interface Unit {
	id: string;
	label: string;
	kind: UnitKind;
	produces: string;
	schemaRef?: string;
	requiredFields?: string[];
	allowedValues?: Record<string, unknown[]>;
	forbiddenFields?: string[];
	gateCheck?: (artifact: unknown) => GateResult;
	gateScript?: string;
	gateScriptArgs?: (ctx: SureHookContext) => string[];
	/** Non-gate scripts under scripts/ the agent may run while this unit is current (preToolCall). */
	helperScripts?: string[];
	/** Files or dirs under artifacts/ hashed together with produces (gate re-runs when any of them change). */
	gateInputs?: string[];
}

export const MAIN_FLOW_UNITS: Unit[] = [
	{
		id: "dataset_scope",
		label: "Dataset scope",
		kind: "linear",
		produces: "dataset_decision.json",
		schemaRef: "dataset_decision.schema.json",
		requiredFields: ["selected_datasets", "skipped_datasets", "selection_basis"],
		forbiddenFields: ["execution_path", "report_persisted"],
	},
	// EXECUTE_EVALUATION — the agent runs ../sure_infer/scripts/run_eval.py,
	// which scores the source predictions, appends the evaluation batch into
	// the source directory and writes eval_run_report.json. The gate validates
	// that report against the resolved prediction source and the batch.
	{
		id: "execute_evaluation",
		label: "Execute evaluation",
		kind: "gate",
		produces: "eval_run_report.json",
		schemaRef: "eval_run_report.schema.json",
		requiredFields: ["schema", "run_id", "run_dir", "evaluation_only", "old_evaluation_reused", "source_identity"],
		allowedValues: { schema: ["sure.eval.run_report.v1"] },
		forbiddenFields: ["report_persisted", "execution_path_actual"],
		gateScript: "check_eval_run_report.py",
	},
	{
		id: "assessment",
		label: "Assessment",
		kind: "gate",
		produces: "assessment_report.json",
		schemaRef: "assessment_report.schema.json",
		requiredFields: ["anomaly_detected", "user_confirmed"],
		forbiddenFields: ["report_persisted"],
		gateScript: "check_assessment.py",
	},
	// Memory extraction (spec §4.1). Sits after the business conclusion and
	// before the closing unit, so every run that reaches sure_finish passes
	// through it. The gate reads candidates/ and memory_evidence/ next to the
	// declaration, hence gateInputs (the hooks hash them together with produces
	// so an edited candidate re-runs the gate). build_run_digest.py is only a
	// --out preview helper: the gate trusts the digest the hook built on entry.
	{
		id: "extract_lessons",
		label: "Extract lessons",
		kind: "gate",
		produces: "extraction_declaration.json",
		schemaRef: "extraction_declaration.schema.json",
		requiredFields: [
			"schema",
			"no_new_lessons",
			"no_lessons_reason",
			"covered_by",
			"candidates",
			"infra_noise",
			"infra_evidence",
		],
		allowedValues: { schema: ["sure.memory.extraction.v2"] },
		gateScript: "check_memory_extraction.py",
		gateInputs: ["candidates", "memory_evidence"],
		helperScripts: ["build_run_digest.py"],
	},
	{
		id: "run_report",
		label: "Run report",
		kind: "gate",
		produces: "main_agent_run_report.json",
		schemaRef: "run_report.schema.json",
		requiredFields: ["report_persisted", "execution_path_actual"],
		gateScript: "check_run_report.py",
		gateScriptArgs: () => ["--profile", "eval"],
	},
];

export const TOTAL_UNITS = MAIN_FLOW_UNITS.length;
export const FIRST_UNIT = MAIN_FLOW_UNITS[0];
export const LAST_UNIT = MAIN_FLOW_UNITS[MAIN_FLOW_UNITS.length - 1];

export function findUnit(unitId: string): Unit | undefined {
	return MAIN_FLOW_UNITS.find((unit) => unit.id === unitId);
}

export function nextUnit(unitId: string): Unit | undefined {
	const index = MAIN_FLOW_UNITS.findIndex((unit) => unit.id === unitId);
	if (index === -1 || index >= MAIN_FLOW_UNITS.length - 1) {
		return undefined;
	}
	return MAIN_FLOW_UNITS[index + 1];
}
