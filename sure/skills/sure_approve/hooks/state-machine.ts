import { SURE_APPROVE_CANONICAL } from "../../../canonical/skills/sure-approve/definition.ts";
import type { CanonicalSkillDefinition } from "../../../canonical/types.ts";

export type ApproveMode = "audit" | "approve";

export interface Unit {
	id: string;
	label: string;
	produces: string;
	schemaRef: string;
	gateScript: string;
	gateScriptArgs?: string[];
	requiredFields: string[];
}

function projectUnit(unit: (typeof SURE_APPROVE_CANONICAL.workflow.branches)[number]["units"][number]): Unit {
	if (!unit.schema_ref || !unit.gate?.script_id || !unit.required_fields) {
		throw new Error(`Approval unit ${unit.id} is missing its legacy contract fields.`);
	}
	return {
		id: unit.id,
		label: unit.label,
		produces: unit.produces,
		schemaRef: unit.schema_ref,
		gateScript: unit.gate.script_id,
		...(unit.gate.script_args === undefined ? {} : { gateScriptArgs: [...unit.gate.script_args] }),
		requiredFields: [...unit.required_fields],
	};
}

const auditBranch = SURE_APPROVE_CANONICAL.workflow.branches.find((branch) => branch.id === "audit");
const approveBranch = SURE_APPROVE_CANONICAL.workflow.branches.find((branch) => branch.id === "approve");
if (!auditBranch || !approveBranch) throw new Error("Canonical approval branches are incomplete.");

export const AUDIT_UNITS: Unit[] = auditBranch.units.map(projectUnit);
export const DECISION_UNITS: Unit[] = approveBranch.units.map(projectUnit);
export const APPROVE_UNITS: Unit[] = [...AUDIT_UNITS, ...DECISION_UNITS];
export const WORKFLOW_DEFINITION: CanonicalSkillDefinition["workflow"] = SURE_APPROVE_CANONICAL.workflow;

export function unitsForMode(mode: ApproveMode): Unit[] {
	return mode === "approve" ? DECISION_UNITS : AUDIT_UNITS;
}

export function findUnit(unitId: string): Unit | undefined {
	return APPROVE_UNITS.find((unit) => unit.id === unitId);
}

export function nextUnit(mode: ApproveMode, unitId: string): Unit | undefined {
	const units = unitsForMode(mode);
	const index = units.findIndex((unit) => unit.id === unitId);
	return index < 0 ? undefined : units[index + 1];
}
