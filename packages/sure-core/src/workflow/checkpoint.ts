import type { WorkflowBranch, WorkflowCheckpoint, WorkflowCheckpointData, WorkflowDefinition } from "./types.ts";

export interface LegacyCheckpointEnvelope {
	checkpoint?: { data?: unknown };
}

export interface LegacyCheckpointCodecOptions<TMemory = unknown> {
	id: string;
	label: string;
	branch_id: string;
	include_failed_artifact_digests: boolean;
	mode?: string;
	decode_memory?: (value: unknown) => TMemory | undefined;
}

export interface DecodedCheckpoint<TMemory = unknown> {
	data: WorkflowCheckpointData<TMemory>;
	branch_id: string;
	had_failed_artifact_digests: boolean;
	had_memory: boolean;
	legacy_mode?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finiteNonNegative(value: unknown): number | undefined {
	return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
}

export function branchFor(definition: WorkflowDefinition, branchId: string): WorkflowBranch {
	const branch = definition.branches.find((candidate) => candidate.id === branchId);
	if (!branch) throw new Error(`Unknown workflow branch: ${branchId}`);
	return branch;
}

export function initialCheckpoint<TMemory = unknown>(
	definition: WorkflowDefinition,
	branchId = definition.default_branch_id,
	options?: Partial<Pick<LegacyCheckpointCodecOptions<TMemory>, "id" | "label" | "mode">>,
): WorkflowCheckpoint<TMemory> {
	const branch = branchFor(definition, branchId);
	return {
		id: options?.id ?? definition.checkpoint_id ?? "main_flow",
		label: options?.label ?? definition.checkpoint_label ?? definition.workflow_id,
		resumable: true,
		resume_hint: `Start at unit "${branch.initial_unit_id}".`,
		branch_id: branch.id,
		data: {
			currentUnit: branch.initial_unit_id,
			completedUnits: [],
			retries: {},
			failedArtifactDigests: {},
			...(options?.mode === undefined ? {} : { mode: options.mode }),
		},
	};
}

export function decodeLegacyCheckpoint<TMemory = unknown>(
	definition: WorkflowDefinition,
	raw: unknown,
	options: LegacyCheckpointCodecOptions<TMemory>,
): DecodedCheckpoint<TMemory> {
	const fallback = initialCheckpoint(definition, options.branch_id, options);
	const root = isRecord(raw) ? raw : {};
	const envelope = isRecord(root.checkpoint) ? root.checkpoint : {};
	const source = isRecord(envelope.data) ? envelope.data : {};
	const branch = branchFor(definition, options.branch_id);
	const validIds = new Set(branch.units.map((unit) => unit.id));
	const currentUnit =
		typeof source.currentUnit === "string" && validIds.has(source.currentUnit)
			? source.currentUnit
			: fallback.data.currentUnit;
	const completedUnits = Array.isArray(source.completedUnits)
		? source.completedUnits.filter((value): value is string => typeof value === "string" && validIds.has(value))
		: [];
	const retriesRaw = isRecord(source.retries) ? source.retries : {};
	const retries: Record<string, number> = {};
	for (const [unitId, value] of Object.entries(retriesRaw)) {
		const parsed = finiteNonNegative(value);
		if (parsed !== undefined && validIds.has(unitId)) retries[unitId] = parsed;
	}
	const failedRaw = isRecord(source.failedArtifactDigests) ? source.failedArtifactDigests : {};
	const failed: Record<string, string> = {};
	for (const [unitId, value] of Object.entries(failedRaw)) {
		if (typeof value === "string" && validIds.has(unitId)) failed[unitId] = value;
	}
	const blocks = finiteNonNegative(source.blocks);
	const hadMemory = Object.hasOwn(source, "memory");
	const memory = hadMemory ? options.decode_memory?.(source.memory) : undefined;
	const legacyMode = typeof source.mode === "string" ? source.mode : undefined;
	return {
		branch_id: branch.id,
		had_failed_artifact_digests: Object.hasOwn(source, "failedArtifactDigests"),
		had_memory: hadMemory,
		legacy_mode: legacyMode,
		data: {
			currentUnit,
			completedUnits,
			retries,
			...(legacyMode === undefined ? {} : { mode: legacyMode }),
			...(blocks === undefined ? {} : { blocks }),
			...(options.include_failed_artifact_digests ? { failedArtifactDigests: failed } : {}),
			...(memory === undefined ? {} : { memory }),
		},
	};
}

export function encodeLegacyCheckpoint<TMemory>(
	checkpoint: WorkflowCheckpoint<TMemory>,
	options: Pick<LegacyCheckpointCodecOptions<TMemory>, "include_failed_artifact_digests">,
): { checkpoint: { data: Record<string, unknown> } } {
	const data: Record<string, unknown> = {
		currentUnit: checkpoint.data.currentUnit,
		completedUnits: [...checkpoint.data.completedUnits],
		retries: { ...checkpoint.data.retries },
		...(checkpoint.data.blocks === undefined ? {} : { blocks: checkpoint.data.blocks }),
		...(checkpoint.data.mode === undefined ? {} : { mode: checkpoint.data.mode }),
		...(options.include_failed_artifact_digests && checkpoint.data.failedArtifactDigests
			? { failedArtifactDigests: { ...checkpoint.data.failedArtifactDigests } }
			: {}),
		...(checkpoint.data.memory === undefined ? {} : { memory: checkpoint.data.memory }),
	};
	return { checkpoint: { data } };
}
