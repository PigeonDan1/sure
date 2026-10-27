import type { AgentMessage } from "@earendil-works/pi-agent-core";
import type { AssistantMessage, ToolCall, ToolResultMessage } from "@earendil-works/pi-ai";
import { ContextOutputIndex } from "./context-folding-dedup.ts";
import { collectContextHandoffs } from "./context-folding-handoff.ts";
import { convertToLlm } from "./messages.ts";
import type { SessionEntry, SessionMessageEntry } from "./session-manager.ts";

export const CONTEXT_RESTORE_TOOL = "context_restore";

export interface ContextFoldingOptions {
	chunkSize?: number;
	minSavingsCharacters?: number;
	pinnedTurnIds?: ReadonlySet<string>;
}

export interface ContextTurn {
	id: string;
	assistant: AssistantMessage;
	results: ToolResultMessage<unknown>[];
	progress?: string;
	start: number;
	end: number;
}

export interface ContextFoldingMetrics {
	completedTurns: number;
	boundaryTurnId?: string;
	foldedTurns: number;
	deduplicatedResults: number;
	deduplicatedBlocks: number;
	foldedInvocations: number;
	staleReferences: number;
	ignoredStaleReferences: number;
	decisions: Array<{ turnId: string; reason: string }>;
	originalCharacters: number;
	renderedCharacters: number;
}

export interface ContextFoldingResult {
	messages: AgentMessage[];
	metrics: ContextFoldingMetrics;
}

function signals(message: AssistantMessage): { progress?: string; stale: string[] } {
	const lines = message.content
		.filter((part) => part.type === "text")
		.map((part) => part.text)
		.join("\n")
		.trimStart()
		.split(/\r?\n/, 3);
	if (!/^(?:PROGRESS|STALE):/.test(lines[0] ?? "")) return { stale: [] };
	const progress = /^PROGRESS:\s*(\S[^\r\n]{0,239})$/.exec(lines[0] ?? "")?.[1];
	const staleLine = /^STALE:\s*([^\r\n]{0,512})$/.exec(lines[progress ? 1 : 0] ?? "")?.[1];
	const stale = staleLine ? staleLine.split(",").map((value) => value.trim()) : [];
	return { progress, stale: stale.filter((value) => /^[A-Za-z0-9_-]{1,128}$/.test(value) && value !== "-") };
}

function messageEntries(entries: readonly SessionEntry[]): SessionMessageEntry[] {
	return entries.filter((entry): entry is SessionMessageEntry => entry.type === "message");
}

export function collectContextTurns(entries: readonly SessionEntry[]): ContextTurn[] {
	const messages = messageEntries(entries);
	const turns: ContextTurn[] = [];
	for (let start = 0; start < messages.length; start++) {
		const entry = messages[start];
		if (entry.message.role !== "assistant" || entry.message.stopReason !== "toolUse") continue;
		const calls = entry.message.content.filter((part) => part.type === "toolCall");
		const expected = new Map(calls.map((call) => [call.id, call.name]));
		if (calls.length === 0 || expected.size !== calls.length) continue;
		const results: ToolResultMessage<unknown>[] = [];
		let end = start + 1;
		while (end < messages.length && messages[end].message.role === "toolResult") {
			const result = messages[end].message;
			if (result.role !== "toolResult" || expected.get(result.toolCallId) !== result.toolName) break;
			expected.delete(result.toolCallId);
			results.push(result);
			end++;
		}
		if (expected.size > 0 || messages[end]?.message.role === "toolResult") continue;
		turns.push({
			id: entry.id,
			assistant: entry.message,
			results,
			progress: signals(entry.message).progress,
			start,
			end: end - 1,
		});
	}
	return turns;
}

function compressible(turn: ContextTurn, pinned: ReadonlySet<string>): boolean {
	return (
		!pinned.has(turn.id) &&
		turn.results.every(
			(result) =>
				result.toolName !== CONTEXT_RESTORE_TOOL &&
				!result.isError &&
				!result.addedToolNames?.length &&
				result.content.every((part) => part.type === "text"),
		)
	);
}

