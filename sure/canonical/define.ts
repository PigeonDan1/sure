import { validateExecutionInputContract } from "../../packages/sure-core/src/execution/input-contract.ts";
import type { CanonicalSkillDefinition } from "./types.ts";

const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const COMMAND_PATTERN = /^[a-z0-9_][a-z0-9_-]*$/;
const OPERATION_PATTERN = /^sure\.[a-z0-9][a-z0-9._:-]*$/;

function assertRelativeResource(value: string, label: string): void {
	if (value.startsWith("/") || value.includes("\\") || value.split("/").includes("..")) {
		throw new Error(`${label} must be a relative POSIX resource path: ${value}`);
	}
}

function dispatchCasesOverlap(
	left: Readonly<Record<string, string>>,
	right: Readonly<Record<string, string>>,
): boolean {
	const fields = new Set([...Object.keys(left), ...Object.keys(right)]);
	for (const field of fields) {
		if (left[field] !== undefined && right[field] !== undefined && left[field] !== right[field]) return false;
	}
	return true;
}

function dispatchMatchEquals(left: Readonly<Record<string, string>>, right: Readonly<Record<string, string>>): boolean {
	const leftKeys = Object.keys(left);
	const rightKeys = Object.keys(right);
	return leftKeys.length === rightKeys.length && leftKeys.every((key) => left[key] === right[key]);
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
			if (unit.gate?.backend_operation_id !== undefined && !OPERATION_PATTERN.test(unit.gate.backend_operation_id)) {
				throw new Error(
					`Invalid backend operation id for ${workflow.workflow_id}/${unit.id}: ${unit.gate.backend_operation_id}`,
				);
			}
			if (
				unit.gate?.execution_operation_id !== undefined &&
				!OPERATION_PATTERN.test(unit.gate.execution_operation_id)
			) {
				throw new Error(
					`Invalid execution operation id for ${workflow.workflow_id}/${unit.id}: ${unit.gate.execution_operation_id}`,
				);
			}
			if (unit.gate?.execution_request_operation !== undefined && unit.gate.execution_operation_id === undefined) {
				throw new Error(
					`Execution request operation for ${workflow.workflow_id}/${unit.id} requires execution_operation_id.`,
				);
			}
			if (unit.gate?.execution_input_produces !== undefined) {
				if (unit.gate.execution_operation_id === undefined) {
					throw new Error(
						`Execution input artifact for ${workflow.workflow_id}/${unit.id} requires execution_operation_id.`,
					);
				}
				assertRelativeResource(
					unit.gate.execution_input_produces,
					`execution input artifact ${workflow.workflow_id}/${unit.id}`,
				);
			}
			if (unit.gate?.execution_dispatch !== undefined) {
				if (unit.gate.execution_operation_id !== undefined) {
					throw new Error(
						`Execution dispatch for ${workflow.workflow_id}/${unit.id} cannot coexist with execution_operation_id.`,
					);
				}
				const caseIds = new Set<string>();
				for (const [index, entry] of unit.gate.execution_dispatch.entries()) {
					if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(entry.case_id)) {
						throw new Error(
							`Invalid execution dispatch case id for ${workflow.workflow_id}/${unit.id}/${index}.`,
						);
					}
					if (caseIds.has(entry.case_id)) {
						throw new Error(
							`Duplicate execution dispatch case for ${workflow.workflow_id}/${unit.id}: ${entry.case_id}`,
						);
					}
					caseIds.add(entry.case_id);
					if (!OPERATION_PATTERN.test(entry.operation_id)) {
						throw new Error(
							`Invalid execution dispatch operation id for ${workflow.workflow_id}/${unit.id}: ${entry.operation_id}`,
						);
					}
					if (
						Object.keys(entry.match).length === 0 ||
						Object.values(entry.match).some((value) => value.length === 0)
					) {
						throw new Error(`Execution dispatch match must be non-empty for ${workflow.workflow_id}/${unit.id}.`);
					}
					const inputValidation = validateExecutionInputContract(entry.input_contract);
					if (!inputValidation.valid) {
						throw new Error(
							`Invalid execution input contract for ${workflow.workflow_id}/${unit.id}/${entry.case_id}: ${inputValidation.errors.join("; ")}`,
						);
					}
					if (
						entry.input_contract.selectors.length !== 1 ||
						!dispatchMatchEquals(entry.input_contract.selectors[0]?.match ?? {}, entry.match)
					) {
						throw new Error(
							`Execution dispatch input selector must exactly match its case for ${workflow.workflow_id}/${unit.id}/${entry.case_id}.`,
						);
					}
					for (const previous of unit.gate.execution_dispatch.slice(0, index)) {
						if (dispatchCasesOverlap(previous.match, entry.match)) {
							throw new Error(
								`Overlapping execution dispatch cases for ${workflow.workflow_id}/${unit.id}: ${previous.case_id}/${entry.case_id}`,
							);
						}
					}
				}
			}
			for (const script of [
				...(unit.helper_scripts ?? []),
				...(unit.owned_scripts ?? []),
				unit.gate?.validator_script_id,
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
