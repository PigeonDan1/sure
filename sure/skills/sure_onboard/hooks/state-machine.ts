import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import { SURE_ONBOARD_CANONICAL } from "../../../canonical/skills/sure-onboard/definition.ts";
import type { CanonicalSkillDefinition } from "../../../canonical/types.ts";
import { type LegacyProjectedUnit, projectBranchUnits } from "../../../compatibility/legacy-v1/unit.ts";
import type { GateResult } from "./checkpoints.ts";

export type UnitKind = "linear" | "gate";
export type Unit = LegacyProjectedUnit & {
	gateCheck?: (artifact: unknown) => GateResult;
	gateScriptArgs?: (ctx: SureHookContext) => string[];
};

/** Legacy Pi projection. The canonical definition is the only workflow source. */
export const MODEL_TOOL_UNITS: Unit[] = projectBranchUnits(
	SURE_ONBOARD_CANONICAL.workflow.branches[0]?.units ?? [],
) as Unit[];
export const WORKFLOW_DEFINITION: CanonicalSkillDefinition["workflow"] = SURE_ONBOARD_CANONICAL.workflow;

export const TOTAL_UNITS = MODEL_TOOL_UNITS.length;
export const FIRST_UNIT = MODEL_TOOL_UNITS[0];
export const LAST_UNIT = MODEL_TOOL_UNITS[MODEL_TOOL_UNITS.length - 1];

export function findUnit(unitId: string): Unit | undefined {
	return MODEL_TOOL_UNITS.find((unit) => unit.id === unitId);
}

export function nextUnit(unitId: string): Unit | undefined {
	const index = MODEL_TOOL_UNITS.findIndex((unit) => unit.id === unitId);
	return index < 0 ? undefined : MODEL_TOOL_UNITS[index + 1];
}