function contentCharacters(messages: AgentMessage[]): number {
	let characters = 0;
	for (const message of convertToLlm(messages)) {
		if (typeof message.content === "string") {
			characters += message.content.length;
			continue;
		}
		for (const part of message.content) {
			if (part.type === "text") characters += part.text.length;
			else if (part.type === "thinking") characters += part.thinking.length;
			else if (part.type === "toolCall") characters += part.name.length + JSON.stringify(part.arguments).length;
		}
	}
	return characters;
}

function turnMessages(turn: ContextTurn): AgentMessage[] {
	return [turn.assistant, ...turn.results];
}

function sameTurn(messages: AgentMessage[], start: number, turn: ContextTurn): boolean {
	const original = turnMessages(turn);
	return original.every((message, offset) => JSON.stringify(message) === JSON.stringify(messages[start + offset]));
}

function calls(message: AssistantMessage): ToolCall[] {
	return message.content.filter((part) => part.type === "toolCall");
}

function turnIndexNote(turn: ContextTurn): string {
	return calls(turn.assistant)
		.map((call) => {
			const parameters = Object.entries(call.arguments)
				.filter(
					([key, value]) =>
						key !== "content" && key !== "newText" && key !== "oldText" && typeof value !== "object",
				)
				.map(([key, value]) => `${key}=${JSON.stringify(value)}`)
				.join(", ");
			return `${call.name}(${parameters.length > 240 ? `${parameters.slice(0, 240)}… [argument preview]` : parameters})`;
		})
		.join("; ");
}

