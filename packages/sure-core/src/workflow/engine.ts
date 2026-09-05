import type {
	TransitionAction,
	TransitionResult,
	ValidationSignal,
	WorkflowBranch,
	WorkflowCheckpoint,
	WorkflowDefinition,
	WorkflowUnit,
} from "./types.ts";

export interface ApplyValidationOptions {
	/** Override the definition's default retry budget for this invocation. */
	max_retries?: number;
	/** Behaviour after a changed failing artifact consumes the final retry. */
	on_exhausted?: "block" | "advance" | "terminate";
}

function assertRetryLimit(limit: number): number {
	if (!Number.isInteger(limit) || limit <= 0) {
		throw new Error(`Retry limit must be a positive integer; received ${limit}.`);
	}
	return limit;
}

function unitIndex(branch: WorkflowBranch, unitId: string): number {
	return branch.units.findIndex((unit) => unit.id === unitId);
}

function result<TMemory>(
	accepted: boolean,
	action: TransitionAction,
	checkpoint: WorkflowCheckpoint<TMemory>,
	retryConsumed: boolean,
	exhausted: boolean,
	reason?: string,
): TransitionResult<TMemory> {
	return {
		accepted,
		action,
		checkpoint,
		retry_consumed: retryConsumed,
		exhausted,
		...(reason === undefined ? {} : { reason }),
	};
}

function rejected<TMemory>(checkpoint: WorkflowCheckpoint<TMemory>, reason: string): TransitionResult<TMemory> {
	return result(false, "rejected", checkpoint, false, false, reason);
}

/** Return the branch selected by a checkpoint and fail closed on an unknown branch. */
export function branchForCheckpoint(
	definition: WorkflowDefinition,
	checkpoint: WorkflowCheckpoint,
): WorkflowBranch | undefined {
	return definition.branches.find((branch) => branch.id === checkpoint.branch_id);
}

export function unitFor(definition: WorkflowDefinition, checkpoint: WorkflowCheckpoint): WorkflowUnit | undefined {
	const branch = branchForCheckpoint(definition, checkpoint);
	return branch?.units.find((unit) => unit.id === checkpoint.data.currentUnit);
}

export function nextUnit(definition: WorkflowDefinition, checkpoint: WorkflowCheckpoint): WorkflowUnit | undefined {
	const branch = branchForCheckpoint(definition, checkpoint);
	if (!branch) return undefined;
	const index = unitIndex(branch, checkpoint.data.currentUnit);
	return index < 0 ? undefined : branch.units[index + 1];
}

/**
 * Mark the current unit complete and move exactly one position in the selected
 * branch. The expected unit guard makes stale callers unable to skip a unit.
 */
export function advance<TMemory = unknown>(
	definition: WorkflowDefinition,
	checkpoint: WorkflowCheckpoint<TMemory>,
	expectedUnitId = checkpoint.data.currentUnit,
): TransitionResult<TMemory> {
	if (checkpoint.data.currentUnit !== expectedUnitId) {
		return rejected(
			checkpoint,
			`Expected unit "${expectedUnitId}" does not match checkpoint position "${checkpoint.data.currentUnit}".`,
		);
	}
	const branch = branchForCheckpoint(definition, checkpoint);
	if (!branch) return rejected(checkpoint, `Unknown workflow branch "${checkpoint.branch_id}".`);
	const currentIndex = unitIndex(branch, expectedUnitId);
	if (currentIndex < 0) return rejected(checkpoint, `Unknown workflow unit "${expectedUnitId}".`);

	const completedUnits = checkpoint.data.completedUnits.includes(expectedUnitId)
		? [...checkpoint.data.completedUnits]
		: [...checkpoint.data.completedUnits, expectedUnitId];
	const retries = { ...checkpoint.data.retries };
	const failedArtifactDigests = { ...(checkpoint.data.failedArtifactDigests ?? {}) };
	delete retries[expectedUnitId];
	delete failedArtifactDigests[expectedUnitId];
	const next = branch.units[currentIndex + 1];
	const terminal = next === undefined;
	const data = {
		...checkpoint.data,
		currentUnit: next?.id ?? expectedUnitId,
		completedUnits,
		retries,
		failedArtifactDigests,
	};
	const advanced: WorkflowCheckpoint<TMemory> = {
		...checkpoint,
		resumable: !terminal,
		resume_hint: terminal ? "State machine reached the terminal unit." : `Advanced to unit "${next.id}".`,
		data,
	};
	return result(true, terminal ? "terminal" : "advanced", advanced, false, false);
}

