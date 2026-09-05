import type { WorkflowDefinition } from "../../packages/sure-core/src/index.ts";

export type ArtifactScope = "run-local" | "workspace-global" | "site-published" | "append-only";
export type PublicationMode = "run-local" | "workspace-global" | "site-published" | "append-only";
export type CapabilityClass = "agent_capability" | "execution_capability";

export interface ArtifactContract {
	type: string;
	path?: string;
	required: boolean;
	description: string;
	scope: ArtifactScope;
	publication_mode: PublicationMode;
	owner: "agent" | "harness" | "validator" | "executor" | "site";
}

export interface CapabilityRequirement {
	capability_id: string;
	capability_class: CapabilityClass;
	required: boolean;
	description: string;
}

export interface SemanticValidatorRef {
	id: string;
	operation: "validate" | "execute" | "publish" | "memory";
	script?: string;
	description: string;
}

export interface PiHookDeclaration {
	module: string;
	handler: string;
}

export interface PiManifestProjection {
	name: string;
	command: string;
	description: string;
	prompt: string;
	hooks: Readonly<Record<string, readonly PiHookDeclaration[]>>;
	ui: {
		primaryCounters: readonly string[];
		artifactTypes: readonly string[];
		defaultExpandedSections: readonly string[];
	};
}

export interface CanonicalInstructions {
	common_path: string;
	portable_path: string;
	pi_path: string;
}

export interface CanonicalResources {
	directories: readonly string[];
	/** Compatibility source used while backend files are moved into canonical/. */
	legacy_root?: string;
}

export interface CanonicalSkillDefinition {
	schema: "sure.canonical.skill.v1";
	skill_id: string;
	command_id: string;
	distribution_slug: string;
	display_name: string;
	description: string;
	workflow: WorkflowDefinition;
	unit_outputs: readonly ArtifactContract[];
	internal_evidence: readonly ArtifactContract[];
	published_artifacts: readonly ArtifactContract[];
	capabilities: readonly CapabilityRequirement[];
	semantic_validators: readonly SemanticValidatorRef[];
	instructions: CanonicalInstructions;
	resources: CanonicalResources;
	pi: PiManifestProjection;
}
