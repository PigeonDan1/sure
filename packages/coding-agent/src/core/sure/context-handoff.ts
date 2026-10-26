import { CONTEXT_RESTORE_TOOL, collectContextTurns } from "../context-folding.ts";
import type { ContextHandoff } from "../context-folding-handoff.ts";
import { CONTEXT_HANDOFF_ENTRY } from "../context-folding-handoff.ts";
import type { ExtensionFactory } from "../extensions/index.ts";

/** SURE supplies completion facts; the generic renderer owns retention and recovery. */
export const sureContextHandoffExtension: ExtensionFactory = (pi) => {
	pi.on("tool_result", (event, ctx) => {
		if (event.toolName !== "sure_finish" || event.isError || !pi.getActiveTools().includes(CONTEXT_RESTORE_TOOL))
			return;
		const details: unknown = event.details;
		if (
			!details ||
			typeof details !== "object" ||
			!("accepted" in details) ||
			details.accepted !== true ||
			!("runId" in details)
		)
			return;
		const branch = ctx.sessionManager.getBranch();
		const entry = [...branch]
			.reverse()
			.find(
				(candidate) =>
					candidate.type === "custom" &&
					candidate.customType === "sure.run" &&
					candidate.data &&
					typeof candidate.data === "object" &&
					"runId" in candidate.data &&
					candidate.data.runId === details.runId,
			);
		if (entry?.type !== "custom" || !entry.data || typeof entry.data !== "object") return;
		const run = entry.data as Record<string, unknown>;
		if (
			run.status !== "success" ||
			typeof run.summary !== "string" ||
			typeof run.command !== "string" ||
			typeof run.runId !== "string"
		)
			return;
		const messages = branch.filter((candidate) => candidate.type === "message");
		const sources = messages.flatMap((candidate, index) => {
			if (candidate.message.role !== "user") return [];
			const text =
				typeof candidate.message.content === "string"
					? candidate.message.content
					: candidate.message.content
							.filter((part) => part.type === "text")
							.map((part) => part.text)
							.join("\n");
			return text.startsWith(`<sure_invocation run_id="${run.runId}"`) ? [{ id: candidate.id, index }] : [];
		});
		const turns = collectContextTurns(branch);
		const artifactPaths = Array.isArray(run.artifacts)
			? run.artifacts.flatMap((artifact: unknown) =>
					artifact && typeof artifact === "object" && "path" in artifact && typeof artifact.path === "string"
						? [artifact.path]
						: [],
				)
			: [];
		const summary = [
			`/${run.command} run ${run.runId}: success.`,
			`Inputs: ${typeof run.args === "string" ? run.args : "(see manifest)"}`,
			`Result: ${run.summary}`,
			`Manifest: ${typeof run.manifestPath === "string" ? run.manifestPath : "(not recorded)"}`,
			...artifactPaths.map((path) => `Artifact: ${path}`),
		].join("\n");
		for (const [index, source] of sources.entries()) {
			const end = sources[index + 1]?.index ?? messages.length;
			pi.appendEntry(CONTEXT_HANDOFF_ENTRY, {
				sourceMessageId: source.id,
				turnIds: turns.filter((turn) => turn.start > source.index && turn.end < end).map((turn) => turn.id),
				completionToolCallId: event.toolCallId,
				summary,
			} satisfies ContextHandoff);
		}
	});
};
