import type * as Fs from "node:fs";
import { spawnSync } from "child_process";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ensureTool, getToolPath, type ToolStatus } from "../src/utils/tools-manager.ts";

vi.mock("fs", async (importOriginal) => {
	const actual = await importOriginal<typeof Fs>();
	return {
		...actual,
		existsSync: vi.fn(() => false),
	};
});

vi.mock("child_process", () => ({
	spawnSync: vi.fn(),
}));

afterEach(() => {
	vi.mocked(spawnSync).mockReset();
});

describe("getToolPath", () => {
	it("returns the system command name when it's on PATH", () => {
		vi.mocked(spawnSync).mockReturnValue({ error: undefined } as ReturnType<typeof spawnSync>);

		expect(getToolPath("fd")).toBe("fd");
	});

	it("returns null when the tool can't be found anywhere", () => {
		vi.mocked(spawnSync).mockReturnValue({ error: new Error("not found") } as ReturnType<typeof spawnSync>);

		expect(getToolPath("fd")).toBeNull();
		expect(getToolPath("rg")).toBeNull();
	});
});

describe("ensureTool", () => {
	it("returns the existing path without reporting status when the tool is found", async () => {
		vi.mocked(spawnSync).mockReturnValue({ error: undefined } as ReturnType<typeof spawnSync>);
		const statuses: ToolStatus[] = [];

		const result = await ensureTool("fd", (status) => statuses.push(status));

		expect(result).toBe("fd");
		expect(statuses).toEqual([]);
	});

	it("reports a warning naming what breaks when the tool can't be found", async () => {
		vi.mocked(spawnSync).mockReturnValue({ error: new Error("not found") } as ReturnType<typeof spawnSync>);
		const statuses: ToolStatus[] = [];

		const result = await ensureTool("rg", (status) => statuses.push(status));

		expect(result).toBeUndefined();
		expect(statuses).toEqual([
			{
				type: "warning",
				message: "ripgrep not found. Install it and make sure it is on PATH; the grep tool until then.",
			},
		]);
	});
});
