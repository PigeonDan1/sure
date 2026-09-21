import { EventEmitter } from "node:events";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const spawnMock = vi.hoisted(() => vi.fn());
vi.mock("node:child_process", () => ({ spawn: spawnMock }));

import { editInExternalEditor } from "../src/modes/interactive/external-editor.ts";

// os.tmpdir() reads TMPDIR on POSIX and TEMP/TMP on Windows; overriding all three lets
// every platform case run against a temporary directory whose name contains a space.
const TMP_ENV_KEYS = ["TMPDIR", "TEMP", "TMP"] as const;

describe("editInExternalEditor spawn arguments", () => {
	let spacedTmpDir: string;
	let savedTmpEnv: Record<string, string | undefined>;

	beforeEach(() => {
		spacedTmpDir = mkdtempSync(join(tmpdir(), "pi editor host-"));
		savedTmpEnv = {};
		for (const key of TMP_ENV_KEYS) {
			savedTmpEnv[key] = process.env[key];
			process.env[key] = spacedTmpDir;
		}
		spawnMock.mockReset();
		spawnMock.mockImplementation(() => {
			const child = new EventEmitter();
			queueMicrotask(() => child.emit("close", 0));
			return child;
		});
	});

	afterEach(() => {
		for (const key of TMP_ENV_KEYS) {
			if (savedTmpEnv[key] === undefined) delete process.env[key];
			else process.env[key] = savedTmpEnv[key];
		}
		rmSync(spacedTmpDir, { recursive: true, force: true });
	});

	it.each([
		["win32", true],
		["linux", false],
		["darwin", false],
	])("quotes the prompt path on %s only when spawning through a shell", async (platform, useShell) => {
		const descriptor = Object.getOwnPropertyDescriptor(process, "platform");
		Object.defineProperty(process, "platform", { configurable: true, value: platform });
		try {
			await editInExternalEditor({ command: "editor --wait", content: "original" });
		} finally {
			if (descriptor) Object.defineProperty(process, "platform", descriptor);
		}

		expect(spawnMock).toHaveBeenCalledTimes(1);
		const [editor, args, options] = spawnMock.mock.calls[0] as [string, string[], { shell: boolean }];
		expect(editor).toBe("editor");
		expect(args[0]).toBe("--wait");
		expect(options.shell).toBe(useShell);

		const promptArgument = args[args.length - 1] as string;
		if (useShell) expect(promptArgument).toMatch(/^".+"$/);
		else expect(promptArgument).not.toContain('"');

		const promptPath = useShell ? promptArgument.slice(1, -1) : promptArgument;
		expect(promptPath).toContain(" ");
		expect(promptPath.startsWith(spacedTmpDir)).toBe(true);
		expect(promptPath.endsWith("prompt.md")).toBe(true);
	});
});
