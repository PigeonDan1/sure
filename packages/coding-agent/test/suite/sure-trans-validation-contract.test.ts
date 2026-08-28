import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { TRANS_UNITS } from "../../../../sure/skills/sure_trans/hooks/state-machine.ts";

const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_trans");

// Units whose artifact the agent writes by hand before the gate executes it.
const AGENT_WRITTEN = TRANS_UNITS.filter((unit) => unit.gateScript === "run_trans_validate.py");

// Every field one of these artifacts must carry costs a retry when the agent
// does not know about it: the gate blocks on the missing field before it runs.
// SKILL.md is where the agent learns what to write, so it has to name them all.
describe("the validation artifacts SKILL.md asks the agent to write", () => {
	const skill = readFileSync(join(PACKAGE_DIR, "SKILL.md"), "utf-8");
	// Only a code span that is the key itself counts. Prose like "transformation
	// input", and a flag that happens to share the name such as
	// `docker load --input <tar>`, is not the agent being told which key to write.
	const codeSpans = (skill.match(/`[^`\n]+`/g) ?? []).map((span) => span.slice(1, -1));

	it("covers every unit that hand-writes one", () => {
		expect(AGENT_WRITTEN.length).toBeGreaterThan(0);
	});

	for (const unit of AGENT_WRITTEN) {
		for (const field of unit.requiredFields ?? []) {
			it(`names "${field}" for unit "${unit.id}"`, () => {
				const names = (span: string) =>
					span === field || span.startsWith(`${field}=`) || span.includes(`"${field}"`);
				expect(codeSpans.some(names)).toBe(true);
			});
		}
	}
});
