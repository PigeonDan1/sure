import { existsSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { MemoryService } from "../../../../sure/runtime/memory/service.ts";

const TMP = resolve(__dirname, "tmp-memory-service");

function freshRoot(name: string): string {
	const root = join(TMP, name);
	rmSync(root, { recursive: true, force: true });
	mkdirSync(root, { recursive: true });
	return root;
}

describe("neutral memory reference service", () => {
	it("round-trips logical entry ids without depending on a host path", () => {
		const service = MemoryService.fromRepoRoot(freshRoot("uri"));

		expect(service.uriForEntry("sure_onboard/cuda-mismatch", "bad_case")).toBe(
			"memory://sure_onboard/bad_case/cuda-mismatch",
		);
		expect(service.parseUri("memory://sure_onboard/bad_case/cuda-mismatch")).toEqual({
			skill: "sure_onboard",
			kind: "bad_case",
			slug: "cuda-mismatch",
		});
		expect(service.uriForEntry("_shared/site-gpu", "fact")).toBe("memory://_shared/fact/site-gpu");
		expect(service.uriForPath(service.legacyPath("sure_onboard", "bad_case", "cuda-mismatch"))).toBe(
			"memory://sure_onboard/bad_case/cuda-mismatch",
		);
		expect(service.uriForPath(service.legacyPath("_shared", "fact", "site-gpu"))).toBe(
			"memory://_shared/fact/site-gpu",
		);
	});

	it("keeps legacy writes and legacy-first reads while allowing canonical opt-in", () => {
		const repoRoot = freshRoot("aliases");
		const legacy = MemoryService.fromRepoRoot(repoRoot);
		const legacyPath = legacy.writeReference("sure_infer", "bad_case", "missing-gpu");
		const canonicalPath = legacy.canonicalPath("sure_infer", "bad_case", "missing-gpu");
		mkdirSync(join(legacyPath, ".."), { recursive: true });
		mkdirSync(join(canonicalPath, ".."), { recursive: true });
		writeFileSync(legacyPath, "legacy\n", "utf-8");
		writeFileSync(canonicalPath, "canonical\n", "utf-8");

		expect(legacy.writeReference("sure_infer", "bad_case", "missing-gpu")).toBe(legacyPath);
		expect(legacy.readCandidates("sure_infer", "bad_case", "missing-gpu")[0]).toBe(legacyPath);
		expect(legacy.resolveReference("sure_infer", "bad_case", "missing-gpu")).toBe(legacyPath);

		const canonical = MemoryService.fromRepoRoot(repoRoot, { preferCanonicalReads: true, writeRoot: "canonical" });
		expect(canonical.resolveReference("sure_infer", "bad_case", "missing-gpu")).toBe(canonicalPath);
		expect(canonical.writeReference("sure_infer", "bad_case", "new-entry")).toBe(
			canonical.canonicalPath("sure_infer", "bad_case", "new-entry"),
		);
	});

	it("rejects traversal and malformed logical identities", () => {
		const service = MemoryService.fromRepoRoot(freshRoot("invalid"));

		expect(() => service.logicalUri("../outside", "fact", "entry")).toThrow();
		expect(() => service.logicalUri("sure_infer", "fact", "../outside")).toThrow();
		expect(() => service.parseUri("memory://sure_infer/fact/a/b")).toThrow();
		expect(() => service.parseUri("memory://sure_infer/unknown/entry")).toThrow();
		expect(() => service.uriForEntry("sure_infer/a/b", "fact")).toThrow();
	});

	it("rejects an admitted alias that resolves through a symlink outside its root", () => {
		const repoRoot = freshRoot("symlink");
		const service = MemoryService.fromRepoRoot(repoRoot);
		const outside = freshRoot("symlink-outside");
		const legacyDir = join(repoRoot, "sure", "skills", "sure_eval", "references", "memory");
		mkdirSync(legacyDir, { recursive: true });
		const escapedAlias = join(legacyDir, "bad_cases");
		mkdirSync(join(outside, "bad_cases"), { recursive: true });
		symlinkSync(join(outside, "bad_cases"), escapedAlias, "dir");

		expect(() => service.legacyPath("sure_eval", "bad_case", "escape")).toThrow(/outside|escapes/);
		expect(existsSync(escapedAlias)).toBe(true);
	});

	it("supports an explicitly injected memory root independent of package layout", () => {
		const repoRoot = freshRoot("custom-root");
		const memoryRoot = join(repoRoot, "state", "memory");
		const service = MemoryService.fromRepoRoot(repoRoot, { memoryRoot });

		expect(service.memoryRoot).toBe(resolve(memoryRoot));
		expect(service.roots.repoRoot).toBe(resolve(repoRoot));
	});
});
