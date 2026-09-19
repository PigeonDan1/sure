import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { getModel } from "@earendil-works/pi-ai/compat";
import { afterEach, describe, expect, it } from "vitest";
import { createAgentSession } from "../src/core/sdk.ts";
import { SessionManager } from "../src/core/session-manager.ts";
import { SettingsManager } from "../src/core/settings-manager.ts";
import { assistantMsg, userMsg } from "./utilities.ts";

describe("JSONL export", () => {
	const tempDirs: string[] = [];

	afterEach(() => {
		for (const tempDir of tempDirs.splice(0)) {
			rmSync(tempDir, { recursive: true, force: true });
		}
	});

	it("writes the session header followed by the branch entries", async () => {
		const tempDir = mkdtempSync(join(tmpdir(), "pi-jsonl-export-"));
		tempDirs.push(tempDir);
		const sessionManager = SessionManager.inMemory(tempDir);
		const { session } = await createAgentSession({
			cwd: tempDir,
			agentDir: join(tempDir, "agent"),
			model: getModel("anthropic", "claude-sonnet-4-5")!,
			settingsManager: SettingsManager.inMemory(),
			sessionManager,
		});

		try {
			sessionManager.appendMessage(userMsg("hello"));
			sessionManager.appendMessage(assistantMsg("hi"));
			const entryIds = sessionManager.getBranch().map((entry) => entry.id);

			const outputPath = join(tempDir, "session.jsonl");
			expect(session.exportToJsonl(outputPath)).toBe(outputPath);

			const records = readFileSync(outputPath, "utf8")
				.trim()
				.split("\n")
				.map((line) => JSON.parse(line) as Record<string, unknown>);
			expect(records[0]).toMatchObject({ type: "session", id: sessionManager.getSessionId() });
			expect(records.slice(1).map((record) => record.id)).toEqual(entryIds);
		} finally {
			session.dispose();
		}
	});
});
