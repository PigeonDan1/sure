import { SURE_TRANS_CANONICAL } from "../../../canonical/skills/sure-trans/definition.ts";
import type { CanonicalSkillDefinition } from "../../../canonical/types.ts";
import { projectBranchUnits } from "../../../compatibility/legacy-v1/unit.ts";
import type { GateResult, Unit as LegacyUnit } from "./checkpoints.ts";

// scaffold_adapter.py intentionally emits a draft manifest before the agent
// implements the wrapper. Preserve that stay-on-unit check as a Pi-only
// projection of the canonical auxiliary validator declaration.
function adapterStillDraft(artifact: unknown): GateResult {
	const status =
		typeof artifact === "object" && artifact !== null ? (artifact as Record<string, unknown>).status : undefined;
	if (status !== "draft") return { ok: true };
	return {
		ok: false,
		missing: true,
		reason: "adapter wrapper is still the scaffold",
		repair:
			"adapter/model.py still raises NotImplementedError. Replace it with the model-specific wrapper, " +
			"then rerun scripts/scaffold_adapter.py so the manifest turns ready.",
	};
}

export type Unit = LegacyUnit & { gateCheck?: (artifact: unknown) => GateResult };

const projected = projectBranchUnits(SURE_TRANS_CANONICAL.workflow.branches[0]?.units ?? []) as Unit[];
for (const unit of projected) {
	if (unit.id === "generate_adapter") unit.gateCheck = adapterStillDraft;
}

/** Legacy Pi projection. The canonical definition is the only workflow source. */
export const TRANS_UNITS: Unit[] = projected;
export const WORKFLOW_DEFINITION: CanonicalSkillDefinition["workflow"] = SURE_TRANS_CANONICAL.workflow;
export const TOTAL_UNITS = TRANS_UNITS.length;
export const FIRST_UNIT = TRANS_UNITS[0];
export const LAST_UNIT = TRANS_UNITS[TRANS_UNITS.length - 1];

export function findUnit(unitId: string): Unit | undefined {
	return TRANS_UNITS.find((unit) => unit.id === unitId);
}

export function nextUnit(unitId: string): Unit | undefined {
	const index = TRANS_UNITS.findIndex((unit) => unit.id === unitId);
	return index < 0 ? undefined : TRANS_UNITS[index + 1];
}
