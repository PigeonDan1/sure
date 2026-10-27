import type { ImageContent, TextContent } from "@earendil-works/pi-ai";
import { Type } from "typebox";
import {
	CONTEXT_RESTORE_TOOL,
	type ContextFoldingMetrics,
	type ContextFoldingOptions,
	collectContextTurns,
	renderContextFolding,
} from "./context-folding.ts";
import { collectContextHandoffs } from "./context-folding-handoff.ts";
import { defineTool, type ExtensionContext, type ExtensionFactory } from "./extensions/index.ts";

export interface ContextFoldingExtensionOptions extends ContextFoldingOptions {
	enabled?: boolean;
	getPinnedTurnIds?: (ctx: ExtensionContext) => ReadonlySet<string>;
}

export function createContextFoldingExtension(options: ContextFoldingExtensionOptions = {}): ExtensionFactory {
	return (pi) => {
		if (!(options.enabled ?? process.env.SURE_CONTEXT_FOLDING === "1")) return;
		const configuredChunk = Number(process.env.SURE_CONTEXT_FOLDING_CHUNK_SIZE);
		const chunkSize =
			options.chunkSize ??
			(Number.isSafeInteger(configuredChunk) && configuredChunk >= 1 && configuredChunk <= 64 ? configuredChunk : 3);
		let lastRender: ContextFoldingMetrics | undefined;
		const instructions = [
			"Context management: the harness preserves the original session and may fold older completed tool turns or replace exact duplicate outputs with references.",
			"Large tool turns carry [context-turn ID] labels. Begin tool-call text with optional PROGRESS: describing this step and STALE: listing any earlier completed turn IDs you no longer need, or '-' if none. STALE works without PROGRESS on either turn.",
			`You may mark older turns at any time. The harness keeps the most recent ${chunkSize} to ${2 * chunkSize - 1} complete tool turns in full. Keep unresolved errors, facts you still need, and the code you are about to edit.`,
			`Use ${CONTEXT_RESTORE_TOOL} with turn_ids to retrieve exact stored tool outputs and original tool arguments. Restoring reads a historical snapshot; re-running a tool observes current state. Restore only when the omitted content is needed.`,
		].join("\n");

		pi.on("before_agent_start", (event) => {
			if (!pi.getActiveTools().includes(CONTEXT_RESTORE_TOOL)) return undefined;
			return { systemPrompt: `${event.systemPrompt}\n\n${instructions}` };
		});
		pi.on("session_start", () => {
			lastRender = undefined;
		});
		pi.on("context", (event, ctx) => {
			if (!pi.getActiveTools().includes(CONTEXT_RESTORE_TOOL)) {
				lastRender = undefined;
				return undefined;
			}
			const result = renderContextFolding(event.messages, ctx.sessionManager.getBranch(), {
				...options,
				chunkSize,
				pinnedTurnIds: options.getPinnedTurnIds?.(ctx) ?? options.pinnedTurnIds,
			});
			lastRender = result.metrics;
			return { messages: result.messages };
		});
		pi.on("message_end", (event) => {
			if (event.message.role !== "assistant" || !lastRender) return;
			pi.appendEntry("context-folding.metrics", {
				version: 2,
				chunkSize,
				...lastRender,
				usage: event.message.usage,
			});
			lastRender = undefined;
		});
		pi.registerTool(
			defineTool({
				name: CONTEXT_RESTORE_TOOL,
				label: "Restore Context",
				description:
					"Read original tool calls and outputs, or completed skill instructions, from the current session branch without executing them.",
				parameters: Type.Object({ turn_ids: Type.Array(Type.String(), { minItems: 1, maxItems: 3 }) }),
				async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
					const branch = ctx.sessionManager.getBranch();
					const completeTurns = collectContextTurns(branch);
					const turns = new Map(completeTurns.map((turn) => [turn.id, turn]));
					const sourceIds = new Set(
						collectContextHandoffs(branch, completeTurns).map((handoff) => handoff.sourceMessageId),
					);
					const sources = new Map(
						branch.flatMap((entry) =>
							entry.type === "message" && entry.message.role === "user" && sourceIds.has(entry.id)
								? [[entry.id, entry.message] as const]
								: [],
						),
					);
					const missing = params.turn_ids.filter((id) => !turns.has(id) && !sources.has(id));
					if (missing.length > 0) {
						return {
							content: [
								{
									type: "text",
									text: `Unknown or incomplete turns in this session branch: ${missing.join(", ")}`,
								},
							],
							details: { turnIds: params.turn_ids },
							isError: true,
						};
					}
					const content: Array<TextContent | ImageContent> = [];
					for (const id of new Set(params.turn_ids)) {
						const source = sources.get(id);
						if (source) {
							content.push({
								type: "text",
								text: `[context-manager] Restored skill instructions ${id}. Historical snapshot; no tools were executed.`,
							});
							content.push(
								...(typeof source.content === "string"
									? [{ type: "text" as const, text: source.content }]
									: structuredClone(source.content)),
							);
							continue;
						}
						const turn = turns.get(id);
						if (!turn) continue;
						content.push({
							type: "text",
							text: `[context-manager] Restored turn ${id}. Historical snapshot; no tools were executed.`,
						});
						for (const call of turn.assistant.content) {
							if (call.type !== "toolCall") continue;
							content.push({
								type: "text",
								text: `Tool call ${call.id}: ${call.name} ${JSON.stringify(call.arguments)}`,
							});
						}
						for (const result of turn.results) {
							content.push({
								type: "text",
								text: `Original output ${result.toolCallId} (${result.toolName}, isError=${result.isError}):`,
							});
							content.push(...structuredClone(result.content));
						}
					}
					return { content, details: { turnIds: params.turn_ids } };
				},
			}),
		);
	};
}

export const contextFoldingExtension = createContextFoldingExtension();
