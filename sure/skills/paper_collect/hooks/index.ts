import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { SureHookContext, SureHookResult } from "@earendil-works/pi-coding-agent/hooks";

interface PaperCollection {
	target_count?: unknown;
	collected_count?: unknown;
	papers?: unknown;
}

interface PaperRecord {
	id?: unknown;
	title?: unknown;
	year?: unknown;
	authors?: unknown;
	source?: unknown;
	source_rank?: unknown;
	dedupe_key?: unknown;
	download_status?: unknown;
}

function readJson(path: string): unknown {
	return JSON.parse(readFileSync(path, "utf-8"));
}

function paperCollectionPath(ctx: SureHookContext): string {
	return join(ctx.runDir, "artifacts", "papers.manifest.json");
}

function failure(repair: string, message: string, counters?: Record<string, number>): SureHookResult {
	return {
		ok: false,
		repair,
		state_patch: {
			phase: { id: "validate", label: "Validating paper collection", status: "blocked" },
			message,
			counters,
			diagnostics: [{ severity: "error", message, repair }],
		},
	};
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getPapers(collection: PaperCollection): PaperRecord[] | undefined {
	if (!Array.isArray(collection.papers)) {
		return undefined;
	}
	return collection.papers.filter(isRecord);
}

function hasRequiredPaperFields(paper: PaperRecord): boolean {
	return (
		typeof paper.id === "string" &&
		typeof paper.title === "string" &&
		typeof paper.year === "number" &&
		Array.isArray(paper.authors) &&
		typeof paper.source === "string" &&
		typeof paper.source_rank === "number" &&
		typeof paper.dedupe_key === "string" &&
		typeof paper.download_status === "string"
	);
}

export function preStart(ctx: SureHookContext): SureHookResult {
	const scriptPath = join(ctx.packageDir, "scripts", "paper_collect.mjs");
	if (!existsSync(scriptPath)) {
		return failure("Restore scripts/paper_collect.mjs before running /paper_collect.", "Missing collection script.");
	}
	return {
		ok: true,
		state_patch: {
			phase: { id: "start", label: "Preparing paper collection", status: "running" },
			message: "Paper collection skill package loaded.",
		},
	};
}

export function postToolResult(ctx: SureHookContext): SureHookResult {
	const event = isRecord(ctx.event) ? ctx.event : {};
	if (event.isError === true) {
		return {
			ok: true,
			state_patch: {
				phase: { id: "tool_result", label: "Inspecting tool result", status: "blocked" },
				diagnostics: [
					{
						severity: "warning",
						message: "A tool call returned an error during paper collection.",
						repair: "Inspect the tool output, repair the command or artifact, and continue.",
					},
				],
			},
		};
	}
	return { ok: true };
}

export function preFinish(ctx: SureHookContext): SureHookResult {
	const path = paperCollectionPath(ctx);
	if (!existsSync(path)) {
		return failure(
			"Run scripts/paper_collect.mjs and create artifacts/papers.manifest.json before sure_finish.",
			"Missing paper collection artifact.",
		);
	}

	let collection: PaperCollection;
	try {
		collection = readJson(path) as PaperCollection;
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		return failure(
			"Repair artifacts/papers.manifest.json so it is valid JSON.",
			`Invalid paper collection JSON: ${message}`,
		);
	}

	const papers = getPapers(collection);
	if (!papers) {
		return failure(
			"Write a papers array to artifacts/papers.manifest.json.",
			"Paper collection artifact is missing a papers array.",
		);
	}

	const targetCount = typeof collection.target_count === "number" ? collection.target_count : 1;
	const collectedCount = typeof collection.collected_count === "number" ? collection.collected_count : papers.length;
	const counters = { collected_papers: collectedCount, target_papers: targetCount };

	if (papers.length !== collectedCount) {
		return failure(
			"Make collected_count match the papers array length.",
			"Paper collection count does not match papers array length.",
			counters,
		);
	}

	const dedupeKeys = new Set<string>();
	for (const paper of papers) {
		if (!hasRequiredPaperFields(paper)) {
			return failure(
				"Fill required paper fields: id, title, year, authors, source, source_rank, dedupe_key, download_status.",
				"At least one paper is missing required fields.",
				counters,
			);
		}
		const dedupeKey = paper.dedupe_key as string;
		if (dedupeKeys.has(dedupeKey)) {
			return failure(
				"Remove duplicate papers before finishing.",
				`Duplicate paper dedupe_key found: ${dedupeKey}`,
				counters,
			);
		}
		dedupeKeys.add(dedupeKey);
	}

	const finishStatus = isRecord(ctx.event) && isRecord(ctx.event.finish) ? ctx.event.finish.status : undefined;
	if (finishStatus === "success" && collectedCount < targetCount) {
		return failure(
			"Collect more papers or call sure_finish with status incomplete.",
			"Collected paper count is below target.",
			counters,
		);
	}

	return {
		ok: true,
		state_patch: {
			phase: { id: "validate", label: "Paper collection validated", status: "success", progress: 1 },
			message: `Validated ${collectedCount} papers for target ${targetCount}.`,
			counters,
			artifacts: [
				{
					type: "paper_collection",
					name: "Paper collection manifest",
					path: `.sure/runs/${ctx.run.runId}/artifacts/papers.manifest.json`,
					status: "ready",
					summary: `${collectedCount} collected papers`,
				},
			],
		},
	};
}

export function postFinish(ctx: SureHookContext): SureHookResult {
	return {
		ok: true,
		state_patch: {
			phase: { id: "finish", label: "Paper collection finished", status: ctx.run.status, progress: 1 },
			message: ctx.run.summary ?? "Paper collection run finished.",
		},
	};
}

export function onError(ctx: SureHookContext): SureHookResult {
	return {
		ok: true,
		state_patch: {
			phase: { id: "error", label: "Paper collection interrupted", status: "failed" },
			message: ctx.run.errorSummary ?? ctx.run.lastRepair ?? "Paper collection stopped before completion.",
		},
	};
}
