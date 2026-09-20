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

	it("finds files with node, honouring .gitignore", async () => {
		const dir = fixture();
		const tool = createFindTool(dir);

		const lines = toolText(await tool.execute("call-find", { pattern: "*.ts" })).split("\n");

		expect(lines).toContain("src/alpha.ts");
		expect(lines).not.toContain("build/alpha.ts");
	});
});
