import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import type { GateResult } from "./checkpoints.ts";

// SURE-EVAL main-flow state machine, ported from the upstream SURE-EVAL
// main_flow_agent AGENTS.md (now bundled in references/contracts/).
//
// Each unit maps to a structured-output artifact the agent produces. Linear
// units are LLM self-driven (the hook advances the checkpoint once the
// produces artifact is compliant); gate units are hook-enforced
// (post_tool_result runs validateProduces + a Python semantic gate script and
// blocks on failure). See SKILL.md for the unit contract.
//
// Gate-check split principle (no redundancy, no drift):
//   - validateProduces (checkpoints/validate.ts) owns STRUCTURE: required
//     fields, type, enum (allowedValues merged with schema.enum), and
//     additionalProperties:false (forbidden later-unit fields). Every unit.
//   - The Python gateScript owns SEMANTICS for gate units: cross-field
//     conditions, environment probes (vc availability), filesystem cross-checks
//     (template isolation, lockfile/weights existence). One authoritative
//     checker per concern — no duplicated constant lists, no === true vs
//     truthy drift.
//   - An in-process gateCheck is kept ONLY when it verifies something the
//     Python script cannot: execution_surface (no gateScript — the heavy
//     compliance audit runs at the downstream execution_readiness unit) and
//     execution_readiness (self-reported readiness/audit booleans, disjoint from
//     the python script's template-provenance audit). tool_readiness_routing is
//     a gate-without-script: its handoff_to_tool_agent block is a cross-skill
//     condition no python script owns.
//
// Two red lines (from the main-flow system prompt) are enforced as gates:
//   EXECUTION_SURFACE_ISOLATION — execution_surface.source_provenance.template_file
//     must sit under scripts/templates/; the read-only main-flow reference
//     mirror is audit-only and is rejected for runtime execution surfaces.
//     Checked by execution_readiness gate
//     (scripts/check_execution_surface_compliance.py).
//   EXECUTION_POLICY — execution=vc must use vc_submit; execution=local must use
//     local_docker even when vc is available; execution=auto prefers
//     vc when available and requires an explicit fallback reason otherwise.
//     Checked by scripts/vc_check.py, which probes the REAL environment (the
//     agent-declared vc_available field is NOT trusted in-process).

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
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
	return typeof value === "object" && value !== null && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: undefined;
}

function boolField(record: Record<string, unknown>, key: string): boolean {
	return record[key] === true;
}

// TOOL_READINESS_UNIT — when the model is not onboarded, block and hand off to
// /sure_onboard before /sure_eval can proceed. This is a cross-skill condition
// no Python script owns (it reads a self-reported routing flag, not the
// filesystem), so it stays an in-process gate (kind: "gate", no gateScript).
function checkToolReadiness(artifact: unknown): GateResult {
	const record = asRecord(artifact);
	if (!record) {
		return { ok: false, repair: "tool_readiness_routing.json must be a JSON object.", reason: "not an object" };
	}
	if (boolField(record, "handoff_to_tool_agent")) {
		return {
			ok: false,
			repair:
				"Model is not onboarded (handoff_to_tool_agent=true). Run /sure_onboard to materialize the model into sure/models/<model>/ before /sure_eval can proceed.",
			reason: "handoff to tool agent",
		};
	}
	return { ok: true };
}

// EXECUTION_SURFACE_UNIT — produces both execution_surface.json AND
// run_evaluation.sh. Main-flow templates use entrypoint_path; older harness
// artifacts used entrypoint. Accept both while preserving source_provenance and
// prior-run leakage checks. The heavy red-line isolation audit runs downstream
// via check_execution_surface_compliance.py.
function checkExecutionSurface(artifact: unknown): GateResult {
	const record = asRecord(artifact);
	if (!record) {
		return { ok: false, repair: "execution_surface.json must be a JSON object.", reason: "not an object" };
	}
	const entrypoint =
		typeof record.entrypoint_path === "string"
			? record.entrypoint_path
			: typeof record.entrypoint === "string"
				? record.entrypoint
				: "";
	if (!entrypoint) {
		return {
			ok: false,
			repair:
				"execution_surface.json must declare entrypoint_path or entrypoint (the materialized run_evaluation.sh path).",
			reason: "entrypoint missing",
		};
	}
	const provenance = asRecord(record.source_provenance);
	if (!provenance) {
		return {
			ok: false,
			repair: "execution_surface.json must declare source_provenance (template_file + isolation fields).",
			reason: "source_provenance missing",
		};
	}
	// Forbidden fields: prior-run artifacts must NOT be copied in (anti step-merge / cross-run leakage).
	if ("eval_runs_referenced" in record || "prior_run_scripts_copied" in record) {
		return {
			ok: false,
			repair:
				"execution_surface.json must not reference prior eval_runs or copy prior-run scripts. The execution surface is derived ONLY from the declared template under an approved template root.",
			reason: "prior-run leakage",
		};
	}
	return { ok: true };
}

