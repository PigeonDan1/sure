import { branchForCheckpoint, nextUnit, unitFor } from "./engine.ts";
import type { WorkflowCheckpoint, WorkflowDefinition } from "./types.ts";

export type CheckpointTransitionAction = "unchanged" | "retry" | "advanced" | "terminal";

export interface CheckpointTransitionAudit {
	ok: boolean;
	action?: CheckpointTransitionAction;
	reason?: string;
}

function sameArray(left: readonly string[], right: readonly string[]): boolean {
	return left.length === right.length && left.every((value, index) => value === right[index]);
}

function isPrefix(prefix: readonly string[], value: readonly string[]): boolean {
	return prefix.length <= value.length && prefix.every((entry, index) => entry === value[index]);
}

function hasDuplicates(values: readonly string[]): boolean {
	return new Set(values).size !== values.length;
}

function valueAt(values: Readonly<Record<string, number>>, key: string): number {
	return values[key] ?? 0;
}

function digestAt(values: Readonly<Record<string, string>> | undefined, key: string): string | undefined {
	return values?.[key];
}

function keysOf<T>(...records: Array<Readonly<Record<string, T>> | undefined>): string[] {
	return [...new Set(records.flatMap((record) => (record ? Object.keys(record) : [])))];
}

function reject(reason: string): CheckpointTransitionAudit {
	return { ok: false, reason };
}

/**
 * Audit a legacy-compatible checkpoint patch against the canonical workflow.
 *
 * This is deliberately a structural audit. Domain hooks still decide whether an
 * artifact passed a semantic validator; this function only prevents a host
 * adapter or hook from jumping units, rewriting completed history, or erasing
 * retry evidence. Memory is intentionally opaque and may change with an
 * otherwise legal transition.
 */
export function auditCheckpointTransition<TMemory = unknown>(
	definition: WorkflowDefinition,
	before: WorkflowCheckpoint<TMemory>,
	after: WorkflowCheckpoint<TMemory>,
): CheckpointTransitionAudit {
	if (before.branch_id !== after.branch_id) {
		return reject(`Workflow branch changed from "${before.branch_id}" to "${after.branch_id}".`);
	}
	if (before.id !== after.id) {
		return reject(`Checkpoint id changed from "${before.id}" to "${after.id}".`);
	}
	const branch = branchForCheckpoint(definition, before);
	if (!branch || !unitFor(definition, before) || !unitFor(definition, after)) {
		return reject("Checkpoint refers to an unknown workflow branch or unit.");
	}
	const beforeCompleted = [...before.data.completedUnits];
	const afterCompleted = [...after.data.completedUnits];
	const validIds = new Set(branch.units.map((unit) => unit.id));
	if (hasDuplicates(beforeCompleted) || hasDuplicates(afterCompleted)) {
		return reject("Checkpoint completedUnits contains duplicate unit ids.");
	}
	if (
		!beforeCompleted.every((unitId) => validIds.has(unitId)) ||
		!afterCompleted.every((unitId) => validIds.has(unitId))
	) {
		return reject("Checkpoint completedUnits contains an unknown unit id.");
	}
	if (!isPrefix(beforeCompleted, afterCompleted) || afterCompleted.length - beforeCompleted.length > 1) {
		return reject("Checkpoint completedUnits must append at most the current unit.");
	}

	const completedDelta = afterCompleted.length - beforeCompleted.length;
	const currentChanged = before.data.currentUnit !== after.data.currentUnit;
	const expectedNext = nextUnit(definition, before);
	if (completedDelta === 0 && currentChanged) {
		return reject("Checkpoint currentUnit changed without completing the previous unit.");
	}
	if (completedDelta === 1) {
		const appended = afterCompleted.at(-1);
		if (appended !== before.data.currentUnit) {
			return reject("Checkpoint may only complete its current unit.");
		}
		if (expectedNext && after.data.currentUnit !== expectedNext.id) {
			return reject(`Checkpoint advanced to "${after.data.currentUnit}" instead of "${expectedNext.id}".`);
		}
		if (!expectedNext && after.data.currentUnit !== before.data.currentUnit) {
			return reject("A terminal checkpoint must retain its terminal currentUnit.");
		}
	}
	if (completedDelta === 0 && !sameArray(beforeCompleted, afterCompleted)) {
		return reject("Checkpoint completedUnits changed without an advance.");
	}

	const currentUnit = before.data.currentUnit;
	for (const unitId of keysOf(before.data.retries, after.data.retries)) {
		const beforeRetries = valueAt(before.data.retries, unitId);
		const afterRetries = valueAt(after.data.retries, unitId);
		if (unitId !== currentUnit && beforeRetries !== afterRetries) {
			return reject(`Retry history for non-current unit "${unitId}" changed.`);
		}
		if (unitId === currentUnit) {
			if (completedDelta === 1) {
				if (afterRetries !== 0) return reject("Advancing a unit must clear its retry counter.");
			} else if (afterRetries < beforeRetries || afterRetries > beforeRetries + 1) {
				return reject("A retry counter may only stay unchanged or increase by one.");
			}
		}
	}

	for (const unitId of keysOf(before.data.failedArtifactDigests, after.data.failedArtifactDigests)) {
		const beforeDigest = digestAt(before.data.failedArtifactDigests, unitId);
		const afterDigest = digestAt(after.data.failedArtifactDigests, unitId);
		if (unitId !== currentUnit && beforeDigest !== afterDigest) {
			return reject(`Failed-artifact evidence for non-current unit "${unitId}" changed.`);
		}
		if (unitId === currentUnit && completedDelta === 1 && afterDigest !== undefined) {
			return reject("Advancing a unit must clear its failed-artifact evidence.");
		}
	}

	if ((after.data.blocks ?? 0) < (before.data.blocks ?? 0)) {
		return reject("Checkpoint block count cannot decrease.");
	}
	if (after.data.mode !== before.data.mode && before.data.mode !== undefined) {
		return reject("Checkpoint mode cannot change after a run has started.");
	}

	if (completedDelta === 1 && !expectedNext) {
		return { ok: true, action: "terminal" };
	}
	if (completedDelta === 1) return { ok: true, action: "advanced" };
	if (valueAt(after.data.retries, currentUnit) > valueAt(before.data.retries, currentUnit)) {
		return { ok: true, action: "retry" };
	}
	return { ok: true, action: "unchanged" };
}
