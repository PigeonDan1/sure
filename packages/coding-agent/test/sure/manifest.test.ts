import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { discoverSureSkillPackages } from "../../src/core/sure/manifest.ts";

const repoRoot = fileURLToPath(new URL("../../../../", import.meta.url));

describe("discoverSureSkillPackages", () => {
	it("discovers the bundled SURE skills without diagnostics", () => {
		const result = discoverSureSkillPackages(repoRoot);
		const commands = result.packages.map((pkg) => pkg.manifest.command);
		expect(commands).toContain("sure_approve");
		expect(commands).toContain("sure_infer");
		expect(commands).toContain("sure_eval");
		expect(commands).toContain("sure_trans");
		expect(result.diagnostics.filter((d) => d.message.includes("sure_eval"))).toEqual([]);
		expect(result.diagnostics.filter((d) => d.message.includes("sure_approve"))).toEqual([]);
		expect(result.diagnostics.filter((d) => d.message.includes("sure_infer"))).toEqual([]);
		expect(result.diagnostics.filter((d) => d.message.includes("sure_trans"))).toEqual([]);
	});
});
