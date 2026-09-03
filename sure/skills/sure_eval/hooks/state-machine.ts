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
//     conditions, environment probes, filesystem cross-checks
//     (entrypoint provenance, lockfile/weights existence). One authoritative
//     checker per concern — no duplicated constant lists, no === true vs
//     truthy drift.
//   - An in-process gateCheck is kept ONLY when it verifies something the
//     Python script cannot: execution_surface (no gateScript — the heavy
//     compliance audit runs at the downstream execution_readiness unit) and
//     execution_readiness (self-reported readiness/audit booleans, disjoint from
//     the python script's entrypoint-provenance audit). tool_readiness_routing is
//     a gate-without-script: its handoff_to_tool_agent block is a cross-skill
//     condition no python script owns.
//
// One red line (from the main-flow system prompt) is enforced as a gate:
//   EXECUTION_SURFACE_ISOLATION — execution_surface.json is written by
//     scripts/run_infer.py, never by the agent, and must point at the bundled
//     scripts/infer_entrypoint.py: source_provenance.template_file,
//     template_sha256 and entrypoint_path name that file byte for byte, and no
//     prior eval_runs are referenced. Checked by the execution_readiness gate
//     (scripts/check_execution_surface_compliance.py).

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

// EXECUTION_SURFACE_UNIT — the agent runs scripts/run_infer.py, which writes
// execution_surface.json from scripts/infer_entrypoint.py and launches
// inference. This check only confirms the artifact has the shape run_infer.py
// produces (entrypoint_path; older artifacts used entrypoint), carries
// source_provenance and references no prior run. The authoritative provenance
// and runtime audit runs downstream via check_execution_surface_compliance.py.
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
				"execution_surface.json must declare entrypoint_path (the bundled scripts/infer_entrypoint.py). Run scripts/run_infer.py --run-dir <run_dir>, which writes this artifact; do not author the JSON by hand.",
			reason: "entrypoint missing",
		};
	}
	const provenance = asRecord(record.source_provenance);
	if (!provenance) {
		return {
			ok: false,
			repair:
				"execution_surface.json must declare source_provenance (template_file, template_sha256, isolation_compliance). scripts/run_infer.py --run-dir <run_dir> writes it; do not author the JSON by hand.",
			reason: "source_provenance missing",
		};
	}
	// Forbidden fields: prior-run artifacts must NOT be copied in (anti step-merge / cross-run leakage).
	if ("eval_runs_referenced" in record || "prior_run_scripts_copied" in record) {
		return {
			ok: false,
			repair:
				"execution_surface.json must not reference prior eval_runs or copy prior-run scripts. The execution surface is written by scripts/run_infer.py from the bundled scripts/infer_entrypoint.py; rerun it instead of editing the JSON.",
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
				"EXECUTION_READINESS_UNIT is a gate: execution_ready and isolation_audit.audit_passed must be true before advancing to SMOKE_TEST. Run scripts/check_execution_surface_compliance.py, fix what it reports, then set both fields to true.",
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
		requiredFields: ["execution_path"],
		forbiddenFields: ["report_persisted"],
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
		gateScript: "check_execution_result.py",
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
