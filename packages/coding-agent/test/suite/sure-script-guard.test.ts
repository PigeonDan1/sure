import { describe, expect, it } from "vitest";
import { invokedSkillScripts } from "../../../../sure/runtime/script-guard.ts";

describe("invokedSkillScripts", () => {
	it("returns every script a compound command runs, not just the first", () => {
		expect(invokedSkillScripts("python3 scripts/allowed.py && python3 scripts/other.py")).toEqual([
			"scripts/allowed.py",
			"scripts/other.py",
		]);
	});

	it("ignores a script that is only being read", () => {
		expect(invokedSkillScripts("cat scripts/other.py")).toEqual([]);
		expect(invokedSkillScripts("grep -n def scripts/other.py")).toEqual([]);
	});

	it("still catches a run hidden behind a read", () => {
		expect(invokedSkillScripts("cat scripts/allowed.py; python3 scripts/other.py")).toEqual(["scripts/other.py"]);
	});

	it("returns nothing when no skill script is named", () => {
		expect(invokedSkillScripts("ls -la")).toEqual([]);
	});

	it("honours a caller-supplied prefix", () => {
		expect(invokedSkillScripts("python3 sure_eval/scripts/run.py", "sure_eval/scripts")).toEqual([
			"sure_eval/scripts/run.py",
		]);
	});
});
