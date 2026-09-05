import { SURE_FEED_CANONICAL } from "../../../canonical/skills/sure-feed/definition.ts";
import type { CanonicalSkillDefinition } from "../../../canonical/types.ts";
import { projectBranchUnits } from "../../../compatibility/legacy-v1/unit.ts";
import type { Unit } from "./checkpoints.ts";

/** Legacy Pi projection. Workflow order and contracts live in canonical/. */
export const MODEL_FEED_UNITS: Unit[] = projectBranchUnits(
	SURE_FEED_CANONICAL.workflow.branches[0]?.units ?? [],
) as Unit[];
export const WORKFLOW_DEFINITION: CanonicalSkillDefinition["workflow"] = SURE_FEED_CANONICAL.workflow;

export const TOTAL_UNITS = MODEL_FEED_UNITS.length;
export const FIRST_UNIT = MODEL_FEED_UNITS[0];
export const LAST_UNIT = MODEL_FEED_UNITS[MODEL_FEED_UNITS.length - 1];

export function findUnit(unitId: string): Unit | undefined {
	return MODEL_FEED_UNITS.find((unit) => unit.id === unitId);
}

export function nextUnit(unitId: string): Unit | undefined {
	const index = MODEL_FEED_UNITS.findIndex((unit) => unit.id === unitId);
	return index < 0 ? undefined : MODEL_FEED_UNITS[index + 1];
}
