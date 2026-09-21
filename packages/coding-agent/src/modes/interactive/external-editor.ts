import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parseCommandArgs } from "../../utils/command-args.ts";
import { stripBom } from "../../utils/text.ts";

export interface ExternalEditorOptions {
	command: string;
	content: string;
}

export type ExternalEditorResult = { status: "complete"; content: string } | { status: "failed" };

export async function editInExternalEditor(options: ExternalEditorOptions): Promise<ExternalEditorResult> {
	const directory = mkdtempSync(join(tmpdir(), "pi-editor-"));
	const filePath = join(directory, "prompt.md");
	try {
		writeFileSync(filePath, options.content, "utf-8");
		// Quote-aware: an editor path with a space in it ("C:\Program Files\...") is one
		// argument, not one per word.
		const [editor, ...editorArgs] = parseCommandArgs(options.command);
		if (!editor) {
			return { status: "failed" };
		}
		process.stdout.write(`Launching external editor: ${options.command}\nPi will resume when the editor exits.\n`);

		// Do not use spawnSync here. On Windows, synchronous child_process calls can keep
		// Node/libuv's console input read active after the parent pauses stdin, racing
		// vim/nvim for the console input buffer until Ctrl+C cancels the pending read.
		const useShell = process.platform === "win32";
		// Under shell: true the arguments are concatenated without escaping, so anything
		// holding a space has to be quoted again or the shell word-splits it: the prompt
		// path (its temporary directory can contain a space) and the editor command line.
		const requote = (value: string): string => (useShell && /\s/.test(value) ? `"${value}"` : value);
		const exitCode = await new Promise<number | null>((resolve) => {
			const child = spawn(requote(editor), [...editorArgs.map(requote), useShell ? `"${filePath}"` : filePath], {
				stdio: "inherit",
				shell: useShell,
			});
			child.on("error", () => resolve(null));
			child.on("close", (code) => resolve(code));
		});

		if (exitCode !== 0) {
			return { status: "failed" };
		}

		return { status: "complete", content: stripBom(readFileSync(filePath, "utf-8")).replace(/\n$/, "") };
	} finally {
		try {
			rmSync(directory, { recursive: true, force: true });
		} catch {
			// Cleanup is best effort.
		}
	}
}
