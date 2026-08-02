import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import type { GateResult } from "./checkpoints.ts";

// SURE-EVAL model-tool agent state machine, ported from the upstream
// SURE-EVAL model_tool_agent AGENTS.md (now bundled in references/).
//
// Onboards or repairs an audio model into a reproducible local inference unit
// (wrapper set + spec + verdict) under sure/models/<model_name>/. Linear units
// are LLM self-driven (advance when produces is compliant); gate units run
// validateProduces + a Python semantic gate script and block on failure.
//
// Gate-check split principle (no redundancy, no drift):
//   - validateProduces owns STRUCTURE (required fields, enum, additionalProperties
//     /forbiddenFields) for every unit.
//   - The Python gateScript owns SEMANTICS for gate units (env_ready truthy,
//     weights_ready + resolved-path existence, compat_ok, the import/load/infer/
//     contract booleans + model.py cross-check, verdict terminal status + build/
//     validation + artifact-path existence). One authoritative checker per gate —
//     no duplicated === true vs truthy logic, no duplicated 7-check / 4-test lists.
//   - No in-process gateCheck is kept here: every gate unit's semantic condition
//     is fully owned by its python script. The verdict default template carries
//     status=pending (a scaffold); the verdict gate script correctly rejects
//     non-terminal statuses, forcing the agent to set a real terminal status.

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
	helperScripts?: string[];
}

const TASK_TYPES = [
	"asr",
	"s2tt",
	"sd",
	"ser",
	"tts",
	"vc",
	"kws",
	"slu",
	"gr",
	"speech_understanding",
	"sa-asr",
	"sa_asr",
];
const DEPLOYMENT_TYPES = ["local", "api"];
const BACKENDS = ["uv", "pip", "conda", "pixi", "docker", "api"];
const PACKAGE_PROFILES = ["none", "docker-local", "docker-registry"];

