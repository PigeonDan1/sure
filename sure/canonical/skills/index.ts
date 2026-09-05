export { SURE_APPROVE_CANONICAL } from "./sure-approve/definition.ts";
export { SURE_EVAL_CANONICAL } from "./sure-eval/definition.ts";
export { SURE_FEED_CANONICAL } from "./sure-feed/definition.ts";
export { SURE_INFER_CANONICAL } from "./sure-infer/definition.ts";
export { SURE_ONBOARD_CANONICAL } from "./sure-onboard/definition.ts";
export { SURE_TRANS_CANONICAL } from "./sure-trans/definition.ts";

import type { CanonicalSkillDefinition } from "../types.ts";
import { SURE_APPROVE_CANONICAL } from "./sure-approve/definition.ts";
import { SURE_EVAL_CANONICAL } from "./sure-eval/definition.ts";
import { SURE_FEED_CANONICAL } from "./sure-feed/definition.ts";
import { SURE_INFER_CANONICAL } from "./sure-infer/definition.ts";
import { SURE_ONBOARD_CANONICAL } from "./sure-onboard/definition.ts";
import { SURE_TRANS_CANONICAL } from "./sure-trans/definition.ts";

export const CANONICAL_SKILLS: readonly CanonicalSkillDefinition[] = [
	SURE_FEED_CANONICAL,
	SURE_ONBOARD_CANONICAL,
	SURE_INFER_CANONICAL,
	SURE_EVAL_CANONICAL,
	SURE_APPROVE_CANONICAL,
	SURE_TRANS_CANONICAL,
];

export const CANONICAL_SKILLS_BY_ID: Readonly<Record<string, CanonicalSkillDefinition>> = Object.fromEntries(
	CANONICAL_SKILLS.map((skill) => [skill.skill_id, skill]),
);
