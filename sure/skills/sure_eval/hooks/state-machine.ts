import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import { SURE_EVAL_CANONICAL } from "../../../canonical/skills/sure-eval/definition.ts";
import type { CanonicalSkillDefinition } from "../../../canonical/types.ts";
import { type LegacyProjectedUnit, projectBranchUnits } from "../../../compatibility/legacy-v1/unit.ts";
import type { GateResult } from "./checkpoints.ts";

export type UnitKind = "linear" | "gate";
export type Unit = LegacyProjectedUnit & {
	gateCheck?: (artifact: unknown) => GateResult;
	gateScriptArgs?: (ctx: SureHookContext) => string[];
	helperScripts?: string[];
};

/** Legacy Pi projection. The canonical definition is the only workflow source. */
export const MAIN_FLOW_UNITS: Unit[] = projectBranchUnits(
	SURE_EVAL_CANONICAL.workflow.branches[0]?.units ?? [],
) as Unit[];
export const WORKFLOW_DEFINITION: CanonicalSkillDefinition["workflow"] = SURE_EVAL_CANONICAL.workflow;

export const TOTAL_UNITS = MAIN_FLOW_UNITS.length;
export const FIRST_UNIT = MAIN_FLOW_UNITS[0];
export const LAST_UNIT = MAIN_FLOW_UNITS[MAIN_FLOW_UNITS.length - 1];

export function findUnit(unitId: string): Unit | undefined {
	return MAIN_FLOW_UNITS.find((unit) => unit.id === unitId);
}

export function nextUnit(unitId: string): Unit | undefined {
	const index = MAIN_FLOW_UNITS.findIndex((unit) => unit.id === unitId);
	return index < 0 ? undefined : MAIN_FLOW_UNITS[index + 1];
}
