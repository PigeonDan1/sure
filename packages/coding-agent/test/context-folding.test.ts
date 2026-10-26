import type { AgentMessage } from "@earendil-works/pi-agent-core";
import { fauxAssistantMessage, fauxToolCall, type ImageContent, type TextContent } from "@earendil-works/pi-ai";
import { describe, expect, it } from "vitest";
import {
	CONTEXT_RESTORE_TOOL,
	type ContextFoldingOptions,
	collectContextTurns,
	renderContextFolding,
} from "../src/core/context-folding.ts";
import { CONTEXT_HANDOFF_ENTRY } from "../src/core/context-folding-handoff.ts";
import { SessionManager } from "../src/core/session-manager.ts";

const evidence = "Exact version, path, checksum, and validation evidence.\n".repeat(100);

function appendTurn(
	manager: SessionManager,
	output: string,
	stale: string[] = [],
	options: {
		toolName?: string;
		path?: string;
		isError?: boolean;
		content?: Array<TextContent | ImageContent>;
		addedToolNames?: string[];
		text?: string;
		secondTool?: boolean;
		incomplete?: boolean;
		signed?: boolean;
	} = {},
): string {
	const callId = `call-${manager.getEntries().length}`;
	const toolName = options.toolName ?? "read";
	const assistant = fauxAssistantMessage(
		[
			{
				type: "text",
				text: options.text ?? `PROGRESS: Inspect ${options.path ?? callId}\nSTALE: ${stale.join(", ") || "-"}`,
			},
			fauxToolCall(toolName, { path: options.path ?? callId }, { id: callId }),
			...(options.secondTool ? [fauxToolCall("read", { path: "second" }, { id: `${callId}-second` })] : []),
		],
		{ stopReason: "toolUse" },
	);
	if (options.signed) {
		assistant.content.unshift({
			type: "thinking",
			thinking: "Stored reasoning",
			thinkingSignature: "opaque-replay-data",
		});
	}
	const id = manager.appendMessage(assistant);
	manager.appendMessage({
		role: "toolResult",
		toolCallId: callId,
		toolName,
		content: options.content ?? [{ type: "text", text: output }],
		isError: options.isError ?? false,
		addedToolNames: options.addedToolNames,
		timestamp: 1,
	});
	if (options.secondTool && !options.incomplete) {
		manager.appendMessage({
			role: "toolResult",
			toolCallId: `${callId}-second`,
			toolName: "read",
			content: [{ type: "text", text: `Second output\n${evidence}` }],
			isError: false,
			timestamp: 1,
		});
	}
	return id;
}

function render(manager: SessionManager, options: ContextFoldingOptions = {}) {
	return renderContextFolding(manager.buildSessionContext().messages, manager.getBranch(), {
		minSavingsCharacters: 0,
		...options,
	});
}

function text(messages: AgentMessage[]): string {
	return messages
		.map((message) => {
			if (!("content" in message)) return JSON.stringify(message);
			if (typeof message.content === "string") return message.content;
			return message.content.map((part) => (part.type === "text" ? part.text : JSON.stringify(part))).join("\n");
		})
		.join("\n");
}

