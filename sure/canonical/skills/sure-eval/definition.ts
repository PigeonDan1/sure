import { defineCanonicalSkill } from "../../define.ts";
import type { CanonicalSkillDefinition } from "../../types.ts";

export const SURE_EVAL_CANONICAL: CanonicalSkillDefinition = defineCanonicalSkill({
	schema: "sure.canonical.skill.v1",
	skill_id: "sure_eval",
	command_id: "sure-eval",
	distribution_slug: "sure-eval",
	display_name: "SURE Eval",
	description: "Score an existing SURE prediction bundle with the pinned evaluation engine without running inference.",
	workflow: {
		schema: "sure.workflow.definition.v1",
		workflow_id: "sure_eval",
		version: "legacy-v1",
		checkpoint_id: "main_flow",
		checkpoint_label: "SURE evaluation state machine",
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
						id: "execute_evaluation",
						label: "Execute evaluation",
						kind: "gate",
						produces: "eval_run_report.json",
						schema_ref: "eval_run_report.schema.json",
						required_fields: [
							"schema",
							"run_id",
							"run_dir",
							"evaluation_only",
							"old_evaluation_reused",
							"source_identity",
						],
						forbidden_fields: ["report_persisted", "execution_path_actual"],
						allowed_values: {
							schema: ["sure.eval.run_report.v1"],
						},
						gate: {
							validator_id: "python-script",
							backend_operation_id: "sure.eval.validate_eval_report",
							script_id: "check_eval_run_report.py",
						},
					},
					{
						id: "assessment",
						label: "Assessment",
						kind: "gate",
						produces: "assessment_report.json",
						schema_ref: "assessment_report.schema.json",
						required_fields: ["anomaly_detected", "user_confirmed"],
						forbidden_fields: ["report_persisted"],
						gate: {
							validator_id: "python-script",
							backend_operation_id: "sure.eval.validate_assessment",
							script_id: "check_assessment.py",
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
							script_args: ["--profile", "eval"],
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
			type: "unit.execute_evaluation",
			path: "artifacts/eval_run_report.json",
			required: true,
			description: "Output required before advancing from execute_evaluation.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "validator",
		},
		{
			type: "unit.assessment",
			path: "artifacts/assessment_report.json",
			required: true,
			description: "Output required before advancing from assessment.",
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
			type: "runtime_binding",
			path: "artifacts/runtime_binding.json",
			required: true,
			description:
				"Formal three-runtime responsibility declaration: sure_eval binds Harness and Evaluation runtimes and proves that Model Runtime is not used.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "executor",
		},
		{
			type: "prediction_source_resolved",
			path: "artifacts/prediction_source_resolved.json",
			required: true,
			description:
				"Exact model/result identity of the prediction source (local /sure_infer run or approved result), canonical dataset set, and prediction fingerprints.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "agent",
		},
		{
			type: "dataset_decision",
			path: "artifacts/dataset_decision.json",
			required: true,
			description: "Which datasets of the source bundle are scored and why.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "agent",
		},
		{
			type: "eval_run_report",
			path: "artifacts/eval_run_report.json",
			required: true,
			description:
				"Evaluation-only report proving source identity, route execution, complete artifact persistence, and the atomic result-bundle append.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "agent",
		},
		{
			type: "assessment_report",
			path: "artifacts/assessment_report.json",
			required: true,
			description: "Anomaly assessment of the scores and the user's confirmation.",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "agent",
		},
		{
			type: "run_report",
			path: "artifacts/main_agent_run_report.json",
			required: true,
			description: "Closing run report (profile eval).",
			scope: "run-local",
			publication_mode: "run-local",
			owner: "agent",
		},
	],
	capabilities: [
		{
			capability_id: "sure.execution.evaluation-runtime",
			capability_class: "execution_capability",
			required: true,
			description: "Pinned evaluation engine and route implementation.",
		},
		{
			capability_id: "sure.execution.harness-python",
			capability_class: "execution_capability",
			required: true,
			description: "Locked SURE Harness Python runtime for deterministic gates.",
		},
	],
	semantic_validators: [
		{
			id: "python-script",
			operation: "validate",
			script: "check_eval_run_report.py",
			description: "Semantic validator python-script registered by the execute_evaluation gate.",
		},
	],
	instructions: {
		common_path: "instructions.common.md",
		portable_path: "instructions.portable.md",
		pi_path: "instructions.pi.md",
	},
	resources: {
		directories: ["scripts", "schemas"],
		legacy_root: "sure/skills/sure_eval",
	},
	pi: {
		name: "sure_eval",
		command: "/sure_eval",
		description:
			"Score an existing SURE prediction bundle (a local /sure_infer run or an approved result) with the pinned sure-evaluation engine, by metric or exact pipeline_id, without running model inference.",
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
				"runtime_binding",
				"prediction_source_resolved",
				"dataset_decision",
				"eval_run_report",
				"assessment_report",
				"run_report",
			],
			defaultExpandedSections: ["diagnostics", "artifacts"],
		},
	},
});

export default SURE_EVAL_CANONICAL;
