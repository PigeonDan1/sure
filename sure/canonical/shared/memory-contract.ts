/**
 * Host-neutral memory semantics shared by Pi and portable skill projections.
 *
 * This is intentionally data-only: it names the logical identity, roots,
 * advisory lifecycle, and host enforcement profiles without importing a hook
 * runtime or prescribing a particular Python/Node implementation.
 */
export const CANONICAL_MEMORY_CONTRACT = {
	schema: "sure.memory.contract.v1",
	contract_version: 1,
	advisory: true,
	identity: {
		uri_prefix: "memory://",
		entry_id_pattern: "<skill>/<slug>",
		kinds: ["bad_case", "fact"],
		fact_namespace: "_shared",
		bad_case_namespace: "skill_specific",
	},
	roots: [
		{
			id: "memory_root",
			role: "state",
			default_relative: "sure/memory",
			binding: "explicit_or_default",
			mutable: true,
		},
		{
			id: "canonical_root",
			role: "canonical_references",
			default_relative: "sure/canonical",
			binding: "explicit_or_default",
			mutable: false,
		},
		{
			id: "legacy_skills_root",
			role: "compatibility_reference_alias",
			default_relative: "sure/skills",
			binding: "explicit_or_default",
			mutable: true,
		},
	],
	operations: [
		"read_index",
		"append_usage",
		"write_run_digest",
		"check_extraction",
		"publish_reference",
		"promote_reference",
		"resolve_reference",
	],
	lifecycle: {
		checkpoint_field: "memory",
		failure_effect: "diagnostic_only",
		business_verdict_authority: "workflow_and_validators",
		approve_consumes_memory: false,
		confirmed_knowledge_must_be_preserved: true,
	},
	host_profiles: {
		portable: {
			enforcement: "cooperative",
			control_plane: "surectl_or_host_adapter",
			trusted_execution: false,
		},
		pi: {
			enforcement: "mandatory_lifecycle_hooks",
			control_plane: "pi_hook_adapter",
			trusted_execution: "host_policy_dependent",
		},
	},
} as const;

export type CanonicalMemoryContract = typeof CANONICAL_MEMORY_CONTRACT;
