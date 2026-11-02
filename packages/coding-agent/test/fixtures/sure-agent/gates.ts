import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { SureHookContext, SureHookResult } from "../../../src/core/sure/types.ts";

export function preFinish(ctx: SureHookContext): SureHookResult {
	const proof = join(ctx.runDir, "artifacts", "proof.json");
	if (!existsSync(proof)) return { ok: false, repair: "Create the fixture proof before finishing." };
	const value: unknown = JSON.parse(readFileSync(proof, "utf8"));
	if (typeof value !== "object" || value === null || !("fixture" in value) || value.fixture !== "native-gate") {
		return { ok: false, repair: "The fixture proof is invalid." };
	}
	writeFileSync(join(ctx.runDir, "artifacts", "gate-observed.json"), JSON.stringify({ checked: true }));
	return { ok: true };
}