export function retryExhausted<TMemory>(
	checkpoint: WorkflowCheckpoint<TMemory>,
	maxRetries: number,
	unitId = checkpoint.data.currentUnit,
): boolean {
	assertRetryLimit(maxRetries);
	return (checkpoint.data.retries[unitId] ?? 0) >= maxRetries;
}

/**
 * Apply the structural/semantic validator's signal to a checkpoint. Executor
 * lifecycle success is intentionally not accepted here; callers must submit a
 * separate `pass` signal after the artifact validators have run.
 */
export function applyValidation<TMemory = unknown>(
	definition: WorkflowDefinition,
	checkpoint: WorkflowCheckpoint<TMemory>,
	signal: ValidationSignal,
	options: ApplyValidationOptions = {},
): TransitionResult<TMemory> {
	const unit = unitFor(definition, checkpoint);
	if (!unit) return rejected(checkpoint, `Unknown workflow position "${checkpoint.data.currentUnit}".`);
	if (signal.kind === "missing") {
		return result(true, "missing", checkpoint, false, false, signal.reason ?? "Artifact is not produced yet.");
	}
	if (signal.kind === "pass") {
		return advance(definition, checkpoint, unit.id);
	}

	const previousDigest = checkpoint.data.failedArtifactDigests?.[unit.id];
	if (signal.artifact_digest !== undefined && previousDigest === signal.artifact_digest) {
		return result(true, "unchanged", checkpoint, false, false, signal.reason);
	}

	const retries = {
		...checkpoint.data.retries,
		[unit.id]: (checkpoint.data.retries[unit.id] ?? 0) + 1,
	};
	const failedArtifactDigests = { ...(checkpoint.data.failedArtifactDigests ?? {}) };
	if (signal.artifact_digest !== undefined) failedArtifactDigests[unit.id] = signal.artifact_digest;
	const blocked: WorkflowCheckpoint<TMemory> = {
		...checkpoint,
		resume_hint: `Retry unit "${unit.id}" (attempt ${retries[unit.id]}).`,
		data: {
			...checkpoint.data,
			retries,
			blocks: (checkpoint.data.blocks ?? 0) + 1,
			failedArtifactDigests,
		},
	};
	const maxRetries = assertRetryLimit(options.max_retries ?? definition.retry_policy.default_max_retries);
	if (!retryExhausted(blocked, maxRetries, unit.id)) {
		return result(true, "retry", blocked, true, false, signal.reason);
	}

	const policyExemption = definition.retry_policy.exempt_from_exhaustion?.includes(unit.id) ?? false;
	const exhaustionMode = options.on_exhausted ?? (policyExemption ? "advance" : "block");
	if (exhaustionMode === "advance") {
		const advanced = advance(definition, blocked, unit.id);
		if (!advanced.accepted) return advanced;
		return result(true, "exhausted", advanced.checkpoint, true, true, signal.reason);
	}
	if (exhaustionMode === "terminate") {
		const terminated: WorkflowCheckpoint<TMemory> = {
			...blocked,
			resumable: false,
			resume_hint: `Gate "${unit.id}" exhausted ${retries[unit.id]} blocked attempts.`,
		};
		return result(false, "exhausted", terminated, true, true, signal.reason);
	}
	return result(false, "exhausted", blocked, true, true, signal.reason);
}
