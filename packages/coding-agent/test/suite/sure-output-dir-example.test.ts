import { isAbsolute } from "node:path";
import { describe, expect, it, vi } from "vitest";

// Without a usable site policy NFS_ROOT falls back to a placeholder that has no
// directory part, and the example used to come out relative inside an error
// that demands an absolute path.
vi.mock("../../../../sure/site/loader.ts", () => ({
	resolveSitePolicy: () => undefined,
	requireSitePolicy: () => {
		throw new Error("site policy is not configured");
	},
}));

const { NFS_ROOT, resolveOutputDir } = await import("../../src/core/sure/output-dir.ts");

describe("output_dir example without a site policy", () => {
	it("suggests an absolute path", () => {
		expect(NFS_ROOT).toBe("<site-policy-required>");

		const result = resolveOutputDir("output_dir=my_results");

		expect(result.ok).toBe(false);
		const example = /for example output_dir=(.+?) \(got/.exec(result.error ?? "")?.[1];
		expect(example).toBeDefined();
		expect(isAbsolute(String(example))).toBe(true);
	});
});
