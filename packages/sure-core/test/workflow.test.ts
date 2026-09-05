import { describe, expect, it } from "vitest";
import {
	advance,
	applyValidation,
	auditCheckpointTransition,
	initialCheckpoint,
	unitFor,
	validateStructuralArtifact,
	type WorkflowDefinition,
} from "../src/index.ts";

function definition(overrides: Partial<WorkflowDefinition> = {}): WorkflowDefinition {
	return {
		schema: "sure.workflow.definition.v1",
		workflow_id: "test_flow",
		version: "1",
		branches: [
			{
				id: "main",
				initial_unit_id: "a",
				terminal_unit_id: "c",
				units: [
					{ id: "a", label: "A", kind: "linear", produces: "a.json" },
					{ id: "b", label: "B", kind: "gate", produces: "b.json" },
					{ id: "c", label: "C", kind: "gate", produces: "c.json" },
				],
			},
		],
		default_branch_id: "main",
		retry_policy: { default_max_retries: 2 },
		...overrides,
	};
}

describe("host-neutral workflow kernel", () => {
	it("audits only legal one-step checkpoint transitions", () => {
		const def = definition();
		const initial = initialCheckpoint(def);
		const retry = applyValidation(def, initial, {
			kind: "fail",
			reason: "repair",
			artifact_digest: "a-1",
		}).checkpoint;
		expect(auditCheckpointTransition(def, initial, retry)).toMatchObject({ ok: true, action: "retry" });

		const advanced = applyValidation(def, retry, { kind: "pass", artifact_digest: "a-2" }).checkpoint;
		expect(auditCheckpointTransition(def, retry, advanced)).toMatchObject({ ok: true, action: "advanced" });

		const skipped = {
			...initial,
			data: { ...initial.data, currentUnit: "c", completedUnits: ["a", "b"] },
		};
		expect(auditCheckpointTransition(def, initial, skipped)).toMatchObject({ ok: false });
	});

	it("rejects rewritten history and retry evidence", () => {
		const def = definition();
		const initial = initialCheckpoint(def);
		const tampered = {
			...initial,
			data: {
				...initial.data,
				completedUnits: ["a"],
				currentUnit: "b",
				retries: { b: 99 },
			},
		};
		const audit = auditCheckpointTransition(def, initial, tampered);
		expect(audit.ok).toBe(false);
		expect(audit.reason).toMatch(/retry|currentUnit|current unit/i);
	});

	it("advances one unit and preserves run-wide block count", () => {
		const def = definition();
		const initial = initialCheckpoint(def);
		const blocked = applyValidation(def, initial, {
			kind: "fail",
			reason: "bad artifact",
			artifact_digest: "a-1",
		});
		expect(blocked.action).toBe("retry");
		expect(blocked.retry_consumed).toBe(true);
		expect(blocked.checkpoint.data.retries.a).toBe(1);
		expect(blocked.checkpoint.data.blocks).toBe(1);

		const passed = applyValidation(def, blocked.checkpoint, { kind: "pass", artifact_digest: "a-2" });
		expect(passed.action).toBe("advanced");
		expect(passed.checkpoint.data.currentUnit).toBe("b");
		expect(passed.checkpoint.data.completedUnits).toEqual(["a"]);
		expect(passed.checkpoint.data.retries).toEqual({});
		expect(passed.checkpoint.data.blocks).toBe(1);
	});

	it("does not consume a retry when the failed artifact digest is unchanged", () => {
		const def = definition();
		const first = applyValidation(def, initialCheckpoint(def), {
			kind: "fail",
			reason: "repair me",
			artifact_digest: "same",
		});
		const second = applyValidation(def, first.checkpoint, {
			kind: "fail",
			reason: "still broken",
			artifact_digest: "same",
		});
		expect(second.action).toBe("unchanged");
		expect(second.retry_consumed).toBe(false);
		expect(second.checkpoint).toEqual(first.checkpoint);
	});

	it("blocks on exhaustion by default and can model the extraction exception", () => {
		const def = definition({ retry_policy: { default_max_retries: 2 } });
		const first = applyValidation(def, initialCheckpoint(def), {
			kind: "fail",
			reason: "first",
			artifact_digest: "one",
		});
		const exhausted = applyValidation(def, first.checkpoint, {
			kind: "fail",
			reason: "second",
			artifact_digest: "two",
		});
		expect(exhausted.action).toBe("exhausted");
		expect(exhausted.accepted).toBe(false);
		expect(exhausted.exhausted).toBe(true);
		expect(exhausted.checkpoint.data.currentUnit).toBe("a");

		const exemptDef = definition({ retry_policy: { default_max_retries: 1, exempt_from_exhaustion: ["a"] } });
		const advanced = applyValidation(exemptDef, initialCheckpoint(exemptDef), {
			kind: "fail",
			reason: "non-blocking extraction",
			artifact_digest: "digest",
		});
		expect(advanced.action).toBe("exhausted");
		expect(advanced.accepted).toBe(true);
		expect(advanced.checkpoint.data.currentUnit).toBe("b");
	});

	it("rejects stale callers and never duplicates the terminal unit", () => {
		const def = definition();
		const initial = initialCheckpoint(def);
		const rejected = advance(def, initial, "b");
		expect(rejected.accepted).toBe(false);
		expect(rejected.action).toBe("rejected");

		let checkpoint = initial;
		for (const unitId of ["a", "b", "c"]) {
			const transition = applyValidation(def, checkpoint, { kind: "pass", artifact_digest: unitId }, {});
			expect(transition.accepted).toBe(true);
			checkpoint = transition.checkpoint;
		}
		expect(checkpoint.resumable).toBe(false);
		expect(checkpoint.data.completedUnits).toEqual(["a", "b", "c"]);
		const again = applyValidation(def, checkpoint, { kind: "pass" });
		expect(again.action).toBe("terminal");
		expect(again.checkpoint.data.completedUnits).toEqual(["a", "b", "c"]);
	});

	it("keeps missing artifacts distinct from validation failure", () => {
		const def = definition();
		const transition = applyValidation(def, initialCheckpoint(def), { kind: "missing", reason: "not written" });
		expect(transition.action).toBe("missing");
		expect(transition.retry_consumed).toBe(false);
		expect(transition.checkpoint.data.retries).toEqual({});
	});
});

