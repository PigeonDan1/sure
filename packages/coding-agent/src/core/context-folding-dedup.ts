import { createHash } from "node:crypto";
import type { TextContent, ToolResultMessage } from "@earendil-works/pi-ai";

const WINDOW_LINES = 8;

interface OutputSource {
	turnId: string;
	toolCallId: string;
}

interface BlockSource extends OutputSource {
	lines: string[];
	start: number;
	part: number;
}

export interface DeduplicatedOutput {
	content: ToolResultMessage<unknown>["content"];
	changed: boolean;
	blocks: number;
}

/** Each renderer owns an index of raw output spans that remain visible in its prefix. */
export class ContextOutputIndex {
	private outputs = new Map<string, OutputSource>();
	private blocks = new Map<string, BlockSource>();
	private minimumSaving: number;

	constructor(minimumSaving: number) {
		this.minimumSaving = minimumSaving;
	}

	private blockKey(toolName: string, lines: string[], start: number): string {
		return createHash("sha256")
			.update(JSON.stringify([toolName, lines.slice(start, start + WINDOW_LINES)]))
			.digest("hex");
	}

	render(
		result: ToolResultMessage<unknown>,
		turnId: string,
		canCompress: boolean,
		canBeSource: boolean,
	): DeduplicatedOutput {
		if (!canBeSource) return { content: result.content, changed: false, blocks: 0 };
		const key = createHash("sha256")
			.update(JSON.stringify([result.toolName, result.content]))
			.digest("hex");
		const source = this.outputs.get(key);
		const stub = source
			? `[context-manager] Output identical to turn ${source.turnId}, tool call ${source.toolCallId}. Restore this snapshot: context_restore turn_ids ["${turnId}"].`
			: undefined;
		const originalLength = result.content.reduce(
			(sum, part) => sum + (part.type === "text" ? part.text.length : 0),
			0,
		);
		if (canCompress && stub && originalLength > stub.length + this.minimumSaving) {
			return { content: [{ type: "text", text: stub }], changed: true, blocks: 0 };
		}
		const content: TextContent[] = [];
		const visibleSpans: Array<{ lines: string[]; start: number; end: number; part: number }> = [];
		let blocks = 0;
		for (const [partIndex, part] of result.content.entries()) {
			if (part.type !== "text") return { content: result.content, changed: false, blocks: 0 };
			// Preserve every byte, including CRLF and a missing final newline.
			const lines = part.text.match(/[^\n]*\n|[^\n]+$/g) ?? [];
			let rawStart = 0;
			let index = 0;
			while (canCompress && index + WINDOW_LINES <= lines.length) {
				const match = this.blocks.get(this.blockKey(result.toolName, lines, index));
				if (!match) {
					index++;
					continue;
				}
				let length = WINDOW_LINES;
				while (index + length < lines.length && lines[index + length] === match.lines[match.start + length])
					length++;
				const note = `[context-manager] Lines ${index + 1}-${index + length} identical to turn ${match.turnId}, tool call ${match.toolCallId}, text part ${match.part + 1}, lines ${match.start + 1}-${match.start + length}. Restore this snapshot: context_restore turn_ids ["${turnId}"].\n`;
				if (lines.slice(index, index + length).join("").length <= note.length + this.minimumSaving) {
					index++;
					continue;
				}
				if (rawStart < index) {
					content.push({ type: "text", text: lines.slice(rawStart, index).join("") });
					visibleSpans.push({ lines, start: rawStart, end: index, part: partIndex });
				}
				content.push({ type: "text", text: note });
				index += length;
				rawStart = index;
				blocks++;
			}
			if (rawStart < lines.length || lines.length === 0) {
				content.push({ type: "text", text: lines.slice(rawStart).join("") });
				visibleSpans.push({ lines, start: rawStart, end: lines.length, part: partIndex });
			}
		}
		// Never index omitted spans or make a reference point to another reference.
		for (const span of visibleSpans) {
			const retainedLines = span.end === span.lines.length ? span.lines : span.lines.slice(0, span.end);
			for (let index = span.start; index + WINDOW_LINES <= span.end; index++) {
				const blockKey = this.blockKey(result.toolName, span.lines, index);
				if (!this.blocks.has(blockKey)) {
					// Limit extension to the retained span, even if its raw result had later omissions.
					this.blocks.set(blockKey, {
						turnId,
						toolCallId: result.toolCallId,
						lines: retainedLines,
						start: index,
						part: span.part,
					});
				}
			}
		}
		if (blocks === 0 && !source) this.outputs.set(key, { turnId, toolCallId: result.toolCallId });
		return { content: blocks > 0 ? content : result.content, changed: blocks > 0, blocks };
	}
}
