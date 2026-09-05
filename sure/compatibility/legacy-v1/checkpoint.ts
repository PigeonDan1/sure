import {
	applyValidation,
	advance as coreAdvance,
	retryExhausted as coreRetryExhausted,
	decodeLegacyCheckpoint,
	initialCheckpoint,
	type WorkflowCheckpoint,
	type WorkflowCheckpointData,
	type WorkflowDefinition,
} from "../../../packages/sure-core/src/index.ts";

export interface LegacyCheckpointData<TMemory = unknown> {
	currentUnit: string;
	completedUnits: string[];
	retries: Record<string, number>;
	blocks?: number;
	failedArtifactDigests?: Record<string, string>;
	mode?: string;
	memory?: TMemory;
}

export interface LegacyRunCheckpoint<TMemory = unknown> {
	id: string;
	label: string;
	resumable: boolean;
	resume_hint: string;
	data: LegacyCheckpointData<TMemory>;
}

export interface LegacyCheckpointProjection<TMemory = unknown> {
	definition: WorkflowDefinition;
	branchId: string;
	id?: string;
	label?: string;
	mode?: string;
	includeFailedArtifactDigests: boolean;
	decodeMemory?: (value: unknown) => TMemory | undefined;
}

function mutableData<TMemory>(data: WorkflowCheckpointData<TMemory>): LegacyCheckpointData<TMemory> {
	return {
		currentUnit: data.currentUnit,
		completedUnits: [...data.completedUnits],
		retries: { ...data.retries },
		...(data.blocks === undefined ? {} : { blocks: data.blocks }),
		...(data.failedArtifactDigests === undefined ? {} : { failedArtifactDigests: { ...data.failedArtifactDigests } }),
		...(data.mode === undefined ? {} : { mode: data.mode }),
		...(data.memory === undefined ? {} : { memory: data.memory }),
	};
}

function project<TMemory>(checkpoint: WorkflowCheckpoint<TMemory>): LegacyRunCheckpoint<TMemory> {
	return {
		id: checkpoint.id,
		label: checkpoint.label,
		resumable: checkpoint.resumable,
		resume_hint: checkpoint.resume_hint,
		data: mutableData(checkpoint.data),
	};
}

function asCore<TMemory>(
	projection: LegacyCheckpointProjection<TMemory>,
	data: LegacyCheckpointData<TMemory>,
): WorkflowCheckpoint<TMemory> {
	return {
		id: projection.id ?? projection.definition.checkpoint_id ?? "main_flow",
		label: projection.label ?? projection.definition.checkpoint_label ?? projection.definition.workflow_id,
		resumable: true,
		resume_hint: `Resume at unit "${data.currentUnit}".`,
		branch_id: projection.branchId,
		data,
	};
}

export function initialLegacyCheckpoint<TMemory>(
	projection: LegacyCheckpointProjection<TMemory>,
): LegacyRunCheckpoint<TMemory> {
	return project(
		initialCheckpoint(projection.definition, projection.branchId, {
			...(projection.id === undefined ? {} : { id: projection.id }),
			...(projection.label === undefined ? {} : { label: projection.label }),
			...(projection.mode === undefined ? {} : { mode: projection.mode }),
		}),
	);
}

export function decodeLegacyState<TMemory>(
	projection: LegacyCheckpointProjection<TMemory>,
	raw: unknown,
): LegacyRunCheckpoint<TMemory> {
	const decoded = decodeLegacyCheckpoint(projection.definition, raw, {
		id: projection.id ?? projection.definition.checkpoint_id ?? "main_flow",
		label: projection.label ?? projection.definition.checkpoint_label ?? projection.definition.workflow_id,
		branch_id: projection.branchId,
		include_failed_artifact_digests: projection.includeFailedArtifactDigests,
		...(projection.mode === undefined ? {} : { mode: projection.mode }),
		...(projection.decodeMemory === undefined ? {} : { decode_memory: projection.decodeMemory }),
	});
	return {
		...initialLegacyCheckpoint(projection),
		resume_hint: `Resume at unit "${decoded.data.currentUnit}".`,
		data: mutableData(decoded.data),
	};
}

export function advanceLegacyUnit<TMemory>(
	projection: LegacyCheckpointProjection<TMemory>,
	unitId: string,
	data: LegacyCheckpointData<TMemory>,
): LegacyRunCheckpoint<TMemory> | undefined {
	const transition = coreAdvance(projection.definition, asCore(projection, data), unitId);
	return transition.accepted ? project(transition.checkpoint) : undefined;
}

export function bumpLegacyRetry<TMemory>(
	projection: LegacyCheckpointProjection<TMemory>,
	unitId: string,
	data: LegacyCheckpointData<TMemory>,
	artifactDigest?: string,
): LegacyRunCheckpoint<TMemory> {
	const transition = applyValidation(
		projection.definition,
		asCore(projection, data),
		{
			kind: "fail",
			reason: "legacy Pi gate blocked",
		},
		{ max_retries: Number.MAX_SAFE_INTEGER },
	);
	if (!transition.accepted || transition.action !== "retry") {
		throw new Error(
			`Cannot record retry for ${projection.definition.workflow_id}/${unitId}: ${transition.reason ?? transition.action}`,
		);
	}
	// Legacy callers perform the unchanged-artifact check before invoking this
	// helper.  Deliberately attach the digest after the Core retry transition so
	// direct legacy calls retain their historical "always increment" contract.
	if (artifactDigest === undefined) return project(transition.checkpoint);
	return project({
		...transition.checkpoint,
		data: {
			...transition.checkpoint.data,
			failedArtifactDigests: {
				...(transition.checkpoint.data.failedArtifactDigests ?? {}),
				[unitId]: artifactDigest,
			},
		},
	});
}

export function legacyRetryExhausted<TMemory>(
	projection: LegacyCheckpointProjection<TMemory>,
	unitId: string,
	data: LegacyCheckpointData<TMemory>,
	maxRetries: number,
): boolean {
	return coreRetryExhausted(asCore(projection, data), maxRetries, unitId);
}
