import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import {
	advanceLegacyUnit,
	bumpLegacyRetry,
	decodeLegacyState,
	initialLegacyCheckpoint,
	legacyRetryExhausted,
} from "../../../compatibility/legacy-v1/checkpoint.ts";
import { type ApproveMode, findUnit, unitsForMode, WORKFLOW_DEFINITION } from "./state-machine.ts";

export interface CheckpointData {
	mode: ApproveMode;
	currentUnit: string;
	completedUnits: string[];
	retries: Record<string, number>;
	/** Every gate block this run has taken. */
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

const LABEL = "SURE approval state machine";

function projection(mode: ApproveMode) {
	return {
		definition: WORKFLOW_DEFINITION,
		branchId: mode,
		id: "approval_flow",
		label: LABEL,
		mode,
		includeFailedArtifactDigests: true,
	} as const;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function modeFromState(raw: unknown, fallback: ApproveMode): ApproveMode {
	if (!isRecord(raw)) return fallback;
	const checkpoint = isRecord(raw.checkpoint) ? raw.checkpoint : undefined;
	const data = checkpoint && isRecord(checkpoint.data) ? checkpoint.data : undefined;
	return data?.mode === "approve" ? "approve" : data?.mode === "audit" ? "audit" : fallback;
}

function readJson(path: string): unknown {
	return JSON.parse(readFileSync(path, "utf8")) as unknown;
}

export function initialCheckpoint(mode: ApproveMode): RunCheckpoint {
	return initialLegacyCheckpoint(projection(mode)) as RunCheckpoint;
}

export function readCheckpoint(ctx: SureHookContext, fallbackMode: ApproveMode): RunCheckpoint {
	const path = join(ctx.runDir, "state.json");
	if (!existsSync(path)) return initialCheckpoint(fallbackMode);
	try {
		const raw = readJson(path);
		const mode = modeFromState(raw, fallbackMode);
		return decodeLegacyState(projection(mode), raw) as RunCheckpoint;
	} catch {
		return initialCheckpoint(fallbackMode);
	}
}

export function advance(checkpoint: RunCheckpoint): RunCheckpoint {
	const mode = checkpoint.data.mode;
	const next = advanceLegacyUnit(projection(mode), checkpoint.data.currentUnit, checkpoint.data);
	if (next === undefined) {
		// Preserve the legacy total-terminal call behavior if a caller advances
		// an already terminal checkpoint.
		return { ...checkpoint, resumable: false, resume_hint: "State machine reached its terminal unit." };
	}
	return next as RunCheckpoint;
}

export function bumpRetry(checkpoint: RunCheckpoint, artifactDigest: string): RunCheckpoint {
	return bumpLegacyRetry(
		projection(checkpoint.data.mode),
		checkpoint.data.currentUnit,
		checkpoint.data,
		artifactDigest,
	) as RunCheckpoint;
}

export function retryExhausted(checkpoint: RunCheckpoint, maxRetries = 3): boolean {
	return legacyRetryExhausted(
		projection(checkpoint.data.mode),
		checkpoint.data.currentUnit,
		checkpoint.data,
		maxRetries,
	);
}

export { findUnit, unitsForMode };
