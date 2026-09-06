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
			operation_id: "sure.eval.run",
			description: "Execute the pinned evaluator and publish an immutable batch.",
			entrypoint: "scripts/run_eval.py",
			consumer_skill_ids: ["sure_eval"],
			kind: "execute",
			timeout_ms: 3_600_000,
			deterministic: false,
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
			operation_id: "sure.onboard.validate_verdict",
			description: "Validate the terminal onboarding verdict against its preceding evidence.",
			entrypoint: "scripts/check_verdict.py",
			consumer_skill_ids: ["sure_onboard"],
			kind: "validate",
			timeout_ms: 300_000,
			deterministic: true,
		},
	],
};

export const CANONICAL_SEMANTIC_BACKENDS: readonly SemanticBackendBundle[] = [
	SURE_EVALUATION_BACKEND,
	SURE_EVALUATION_VALIDATOR_BACKEND,
	SURE_MEMORY_VALIDATOR_BACKEND,
	SURE_FEED_VALIDATOR_BACKEND,
	SURE_ONBOARD_VALIDATOR_BACKEND,
];
