import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import { type ApproveMode, nextUnit, unitsForMode } from "./state-machine.ts";

export interface CheckpointData {
	mode: ApproveMode;
	currentUnit: string;
	completedUnits: string[];
	retries: Record<string, number>;
	/** Every gate block this run has taken. retries is per-unit and advance()
	 *  clears it, so it cannot answer "how blocked was this run". */
	blocks?: number;
	failedArtifactDigests: Record<string, string>;
}

export interface RunCheckpoint {
	id: string;
	label: string;
	resumable: boolean;
	resume_hint: string;
	data: CheckpointData;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function initialCheckpoint(mode: ApproveMode): RunCheckpoint {
	const first = unitsForMode(mode)[0];
	return {
		id: "approval_flow",
		label: "SURE approval state machine",
		resumable: true,
		resume_hint: `Start at unit "${first.id}".`,
		data: { mode, currentUnit: first.id, completedUnits: [], retries: {}, failedArtifactDigests: {} },
	};
}

export function readCheckpoint(ctx: SureHookContext, fallbackMode: ApproveMode): RunCheckpoint {
	const path = join(ctx.runDir, "state.json");
	if (!existsSync(path)) return initialCheckpoint(fallbackMode);
	try {
		const root: unknown = JSON.parse(readFileSync(path, "utf-8"));
		const checkpoint = isRecord(root) && isRecord(root.checkpoint) ? root.checkpoint : {};
		const data = isRecord(checkpoint.data) ? checkpoint.data : {};
		const mode: ApproveMode = data.mode === "approve" ? "approve" : fallbackMode;
		const initial = initialCheckpoint(mode);
		return {
			...initial,
			data: {
				mode,
				currentUnit: typeof data.currentUnit === "string" ? data.currentUnit : initial.data.currentUnit,
				blocks: typeof data.blocks === "number" ? data.blocks : undefined,
				completedUnits: Array.isArray(data.completedUnits)
					? data.completedUnits.filter((value): value is string => typeof value === "string")
					: [],
				retries: isRecord(data.retries)
					? Object.fromEntries(
							Object.entries(data.retries).filter(
								(entry): entry is [string, number] => typeof entry[1] === "number",
							),
						)
					: {},
				failedArtifactDigests: isRecord(data.failedArtifactDigests)
					? Object.fromEntries(
							Object.entries(data.failedArtifactDigests).filter(
								(entry): entry is [string, string] => typeof entry[1] === "string",
							),
						)
					: {},
			},
		};
	} catch {
		return initialCheckpoint(fallbackMode);
	}
}

export function advance(checkpoint: RunCheckpoint): RunCheckpoint {
	const current = checkpoint.data.currentUnit;
	const completedUnits = checkpoint.data.completedUnits.includes(current)
		? checkpoint.data.completedUnits
		: [...checkpoint.data.completedUnits, current];
	const next = nextUnit(checkpoint.data.mode, current);
	const retries = { ...checkpoint.data.retries };
	const failedArtifactDigests = { ...checkpoint.data.failedArtifactDigests };
	delete retries[current];
	delete failedArtifactDigests[current];
	return {
		...checkpoint,
		resumable: next !== undefined,
		resume_hint: next ? `Advance to unit "${next.id}".` : "State machine reached its terminal unit.",
		data: { ...checkpoint.data, currentUnit: next?.id ?? current, completedUnits, retries, failedArtifactDigests },
	};
}

export function bumpRetry(checkpoint: RunCheckpoint, artifactDigest: string): RunCheckpoint {
	const unit = checkpoint.data.currentUnit;
	const retries = { ...checkpoint.data.retries, [unit]: (checkpoint.data.retries[unit] ?? 0) + 1 };
	return {
		...checkpoint,
		resume_hint: `Retry unit "${unit}" (attempt ${retries[unit]}).`,
		data: {
			...checkpoint.data,
			retries,
			blocks: (checkpoint.data.blocks ?? 0) + 1,
			failedArtifactDigests: { ...checkpoint.data.failedArtifactDigests, [unit]: artifactDigest },
		},
	};
}

export function retryExhausted(checkpoint: RunCheckpoint, maxRetries = 3): boolean {
	return (checkpoint.data.retries[checkpoint.data.currentUnit] ?? 0) >= maxRetries;
}
