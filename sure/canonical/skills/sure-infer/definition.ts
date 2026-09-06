import { defineCanonicalSkill } from "../../define.ts";
import type { CanonicalSkillDefinition } from "../../types.ts";

export const SURE_INFER_CANONICAL: CanonicalSkillDefinition = defineCanonicalSkill({
	schema: "sure.canonical.skill.v1",
	skill_id: "sure_infer",
	command_id: "sure-infer",
	distribution_slug: "sure-infer",
	display_name: "SURE Infer",
	description: "Run an approved model over selected datasets and produce a provenance-bound prediction bundle.",
	workflow: {
		schema: "sure.workflow.definition.v1",
		workflow_id: "sure_infer",
		version: "legacy-v1",
		checkpoint_id: "main_flow",
		checkpoint_label: "SURE inference state machine",
		branches: [
			{
				id: "main",
				units: [
					{
						id: "dataset_scope",
						label: "Dataset scope",
						kind: "linear",
						produces: "dataset_decision.json",
						schema_ref: "dataset_decision.schema.json",
						required_fields: ["selected_datasets", "skipped_datasets", "selection_basis"],
						forbidden_fields: ["execution_path", "report_persisted"],
					},
					{
						id: "execute_inference",
						label: "Execute inference",
						kind: "gate",
						produces: "execution_result.json",
						schema_ref: "execution_result.schema.json",
						required_fields: ["job_status"],
						forbidden_fields: ["report_persisted"],
						allowed_values: {
							job_status: ["succeeded", "failed"],
						},
						gate: {
							validator_id: "python-script",
							backend_operation_id: "sure.infer.validate_execution_result",
							script_id: "check_execution_result.py",
						},
					},
					{
						id: "extract_lessons",
						label: "Extract lessons",
						kind: "gate",
						produces: "extraction_declaration.json",
						schema_ref: "extraction_declaration.schema.json",
						required_fields: [
							"schema",
							"no_new_lessons",
							"no_lessons_reason",
							"covered_by",
							"candidates",
							"infra_noise",
							"infra_evidence",
						],
						allowed_values: {
							schema: ["sure.memory.extraction.v2"],
						},
						gate: {
							validator_id: "python-script",
							backend_operation_id: "sure.memory.validate_extraction",
							script_id: "check_memory_extraction.py",
							gate_inputs: ["candidates", "memory_evidence"],
						},
						helper_scripts: ["build_run_digest.py"],
					},
					{
						id: "run_report",
						label: "Run report",
						kind: "gate",
						produces: "main_agent_run_report.json",
						schema_ref: "run_report.schema.json",
						required_fields: ["report_persisted", "execution_path_actual"],
						gate: {
							validator_id: "python-script",
							backend_operation_id: "sure.eval.validate_run_report",
							script_id: "check_run_report.py",
							script_args: ["--profile", "infer"],
						},
					},
				],
				initial_unit_id: "dataset_scope",
				terminal_unit_id: "run_report",
			},
		],
		default_branch_id: "main",
		retry_policy: {
			default_max_retries: 2,
			exempt_from_exhaustion: ["extract_lessons"],
		},
	},
	unit_outputs: [
		{
			type: "unit.dataset_scope",
			path: "artifacts/dataset_decision.json",
			required: true,
			description: "Output required before advancing from dataset_scope.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "agent",
		},
		{
			type: "unit.execute_inference",
			path: "artifacts/execution_result.json",
			required: true,
			description: "Output required before advancing from execute_inference.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "validator",
		},
		{
			type: "unit.extract_lessons",
			path: "artifacts/extraction_declaration.json",
			required: true,
			description: "Output required before advancing from extract_lessons.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "validator",
		},
		{
			type: "unit.run_report",
			path: "artifacts/main_agent_run_report.json",
			required: true,
			description: "Output required before advancing from run_report.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "validator",
		},
	],
	internal_evidence: [
		{
			type: "checkpoint",
			path: "state.json",
			required: true,
			description: "Legacy-compatible checkpoint projection.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "harness",
		},
		{
			type: "event_log",
			path: "events.jsonl",
			required: true,
			description: "Append-only lifecycle and validator events.",
			scope: "run-local",
			publication_mode: "append-only",
			owner: "harness",
		},
	],
	published_artifacts: [
		{
			type: "eval_input_resolved",
			path: "artifacts/eval_input_resolved.json",
			required: false,
			description:
				"Resolved /sure_infer product input: the approved model binding, canonical datasets and runtime plan every later unit reads.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "agent",
		},
		{
			type: "dataset_decision",
			path: "artifacts/dataset_decision.json",
			required: true,
			description: "Dataset scope chosen by the agent: selected and skipped datasets with the selection basis.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "agent",
		},
		{
			type: "execution_surface",
			path: "artifacts/execution_surface.json",
			required: true,
			description:
				"Execution surface written by scripts/run_infer.py: the bundled entrypoint, its digest, and the approved deployment binding summary.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "executor",
		},
		{
			type: "execution_result",
			path: "artifacts/execution_result.json",
			required: true,
			description:
				"Terminal record of the inference launch: job status, exit code, failed stage, product directory and per-dataset counts.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "executor",
		},
		{
			type: "run_report",
			path: "artifacts/main_agent_run_report.json",
			required: true,
			description: "Final SURE-INFER run report (main_agent_run_report.json).",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "agent",
		},
		{
			type: "protocol",
			path: "artifacts/protocol.yaml",
			required: false,
			description: "Inference-only protocol.yaml using sure.eval.inference_protocol.v1.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "agent",
		},
		{
			type: "prediction_manifests",
			path: "artifacts/predictions",
			required: false,
			description: "Prediction inventory and conversion trace: manifest.json and conversion_manifest.json.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "agent",
		},
		{
			type: "model_eval_manifest",
			required: false,
			description: "Optional aligned model evaluation manifest (model_eval_manifest.json).",
			scope: "workspace-global",
			publication_mode: "workspace-global",
			owner: "agent",
		},
	],
	capabilities: [
		{
			capability_id: "sure.execution.model-runtime",
			capability_class: "execution_capability",
			required: true,
			description: "Approved model runtime bound by the sealed deployment manifest.",
		},
		{
			capability_id: "sure.execution.harness-python",
			capability_class: "execution_capability",
			required: true,
			description: "Locked SURE Harness Python runtime for protocol checks.",
		},
	],
	semantic_validators: [
		{
			id: "python-script",
			operation: "validate",
			script: "check_execution_result.py",
			description: "Semantic validator python-script registered by the execute_inference gate.",
		},
	],
	instructions: {
		common_path: "instructions.common.md",
		portable_path: "instructions.portable.md",
		pi_path: "instructions.pi.md",
	},
	resources: {
		directories: ["scripts", "schemas", "references", "examples", "config"],
		legacy_root: "sure/skills/sure_infer",
	},
	pi: {
		name: "sure_infer",
		command: "/sure_infer",
		description:
			"Run an approved model over selected datasets: verify its sealed runtime binding, launch the bundled inference entrypoint in the approved container or trusted-host Python, and write predictions, protocol.yaml and reference projections for /sure_eval.",
		prompt: "SKILL.md",
		hooks: {
			pre_start: [
				{
					module: "hooks/index.ts",
					handler: "preStart",
				},
			],
			pre_tool_call: [
				{
					module: "hooks/index.ts",
					handler: "preToolCall",
				},
			],
			post_tool_result: [
				{
					module: "hooks/index.ts",
					handler: "postToolResult",
				},
			],
			pre_finish: [
				{
					module: "hooks/index.ts",
					handler: "preFinish",
				},
			],
			post_finish: [
				{
					module: "hooks/index.ts",
					handler: "postFinish",
				},
			],
			on_error: [
				{
					module: "hooks/index.ts",
					handler: "onError",
				},
			],
		},
		ui: {
			primaryCounters: ["completed_units", "total_units", "gate_blocks"],
			artifactTypes: [
				"eval_input_resolved",
				"dataset_decision",
				"execution_surface",
				"execution_result",
				"run_report",
				"protocol",
				"prediction_manifests",
				"model_eval_manifest",
			],
			defaultExpandedSections: ["diagnostics", "artifacts"],
		},
	},
});

export default SURE_INFER_CANONICAL;
