import { describe, expect, it, vi } from "vitest";
import type { SureRunRecord } from "../../src/core/sure/types.ts";

// Sure's containment gates are pure path-string math, so the Windows-only hole
// (path.relative() returns a whole absolute path when the two arguments sit on
// different volumes) is reproducible on any host by handing the modules under
// test win32 path semantics instead of the host's.
vi.mock("node:path", async () => {
	const actual = await vi.importActual<typeof import("node:path")>("node:path");
	return { ...actual.win32, default: actual.win32 };
});

const { SureRunManager } = await import("../../src/core/sure/run-manager.ts");
const { resolveSurePackagePath } = await import("../../src/core/sure/manifest.ts");

const PROJECT = "C:\\project";
const PACKAGE_DIR = "C:\\project\\.sure\\skills\\demo";

const record = {
	runId: "r1",
	cwd: PROJECT,
	runDir: "C:\\project\\.sure\\runs\\r1",
} as unknown as SureRunRecord;

describe("Sure path containment under win32 path semantics", () => {
	const manager = new SureRunManager(PROJECT);

	it("rejects a run path on another volume", () => {
		expect(manager.resolveRunPath(record, "D:\\evil\\manifest.json")).toBeUndefined();
	});

	it("rejects a run path on a UNC share", () => {
		expect(manager.resolveRunPath(record, "\\\\server\\share\\manifest.json")).toBeUndefined();
	});

	it("rejects a skill package path on another volume", () => {
		expect(resolveSurePackagePath(PACKAGE_DIR, "D:\\evil\\hook.js")).toBeUndefined();
	});

	it("rejects a skill package path on a UNC share", () => {
		expect(resolveSurePackagePath(PACKAGE_DIR, "\\\\server\\share\\hook.js")).toBeUndefined();
	});

	it("keeps accepting paths that really are inside", () => {
		expect(manager.resolveRunPath(record, "artifacts\\out.json")).toBe("C:\\project\\artifacts\\out.json");
		expect(manager.resolveRunPath(record, "C:\\project\\artifacts\\out.json")).toBe(
			"C:\\project\\artifacts\\out.json",
		);
		// Windows spells the same directory with either drive-letter case.
		expect(manager.resolveRunPath(record, "c:\\PROJECT\\artifacts\\out.json")).toBe(
			"c:\\PROJECT\\artifacts\\out.json",
		);
		// The base itself, with and without a trailing separator.
		expect(manager.resolveRunPath(record, ".")).toBe(PROJECT);
		expect(manager.resolveRunPath(record, "C:\\project\\")).toBe(PROJECT);
		// Inside the run directory but outside the project is still allowed.
		expect(manager.resolveRunPath(record, ".sure\\runs\\r1\\artifacts\\a.json")).toBe(
			"C:\\project\\.sure\\runs\\r1\\artifacts\\a.json",
		);
		expect(resolveSurePackagePath(PACKAGE_DIR, "hooks/index.ts")).toBe(
			"C:\\project\\.sure\\skills\\demo\\hooks\\index.ts",
		);
	});
});