export const MODEL_TOOL_UNITS: Unit[] = [
	{
		id: "load_model_input",
		label: "Load MODEL_INPUT",
		kind: "gate",
		produces: "model_input_resolved.json",
		schemaRef: "model_input_resolved.schema.json",
		requiredFields: ["model_id", "model_name", "model_dir", "repo_url", "deployment_type", "package_profile"],
		allowedValues: {
			task_type: TASK_TYPES,
			deployment_type: DEPLOYMENT_TYPES,
			package_profile: PACKAGE_PROFILES,
		},
		forbiddenFields: ["status", "wrapper_path"],
		gateScript: "check_model_input.py",
		helperScripts: ["materialize_onboard_inputs.py"],
	},
	{
		id: "context_selection",
		label: "Select context",
		kind: "linear",
		produces: "context_selection.json",
		schemaRef: "context_selection.schema.json",
		requiredFields: ["task_type", "selected_references"],
		allowedValues: {
			task_type: TASK_TYPES,
		},
		forbiddenFields: ["status", "wrapper_path"],
	},
	{
		id: "discover",
		label: "Discover repo",
		kind: "linear",
		produces: "repo_summary.json",
		schemaRef: "repo_summary.schema.json",
		requiredFields: ["repo_url"],
		forbiddenFields: ["status", "wrapper_path"],
	},
	{
		id: "classify",
		label: "Classify model",
		kind: "linear",
		produces: "classification.json",
		schemaRef: "classification.schema.json",
		requiredFields: ["task_type"],
		allowedValues: {
			task_type: TASK_TYPES,
		},
		forbiddenFields: ["status", "wrapper_path"],
	},
	{
		id: "plan",
		label: "Select backend",
		kind: "linear",
		produces: "backend_choice.json",
		schemaRef: "backend_choice.schema.json",
		requiredFields: ["backend"],
		allowedValues: { backend: BACKENDS },
		forbiddenFields: ["status", "wrapper_path"],
	},
	{
		id: "build_plan",
		label: "Build plan",
		kind: "gate",
		produces: "build_plan.json",
		schemaRef: "build_plan.schema.json",
		requiredFields: ["model_id", "model_dir", "backend", "steps", "package_profile"],
		allowedValues: {
			backend: BACKENDS,
			package_profile: PACKAGE_PROFILES,
		},
		forbiddenFields: ["wrapper_path"],
		gateScript: "check_build_plan.py",
	},
	{
		id: "validate_spec",
		label: "Validate spec",
		kind: "gate",
		produces: "spec_validation.json",
		schemaRef: "spec_validation.schema.json",
		requiredFields: ["checks", "status"],
		gateScript: "check_spec.py",
	},
	{
		id: "prepare_fixture",
		label: "Prepare fixture",
		kind: "gate",
		produces: "fixture_manifest.json",
		schemaRef: "fixture_manifest.schema.json",
		requiredFields: ["model_dir", "task_type", "staged_dir", "gt_jsonl", "samples", "sample_count"],
		gateScript: "check_fixture.py",
		helperScripts: ["prepare_fixture.py"],
	},
	{
		id: "build_env",
		label: "Build environment",
		kind: "gate",
		produces: "build_env_result.json",
		schemaRef: "build_env_result.schema.json",
		requiredFields: ["env_ready"],
		gateScript: "check_env.py",
	},
	{
		id: "fetch_weights",
		label: "Fetch weights",
		kind: "gate",
		produces: "weights_manifest.json",
		schemaRef: "weights_manifest.schema.json",
		gateScript: "check_weights.py",
	},
	{
		id: "validate_env_compat",
		label: "Validate env compatibility",
		kind: "gate",
		produces: "env_compat_result.json",
		schemaRef: "env_compat_result.schema.json",
		requiredFields: ["compat_ok"],
		gateScript: "check_env_compat.py",
	},
	{
		id: "generate_wrapper",
		label: "Generate wrapper",
		kind: "linear",
		produces: "wrapper_manifest.json",
		schemaRef: "wrapper_manifest.schema.json",
		requiredFields: ["wrapper_path"],
		forbiddenFields: ["status"],
	},
	{
		id: "validate_import",
		label: "Validate import",
		kind: "gate",
		produces: "import_result.json",
		schemaRef: "import_result.schema.json",
		requiredFields: ["import_passed"],
		gateScript: "run_validate.py",
	},
	{
		id: "validate_load",
		label: "Validate load",
		kind: "gate",
		produces: "load_result.json",
		schemaRef: "load_result.schema.json",
		requiredFields: ["load_passed"],
		gateScript: "run_validate.py",
	},
	{
		id: "validate_infer",
		label: "Validate inference",
		kind: "gate",
		produces: "infer_result.json",
		schemaRef: "infer_result.schema.json",
		requiredFields: ["infer_passed"],
		gateScript: "run_validate.py",
	},
	{
		id: "validate_contract",
		label: "Validate contract",
		kind: "gate",
		produces: "contract_result.json",
		schemaRef: "contract_result.schema.json",
		requiredFields: ["contract_passed"],
		gateScript: "run_validate.py",
	},
	{
		id: "save_artifacts",
		label: "Save artifacts",
		kind: "gate",
		produces: "artifact_manifest.json",
		schemaRef: "artifact_manifest.schema.json",
		gateScript: "check_artifact_manifest.py",
		helperScripts: ["stage_model_artifacts.py", "adopt_reference_model.py"],
	},
	{
		id: "package_gate",
		label: "Package gate",
		kind: "gate",
		produces: "package_gate.json",
		schemaRef: "package_gate.schema.json",
		requiredFields: ["status", "package_profile", "readiness"],
		allowedValues: {
			status: ["passed", "failed", "blocked", "skipped"],
			package_profile: PACKAGE_PROFILES,
		},
		gateScript: "check_package_gate.py",
	},
	{
		id: "verdict",
		label: "Verdict",
		kind: "gate",
		produces: "verdict.json",
		schemaRef: "verdict.schema.json",
		requiredFields: ["status"],
		gateScript: "check_verdict.py",
	},
];

export const TOTAL_UNITS = MODEL_TOOL_UNITS.length;
export const FIRST_UNIT = MODEL_TOOL_UNITS[0];
export const LAST_UNIT = MODEL_TOOL_UNITS[MODEL_TOOL_UNITS.length - 1];

export function findUnit(unitId: string): Unit | undefined {
	return MODEL_TOOL_UNITS.find((unit) => unit.id === unitId);
}

export function nextUnit(unitId: string): Unit | undefined {
	const index = MODEL_TOOL_UNITS.findIndex((unit) => unit.id === unitId);
	if (index === -1 || index >= MODEL_TOOL_UNITS.length - 1) {
		return undefined;
	}
	return MODEL_TOOL_UNITS[index + 1];
}