describe("reversible context folding", () => {
	it("folds complete stale turns after the chunk boundary without changing stored history", () => {
		const manager = SessionManager.inMemory();
		manager.appendMessage({ role: "user", content: "Keep the original task and constraints", timestamp: 0 });
		const first = appendTurn(manager, `First output\n${evidence}`);
		appendTurn(manager, `Second output\n${evidence}`, [first]);
		for (let index = 3; index <= 5; index++) appendTurn(manager, `Output ${index}\n${evidence}`);
		expect(render(manager).metrics.foldedTurns).toBe(0);
		appendTurn(manager, `Output 6\n${evidence}`);
		const original = JSON.stringify(manager.getEntries());
		const result = render(manager);
		expect(result.metrics.foldedTurns).toBe(1);
		expect(text(result.messages)).toContain(`Turn ${first} folded`);
		expect(text(result.messages)).not.toContain("First output");
		expect(text(result.messages)).toContain("Keep the original task and constraints");
		expect(result.metrics.renderedCharacters).toBeLessThan(result.metrics.originalCharacters);
		expect(JSON.stringify(manager.getEntries())).toBe(original);
		expect(collectContextTurns(manager.getBranch())[0].results[0].content).toEqual([
			{ type: "text", text: `First output\n${evidence}` },
		]);
	});

	it("keeps the rendered prefix identical between boundary advances", () => {
		const manager = SessionManager.inMemory();
		const first = appendTurn(manager, `First\n${evidence}`);
		appendTurn(manager, `Second\n${evidence}`, [first]);
		appendTurn(manager, `Third\n${evidence}`);
		const fourth = appendTurn(manager, `Fourth\n${evidence}`);
		appendTurn(manager, `Fifth\n${evidence}`, [fourth]);
		appendTurn(manager, `Sixth\n${evidence}`);
		const prefix = render(manager).messages;
		appendTurn(manager, `Seventh\n${evidence}`);
		expect(render(manager).messages.slice(0, prefix.length)).toEqual(prefix);
		manager.appendMessage({ role: "user", content: "Next skill", timestamp: 2 });
		appendTurn(manager, `Eighth\n${evidence}`);
		expect(render(manager).messages.slice(0, prefix.length)).toEqual(prefix);
		appendTurn(manager, `Ninth\n${evidence}`);
		expect(text(render(manager).messages)).toContain(`Turn ${fourth} folded`);
		expect(text(render(manager).messages)).toContain("Next skill");
	});

	it("reconstructs the same view from serialized history", () => {
		const manager = SessionManager.inMemory();
		const first = appendTurn(manager, evidence);
		appendTurn(manager, "A later result", [first]);
		const entries = JSON.parse(JSON.stringify(manager.getBranch()));
		const messages = JSON.parse(JSON.stringify(manager.buildSessionContext().messages));
		expect(renderContextFolding(messages, entries, { chunkSize: 1 })).toEqual(
			render(manager, { chunkSize: 1, minSavingsCharacters: 512 }),
		);
	});

	it("isolates rendered objects from the original archive", () => {
		const manager = SessionManager.inMemory();
		appendTurn(manager, evidence, [], { signed: true });
		const original = JSON.stringify(manager.getEntries());
		const result = render(manager);
		const assistant = result.messages.find((message) => message.role === "assistant");
		const output = result.messages.find((message) => message.role === "toolResult");
		if (assistant?.role !== "assistant" || output?.role !== "toolResult") throw new Error("Missing test turn");
		assistant.content.length = 0;
		output.content.length = 0;
		expect(JSON.stringify(manager.getEntries())).toBe(original);
	});

	it("accepts delayed release without requiring either turn to supply progress", () => {
		const manager = SessionManager.inMemory();
		const first = appendTurn(manager, evidence, [], { text: "", path: "old-evidence.txt" });
		for (let index = 2; index <= 4; index++) appendTurn(manager, `${index}\n${evidence}`);
		appendTurn(manager, "Fifth", [], { text: `\n\nSTALE: ${first}, future-id` });
		appendTurn(manager, "Sixth");
		const result = render(manager);
		expect(result.metrics.foldedTurns).toBe(1);
		expect(result.metrics.ignoredStaleReferences).toBe(1);
		expect(text(result.messages)).toContain('read(path="old-evidence.txt")');
		expect(text(result.messages)).not.toContain("undefined");
	});

	it("ignores metadata quoted after ordinary prose and preserves the protected recent chunk", () => {
		const manager = SessionManager.inMemory();
		const first = appendTurn(manager, evidence);
		appendTurn(manager, "Second", [], { text: `An ordinary answer\nSTALE: ${first}` });
		const third = appendTurn(manager, evidence);
		appendTurn(manager, "Fourth", [third]);
		appendTurn(manager, "Fifth");
		appendTurn(manager, "Sixth");
		const result = render(manager);
		expect(result.metrics.foldedTurns).toBe(1);
		expect(result.metrics.decisions).toContainEqual({ turnId: first, reason: "not_marked_stale" });
		expect(result.metrics.decisions).toContainEqual({ turnId: third, reason: "folded" });
	});

	it("removes a parallel tool batch atomically", () => {
		const manager = SessionManager.inMemory();
		const first = appendTurn(manager, evidence, [], { secondTool: true });
		appendTurn(manager, "Complete", [first]);
		const result = render(manager, { chunkSize: 1 });
		expect(result.metrics.foldedTurns).toBe(1);
		expect(result.messages.filter((message) => message.role === "toolResult")).toHaveLength(1);
		expect(result.messages.filter((message) => message.role === "assistant")).toHaveLength(1);
	});

	it("preserves incomplete tool batches and provider metadata", () => {
		const manager = SessionManager.inMemory();
		appendTurn(manager, evidence, [], { secondTool: true, incomplete: true });
		const original = manager.buildSessionContext().messages;
		expect(collectContextTurns(manager.getBranch())).toHaveLength(0);
		expect(render(manager, { chunkSize: 1 }).messages).toEqual(original);
	});

	it.each([
		{ isError: true },
		{ toolName: CONTEXT_RESTORE_TOOL },
		{ addedToolNames: ["new_tool"] },
		{ content: [{ type: "image" as const, data: "original-image", mimeType: "image/png" }] },
	])("protects errors, restored outputs, tool load points, and images: %j", (options) => {
		const manager = SessionManager.inMemory();
		const first = appendTurn(manager, evidence, [], options);
		appendTurn(manager, "Later", [first]);
		const result = render(manager, { chunkSize: 1 });
		expect(result.metrics.foldedTurns).toBe(0);
		expect(result.metrics.deduplicatedResults).toBe(0);
	});

	it("supports caller-provided pins without skill-specific logic", () => {
		const manager = SessionManager.inMemory();
		const first = appendTurn(manager, evidence);
		appendTurn(manager, "Later", [first]);
		expect(render(manager, { chunkSize: 1, pinnedTurnIds: new Set([first]) }).metrics.foldedTurns).toBe(0);
	});

	it("deduplicates exact outputs while preserving call/result pairs and signatures", () => {
		const manager = SessionManager.inMemory();
		const first = appendTurn(manager, evidence, [], { signed: true });
		const second = appendTurn(manager, evidence, [], { signed: true });
		appendTurn(manager, "Tail");
		const result = render(manager, { chunkSize: 1 });
		expect(result.metrics.deduplicatedResults).toBe(1);
		expect(text(result.messages)).toContain(`Output identical to turn ${first}`);
		expect(text(result.messages)).toContain(`turn_ids ["${second}"]`);
		const originals = manager.buildSessionContext().messages;
		expect(result.messages.filter((message) => message.role === "assistant")).toEqual(
			originals.filter((message) => message.role === "assistant"),
		);
		expect(collectContextTurns(manager.getBranch())[1].results[0].content).toEqual([
			{ type: "text", text: evidence },
		]);
	});

	it("deduplicates stable spans and preserves changes from repeated commands", () => {
		const manager = SessionManager.inMemory();
		appendTurn(manager, `Version one\n${evidence}`, [], { path: "same-file" });
		appendTurn(manager, `Version two\n${evidence}`, [], { path: "same-file" });
		appendTurn(manager, "Tail");
		const result = render(manager, { chunkSize: 1 });
		expect(result.metrics.deduplicatedResults).toBe(1);
		expect(result.metrics.deduplicatedBlocks).toBe(1);
		expect(text(result.messages)).toContain("Version two");
	});

	it("keeps a visible canonical copy when an earlier duplicate source was folded", () => {
		const manager = SessionManager.inMemory();
		const first = appendTurn(manager, evidence);
		appendTurn(manager, "Other", [first]);
		appendTurn(manager, evidence);
		appendTurn(manager, "Tail");
		const result = render(manager, { chunkSize: 1 });
		expect(result.metrics.foldedTurns).toBe(1);
		expect(result.metrics.deduplicatedResults).toBe(0);
		expect(
			result.messages.some(
				(message) =>
					message.role === "toolResult" &&
					message.content.some((part) => part.type === "text" && part.text === evidence),
			),
		).toBe(true);
	});

	it("protects re-executed commands after their earlier turn was folded", () => {
		const manager = SessionManager.inMemory();
		const first = appendTurn(manager, evidence, [], { path: "source.ts" });
		appendTurn(manager, "Other", [first]);
		const restored = appendTurn(manager, evidence, [], { path: "source.ts" });
		appendTurn(manager, "Later", [restored]);
		const result = render(manager, { chunkSize: 1 });
		expect(text(result.messages)).toContain(`Turn ${first} folded`);
		expect(text(result.messages)).not.toContain(`Turn ${restored} folded`);
		expect(text(result.messages)).toContain(evidence.slice(0, 100));
	});

	it("does not resurrect compacted messages or overwrite another context transform", () => {
		const manager = SessionManager.inMemory();
		const first = appendTurn(manager, evidence);
		appendTurn(manager, "Tail", [first]);
		const turns = collectContextTurns(manager.getBranch());
		const original = manager.buildSessionContext().messages;
		const changed = original.map(
			(message): AgentMessage =>
				message.role === "toolResult"
					? { ...message, content: [{ type: "text", text: "Changed by another extension" }] }
					: message,
		);
		expect(text(renderContextFolding(changed, manager.getBranch(), { chunkSize: 1 }).messages)).not.toContain(
			"folded",
		);
		const tail: AgentMessage[] = [
			{ role: "compactionSummary", summary: "Earlier work", tokensBefore: 5000, timestamp: 1 },
			turns[1].assistant,
			...turns[1].results,
		];
		const result = renderContextFolding(tail, manager.getBranch(), { chunkSize: 1 });
		expect(result.metrics.foldedTurns).toBe(0);
		expect(text(result.messages)).not.toContain(evidence.slice(0, 100));
		expect(collectContextTurns(manager.getBranch()).find((turn) => turn.id === first)).toBeDefined();
	});

	it("rejects invalid rendering settings", () => {
		expect(() => renderContextFolding([], [], { chunkSize: 0 })).toThrow("chunkSize");
		expect(() => renderContextFolding([], [], { minSavingsCharacters: -1 })).toThrow("minSavingsCharacters");
	});

	it("deduplicates overlapping reads without normalizing changed bytes or chaining references", () => {
		const manager = SessionManager.inMemory();
		const lines = Array.from(
			{ length: 40 },
			(_, index) => `line ${index}: ${"exact code and spaces ".repeat(8)}\r\n`,
		);
		const first = appendTurn(manager, `status=old\r\n${lines.join("")}old tail`, [], { text: "" });
		const second = appendTurn(manager, `status=new\r\n${lines.slice(5, 30).join("")}new tail`, [], { text: "" });
		appendTurn(manager, `status=third\r\n${lines.slice(10, 20).join("")}third tail`, [], { text: "" });
		appendTurn(manager, "Tail");
		const result = render(manager, { chunkSize: 1 });
		expect(result.metrics.deduplicatedBlocks).toBe(2);
		const rendered = text(result.messages);
		expect(rendered).toContain("status=new\r\n");
		expect(rendered).toContain("new tail");
		expect(rendered).toContain("third tail");
		expect(rendered).toContain(`identical to turn ${first}`);
		expect(rendered).not.toContain(`identical to turn ${second}`);
		expect(collectContextTurns(manager.getBranch())[1].results[0].content).toEqual([
			{ type: "text", text: `status=new\r\n${lines.slice(5, 30).join("")}new tail` },
		]);
	});

	it("keeps short outputs unchanged instead of adding larger labels or references", () => {
		const manager = SessionManager.inMemory();
		appendTurn(manager, "ok", [], { text: "" });
		appendTurn(manager, "ok", [], { text: "" });
		appendTurn(manager, "Tail", [], { text: "" });
		const original = manager.buildSessionContext().messages;
		expect(renderContextFolding(original, manager.getBranch(), { chunkSize: 1 }).messages).toEqual(original);
	});

	it("records whether an eligible release was protected or too small", () => {
		const manager = SessionManager.inMemory();
		const small = appendTurn(manager, "ok", [], { text: "" });
		const failed = appendTurn(manager, evidence, [small], { isError: true });
		appendTurn(manager, "Tail", [failed]);
		const result = render(manager, { chunkSize: 1, minSavingsCharacters: 512 });
		expect(result.metrics.decisions).toContainEqual({ turnId: small, reason: "insufficient_savings" });
		expect(result.metrics.decisions).toContainEqual({ turnId: failed, reason: "protected" });
	});

	it("folds completed skill instructions and unsigned tool turns from a verified handoff", () => {
		const manager = SessionManager.inMemory();
		manager.appendMessage({ role: "user", content: "Persistent user constraint", timestamp: 0 });
		const source = manager.appendMessage({
			role: "user",
			content: `Completed skill manual\n${evidence}`,
			timestamp: 0,
		});
		const first = appendTurn(manager, evidence, [], { text: "" });
		appendTurn(manager, "Success", [], { toolName: "finish", text: "" });
		const completion = collectContextTurns(manager.getBranch())[1].results[0].toolCallId;
		manager.appendCustomEntry(CONTEXT_HANDOFF_ENTRY, {
			sourceMessageId: source,
			turnIds: [first],
			completionToolCallId: completion,
			summary: "Validated handoff: model_input.yaml; unresolved issues: none.",
		});
		appendTurn(manager, "New task");
		const original = JSON.stringify(manager.getEntries());
		const result = render(manager, { chunkSize: 1 });
		expect(result.metrics.foldedTurns).toBe(1);
		expect(result.metrics.foldedInvocations).toBe(1);
		expect(text(result.messages)).not.toContain("Completed skill manual");
		expect(text(result.messages)).toContain("Persistent user constraint");
		expect(text(result.messages)).toContain("model_input.yaml");
		expect(text(result.messages)).toContain(`turn_ids ["${source}"]`);
		expect(JSON.stringify(manager.getEntries())).toBe(original);
	});

	it("rejects handoffs with failed completion or unknown branch IDs", () => {
		const manager = SessionManager.inMemory();
		const source = manager.appendMessage({ role: "user", content: evidence, timestamp: 0 });
		const first = appendTurn(manager, evidence, [], { text: "" });
		appendTurn(manager, "Failed", [], { toolName: "finish", isError: true });
		const completion = collectContextTurns(manager.getBranch())[1].results[0].toolCallId;
		manager.appendCustomEntry(CONTEXT_HANDOFF_ENTRY, {
			sourceMessageId: source,
			turnIds: [first],
			completionToolCallId: completion,
			summary: "Invalid handoff",
		});
		appendTurn(manager, "New task");
		const result = render(manager, { chunkSize: 1 });
		expect(result.metrics.foldedTurns).toBe(0);
		expect(result.metrics.foldedInvocations).toBe(0);
	});

	it("protects a fresh execution after a successful handoff released the earlier snapshot", () => {
		const manager = SessionManager.inMemory();
		const source = manager.appendMessage({ role: "user", content: evidence, timestamp: 0 });
		const first = appendTurn(manager, evidence, [], { path: "current.txt", text: "" });
		appendTurn(manager, "Success", [], { toolName: "finish", text: "" });
		const completion = collectContextTurns(manager.getBranch())[1].results[0].toolCallId;
		manager.appendCustomEntry(CONTEXT_HANDOFF_ENTRY, {
			sourceMessageId: source,
			turnIds: [first],
			completionToolCallId: completion,
			summary: "Validated handoff",
		});
		appendTurn(manager, "Next task");
		const fresh = appendTurn(manager, evidence, [], { path: "current.txt", text: "" });
		appendTurn(manager, "Later", [fresh]);
		const result = render(manager, { chunkSize: 1 });
		expect(result.metrics.decisions).toContainEqual({ turnId: first, reason: "folded" });
		expect(result.metrics.decisions).toContainEqual({ turnId: fresh, reason: "protected" });
		expect(text(result.messages)).toContain(evidence);
	});

	it("rejects a handoff that includes a turn outside its source and completion", () => {
		const manager = SessionManager.inMemory();
		const unrelated = appendTurn(manager, evidence);
		const source = manager.appendMessage({ role: "user", content: evidence, timestamp: 0 });
		appendTurn(manager, "Success", [], { toolName: "finish" });
		const completion = collectContextTurns(manager.getBranch())[1].results[0].toolCallId;
		manager.appendCustomEntry(CONTEXT_HANDOFF_ENTRY, {
			sourceMessageId: source,
			turnIds: [unrelated],
			completionToolCallId: completion,
			summary: "Invalid scope",
		});
		appendTurn(manager, "Tail");
		const result = render(manager, { chunkSize: 1 });
		expect(result.metrics.foldedInvocations).toBe(0);
		expect(result.metrics.foldedTurns).toBe(0);
	});

	it("defers a delayed release until the next boundary without changing its earlier prefix", () => {
		const manager = SessionManager.inMemory();
		const first = appendTurn(manager, evidence, [], { text: "" });
		for (let index = 2; index <= 6; index++) appendTurn(manager, `${index}\n${evidence}`);
		const prefix = render(manager).messages;
		appendTurn(manager, "Delayed release", [first]);
		expect(render(manager).messages.slice(0, prefix.length)).toEqual(prefix);
		appendTurn(manager, "Eighth");
		expect(render(manager).messages.slice(0, prefix.length)).toEqual(prefix);
		appendTurn(manager, "Ninth");
		expect(render(manager).metrics.decisions).toContainEqual({ turnId: first, reason: "folded" });
	});
});