// EXECUTION_READINESS_UNIT — red line 1 (execution surface isolation). The
// blocking booleans (execution_ready and isolation_audit.audit_passed) must be
// true. Bounded smoke is intentionally checked by the following smoke_test unit;
// requiring smoke_test_passed here would deadlock because this unit cannot run
// scripts/run_smoke.py.
function checkExecutionReadiness(artifact: unknown): GateResult {
	const record = asRecord(artifact);
	if (!record) {
		return { ok: false, repair: "execution_readiness_report.json must be a JSON object.", reason: "not an object" };
	}
	const executionReady = boolField(record, "execution_ready");
	const isolation = asRecord(record.isolation_audit);
	const auditPassed = isolation ? boolField(isolation, "audit_passed") : false;
	if (!executionReady || !auditPassed) {
		return {
			ok: false,
			repair:
				"EXECUTION_READINESS_UNIT is a gate: execution_ready and isolation_audit.audit_passed must be true before advancing to SMOKE_TEST. Run scripts/check_execution_surface_compliance.py, resolve route blockers, then set both fields to true.",
			reason: `execution_ready=${executionReady} audit_passed=${auditPassed}`,
		};
	}
	return { ok: true };
}

export const MAIN_FLOW_UNITS: Unit[] = [
	{
		id: "task_classification",
		label: "Task classification",
		kind: "linear",
		produces: "task_classification.json",
		schemaRef: "task_classification.schema.json",
		requiredFields: ["task_type", "reason", "need_tool_workflow", "confidence", "input_signals"],
		allowedValues: {
			task_type: ["onboarding_then_evaluate", "evaluate_existing_model", "repair_broken_model", "audit_results"],
		},
		forbiddenFields: ["execution_path", "report_persisted"],
	},
	{
		id: "tool_readiness_routing",
		label: "Tool readiness & routing",
		kind: "gate",
		produces: "tool_readiness_routing.json",
		schemaRef: "tool_readiness_routing.schema.json",
		requiredFields: ["readiness", "model_dir"],
		allowedValues: { readiness: ["ready", "needs_onboarding", "needs_repair", "unavailable"] },
		forbiddenFields: ["execution_path", "report_persisted"],
		gateCheck: checkToolReadiness,
	},
	{
		id: "plan",
		label: "Execution plan",
		kind: "linear",
		produces: "main_agent_plan.json",
		schemaRef: "main_agent_plan.schema.json",
		requiredFields: ["goal", "task_type", "need_tool_workflow", "execution_steps", "stop_condition", "notes"],
		forbiddenFields: ["execution_path", "report_persisted"],
	},
	{
		id: "dataset_scope",
		label: "Dataset scope",
		kind: "linear",
		produces: "dataset_decision.json",
		schemaRef: "dataset_decision.schema.json",
		requiredFields: ["selected_datasets", "skipped_datasets", "selection_basis"],
		forbiddenFields: ["execution_path", "report_persisted"],
	},
	{
		id: "script_routing",
		label: "Script routing",
		kind: "gate",
		produces: "script_routing.json",
		schemaRef: "script_routing.schema.json",
		requiredFields: ["steps"],
		forbiddenFields: ["execution_path", "report_persisted"],
		gateScript: "check_script_routing.py",
	},
	{
		id: "execution_surface",
		label: "Execution surface",
		kind: "gate",
		produces: "execution_surface.json",
		schemaRef: "execution_surface_v2.schema.json",
		requiredFields: ["source_provenance", "deployment_binding"],
		forbiddenFields: ["report_persisted", "execution_path_actual"],
		gateCheck: checkExecutionSurface,
	},
	{
		id: "execution_readiness",
		label: "Execution readiness",
		kind: "gate",
		produces: "execution_readiness_report.json",
		schemaRef: "execution_readiness_report.schema.json",
		requiredFields: ["execution_ready", "isolation_audit"],
		forbiddenFields: ["report_persisted", "execution_path_actual"],
		gateCheck: checkExecutionReadiness,
		gateScript: "check_execution_surface_compliance.py",
	},
	{
		id: "smoke_test",
		label: "Bounded smoke test",
		kind: "gate",
		produces: "smoke_test_result.json",
		schemaRef: "smoke_test_result.schema.json",
		requiredFields: ["smoke_passed"],
		forbiddenFields: ["report_persisted", "execution_path_actual"],
		gateScript: "run_smoke.py",
	},
	{
		id: "submit_vc_run",
		label: "Submit execution run",
		kind: "gate",
		produces: "submit_result.json",
		schemaRef: "submit_result.schema.json",
		requiredFields: ["execution_path", "vc_available"],
		forbiddenFields: ["report_persisted"],
		gateScript: "vc_check.py",
	},
	{
		id: "execute_wait",
		label: "Execute & wait for completion",
		kind: "gate",
		produces: "execution_result.json",
		schemaRef: "execution_result.schema.json",
		requiredFields: ["job_status"],
		allowedValues: { job_status: ["succeeded", "running", "failed", "partial"] },
		forbiddenFields: ["report_persisted"],
		gateScript: "wait_vc_execution.py",
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
	{
		id: "run_report",
		label: "Run report",
		kind: "gate",
		produces: "main_agent_run_report.json",
		schemaRef: "run_report.schema.json",
		requiredFields: ["report_persisted", "execution_path_actual"],
		gateScript: "check_run_report.py",
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
