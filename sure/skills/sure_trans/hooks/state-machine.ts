import type { GateResult, Unit } from "./checkpoints.ts";

const FRAMEWORKS = ["pytorch_transformers"];
const DETECTED_FRAMEWORKS = ["pytorch_transformers", "pytorch_non_transformers", "tensorflow", "jax_flax", "unknown"];

function checked(kind: string): Pick<Unit, "gateScript" | "gateScriptArgs"> {
	return { gateScript: "check_artifact.py", gateScriptArgs: () => ["--kind", kind] };
}

function validated(kind: string): Pick<Unit, "gateScript" | "gateScriptArgs"> {
	return { gateScript: "run_trans_validate.py", gateScriptArgs: () => ["--kind", kind] };
}

// scaffold_adapter.py copies a model.py template that still raises
// NotImplementedError, so the manifest it writes is always draft, and SKILL.md
// has the agent run the scaffold before implementing the wrapper. That
// intermediate state is the documented order, not a failed attempt, so it must
// not cost a retry.
function adapterStillDraft(artifact: unknown): GateResult {
	const status =
		typeof artifact === "object" && artifact !== null ? (artifact as Record<string, unknown>).status : undefined;
	if (status !== "draft") {
		return { ok: true };
	}
	return {
		ok: false,
		missing: true,
		reason: "adapter wrapper is still the scaffold",
		repair:
			"adapter/model.py still raises NotImplementedError. Replace it with the model-specific wrapper, " +
			"then rerun scripts/scaffold_adapter.py so the manifest turns ready.",
	};
}

