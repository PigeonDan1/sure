import { readFileSync } from "node:fs";
import { isAbsolute, join, resolve } from "node:path";
import type { CapabilityRequirement, WorkflowDefinition, WorkflowUnit } from "@earendil-works/sure-core";

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(value: unknown, label: string): string {
	if (typeof value !== "string" || value.trim() === "") throw new Error(`${label} must be a non-empty string.`);
	return value;
}

function validateDefinition(value: unknown): WorkflowDefinition {
	if (!isRecord(value)) throw new Error("Workflow definition must be a JSON object.");
	const schema = requiredString(value.schema, "workflow schema");
	if (schema !== "sure.workflow.definition.v1") throw new Error(`Unsupported workflow schema: ${schema}`);
	const workflowId = requiredString(value.workflow_id, "workflow_id");
	const version = requiredString(value.version, "workflow version");
	if (!Array.isArray(value.branches) || value.branches.length === 0) throw new Error("Workflow needs branches.");
	const branches = value.branches.map((branchValue, branchIndex) => {
		if (!isRecord(branchValue)) throw new Error(`branches[${branchIndex}] must be an object.`);
		const id = requiredString(branchValue.id, `branches[${branchIndex}].id`);
		const initial = requiredString(branchValue.initial_unit_id, `${id}.initial_unit_id`);
		const terminal = requiredString(branchValue.terminal_unit_id, `${id}.terminal_unit_id`);
		if (!Array.isArray(branchValue.units) || branchValue.units.length === 0)
			throw new Error(`Branch ${id} has no units.`);
		const units = branchValue.units.map((unitValue, unitIndex) => {
			if (!isRecord(unitValue)) throw new Error(`${id}.units[${unitIndex}] must be an object.`);
			const unitId = requiredString(unitValue.id, `${id}.units[${unitIndex}].id`);
			const kind = unitValue.kind;
			if (kind !== "linear" && kind !== "gate") throw new Error(`Unit ${unitId} has an invalid kind.`);
			requiredString(unitValue.produces, `${id}.${unitId}.produces`);
			if (kind === "gate" && !isRecord(unitValue.gate))
				throw new Error(`Gate ${id}.${unitId} has no gate declaration.`);
			return unitValue as unknown as WorkflowUnit;
		});
		const ids = new Set(units.map((unit) => unit.id));
		if (!ids.has(initial) || !ids.has(terminal)) throw new Error(`Branch ${id} points at an unknown unit.`);
		return { id, initial_unit_id: initial, terminal_unit_id: terminal, units };
	});
	const defaultBranch = requiredString(value.default_branch_id, "default_branch_id");
	if (!branches.some((branch) => branch.id === defaultBranch))
		throw new Error(`Unknown default branch: ${defaultBranch}`);
	if (!isRecord(value.retry_policy) || typeof value.retry_policy.default_max_retries !== "number") {
		throw new Error("Workflow retry_policy.default_max_retries is required.");
	}
	return {
		schema: "sure.workflow.definition.v1",
		workflow_id: workflowId,
		version,
		...(typeof value.checkpoint_id === "string" ? { checkpoint_id: value.checkpoint_id } : {}),
		...(typeof value.checkpoint_label === "string" ? { checkpoint_label: value.checkpoint_label } : {}),
		branches,
		default_branch_id: defaultBranch,
		retry_policy: value.retry_policy as unknown as WorkflowDefinition["retry_policy"],
	};
}

export interface LoadedDefinition {
	definition: WorkflowDefinition;
	capabilities: readonly CapabilityRequirement[];
	path: string;
	root: string;
	registryPath: string;
}

export function loadDefinition(
	root: string,
	explicitPath: string | undefined,
	skill: string | undefined,
): LoadedDefinition {
	const candidates: string[] = [];
	if (explicitPath) candidates.push(isAbsolute(explicitPath) ? explicitPath : resolve(root, explicitPath));
	if (skill) {
		const slug = skill.replaceAll("_", "-");
		candidates.push(join(root, "sure", "dist", "agent-skills", slug, "canonical-definition.json"));
		candidates.push(join(root, "sure", "generated", "pi", "skills", skill, "canonical-definition.json"));
	}
	const path = candidates.find((candidate) => {
		try {
			readFileSync(candidate);
			return true;
		} catch {
			return false;
		}
	});
	if (!path) throw new Error(`Cannot find workflow definition${skill ? ` for ${skill}` : ""}. Use --definition.`);
	let raw: unknown;
	try {
		raw = JSON.parse(readFileSync(path, "utf8")) as unknown;
	} catch (error) {
		throw new Error(
			`Cannot parse workflow definition ${path}: ${error instanceof Error ? error.message : String(error)}`,
		);
	}
	const wrapper = isRecord(raw) && isRecord(raw.workflow) ? raw.workflow : raw;
	const definition = validateDefinition(wrapper);
	const rawCapabilities = isRecord(raw) && Array.isArray(raw.capabilities) ? raw.capabilities : [];
	const capabilities = rawCapabilities.map((value, index) => {
		if (!isRecord(value)) throw new Error(`capabilities[${index}] must be an object.`);
		if (
			typeof value.capability_id !== "string" ||
			typeof value.capability_class !== "string" ||
			typeof value.required !== "boolean"
		) {
			throw new Error(`capabilities[${index}] has an invalid wire shape.`);
		}
		const capabilityClass = value.capability_class;
		if (capabilityClass !== "agent_capability" && capabilityClass !== "execution_capability") {
			throw new Error(`capabilities[${index}].capability_class is invalid.`);
		}
		const typedClass = capabilityClass as CapabilityRequirement["capability_class"];
		return {
			capability_id: value.capability_id,
			capability_class: typedClass,
			required: value.required,
			...(isRecord(value.constraints)
				? { constraints: value.constraints as CapabilityRequirement["constraints"] }
				: {}),
		};
	});
	const registryPath = join(resolve(path, ".."), "validator-registry.json");
	return { definition, capabilities, path: resolve(path), root: resolve(path, ".."), registryPath };
}

export function unitForCurrent(definition: WorkflowDefinition, unitId: string, branchId: string): WorkflowUnit {
	const branch = definition.branches.find((candidate) => candidate.id === branchId);
	if (!branch) throw new Error(`Unknown workflow branch: ${branchId}`);
	const unit = branch.units.find((candidate) => candidate.id === unitId);
	if (!unit) throw new Error(`Unknown workflow unit: ${branchId}/${unitId}`);
	return unit;
}
