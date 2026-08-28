import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { discoverSureSkillPackages } from "../../src/core/sure/manifest.ts";

const repoRoot = fileURLToPath(new URL("../../../../", import.meta.url));

describe("discoverSureSkillPackages", () => {
	it("discovers the bundled SURE skills without diagnostics", () => {
		const result = discoverSureSkillPackages(repoRoot);
		const commands = result.packages.map((pkg) => pkg.manifest.command);
		expect(commands).toContain("sure_reval");
		expect(commands).toContain("sure_trans");
		expect(result.diagnostics.filter((d) => d.message.includes("sure_reval"))).toEqual([]);
		expect(result.diagnostics.filter((d) => d.message.includes("sure_trans"))).toEqual([]);
	});
});