export const TRANS_UNITS: Unit[] = [
	{
		id: "load_trans_input",
		label: "Load transformation input",
		kind: "gate",
		produces: "trans_input_resolved.json",
		schemaRef: "trans_input_resolved.schema.json",
		requiredFields: [
			"dockerfile",
			"build_context",
			"model_path",
			"inference_entrypoint",
			"framework",
			"model_name",
			"model_dir",
			"task_type",
			"device",
			"source_image_policy",
		],
		allowedValues: {
			framework: FRAMEWORKS,
			task_type: ["asr", "s2tt", "tts", "vc"],
			device: ["auto", "cuda", "cpu"],
			source_image_policy: ["auto", "build", "load"],
		},
		ownedScripts: ["materialize_trans_inputs.py"],
		...checked("input"),
	},
	{
		id: "inspect_dependencies",
		label: "Inspect inference dependencies",
		kind: "gate",
		produces: "inference_dependency_report.json",
		schemaRef: "inference_dependency_report.schema.json",
		requiredFields: [
			"entrypoint",
			"build_context",
			"docker_copy_sources",
			"python_imports",
			"support_paths",
			"unresolved",
			"external_paths",
			"status",
		],
		ownedScripts: ["inspect_dependencies.py"],
		...checked("dependencies"),
	},
	{
		id: "detect_framework",
		label: "Validate framework",
		kind: "gate",
		produces: "framework_detection.json",
		schemaRef: "framework_detection.schema.json",
		requiredFields: ["declared", "detected", "primary_model_compatible", "conversion_required", "status", "evidence"],
		allowedValues: { declared: FRAMEWORKS, detected: DETECTED_FRAMEWORKS },
		ownedScripts: ["detect_framework.py"],
		...checked("framework"),
	},
	{
		id: "prepare_fixture",
		label: "Prepare bounded smoke fixture",
		kind: "gate",
		produces: "fixture_manifest.json",
		schemaRef: "fixture_manifest.schema.json",
		requiredFields: ["status", "model_name", "task_type", "source_path", "staged_path", "sha256", "sample_count"],
		ownedScripts: ["prepare_fixture.py"],
		...checked("fixture"),
	},
	{
		id: "build_source_image",
		label: "Materialize source image",
		kind: "gate",
		produces: "source_image_result.json",
		schemaRef: "source_image_result.schema.json",
		requiredFields: [
			"status",
			"image",
			"image_id",
			"dockerfile",
			"dockerfile_sha256",
			"build_context",
			"source_image_policy",
		],
		gateScript: "run_docker_build.py",
		ownedScripts: ["run_docker_build.py"],
	},
	{
		id: "validate_env_compat",
		label: "Validate source execution compatibility",
		kind: "gate",
		produces: "execution_compat.json",
		schemaRef: "execution_compat.schema.json",
		requiredFields: ["status"],
		gateScript: "run_execution_compat.py",
		// The gate pushes the source image and submits the probe through vc_exec.py;
		// SKILL.md tells the agent to drive retries through the same CLI.
		ownedScripts: ["vc_exec.py"],
	},
	{
		id: "validate_original_inference",
		label: "Validate original inference baseline",
		kind: "gate",
		produces: "original_inference_result.json",
		schemaRef: "original_inference_result.schema.json",
		requiredFields: ["status", "input", "run_command"],
		...validated("original_inference"),
	},
	{
		id: "stage_model_payload",
		label: "Stage model payload",
		kind: "gate",
		produces: "model_payload_manifest.json",
		schemaRef: "model_payload_manifest.schema.json",
		requiredFields: ["status", "source", "destination", "policy", "file_count", "total_bytes"],
		ownedScripts: ["stage_model_payload.py"],
		...checked("model_payload"),
	},
	{
		id: "generate_adapter",
		label: "Generate SURE adapter",
		kind: "gate",
		produces: "adapter_manifest.json",
		schemaRef: "adapter_manifest.schema.json",
		requiredFields: [
			"status",
			"strategy",
			"model_py",
			"init_py",
			"validate_py",
			"server_py",
			"config_yaml",
			"model_spec",
			"dockerfile",
			"io_contract",
		],
		ownedScripts: ["scaffold_adapter.py"],
		gateCheck: adapterStillDraft,
		...checked("adapter"),
	},
	{
		id: "build_adapter_image",
		label: "Build Eval adapter image",
		kind: "gate",
		produces: "adapter_image_result.json",
		schemaRef: "adapter_image_result.schema.json",
		requiredFields: ["status", "source_image", "target_image", "image_id", "server_command", "working_dir"],
		...checked("adapter_image"),
	},
	{
		id: "validate_import",
		label: "Validate adapter import",
		kind: "gate",
		produces: "import_result.json",
		schemaRef: "import_result.schema.json",
		requiredFields: ["status", "run_command"],
		...validated("import"),
	},
	{
		id: "validate_load",
		label: "Validate persistent model load",
		kind: "gate",
		produces: "load_result.json",
		schemaRef: "load_result.schema.json",
		requiredFields: ["status", "run_command"],
		...validated("load"),
	},
	{
		id: "validate_infer",
		label: "Validate adapter inference",
		kind: "gate",
		produces: "infer_result.json",
		schemaRef: "infer_result.schema.json",
		requiredFields: ["status", "input", "run_command"],
		...validated("infer"),
	},
	{
		id: "validate_contract",
		label: "Validate adapter output contract",
		kind: "gate",
		produces: "contract_result.json",
		schemaRef: "contract_result.schema.json",
		requiredFields: ["status", "run_command"],
		...validated("contract"),
	},
	{
		id: "validate_mcp",
		label: "Validate MCP protocol",
		kind: "gate",
		produces: "mcp_result.json",
		schemaRef: "mcp_result.schema.json",
		requiredFields: ["status", "tool_name", "run_command"],
		ownedScripts: ["mcp_smoke.py"],
		...validated("mcp"),
	},
	{
		id: "validate_equivalence",
		label: "Compare original and MCP inference",
		kind: "gate",
		produces: "equivalence_result.json",
		schemaRef: "equivalence_result.schema.json",
		requiredFields: ["status", "baseline_output", "adapter_output", "run_command"],
		...validated("equivalence"),
	},
	{
		id: "package_container",
		label: "Push and verify immutable image",
		kind: "gate",
		produces: "docker_registry_result.json",
		schemaRef: "docker_registry_result.schema.json",
		requiredFields: ["schema", "status", "target_image", "target_image_digest", "target_image_ref", "pull_verified"],
		ownedScripts: ["vc_exec.py", "mcp_smoke.py"],
		...checked("registry"),
	},
	{
		id: "write_runtime_inventory",
		label: "Write Eval runtime inventory",
		kind: "gate",
		produces: "runtime_inventory.json",
		schemaRef: "runtime_inventory.schema.json",
		requiredFields: ["schema", "status", "model", "container_runtime", "weights", "readiness", "policy"],
		...checked("runtime_inventory"),
		ownedScripts: ["write_runtime_inventory.py"],
	},
	{
		id: "verdict",
		label: "Write transformation verdict",
		kind: "gate",
		produces: "verdict.json",
		schemaRef: "verdict.schema.json",
		requiredFields: ["status", "model_name", "readiness", "validation", "artifacts"],
		...checked("verdict"),
		ownedScripts: ["write_verdict.py"],
	},
	{
		id: "finalize_model_bundle",
		label: "Seal Eval-ready model bundle",
		kind: "gate",
		produces: "deployment_ready.json",
		schemaRef: "deployment_ready.schema.json",
		requiredFields: [
			"schema",
			"status",
			"model_name",
			"package_profile",
			"execution_policy",
			"required_artifact_sha256",
			"bundle_identity_sha256",
		],
		ownedScripts: ["finalize_trans_bundle.py"],
		...checked("deployment_ready"),
	},
];

export const TOTAL_UNITS = TRANS_UNITS.length;
export const FIRST_UNIT = TRANS_UNITS[0];
export const LAST_UNIT = TRANS_UNITS[TRANS_UNITS.length - 1];

export function findUnit(unitId: string): Unit | undefined {
	return TRANS_UNITS.find((unit) => unit.id === unitId);
}

export function nextUnit(unitId: string): Unit | undefined {
	const index = TRANS_UNITS.findIndex((unit) => unit.id === unitId);
	return index < 0 || index >= TRANS_UNITS.length - 1 ? undefined : TRANS_UNITS[index + 1];
}
