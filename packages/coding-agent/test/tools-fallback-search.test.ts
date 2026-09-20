import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

// Force the fallback whether or not this machine has ripgrep or fd installed.
vi.mock("../src/utils/tools-manager.ts", () => ({
	ensureTool: vi.fn(async () => undefined),
	getToolPath: vi.fn(() => null),
}));

// The wrapped tools are what the agent registers, and their execute() defaults
// the extension context, so the search root is the cwd passed in here.
import { grepFallback } from "../src/core/tools/fallback-search.ts";
import { createFindTool } from "../src/core/tools/find.ts";
import { createGrepTool } from "../src/core/tools/grep.ts";

function toolText(result: { content: Array<{ type: string; text?: string }> }): string {
	return result.content[0]?.text ?? "";
}

describe("grep and find without ripgrep or fd", () => {
	const tempDirs: string[] = [];

	afterEach(() => {
		for (const dir of tempDirs.splice(0)) rmSync(dir, { recursive: true, force: true });
	});

	function fixture(): string {
		const dir = mkdtempSync(join(tmpdir(), "pi-fallback-search-"));
		tempDirs.push(dir);
		mkdirSync(join(dir, "src"), { recursive: true });
		mkdirSync(join(dir, "build"), { recursive: true });
		writeFileSync(join(dir, ".gitignore"), "build/\n");
		writeFileSync(join(dir, "src", "alpha.ts"), "const needle = 1;\nconst other = 2;\n");
		writeFileSync(join(dir, "src", "beta.txt"), "needle in a text file\n");
		writeFileSync(join(dir, "src", "binary.ts"), Buffer.from([0x6e, 0x65, 0x65, 0x64, 0x6c, 0x65, 0x00]));
		writeFileSync(join(dir, "build", "alpha.ts"), "const needle = 3;\n");
		return dir;
	}

	it("greps with node, honouring the glob, .gitignore and binary files", async () => {
		const dir = fixture();
		const tool = createGrepTool(dir);

		const text = toolText(await tool.execute("call-grep", { pattern: "needle", glob: "*.ts" }));

		expect(text).toContain("src/alpha.ts:1:");
		expect(text).toContain("const needle = 1;");
		expect(text).not.toContain("beta.txt"); // glob filtered it out
		expect(text).not.toContain("build"); // .gitignore filtered it out
		expect(text).not.toContain("binary.ts"); // NUL byte in the first 8 KiB
	});

	it("stops at the last real line of a file ending in a newline", async () => {
		const dir = fixture();
		const tool = createGrepTool(dir);

		// "^" matches the empty string, so a phantom line after the final newline
		// shows up as a hit of its own and eats the match limit.
		const lines = toolText(await tool.execute("call-grep", { pattern: "^", glob: "alpha.ts" })).split("\n");

		expect(lines).toEqual(["src/alpha.ts:1: const needle = 1;", "src/alpha.ts:2: const other = 2;"]);
	});

	it("gives up between the files of one flat directory when aborted", async () => {
		const dir = mkdtempSync(join(tmpdir(), "pi-fallback-abort-"));
		tempDirs.push(dir);
		const fileCount = 20;
		for (let index = 0; index < fileCount; index++) {
			writeFileSync(join(dir, `f${index}.txt`), "needle\n");
		}
		// The walk reads `aborted` once per directory, so a real timer would race
		// the whole flat scan. Flipping the flag on a later read aborts partway
		// through the one directory without waiting for anything.
		let reads = 0;
		const signal = {
			get aborted() {
				return reads++ >= 2;
			},
		} as unknown as AbortSignal;

		const matches = await grepFallback({
			searchPath: dir,
			isDirectory: true,
			pattern: "needle",
			limit: fileCount * 2,
			signal,
		});

		expect(matches.length).toBeGreaterThan(0); // the scan did start
		expect(matches.length).toBeLessThan(fileCount); // and it stopped before the end
	});

	it("finds files with node, honouring .gitignore", async () => {
		const dir = fixture();
		const tool = createFindTool(dir);

		const lines = toolText(await tool.execute("call-find", { pattern: "*.ts" })).split("\n");

		expect(lines).toContain("src/alpha.ts");
		expect(lines).not.toContain("build/alpha.ts");
	});
});
