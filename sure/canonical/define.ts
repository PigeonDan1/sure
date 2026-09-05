import type { CanonicalSkillDefinition } from "./types.ts";

const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const COMMAND_PATTERN = /^[a-z0-9_][a-z0-9_-]*$/;

function assertRelativeResource(value: string, label: string): void {
	if (value.startsWith("/") || value.includes("\\") || value.split("/").includes("..")) {
		throw new Error(`${label} must be a relative POSIX resource path: ${value}`);
	}
}

function assertWorkflow(definition: CanonicalSkillDefinition): void {
	const workflow = definition.workflow;
	const branchIds = new Set<string>();
	for (const branch of workflow.branches) {
		if (branchIds.has(branch.id)) throw new Error(`Duplicate workflow branch: ${branch.id}`);
		branchIds.add(branch.id);
		const unitIds = new Set<string>();
		for (const unit of branch.units) {
			if (unitIds.has(unit.id)) throw new Error(`Duplicate workflow unit: ${workflow.workflow_id}/${unit.id}`);
			unitIds.add(unit.id);
			if (unit.kind === "gate" && unit.gate === undefined) {
				throw new Error(`Gate unit ${workflow.workflow_id}/${unit.id} has no validator definition.`);
			}
			for (const script of [
				...(unit.helper_scripts ?? []),
				...(unit.owned_scripts ?? []),
				unit.gate?.script_id,
			].filter((value): value is string => value !== undefined)) {
				assertRelativeResource(script, `workflow script ${workflow.workflow_id}/${unit.id}`);
			}
		}
		if (!unitIds.has(branch.initial_unit_id) || !unitIds.has(branch.terminal_unit_id)) {
			throw new Error(`Workflow branch ${workflow.workflow_id}/${branch.id} points at an unknown unit.`);
		}
	}
	if (!branchIds.has(workflow.default_branch_id))
		throw new Error(`Unknown default branch: ${workflow.default_branch_id}`);
}

function assertContracts(definition: CanonicalSkillDefinition): void {
	for (const contracts of [definition.unit_outputs, definition.internal_evidence, definition.published_artifacts]) {
		const seen = new Set<string>();
		for (const contract of contracts) {
			if (seen.has(contract.type)) throw new Error(`Duplicate artifact contract type: ${contract.type}`);
			seen.add(contract.type);
			if (contract.path !== undefined) assertRelativeResource(contract.path, `artifact contract ${contract.type}`);
		}
	}
	for (const contract of [
		...definition.unit_outputs,
		...definition.internal_evidence,
		...definition.published_artifacts,
	]) {
		if (contract.path !== undefined) assertRelativeResource(contract.path, `artifact contract ${contract.type}`);
	}
}

function assertCapabilities(definition: CanonicalSkillDefinition): void {
	const ids = new Set<string>();
	for (const capability of definition.capabilities) {
		if (!/^sure\.[a-z0-9][a-z0-9.-]*$/.test(capability.capability_id)) {
			throw new Error(`Invalid capability id: ${capability.capability_id}`);
		}
		if (ids.has(capability.capability_id)) throw new Error(`Duplicate capability id: ${capability.capability_id}`);
		ids.add(capability.capability_id);
	}
}

/** Validate canonical declarations once at module load, before generation or execution. */
export function defineCanonicalSkill(definition: CanonicalSkillDefinition): CanonicalSkillDefinition {
	if (!SLUG_PATTERN.test(definition.distribution_slug)) {
		throw new Error(`Invalid distribution slug: ${definition.distribution_slug}`);
	}
	if (!COMMAND_PATTERN.test(definition.command_id) || definition.command_id.includes("/")) {
		throw new Error(`Invalid host-neutral command id: ${definition.command_id}`);
	}
	if (!definition.pi.command.startsWith("/"))
		throw new Error(`Pi command must start with '/': ${definition.pi.command}`);
	if (definition.pi.name !== definition.workflow.workflow_id) {
		throw new Error(
			`Pi name and legacy workflow id differ: ${definition.pi.name} / ${definition.workflow.workflow_id}`,
		);
	}
	assertWorkflow(definition);
	assertContracts(definition);
	assertCapabilities(definition);
	for (const directory of definition.resources.directories) assertRelativeResource(directory, "resource directory");
	return definition;
}
