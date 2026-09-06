export { defineCanonicalSkill } from "./define.ts";
export type { CanonicalMemoryContract } from "./shared/memory-contract.ts";
export { CANONICAL_MEMORY_CONTRACT } from "./shared/memory-contract.ts";
export { CANONICAL_SKILLS, CANONICAL_SKILLS_BY_ID } from "./skills/index.ts";
export type {
	ArtifactContract,
	ArtifactScope,
	CanonicalInstructions,
	CanonicalResources,
	CanonicalSkillDefinition,
	CapabilityClass,
	CapabilityRequirement,
	PiHookDeclaration,
	PiManifestProjection,
	PublicationMode,
	SemanticValidatorRef,
} from "./types.ts";
export {
	canonicalValidatorDescriptors,
	canonicalValidatorRegistry,
	validatorIdsForSkill,
} from "./validators/index.ts";
