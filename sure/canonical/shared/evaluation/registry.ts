/**
 * Canonical declarations for semantic evaluation backends.
 *
 * The declaration deliberately contains both the canonical and compatibility
 * roots.  During the migration the byte-compatible legacy tree is still a
 * supported installation, but selection is made by an operation id rather
 * than by a hook walking into a sibling skill directory.  The generator
 * materializes the digests and the host adapters resolve this manifest before
 * spawning Python.
 */
import type { ExecutionInputContract } from "../../../../packages/sure-core/src/execution/input-contract.ts";
import {
	SURE_TRANS_PYTHON_ADAPTER_INPUT_CONTRACT,
	SURE_TRANS_PYTHON_PACKAGE_INPUT_CONTRACT,
} from "./trans-execution-contracts.ts";

export interface SemanticBackendOperation {
	operation_id: string;
	description: string;
	entrypoint: string;
	consumer_skill_ids: readonly string[];
	/** Stable semantic operation kind, independent of the host executor. */
	kind: "execute" | "validate" | "resolve";
	timeout_ms: number;
	deterministic: boolean;
	/** Refuse execution unless the run carries immutable site-policy evidence. */
	requires_policy_snapshot?: boolean;
	/** Relationship between an execute operation and its gate artifact. */
	artifact_mode?: "preexisting" | "mutating" | "producing";
	/** Declarative output boundary consumed by the host-neutral executor. */
	output_contract?: SemanticBackendOutputContract;
	/** Conditional, fail-closed input boundary for runtime-specific operations. */
	input_contract?: ExecutionInputContract;
	/** Static execution capabilities; input-dependent capabilities stay in the adapter. */
	capability_requirements?: readonly SemanticBackendCapabilityRequirement[];
}

export interface SemanticBackendCapabilityRequirement {
	capability_id: string;
	capability_class: "execution_capability";
	required: boolean;
}

export interface SemanticBackendOutputSpec {
	artifact_id: string;
	path: string;
	kind: "file" | "directory";
	required: boolean;
}

export interface SemanticBackendOutputContract {
	schema: "sure.execution_output_contract.v1";
	mode: "preexisting" | "mutating" | "producing";
	outputs: readonly SemanticBackendOutputSpec[];
	temporary_paths: readonly string[];
	allow_missing_on_failure: boolean;
	retain_failed_outputs: boolean;
}

function transOutputContract(
	mode: SemanticBackendOutputContract["mode"],
	artifactId: string,
	path: string,
): SemanticBackendOutputContract {
	return {
		schema: "sure.execution_output_contract.v1",
		mode,
		outputs: [{ artifact_id: artifactId, path, kind: "file", required: true }],
		// The legacy runners retain ordinary logs on successful runs. They are
		// intentionally outside the declared output set; only the result file is
		// eligible to bind a workflow artifact.
		temporary_paths: [],
		allow_missing_on_failure: true,
		retain_failed_outputs: false,
	};
}

export type SemanticBackendRootKind = "skill" | "repository";

export interface SemanticBackendBundle {
	schema: "sure.semantic.backend.bundle.v1";
	bundle_id: string;
	version: string;
	description: string;
	/** Skill directory relative to sure/canonical/skills/. */
	canonical_root: string;
	/** Defaults to skill; repository roots are relative to the checkout root. */
	canonical_root_kind?: SemanticBackendRootKind;
	/** Compatibility directory relative to sure/. */
	legacy_root: string;
	/** Defaults to skill; repository roots are relative to the checkout root. */
	legacy_root_kind?: SemanticBackendRootKind;
	/** Subtree containing every executable byte that defines this backend. */
	integrity_root: string;
	operations: readonly SemanticBackendOperation[];
}

/**
 * The current evaluator implementation is intentionally still the existing
 * sure-infer tree.  Naming that fact in a manifest makes the dependency
 * explicit and hashable; a later PR can move the tree without changing the
 * operation ids or contracts.
 */