describe("host-neutral structural validator", () => {
	const unit = {
		id: "unit",
		label: "Unit",
		kind: "gate" as const,
		produces: "unit.json",
		required_fields: ["name"],
		allowed_values: { status: ["ready"] },
		forbidden_fields: ["later"],
	};
	const schema = {
		type: "object",
		required: ["status"],
		properties: {
			name: { type: "string" },
			status: { type: "string", enum: ["ready", "blocked"] },
		},
		additionalProperties: false,
	};

	it("enforces required fields, types, enums, forbidden fields, and extra keys", () => {
		expect(validateStructuralArtifact(unit, undefined, schema).missing).toBe(true);
		expect(validateStructuralArtifact(unit, { status: "ready" }, schema).reason).toContain("missing field name");
		expect(validateStructuralArtifact(unit, { name: 4, status: "ready" }, schema).reason).toBe("type mismatch");
		expect(validateStructuralArtifact(unit, { name: "x", status: "blocked" }, schema).reason).toBe(
			"value out of domain",
		);
		expect(validateStructuralArtifact(unit, { name: "x", status: "ready", later: true }, schema).reason).toContain(
			"forbidden field later",
		);
		expect(validateStructuralArtifact(unit, { name: "x", status: "ready", extra: true }, schema).reason).toContain(
			"additional properties",
		);
		expect(validateStructuralArtifact(unit, { name: "x", status: "ready", $schema: "inline" }, schema).ok).toBe(true);
	});

	it("does not execute semantic or host-specific checks", () => {
		const def = definition();
		expect(unitFor(def, initialCheckpoint(def))?.id).toBe("a");
		expect(validateStructuralArtifact(unit, { name: "x", status: "ready" }, schema).ok).toBe(true);
	});
});
