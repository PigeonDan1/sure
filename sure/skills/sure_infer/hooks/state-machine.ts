import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import type { GateResult } from "./checkpoints.ts";

// SURE-INFER state machine: run an approved model over the selected datasets.
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
// The execution surface is never agent-authored: scripts/run_infer.py writes
// execution_surface.json from the bundled scripts/infer_entrypoint.py, runs
// the compliance checks and launches inference. check_execution_result.py then
// validates execution_result.json against that surface and the approved input.

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
	// EXECUTE_INFERENCE — the agent runs scripts/run_infer.py --run-dir <run_dir>,
	// which writes execution_surface.json and execution_result.json. The gate
	// validates the terminal record and cross-checks the product tree.
	{
		id: "execute_inference",
		label: "Execute inference",
		kind: "gate",
		produces: "execution_result.json",
		schemaRef: "execution_result.schema.json",
		requiredFields: ["job_status"],
		allowedValues: { job_status: ["succeeded", "failed"] },
		forbiddenFields: ["report_persisted"],
		gateScript: "check_execution_result.py",
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
		gateScriptArgs: () => ["--profile", "infer"],
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