export const SURE_EVALUATION_BACKEND: SemanticBackendBundle = {
	schema: "sure.semantic.backend.bundle.v1",
	bundle_id: "sure-evaluation-backend",
	version: "legacy-v1",
	description: "SURE evaluation source resolver, runtime bridge, and report runner.",
	canonical_root: "sure-infer",
	legacy_root: "skills/sure_infer",
	integrity_root: "scripts",
	operations: [
		{
			operation_id: "sure.eval.resolve_prediction_source",
			description: "Resolve and fingerprint the immutable prediction source.",
			entrypoint: "scripts/resolve_prediction_source.py",
			consumer_skill_ids: ["sure_eval"],
			kind: "resolve",
			timeout_ms: 120_000,
			deterministic: true,
		},
		{
			operation_id: "sure.eval.evaluation_runtime",
			description: "Resolve and verify the pinned evaluation runtime.",
			entrypoint: "scripts/evaluation_runtime.py",
			consumer_skill_ids: ["sure_eval"],
			kind: "resolve",
			timeout_ms: 120_000,
			deterministic: true,
		},
		{
			operation_id: "sure.infer.resolve_deployment_binding",
			description: "Resolve the shared deployment-binding implementation for approval and inference consumers.",
			entrypoint: "scripts/deployment_binding.py",
			consumer_skill_ids: ["sure_infer", "sure_approve"],
			kind: "resolve",
			timeout_ms: 120_000,
			deterministic: true,
		},
		{
			operation_id: "sure.eval.run",
			description: "Execute the pinned evaluator and publish an immutable batch.",
			entrypoint: "scripts/run_eval.py",
			consumer_skill_ids: ["sure_eval"],
			kind: "execute",
			timeout_ms: 3_600_000,
			deterministic: false,
			artifact_mode: "producing",
		},
		{
			operation_id: "sure.infer.validate_execution_result",
			description: "Validate the terminal inference execution result and its bound evidence.",
			entrypoint: "scripts/check_execution_result.py",
			consumer_skill_ids: ["sure_infer"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.eval.validate_assessment",
			description: "Validate the evaluation assessment artifact.",
			entrypoint: "scripts/check_assessment.py",
			consumer_skill_ids: ["sure_eval"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.eval.validate_run_report",
			description: "Validate a terminal inference or evaluation run report under its declared profile.",
			entrypoint: "scripts/check_run_report.py",
			consumer_skill_ids: ["sure_eval", "sure_infer"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
	],
};

export const SURE_EVALUATION_VALIDATOR_BACKEND: SemanticBackendBundle = {
	schema: "sure.semantic.backend.bundle.v1",
	bundle_id: "sure-evaluation-validators",
	version: "legacy-v1",
	description: "Evaluation report validator implementation.",
	canonical_root: "sure-eval",
	legacy_root: "skills/sure_eval",
	integrity_root: "scripts",
	operations: [
		{
			operation_id: "sure.eval.validate_eval_report",
			description: "Validate the complete evaluation evidence graph before publication or finish.",
			entrypoint: "scripts/check_eval_run_report.py",
			consumer_skill_ids: ["sure_eval"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
	],
};

export const SURE_MEMORY_VALIDATOR_BACKEND: SemanticBackendBundle = {
	schema: "sure.semantic.backend.bundle.v1",
	bundle_id: "sure-memory-validator",
	version: "legacy-v1",
	description: "Shared SURE memory extraction validator backed by the existing memory rules.",
	canonical_root: "sure/canonical/shared/memory-backend",
	canonical_root_kind: "repository",
	legacy_root: "sure/skills/sure_infer",
	legacy_root_kind: "repository",
	integrity_root: "scripts",
	operations: [
		{
			operation_id: "sure.memory.validate_extraction",
			description: "Validate an extraction declaration and its provenance-bound memory candidates.",
			entrypoint: "scripts/check_memory_extraction.py",
			consumer_skill_ids: ["sure_feed", "sure_onboard", "sure_infer", "sure_eval", "sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
	],
};

export const SURE_FEED_VALIDATOR_BACKEND: SemanticBackendBundle = {
	schema: "sure.semantic.backend.bundle.v1",
	bundle_id: "sure-feed-validators",
	version: "legacy-v1",
	description: "SURE feed task, model-input, and ranking validators.",
	canonical_root: "sure/canonical/shared/feed-validator",
	canonical_root_kind: "repository",
	legacy_root: "sure/skills/sure_feed",
	legacy_root_kind: "repository",
	integrity_root: "scripts",
	operations: [
		{
			operation_id: "sure.feed.validate_match_task",
			description: "Validate feed candidate task matching and provenance.",
			entrypoint: "scripts/check_match_task.py",
			consumer_skill_ids: ["sure_feed"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.feed.validate_model_input",
			description: "Validate synthesized feed model-input envelopes.",
			entrypoint: "scripts/check_model_input.py",
			consumer_skill_ids: ["sure_feed"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.feed.validate_rank_select",
			description: "Validate the selected feed candidate set and score domain.",
			entrypoint: "scripts/check_rank_select.py",
			consumer_skill_ids: ["sure_feed"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
	],
};

export const SURE_ONBOARD_VALIDATOR_BACKEND: SemanticBackendBundle = {
	schema: "sure.semantic.backend.bundle.v1",
	bundle_id: "sure-onboard-validators",
	version: "legacy-v1",
	description: "SURE onboard artifact and immutable site-policy validators.",
	canonical_root: "sure/canonical/shared/onboard-validator",
	canonical_root_kind: "repository",
	legacy_root: "sure/skills/sure_onboard",
	legacy_root_kind: "repository",
	integrity_root: "scripts",
	operations: [
		{
			operation_id: "sure.onboard.validate_model_input",
			description: "Validate normalized onboarding input against the immutable site policy.",
			entrypoint: "scripts/check_model_input.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
			requires_policy_snapshot: true,
		},
		{
			operation_id: "sure.onboard.validate_build_plan",
			description: "Validate an executable onboarding build plan.",
			entrypoint: "scripts/check_build_plan.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.onboard.validate_spec",
			description: "Validate the seven onboarding specification checks.",
			entrypoint: "scripts/check_spec.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.onboard.validate_fixture",
			description: "Validate the staged onboarding fixture and annotation evidence.",
			entrypoint: "scripts/check_fixture.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.onboard.validate_weights",
			description: "Validate resolved onboarding weight paths and provenance.",
			entrypoint: "scripts/check_weights.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.onboard.validate_artifact_manifest",
			description: "Validate the onboarded model artifact manifest and referenced files.",
			entrypoint: "scripts/check_artifact_manifest.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.onboard.validate_package_gate",
			description: "Validate package readiness against the immutable site policy and prior execution evidence.",
			entrypoint: "scripts/check_package_gate.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
			requires_policy_snapshot: true,
		},
		{
			operation_id: "sure.onboard.validate_runtime_inventory",
			description: "Validate the sealed runtime inventory against immutable site and runtime bindings.",
			entrypoint: "scripts/check_runtime_inventory.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
			requires_policy_snapshot: true,
		},
		{
			operation_id: "sure.onboard.validate_verdict",
			description: "Validate the terminal onboarding verdict against its preceding evidence.",
			entrypoint: "scripts/check_verdict.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.onboard.validate_finalized_bundle",
			description: "Validate the terminal model bundle, complete artifact hashes, and frozen runtime policy.",
			entrypoint: "scripts/check_finalized_bundle.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
			requires_policy_snapshot: true,
		},
	],
};

/**
 * Compatibility execution adapters for onboarding gates whose historical
 * scripts both perform the host/model action and emit the gate result. They
 * are deliberately separate from the validator bundle: a receipt proves that
 * the locked action ran, while Core still decides whether the gate may advance.
 */
export const SURE_ONBOARD_EXECUTION_BACKEND: SemanticBackendBundle = {
	schema: "sure.semantic.backend.bundle.v1",
	bundle_id: "sure-onboard-execution",
	version: "legacy-v1",
	description: "Onboarding environment, model-runtime, and container execution adapters.",
	canonical_root: "sure/canonical/shared/onboard-execution",
	canonical_root_kind: "repository",
	legacy_root: "sure/skills/sure_onboard",
	legacy_root_kind: "repository",
	integrity_root: "scripts",
	operations: [
		{
			operation_id: "sure.onboard.execute_build_env",
			description: "Build and probe the model execution environment.",
			entrypoint: "scripts/check_env.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "execute",
			timeout_ms: 3_600_000,
			deterministic: false,
			artifact_mode: "preexisting",
			requires_policy_snapshot: true,
		},
		{
			operation_id: "sure.onboard.execute_env_compat",
			description: "Probe device and environment compatibility for the staged model.",
			entrypoint: "scripts/check_env_compat.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "execute",
			timeout_ms: 3_600_000,
			deterministic: false,
			artifact_mode: "preexisting",
		},
		{
			operation_id: "sure.onboard.execute_import",
			description: "Execute the model import validation command in its declared runtime.",
			entrypoint: "scripts/run_validate.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "execute",
			timeout_ms: 3_600_000,
			deterministic: false,
			artifact_mode: "mutating",
		},
		{
			operation_id: "sure.onboard.execute_load",
			description: "Execute the model load validation command in its declared runtime.",
			entrypoint: "scripts/run_validate.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "execute",
			timeout_ms: 3_600_000,
			deterministic: false,
			artifact_mode: "mutating",
		},
		{
			operation_id: "sure.onboard.execute_infer",
			description: "Execute the model inference smoke validation in its declared runtime.",
			entrypoint: "scripts/run_validate.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "execute",
			timeout_ms: 3_600_000,
			deterministic: false,
			artifact_mode: "mutating",
		},
		{
			operation_id: "sure.onboard.execute_contract",
			description: "Execute and check the model output contract in its declared runtime.",
			entrypoint: "scripts/run_validate.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "execute",
			timeout_ms: 3_600_000,
			deterministic: false,
			artifact_mode: "mutating",
		},
		{
			operation_id: "sure.onboard.execute_package_container",
			description: "Build/probe the digest-pinned onboarding container package.",
			entrypoint: "scripts/check_container_package.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "execute",
			timeout_ms: 3_600_000,
			deterministic: false,
			artifact_mode: "preexisting",
			requires_policy_snapshot: true,
		},
	],
};

/**
 * Shared deterministic TRANS artifact validator.  The historical checker
 * covers several artifact kinds, so each kind receives its own stable
 * operation id while the implementation tree remains one locked backend.
 */
export const SURE_TRANS_VALIDATOR_BACKEND: SemanticBackendBundle = {
	schema: "sure.semantic.backend.bundle.v1",
	bundle_id: "sure-trans-validators",
	version: "legacy-v1",
	description: "SURE transformation artifact and delivery validators.",
	canonical_root: "sure/canonical/shared/trans-validator",
	canonical_root_kind: "repository",
	legacy_root: "sure/skills/sure_trans",
	legacy_root_kind: "repository",
	integrity_root: "scripts",
	operations: [
		{
			operation_id: "sure.trans.validate_input",
			description: "Validate resolved transformation input and path policy.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_dependencies",
			description: "Validate the transformation dependency report.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_framework",
			description: "Validate detected framework and model framework evidence.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_fixture",
			description: "Validate the bounded transformation smoke fixture and provenance.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_source_image",
			description: "Validate source image materialization evidence.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_execution_compat",
			description: "Validate source-runtime execution compatibility evidence.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_original_inference",
			description: "Validate the recorded original inference baseline.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_import",
			description: "Validate recorded adapter import execution evidence.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_load",
			description: "Validate recorded adapter persistent-load evidence.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_infer",
			description: "Validate recorded adapter inference evidence.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_contract",
			description: "Validate recorded adapter output-contract evidence.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_mcp",
			description: "Validate recorded MCP protocol evidence.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_equivalence",
			description: "Validate recorded original-versus-adapter equivalence evidence.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_model_payload",
			description: "Validate the staged model payload manifest and file hashes.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_adapter",
			description: "Validate the generated transformation adapter manifest.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_adapter_image",
			description: "Validate the materialized adapter runtime image evidence.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_package_container",
			description: "Validate the digest-pinned transformation package publication.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
			requires_policy_snapshot: true,
		},
		{
			operation_id: "sure.trans.validate_runtime_inventory",
			description: "Validate the sealed transformation runtime inventory and mount policy.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
			requires_policy_snapshot: true,
		},
		{
			operation_id: "sure.trans.validate_verdict",
			description: "Validate the transformation terminal verdict readiness evidence.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.trans.validate_finalized_bundle",
			description: "Validate the finalized transformation bundle and required artifact hashes.",
			entrypoint: "scripts/check_artifact.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
			requires_policy_snapshot: true,
		},
	],
};

/**
 * TRANS execution adapters. The entrypoints remain the proven Python runners;
 * registering them here moves path selection and output binding into SURE Core
 * without changing their legacy workflow semantics. Workflow gates bind these
 * operations only together with an independent validator operation, so a
 * successful process cannot by itself manufacture a semantic PASS.
 */
export const SURE_TRANS_EXECUTION_BACKEND: SemanticBackendBundle = {
	schema: "sure.semantic.backend.bundle.v1",
	bundle_id: "sure-trans-execution",
	version: "legacy-v1",
	description: "Docker, VC, local Python, and uv execution adapters for SURE transformation.",
	canonical_root: "sure/canonical/skills/sure-trans",
	canonical_root_kind: "repository",
	legacy_root: "sure/skills/sure_trans",
	legacy_root_kind: "repository",
	integrity_root: "scripts",
	operations: [
		{
			operation_id: "sure.trans.execute_source_image",
			description: "Materialize and verify the source Docker or locked Python runtime.",
			entrypoint: "scripts/run_docker_build.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "execute",
			timeout_ms: 7_200_000,
			deterministic: false,
			artifact_mode: "producing",
			output_contract: transOutputContract("producing", "source-image-result", "source_image_result.json"),
		},
		{
			operation_id: "sure.trans.execute_env_compat",
			description: "Probe source-runtime compatibility on local Docker, VC, or Python.",
			entrypoint: "scripts/run_execution_compat.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "execute",
			timeout_ms: 7_200_000,
			deterministic: false,
			artifact_mode: "producing",
			output_contract: transOutputContract("producing", "execution-compat", "execution_compat.json"),
		},
		{
			operation_id: "sure.trans.execute_original_inference",
			description: "Run and record the original model inference baseline.",
			entrypoint: "scripts/run_trans_validate.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "execute",
			timeout_ms: 7_200_000,
			deterministic: false,
			artifact_mode: "mutating",
			output_contract: transOutputContract(
				"mutating",
				"original-inference-result",
				"original_inference_result.json",
			),
		},
		{
			operation_id: "sure.trans.execute_adapter_image",
			description: "Materialize the generated Python adapter runtime evidence.",
			entrypoint: "scripts/materialize_adapter_runtime.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "execute",
			timeout_ms: 3_600_000,
			deterministic: false,
			artifact_mode: "producing",
			input_contract: SURE_TRANS_PYTHON_ADAPTER_INPUT_CONTRACT,
			output_contract: transOutputContract("producing", "adapter-image-result", "adapter_image_result.json"),
		},
		{
			operation_id: "sure.trans.execute_import",
			description: "Execute the generated adapter import validation.",
			entrypoint: "scripts/run_trans_validate.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "execute",
			timeout_ms: 7_200_000,
			deterministic: false,
			artifact_mode: "mutating",
			output_contract: transOutputContract("mutating", "import-result", "import_result.json"),
		},
		{
			operation_id: "sure.trans.execute_load",
			description: "Execute the generated adapter persistent-load validation.",
			entrypoint: "scripts/run_trans_validate.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "execute",
			timeout_ms: 7_200_000,
			deterministic: false,
			artifact_mode: "mutating",
			output_contract: transOutputContract("mutating", "load-result", "load_result.json"),
		},
		{
			operation_id: "sure.trans.execute_infer",
			description: "Execute the generated adapter inference validation.",
			entrypoint: "scripts/run_trans_validate.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "execute",
			timeout_ms: 7_200_000,
			deterministic: false,
			artifact_mode: "mutating",
			output_contract: transOutputContract("mutating", "infer-result", "infer_result.json"),
		},
		{
			operation_id: "sure.trans.execute_contract",
			description: "Execute the generated adapter output-contract validation.",
			entrypoint: "scripts/run_trans_validate.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "execute",
			timeout_ms: 7_200_000,
			deterministic: false,
			artifact_mode: "mutating",
			output_contract: transOutputContract("mutating", "contract-result", "contract_result.json"),
		},
		{
			operation_id: "sure.trans.execute_mcp",
			description: "Execute and record the generated MCP protocol validation.",
			entrypoint: "scripts/run_trans_validate.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "execute",
			timeout_ms: 7_200_000,
			deterministic: false,
			artifact_mode: "mutating",
			output_contract: transOutputContract("mutating", "mcp-result", "mcp_result.json"),
		},
		{
			operation_id: "sure.trans.execute_equivalence",
			description: "Execute the original-versus-adapter equivalence validation.",
			entrypoint: "scripts/run_trans_validate.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "execute",
			timeout_ms: 7_200_000,
			deterministic: false,
			artifact_mode: "mutating",
			output_contract: transOutputContract("mutating", "equivalence-result", "equivalence_result.json"),
		},
		{
			operation_id: "sure.trans.execute_package_container",
			description: "Package the validated Python runtime through the uv/site-policy adapter.",
			entrypoint: "scripts/package_python_runtime.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "execute",
			timeout_ms: 7_200_000,
			deterministic: false,
			artifact_mode: "producing",
			input_contract: SURE_TRANS_PYTHON_PACKAGE_INPUT_CONTRACT,
			requires_policy_snapshot: true,
			capability_requirements: [
				{ capability_id: "sure.execution.uv", capability_class: "execution_capability", required: true },
			],
			output_contract: transOutputContract("producing", "package-container-result", "docker_registry_result.json"),
		},
		{
			operation_id: "sure.trans.execute_adapter_image.python",
			description: "Materialize the generated Python adapter runtime evidence for the Python source profile.",
			entrypoint: "scripts/materialize_adapter_runtime.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "execute",
			timeout_ms: 3_600_000,
			deterministic: false,
			artifact_mode: "producing",
			input_contract: SURE_TRANS_PYTHON_ADAPTER_INPUT_CONTRACT,
			output_contract: transOutputContract("producing", "adapter-image-result", "adapter_image_result.json"),
		},
		{
			operation_id: "sure.trans.execute_package_container.python",
			description:
				"Package the validated Python runtime through the uv/site-policy adapter for package_profile=none.",
			entrypoint: "scripts/package_python_runtime.py",
			consumer_skill_ids: ["sure_trans"],
			kind: "execute",
			timeout_ms: 7_200_000,
			deterministic: false,
			artifact_mode: "producing",
			input_contract: SURE_TRANS_PYTHON_PACKAGE_INPUT_CONTRACT,
			requires_policy_snapshot: true,
			capability_requirements: [
				{ capability_id: "sure.execution.uv", capability_class: "execution_capability", required: true },
			],
			output_contract: transOutputContract("producing", "package-container-result", "docker_registry_result.json"),
		},
	],
};

/**
 * Read-only approval evidence checks. The historical approval commands also
 * create artifacts and remain Pi lifecycle entrypoints; these operations are
 * deliberately separate and only re-compute/verify an already written
 * artifact for portable/Core validation.
 */
export const SURE_APPROVE_VALIDATOR_BACKEND: SemanticBackendBundle = {
	schema: "sure.semantic.backend.bundle.v1",
	bundle_id: "sure-approve-validators",
	version: "legacy-v1",
	description: "Read-only approval audit and human-decision evidence validators.",
	canonical_root: "sure-approve",
	legacy_root: "skills/sure_approve",
	integrity_root: "scripts",
	operations: [
		{
			operation_id: "sure.approve.validate_input_resolved",
			description: "Recompute and validate the resolved approval input evidence.",
			entrypoint: "scripts/check_approval_artifact.py",
			consumer_skill_ids: ["sure_approve"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
			requires_policy_snapshot: true,
		},
		{
			operation_id: "sure.approve.validate_producer_contract",
			description: "Recompute and validate producer contract classification evidence.",
			entrypoint: "scripts/check_approval_artifact.py",
			consumer_skill_ids: ["sure_approve"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.approve.validate_integrity",
			description: "Recompute and validate the immutable bundle integrity audit.",
			entrypoint: "scripts/check_approval_artifact.py",
			consumer_skill_ids: ["sure_approve"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
			requires_policy_snapshot: true,
		},
		{
			operation_id: "sure.approve.validate_repair_plan",
			description: "Recompute and validate the bounded repair plan without applying repairs.",
			entrypoint: "scripts/check_approval_artifact.py",
			consumer_skill_ids: ["sure_approve"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.approve.validate_manifest",
			description: "Validate the sealed candidate manifest and current candidate digest.",
			entrypoint: "scripts/check_approval_artifact.py",
			consumer_skill_ids: ["sure_approve"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
		{
			operation_id: "sure.approve.validate_review_packet",
			description: "Validate review packet links, policy identity, and candidate/source digests.",
			entrypoint: "scripts/check_approval_artifact.py",
			consumer_skill_ids: ["sure_approve"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
			requires_policy_snapshot: true,
		},
		{
			operation_id: "sure.approve.validate_decision",
			description: "Validate an explicit human approval or rejection against the review packet.",
			entrypoint: "scripts/check_approval_artifact.py",
			consumer_skill_ids: ["sure_approve"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
			requires_policy_snapshot: true,
		},
	],
};

export const CANONICAL_SEMANTIC_BACKENDS: readonly SemanticBackendBundle[] = [
	SURE_EVALUATION_BACKEND,
	SURE_EVALUATION_VALIDATOR_BACKEND,
	SURE_MEMORY_VALIDATOR_BACKEND,
	SURE_FEED_VALIDATOR_BACKEND,
	SURE_ONBOARD_VALIDATOR_BACKEND,
	SURE_ONBOARD_EXECUTION_BACKEND,
	SURE_TRANS_VALIDATOR_BACKEND,
	SURE_TRANS_EXECUTION_BACKEND,
	SURE_APPROVE_VALIDATOR_BACKEND,
];
