import { type ValidatorDescriptor, ValidatorRegistry } from "../../../packages/sure-core/src/validation/index.ts";
import { CANONICAL_SKILLS } from "../skills/index.ts";
import type { CanonicalSkillDefinition } from "../types.ts";

function idPart(value: string): string {
	return (
		value
			.toLowerCase()
			.replaceAll(/[^a-z0-9]+/g, "_")
			.replace(/^_+|_+$/g, "") || "value"
	);
}

function unitValidatorId(skill: CanonicalSkillDefinition, branchId: string, unitId: string): string {
	return `sure.${idPart(skill.skill_id)}.${idPart(branchId)}.${idPart(unitId)}`;
}

function semanticValidatorId(skill: CanonicalSkillDefinition, validatorId: string): string {
	return `sure.${idPart(skill.skill_id)}.semantic.${idPart(validatorId)}`;
}

function descriptorForUnit(
	skill: CanonicalSkillDefinition,
	branchId: string,
	unitId: string,
	validatorId: string,
	scriptId: string | undefined,
	scriptArgs: readonly string[] | undefined,
	legacyId: string,
	descriptorIdUnitId = unitId,
): ValidatorDescriptor {
	return {
		id: unitValidatorId(skill, branchId, descriptorIdUnitId),
		version: "legacy-v1",
		authority: scriptId === undefined && validatorId === "structural" ? "structural" : "semantic",
		operation: "validate",
		deterministic: true,
		skill_id: skill.skill_id,
		branch_id: branchId,
		unit_id: unitId,
		legacy_id: legacyId,
		...(scriptArgs === undefined ? {} : { script_args: scriptArgs }),
		...(scriptId === undefined ? {} : { resource_path: `${skill.distribution_slug}/scripts/${scriptId}` }),
		input_contract: `workflow:${skill.workflow.workflow_id}/${branchId}/${unitId}`,
		output_contract: "sure.validation.signal.v1",
	};
}

function descriptorsForSkill(skill: CanonicalSkillDefinition): ValidatorDescriptor[] {
	const descriptors: ValidatorDescriptor[] = [];
	for (const branch of skill.workflow.branches) {
		for (const unit of branch.units) {
			if (unit.gate === undefined) continue;
			descriptors.push(
				descriptorForUnit(
					skill,
					branch.id,
					unit.id,
					unit.gate.validator_id,
					unit.gate.script_id,
					unit.gate.script_args,
					unit.gate.validator_id,
				),
			);
			for (const auxiliary of unit.gate.auxiliary_validator_ids ?? []) {
				descriptors.push(
					descriptorForUnit(
						skill,
						branch.id,
						unit.id,
						auxiliary,
						undefined,
						undefined,
						auxiliary,
						`${unit.id}.aux.${idPart(auxiliary)}`,
					),
				);
			}
		}
	}
	for (const validator of skill.semantic_validators) {
		descriptors.push({
			id: semanticValidatorId(skill, validator.id),
			version: "legacy-v1",
			authority: "semantic",
			operation: validator.operation,
			deterministic: true,
			skill_id: skill.skill_id,
			legacy_id: validator.id,
			...(validator.script === undefined
				? {}
				: { resource_path: `${skill.distribution_slug}/scripts/${validator.script}` }),
			output_contract: "sure.validation.signal.v1",
		});
	}
	return descriptors;
}

/** Build the authoritative registry from canonical workflows, never from host adapters. */
export function canonicalValidatorDescriptors(
	skills: readonly CanonicalSkillDefinition[] = CANONICAL_SKILLS,
): readonly ValidatorDescriptor[] {
	const descriptors: ValidatorDescriptor[] = [
		{
			id: "sure.structural.artifact",
			version: "1.0.0",
			authority: "structural",
			operation: "validate",
			deterministic: true,
			output_contract: "sure.validation.signal.v1",
		},
	];
	for (const skill of skills) descriptors.push(...descriptorsForSkill(skill));
	return descriptors;
}

export function canonicalValidatorRegistry(
	skills: readonly CanonicalSkillDefinition[] = CANONICAL_SKILLS,
): ValidatorRegistry {
	return new ValidatorRegistry(canonicalValidatorDescriptors(skills));
}

export function validatorIdsForSkill(skill: CanonicalSkillDefinition): readonly string[] {
	return canonicalValidatorDescriptors([skill])
		.filter((descriptor) => descriptor.skill_id === skill.skill_id)
		.map((descriptor) => descriptor.id);
}
