import type { WorkflowDefinition } from "../../packages/sure-core/src/index.ts";
import {
	SURE_APPROVE_CANONICAL,
	SURE_EVAL_CANONICAL,
	SURE_FEED_CANONICAL,
	SURE_INFER_CANONICAL,
	SURE_ONBOARD_CANONICAL,
	SURE_TRANS_CANONICAL,
} from "../canonical/skills/index.ts";

export type LegacySkillId = "sure_feed" | "sure_onboard" | "sure_infer" | "sure_eval" | "sure_trans" | "sure_approve";

/**
 * Compatibility lookup used by legacy Pi checkpoint facades and the Pi
 * controller. Workflow authority belongs to canonical/skills; this module only
 * preserves the old underscore-based skill identifiers.
 */
export const LEGACY_WORKFLOW_DEFINITIONS: Readonly<Record<LegacySkillId, WorkflowDefinition>> = {
	sure_feed: SURE_FEED_CANONICAL.workflow,
	sure_onboard: SURE_ONBOARD_CANONICAL.workflow,
	sure_infer: SURE_INFER_CANONICAL.workflow,
	sure_eval: SURE_EVAL_CANONICAL.workflow,
	sure_trans: SURE_TRANS_CANONICAL.workflow,
	sure_approve: SURE_APPROVE_CANONICAL.workflow,
};

export function coreDefinitionForLegacy(skillId: LegacySkillId): WorkflowDefinition {
	return LEGACY_WORKFLOW_DEFINITIONS[skillId];
}

export function legacyUnitCounts(): Readonly<Record<LegacySkillId, number>> {
	return Object.fromEntries(
		(Object.keys(LEGACY_WORKFLOW_DEFINITIONS) as LegacySkillId[]).map((skillId) => [
			skillId,
			LEGACY_WORKFLOW_DEFINITIONS[skillId].branches.reduce((sum, branch) => sum + branch.units.length, 0),
		]),
	) as Readonly<Record<LegacySkillId, number>>;
}
