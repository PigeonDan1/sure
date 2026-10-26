import { fauxAssistantMessage, fauxToolCall } from "@earendil-works/pi-ai";
import { type FauxProviderRegistration, registerFauxProvider } from "@earendil-works/pi-ai/compat";
import { Type } from "typebox";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CONTEXT_RESTORE_TOOL, collectContextTurns } from "../../src/core/context-folding.ts";
import { createContextFoldingExtension } from "../../src/core/context-folding-extension.ts";
import { withDefaultExtensionFactories } from "../../src/core/default-extensions.ts";
import type { ExtensionFactory } from "../../src/core/extensions/index.ts";
import { createTestExtensionsResult, createTestResourceLoader } from "../utilities.ts";
import { createHarness, getMessageText, type Harness } from "./harness.ts";

const originalOutput = "Original tool evidence, with exact paths and versions.\n".repeat(100);

describe("session context folding integration", () => {
	const harnesses: Harness[] = [];
	const providers: FauxProviderRegistration[] = [];
	afterEach(() => {
		while (providers.length > 0) providers.pop()?.unregister();
		while (harnesses.length > 0) harnesses.pop()?.cleanup();
		vi.unstubAllEnvs();
	});

	it("folds across user turns and reloads, restores snapshots, and accounts for actual calls", async () => {
		let executions = 0;
		let firstTurn = "";
		const factories: ExtensionFactory[] = [
			createContextFoldingExtension({ enabled: true, chunkSize: 1, minSavingsCharacters: 0 }),
			(pi) => {
				pi.registerTool({
					name: "inspect",
					label: "Inspect",
					description: "Inspect local test evidence",
					parameters: Type.Object({ path: Type.String() }),
					execute: async () => {
						executions++;
						return {
							content: [{ type: "text", text: executions === 1 ? originalOutput : "Second tool result" }],
							details: {},
						};
					},
				});
				pi.on("tool_result", (event) => {
					if (event.toolName !== CONTEXT_RESTORE_TOOL) return;
					const output = event.content.find((part) => part.type === "text" && part.text === originalOutput);
					if (output?.type === "text") output.text += "\nExtension annotation";
				});
			},
		];
		let extensions = await createTestExtensionsResult(factories);
		const resourceLoader = createTestResourceLoader();
		resourceLoader.getExtensions = () => extensions;
		resourceLoader.reload = async () => {
			extensions = await createTestExtensionsResult(factories);
		};
		const harness = await createHarness({ resourceLoader });
		harnesses.push(harness);
		harness.setResponses([
			(context) => {
				expect(context.systemPrompt).toContain("PROGRESS:");
				return fauxAssistantMessage(
					[
						{ type: "text", text: "PROGRESS: Inspect first file\nSTALE: -" },
						fauxToolCall("inspect", { path: "first.txt" }),
					],
					{ stopReason: "toolUse" },
				);
			},
			(context) => {
				firstTurn = collectContextTurns(harness.sessionManager.getBranch())[0].id;
				expect(JSON.stringify(context.messages)).toContain(`[context-turn ${firstTurn}]`);
				return fauxAssistantMessage("First task finished");
			},
		]);
		await harness.session.prompt("First task instructions");
		await harness.session.reload();
		expect(harness.session.getActiveToolNames()).toContain("inspect");
		const resumed = registerFauxProvider({ api: harness.faux.api, provider: harness.getModel().provider });
		providers.push(resumed);
		resumed.setResponses([
			fauxAssistantMessage(
				[
					{ type: "text", text: `PROGRESS: Inspect second file\nSTALE: ${firstTurn}` },
					fauxToolCall("inspect", { path: "second.txt" }),
				],
				{ stopReason: "toolUse" },
			),
			(context) => {
				const serialized = JSON.stringify(context.messages);
				expect(serialized).toContain(`Turn ${firstTurn} folded`);
				expect(serialized).not.toContain(originalOutput.slice(0, 100));
				expect(serialized).toContain("First task instructions");
				expect(serialized).toContain("Second task instructions");
				return fauxAssistantMessage(
					[
						{ type: "text", text: "PROGRESS: Recover original evidence\nSTALE: -" },
						fauxToolCall(CONTEXT_RESTORE_TOOL, { turn_ids: [firstTurn] }),
					],
					{ stopReason: "toolUse" },
				);
			},
			(context) => {
				const restored = context.messages.find(
					(message) => message.role === "toolResult" && message.toolName === CONTEXT_RESTORE_TOOL,
				);
				expect(getMessageText(restored)).toContain("Historical snapshot; no tools were executed");
				expect(getMessageText(restored)).toContain(originalOutput);
				expect(getMessageText(restored)).toContain('"path":"first.txt"');
				return fauxAssistantMessage("Second task finished");
			},
		]);
		await harness.session.prompt("Second task instructions");
		expect(
			harness.session.messages.flatMap((message) =>
				message.role === "assistant" && message.errorMessage ? [message.errorMessage] : [],
			),
		).toEqual([]);
		expect(executions).toBe(2);
		expect(collectContextTurns(harness.sessionManager.getBranch())[0].results[0].content).toEqual([
			{ type: "text", text: originalOutput },
		]);
		const metrics = harness.sessionManager
			.getEntries()
			.filter((entry) => entry.type === "custom" && entry.customType === "context-folding.metrics");
		expect(metrics).toHaveLength(5);
		expect(
			metrics.some(
				(entry) =>
					entry.type === "custom" &&
					typeof entry.data === "object" &&
					entry.data !== null &&
					"foldedTurns" in entry.data &&
					entry.data.foldedTurns === 1,
			),
		).toBe(true);
		expect(resumed.getPendingResponseCount()).toBe(0);
	});

	it("rejects restore IDs outside the active session branch", async () => {
		const harness = await createHarness({ extensionFactories: [createContextFoldingExtension({ enabled: true })] });
		harnesses.push(harness);
		const root = harness.sessionManager.appendMessage({ role: "user", content: "Shared root", timestamp: 0 });
		const unrelated = harness.sessionManager.appendMessage(
			fauxAssistantMessage([fauxToolCall("inspect", { path: "sibling.txt" }, { id: "sibling-call" })], {
				stopReason: "toolUse",
			}),
		);
		harness.sessionManager.appendMessage({
			role: "toolResult",
			toolCallId: "sibling-call",
			toolName: "inspect",
			content: [{ type: "text", text: "Sibling branch evidence" }],
			isError: false,
			timestamp: 1,
		});
		harness.sessionManager.branch(root);
		harness.setResponses([
			fauxAssistantMessage([fauxToolCall(CONTEXT_RESTORE_TOOL, { turn_ids: [unrelated] })], {
				stopReason: "toolUse",
			}),
			(context) => {
				const result = context.messages.find((message) => message.role === "toolResult");
				expect(result?.role === "toolResult" && result.isError).toBe(true);
				expect(getMessageText(result)).toContain("Unknown or incomplete turns");
				return fauxAssistantMessage("No snapshot exists");
			},
		]);
		await harness.session.prompt("Restore unrelated history");
		expect(harness.sessionManager.getEntry(unrelated)).toBeDefined();
		expect(harness.sessionManager.getBranch().some((entry) => entry.id === unrelated)).toBe(false);
	});

	it("enables through the environment and falls back from an invalid chunk size", async () => {
		vi.stubEnv("SURE_CONTEXT_FOLDING", "1");
		vi.stubEnv("SURE_CONTEXT_FOLDING_CHUNK_SIZE", "invalid");
		const harness = await createHarness({ extensionFactories: [createContextFoldingExtension()] });
		harnesses.push(harness);
		expect(harness.session.getActiveToolNames()).toContain(CONTEXT_RESTORE_TOOL);
		harness.setResponses([
			(context) => {
				expect(context.systemPrompt).toContain("most recent 3 to 5 complete tool turns");
				return fauxAssistantMessage("Enabled");
			},
		]);
		await harness.session.prompt("Test opt-in configuration");
		expect(getMessageText(harness.session.messages.at(-1))).toBe("Enabled");
	});

	it("suspends folding when the restore tool is excluded", async () => {
		const harness = await createHarness({
			excludedToolNames: [CONTEXT_RESTORE_TOOL],
			extensionFactories: [createContextFoldingExtension({ enabled: true })],
		});
		harnesses.push(harness);
		expect(harness.session.getActiveToolNames()).not.toContain(CONTEXT_RESTORE_TOOL);
		harness.setResponses([
			(context) => {
				expect(context.systemPrompt).not.toContain("Context management:");
				return fauxAssistantMessage("Restore unavailable");
			},
		]);
		await harness.session.prompt("Keep history available");
		expect(getMessageText(harness.session.messages.at(-1))).toBe("Restore unavailable");
		expect(
			harness.sessionManager
				.getEntries()
				.filter((entry) => entry.type === "custom" && entry.customType === "context-folding.metrics"),
		).toEqual([]);
	});

	it("remains inactive by default and is registered in the default factories", async () => {
		vi.stubEnv("SURE_CONTEXT_FOLDING", "0");
		const harness = await createHarness({ extensionFactories: [createContextFoldingExtension()] });
		harnesses.push(harness);
		expect(harness.session.getAllTools().some((tool) => tool.name === CONTEXT_RESTORE_TOOL)).toBe(false);
		harness.setResponses([
			(context) => {
				expect(context.systemPrompt).not.toContain("Context management:");
				return fauxAssistantMessage("Unchanged");
			},
		]);
		await harness.session.prompt("Keep ordinary session behavior");
		expect(
			withDefaultExtensionFactories().some(
				(extension) => typeof extension !== "function" && extension.name === "context-folding",
			),
		).toBe(true);
	});
});