export function renderContextFolding(
	messages: AgentMessage[],
	entries: readonly SessionEntry[],
	options: ContextFoldingOptions = {},
): ContextFoldingResult {
	const chunkSize = options.chunkSize ?? 3;
	const minimumSaving = options.minSavingsCharacters ?? 512;
	if (!Number.isSafeInteger(chunkSize) || chunkSize < 1 || chunkSize > 64) {
		throw new Error("Context folding chunkSize must be an integer from 1 to 64.");
	}
	if (!Number.isSafeInteger(minimumSaving) || minimumSaving < 0) {
		throw new Error("Context folding minSavingsCharacters must be a nonnegative integer.");
	}
	const turns = collectContextTurns(entries);
	const anchor = Math.floor(turns.length / chunkSize) * chunkSize;
	const boundary = Math.max(0, anchor - chunkSize);
	const cutoff = anchor > 0 ? turns[anchor - 1].end : -1;
	const markedAt = new Map<string, number>();
	const archivedMessages = messageEntries(entries);
	const turnsById = new Map(turns.map((turn) => [turn.id, turn]));
	let staleReferences = 0;
	let ignoredStaleReferences = 0;
	for (let index = 0; index < archivedMessages.length; index++) {
		const message = archivedMessages[index].message;
		if (message.role !== "assistant") continue;
		for (const turnId of signals(message).stale) {
			staleReferences++;
			const target = turnsById.get(turnId);
			if (!target || target.end >= index) ignoredStaleReferences++;
			else if (!markedAt.has(turnId)) markedAt.set(turnId, index);
		}
	}
	const declaredHandoffs = collectContextHandoffs(entries, turns);
	for (const handoff of declaredHandoffs) {
		const completion = turns.find((turn) =>
			turn.results.some((result) => result.toolCallId === handoff.completionToolCallId),
		);
		if (!completion) continue;
		for (const id of handoff.turnIds) {
			markedAt.set(id, Math.min(markedAt.get(id) ?? Number.POSITIVE_INFINITY, completion.end));
		}
	}
	const stale = new Set([...markedAt].filter(([, index]) => index <= cutoff).map(([id]) => id));
	const handoffs = declaredHandoffs.filter((handoff) => {
		const completion = turns.find((turn) =>
			turn.results.some((result) => result.toolCallId === handoff.completionToolCallId),
		);
		return completion && turns.indexOf(completion) < boundary;
	});
	const pinned = new Set(options.pinnedTurnIds);
	const previousCalls = new Map<string, { turn: ContextTurn; index: number }>();
	for (const [index, turn] of turns.entries()) {
		const key = JSON.stringify(calls(turn.assistant).map((call) => [call.name, call.arguments]));
		const previous = previousCalls.get(key);
		const previousAnchor = Math.floor(index / chunkSize) * chunkSize;
		const previousBoundary = Math.max(0, previousAnchor - chunkSize);
		const previousCutoff = previousAnchor > 0 ? turns[previousAnchor - 1].end : -1;
		if (
			previous &&
			previous.index < previousBoundary &&
			(markedAt.get(previous.turn.id) ?? Number.POSITIVE_INFINITY) <= previousCutoff
		) {
			pinned.add(turn.id);
		}
		if (compressible(turn, pinned)) previousCalls.set(key, { turn, index });
	}
	const eligible = new Set(turns.slice(0, boundary).map((turn) => turn.id));
	const byFirstCall = new Map(turns.map((turn) => [calls(turn.assistant)[0].id, turn]));
	const outputIndex = new ContextOutputIndex(minimumSaving);
	const invocationReplacements = new Map(
		handoffs.map((handoff) => [
			archivedMessages.find((entry) => entry.id === handoff.sourceMessageId)?.message,
			{ id: handoff.sourceMessageId, summary: handoff.summary },
		]),
	);
	const rendered: AgentMessage[] = [];
	let foldedTurns = 0;
	let deduplicatedResults = 0;
	let deduplicatedBlocks = 0;
	let foldedInvocations = 0;
	const decisions: ContextFoldingMetrics["decisions"] = [];
	for (let index = 0; index < messages.length; index++) {
		const message = messages[index];
		// Match by exact stored value so an earlier context transform always takes precedence.
		const invocation = [...invocationReplacements].find(
			([original]) => original && JSON.stringify(original) === JSON.stringify(message),
		)?.[1];
		if (invocation) {
			const note = `[context-manager] Completed skill handoff ${invocation.id}.\n${invocation.summary}\nOriginal instructions: context_restore turn_ids ["${invocation.id}"].`;
			if (contentCharacters([message]) > note.length + minimumSaving) {
				rendered.push({
					role: "custom",
					customType: "context-folding",
					content: note,
					display: false,
					timestamp: message.timestamp,
				});
				foldedInvocations++;
				continue;
			}
		}
		const firstCall = message.role === "assistant" ? calls(message)[0] : undefined;
		const turn = firstCall ? byFirstCall.get(firstCall.id) : undefined;
		if (!turn || !sameTurn(messages, index, turn)) {
			if (turn) decisions.push({ turnId: turn.id, reason: "context_transformed" });
			rendered.push(message);
			continue;
		}
		const canCompress = eligible.has(turn.id) && compressible(turn, pinned);
		const turnCharacters = contentCharacters(turnMessages(turn));
		const note = `[context-manager] Turn ${turn.id} folded: ${turn.progress ?? "Released tool snapshot"}. Tools: ${turnIndexNote(turn)}. Use ${CONTEXT_RESTORE_TOOL} with turn_ids ["${turn.id}"] to read its stored tool calls and outputs.`;
		if (canCompress && stale.has(turn.id) && turnCharacters > note.length + minimumSaving) {
			rendered.push({
				role: "custom",
				customType: "context-folding",
				content: note,
				display: false,
				timestamp: turn.assistant.timestamp,
			});
			foldedTurns++;
			decisions.push({ turnId: turn.id, reason: "folded" });
		} else {
			decisions.push({
				turnId: turn.id,
				reason: !compressible(turn, pinned)
					? "protected"
					: !eligible.has(turn.id)
						? "recent"
						: !stale.has(turn.id)
							? "not_marked_stale"
							: "insufficient_savings",
			});
			rendered.push(message);
			for (const result of turn.results) {
				const duplicate = outputIndex.render(result, turn.id, canCompress, compressible(turn, pinned));
				const label =
					turnCharacters > minimumSaving + 256 && compressible(turn, pinned)
						? [{ type: "text" as const, text: `[context-turn ${turn.id}]` }]
						: [];
				rendered.push({
					...result,
					content: [...label, ...duplicate.content],
				});
				if (duplicate.changed) deduplicatedResults++;
				deduplicatedBlocks += duplicate.blocks;
			}
		}
		index += turn.results.length;
	}
	return {
		messages: structuredClone(rendered),
		metrics: {
			completedTurns: turns.length,
			boundaryTurnId: boundary > 0 ? turns[boundary - 1].id : undefined,
			foldedTurns,
			deduplicatedResults,
			deduplicatedBlocks,
			foldedInvocations,
			staleReferences,
			ignoredStaleReferences,
			decisions,
			originalCharacters: contentCharacters(messages),
			renderedCharacters: contentCharacters(rendered),
		},
	};
}
