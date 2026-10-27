import type { ContextTurn } from "./context-folding.ts";
import type { SessionEntry, SessionMessageEntry } from "./session-manager.ts";

export const CONTEXT_HANDOFF_ENTRY = "context-folding.handoff";

export interface ContextHandoff {
	sourceMessageId: string;
	turnIds: string[];
	completionToolCallId: string;
	summary: string;
}

/** Accept only declarations whose source, tool turns and successful completion are on this branch. */
export function collectContextHandoffs(
	entries: readonly SessionEntry[],
	turns: readonly ContextTurn[],
): ContextHandoff[] {
	const messages = entries.filter((entry): entry is SessionMessageEntry => entry.type === "message");
	const handoffs: ContextHandoff[] = [];
	for (const entry of entries) {
		if (entry.type !== "custom" || entry.customType !== CONTEXT_HANDOFF_ENTRY) continue;
		const data: unknown = entry.data;
		if (!data || typeof data !== "object") continue;
		const value = data as Record<string, unknown>;
		if (
			typeof value.sourceMessageId !== "string" ||
			typeof value.completionToolCallId !== "string" ||
			typeof value.summary !== "string" ||
			value.summary.trim() === "" ||
			!Array.isArray(value.turnIds) ||
			!value.turnIds.every((id): id is string => typeof id === "string")
		)
			continue;
		const sourceIndex = messages.findIndex((message) => message.id === value.sourceMessageId);
		const source = messages[sourceIndex]?.message;
		if (
			source?.role !== "user" ||
			(typeof source.content !== "string" && source.content.some((part) => part.type !== "text"))
		)
			continue;
		const completion = turns.find((turn) =>
			turn.results.some((result) => result.toolCallId === value.completionToolCallId && !result.isError),
		);
		if (!completion || completion.start <= sourceIndex) continue;
		const ids = value.turnIds;
		if (
			!ids.every((id) =>
				turns.some((turn) => turn.id === id && turn.start > sourceIndex && turn.end < completion.start),
			)
		)
			continue;
		handoffs.push({
			sourceMessageId: value.sourceMessageId,
			turnIds: ids,
			completionToolCallId: value.completionToolCallId,
			summary: value.summary,
		});
	}
	return handoffs;
}
