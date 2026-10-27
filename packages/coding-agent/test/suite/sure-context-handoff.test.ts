import { mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fauxAssistantMessage, fauxToolCall } from "@earendil-works/pi-ai";
import { Type } from "typebox";
import { afterEach, describe, expect, it } from "vitest";
import { CONTEXT_RESTORE_TOOL } from "../../src/core/context-folding.ts";
import { createContextFoldingExtension } from "../../src/core/context-folding-extension.ts";
import { CONTEXT_HANDOFF_ENTRY } from "../../src/core/context-folding-handoff.ts";
import { sureExtension } from "../../src/core/sure/index.ts";
import { createHarness, getMessageText, type Harness } from "./harness.ts";

const manual = "Local skill instructions: collect evidence, validate the manifest, and finish.\n".repeat(100);

describe("SURE context handoffs", () => {
	let harness: Harness | undefined;
	afterEach(() => harness?.cleanup());

	it("records only accepted success, folds its instructions, and restores the exact source", async () => {
		let executions = 0;
		const current = await createHarness({
			extensionFactories: [
				createContextFoldingExtension({ enabled: true, chunkSize: 1 }),
				sureExtension,
				(pi) => {
					pi.registerTool({
						name: "inspect",
						label: "Inspect",
						description: "Read local fixture evidence",
						parameters: Type.Object({}),
						execute: async () => {
							executions++;
							return { content: [{ type: "text", text: manual }], details: {} };
						},
					});
				},
			],
		});
		harness = current;
		await current.session.bindExtensions({
			mode: "tui",
			onError: (error) => {
				throw new Error(JSON.stringify(error));
			},
			commandContextActions: {
				waitForIdle: () => current.session.agent.waitForIdle(),
				newSession: async () => ({ cancelled: false }),
				fork: async () => ({ cancelled: false }),
				navigateTree: async () => ({ cancelled: false }),
				switchSession: async () => ({ cancelled: false }),
				reload: async () => {},
			},
		});
		const skillDir = join(current.tempDir, "sure", "skills", "fixture");
		mkdirSync(skillDir, { recursive: true });
		writeFileSync(join(skillDir, "SKILL.md"), manual);
		writeFileSync(
			join(skillDir, "sure.skill.json"),
			JSON.stringify({ name: "fixture", command: "sure_feed", description: "Local fixture", prompt: "SKILL.md" }),
		);
		let sourceId = "";
		let manifestPath = "";
		current.setResponses([
			fauxAssistantMessage([fauxToolCall("inspect", {})], { stopReason: "toolUse" }),
			() => {
				const source = current.sessionManager
					.getBranch()
					.find(
						(entry) =>
							entry.type === "message" &&
							entry.message.role === "user" &&
							getMessageText(entry.message).startsWith("<sure_invocation"),
					);
				if (!source) throw new Error("Missing skill source");
				sourceId = source.id;
				const [runId] = readdirSync(join(current.tempDir, ".sure", "runs"));
				manifestPath = join(current.tempDir, ".sure", "runs", runId, "manifest.json");
				return fauxAssistantMessage(
					[
						fauxToolCall("sure_finish", {
							status: "success",
							manifest_path: manifestPath,
							summary: "Premature finish",
						}),
					],
					{ stopReason: "toolUse" },
				);
			},
			() => {
				expect(
					current.sessionManager
						.getBranch()
						.some((entry) => entry.type === "custom" && entry.customType === CONTEXT_HANDOFF_ENTRY),
				).toBe(false);
				const runId = readdirSync(join(current.tempDir, ".sure", "runs"))[0];
				writeFileSync(
					manifestPath,
					JSON.stringify({
						schema_version: "1",
						run_id: runId,
						skill_name: "fixture",
						status: "success",
						created_at: new Date().toISOString(),
						inputs: {},
						outputs: {},
						validation: {},
					}),
				);
				return fauxAssistantMessage(
					[
						fauxToolCall("sure_finish", {
							status: "success",
							manifest_path: manifestPath,
							summary: "Evidence validated",
						}),
					],
					{ stopReason: "toolUse" },
				);
			},
		]);
		await current.session.prompt("/sure_feed model=fixture");
		await current.session.agent.waitForIdle();
		expect(current.getPendingResponseCount()).toBe(0);
		const handoffs = current.sessionManager
			.getBranch()
			.filter((entry) => entry.type === "custom" && entry.customType === CONTEXT_HANDOFF_ENTRY);
		expect(handoffs).toHaveLength(1);
		current.setResponses([
			fauxAssistantMessage([fauxToolCall("inspect", {})], { stopReason: "toolUse" }),
			(context) => {
				const serialized = JSON.stringify(context.messages);
				expect(serialized).toContain("Completed skill handoff");
				expect(serialized).toContain("Evidence validated");
				expect(serialized).toContain(manifestPath);
				expect(
					context.messages.some(
						(message) => message.role === "user" && getMessageText(message).startsWith("<sure_invocation"),
					),
				).toBe(false);
				return fauxAssistantMessage([fauxToolCall(CONTEXT_RESTORE_TOOL, { turn_ids: [sourceId] })], {
					stopReason: "toolUse",
				});
			},
			(context) => {
				const restored = context.messages.find(
					(message) => message.role === "toolResult" && message.toolName === CONTEXT_RESTORE_TOOL,
				);
				expect(getMessageText(restored)).toContain(manual);
				expect(getMessageText(restored)).toContain("Historical snapshot; no tools were executed");
				return fauxAssistantMessage("Done");
			},
		]);
		await current.session.prompt("Continue with the next task");
		expect(current.getPendingResponseCount()).toBe(0);
		expect(executions).toBe(2);
		expect(
			current.session.messages.flatMap((message) =>
				message.role === "assistant" && message.errorMessage ? [message.errorMessage] : [],
			),
		).toEqual([]);
	});
});
