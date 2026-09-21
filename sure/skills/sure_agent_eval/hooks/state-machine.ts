import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import type { GateResult } from "./checkpoints.ts";

// SURE-AGENT-EVAL state machine: evaluate an Agent (an ordered chain of
// approved models, e.g. ASR model -> LLM translator) over speech datasets and
// score its answers with the pinned sure-evaluation engine.
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
// resolve_agent.py runs at pre_start (the hook itself, not the agent);
// agent_runner.py and run_agent_eval.py are the only backend scripts the agent
// may invoke, each from its own unit.

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
	// RESOLVE_AGENT — pre_start already ran scripts/resolve_agent.py and wrote
	// agent_spec_resolved.json; this linear unit exists so the agent reads the
	// resolved plan and confirms it before any model is started.
	{
		id: "resolve_agent",
		label: "Resolve agent",
		kind: "linear",
		produces: "agent_spec_resolved.json",
		schemaRef: "agent_spec_resolved.schema.json",
		requiredFields: ["schema", "agent", "stages", "datasets", "metrics", "runtime"],
		allowedValues: { schema: ["sure.agent_eval.spec_resolved.v1"] },
		forbiddenFields: ["job_status", "status", "report_persisted"],
		helperScripts: ["resolve_agent.py"],
	},
	// RUN_AGENT — the agent runs scripts/agent_runner.py --run-dir <run_dir>,
	// which chains the stages (stage 1 via the model's MCP tool, later stages
	// via the API-model pattern), writes the /sure_infer-compatible bundle
	// (predictions/, protocol.yaml, prediction_generation_status.json,
	// references/) and records execution_result.json. The gate validates the
	// terminal record against the resolved spec and the product tree.
	{
		id: "run_agent",
		label: "Run agent",
		kind: "gate",
		produces: "execution_result.json",
		schemaRef: "execution_result.schema.json",
		requiredFields: ["schema", "job_status", "product_dir", "datasets"],
		allowedValues: { schema: ["sure.agent_eval.execution_result.v1"], job_status: ["succeeded", "failed"] },
		forbiddenFields: ["status", "report_persisted", "batch_id"],
		gateScript: "check_agent_execution.py",
		helperScripts: ["agent_runner.py"],
	},
	// EVALUATE — the agent runs scripts/run_agent_eval.py --run-dir <run_dir>,
	// which validates the bundle predictions, resolves the requested metrics to
	// engine pipelines (S2TT task from the dataset projection) and scores them
	// with the pinned engine in the locked Evaluation Runtime, appending the
	// batch into the bundle's evaluation_runs/. The gate validates the report
	// against the resolved spec and the batch.
	{
		id: "evaluate",
		label: "Evaluate",
		kind: "gate",
		produces: "eval_run_report.json",
		schemaRef: "eval_run_report.schema.json",
		requiredFields: ["schema", "run_id", "status", "agent", "evaluation_only", "batch_id", "datasets"],
		allowedValues: { schema: ["sure.agent_eval.eval_run_report.v1"], status: ["success", "failed"] },
		forbiddenFields: ["report_persisted"],
		gateScript: "check_agent_eval_report.py",
		helperScripts: ["run_agent_eval.py"],
	},
	{
		id: "run_report",
		label: "Run report",
		kind: "gate",
		produces: "main_agent_run_report.json",
		schemaRef: "run_report.schema.json",
		requiredFields: ["report_persisted", "execution_path_actual", "run_dir"],
		gateScript: "check_agent_run_report.py",
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
