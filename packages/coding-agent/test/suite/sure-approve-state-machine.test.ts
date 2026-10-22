import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
	advance,
	bumpRetry,
	initialCheckpoint,
	retryExhausted,
} from "../../../../sure/skills/sure_approve/hooks/checkpoints.ts";
import {
	gateBlocks,
	modeFromArgs,
	preFinish,
	preStart,
	preToolCall,
} from "../../../../sure/skills/sure_approve/hooks/index.ts";
import {
	APPROVE_UNITS,
	AUDIT_UNITS,
	DECISION_UNITS,
	nextUnit,
	unitsForMode,
} from "../../../../sure/skills/sure_approve/hooks/state-machine.ts";
import { validateProduces } from "../../../../sure/skills/sure_approve/hooks/validate.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

describe("sure_approve state machine", () => {
	it("keeps audit and human approval in separate runs", () => {
		expect(AUDIT_UNITS.at(-1)?.id).toBe("prepare_review");
		expect(DECISION_UNITS[0]?.id).toBe("verify_decision");
		expect(nextUnit("audit", "prepare_review")).toBeUndefined();
		expect(nextUnit("approve", "verify_decision")?.id).toBe("publish");
		expect(unitsForMode("audit")).not.toContain(DECISION_UNITS[0]);
	});

	it("infers approval mode from the prior review and explicit decision", () => {
		expect(modeFromArgs("review_manifest=/tmp/review.json decision=approve")).toBe("approve");
		expect(modeFromArgs("model_dir=/tmp/model")).toBe("audit");
	});

	it("rejects command-level approval root overrides", () => {
		const result = preStart({
			args: "model_dir=/tmp/model approve_dir=/tmp/custom",
		} as SureHookContext);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("storage.approved_models_roots[0]");
	});

	it("blocks an out-of-order script run through the powershell tool", () => {
		const result = preToolCall({
			args: "model_dir=/tmp/model",
			// No state.json there, so the run sits at the first audit unit,
			// resolve_input, whose only permitted script is resolve_approve_input.py.
			runDir: "sure-approve-run-that-does-not-exist",
			event: {
				type: "tool_call",
				toolCallId: "t1",
				toolName: "powershell",
				input: { command: "python3 scripts/audit_bundle.py --kind producer" },
			},
		} as unknown as SureHookContext);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("scripts/audit_bundle.py");
	});

	it("orders publication only after an explicit decision", () => {
		expect(APPROVE_UNITS.map((unit) => unit.id)).toEqual([
			"resolve_input",
			"classify_producer",
			"audit_integrity",
			"plan_repairs",
			"apply_repairs",
			"seal_candidate",
			"verify_runtime",
			"prepare_review",
			"verify_decision",
			"publish",
			"verify_publication",
		]);
	});

	it("rejects a forged non-passing gate artifact", () => {
		const unit = APPROVE_UNITS.find((candidate) => candidate.id === "publish")!;
		expect(
			validateProduces(unit, {
				schema: "sure.approve.publication_result.v1",
				status: "failed",
				destination: "/tmp/model",
				candidate_digest: "0".repeat(64),
				eval_visible: false,
			}),
		).toContain("non-passing status");
	});

	it("accepts an explicit rejection decision without treating it as approval", () => {
		const unit = APPROVE_UNITS.find((candidate) => candidate.id === "verify_decision")!;
		expect(
			validateProduces(unit, {
				schema: "sure.approve.approval_decision.v1",
				status: "rejected",
				decision: "reject",
				review_packet: "/tmp/review.json",
				review_packet_digest: "1".repeat(64),
				candidate_digest: "2".repeat(64),
				actor: { os_user: "reviewer" },
			}),
		).toBeUndefined();
	});

	it("rejects an artifact that claims the wrong schema", () => {
		const unit = APPROVE_UNITS.find((candidate) => candidate.id === "publish")!;
		expect(
			validateProduces(unit, {
				schema: "sure.approve.approval_ready.v1",
				status: "passed",
				destination: "/tmp/model",
				candidate_digest: "0".repeat(64),
				eval_visible: false,
			}),
		).toContain("unsupported schema");
	});

	it("finishes a failed audit only with matching blocked-gate evidence and no publication", () => {
		const runDir = mkdtempSync(join(tmpdir(), "sure-approve-failed-"));
		try {
			mkdirSync(join(runDir, "artifacts"));
			const artifact = {
				schema: "sure.approve.runtime_verification.v1",
				status: "failed",
				runtime_kind: "python",
				binding: {},
				smoke: {},
				error: "generated sample audio must be stored below artifacts/outputs",
			};
			writeFileSync(join(runDir, "artifacts", "runtime_verification.json"), JSON.stringify(artifact));
			let checkpoint = initialCheckpoint("audit");
			for (let index = 0; index < 6; index += 1) checkpoint = advance(checkpoint);
			checkpoint = bumpRetry(checkpoint, createHash("sha256").update(JSON.stringify(artifact)).digest("hex"));
			writeFileSync(join(runDir, "state.json"), JSON.stringify({ checkpoint }));
			const event = {
				finish: { status: "failed" },
				manifest: { validation: { failed_unit: "verify_runtime", error: artifact.error, published: false } },
			};
			const context = {
				args: "model_dir=/tmp/model",
				runDir,
				event,
			} as SureHookContext;
			expect(preFinish(context).ok).toBe(true);
			expect(preFinish({ ...context, event: { ...event, finish: { status: "success" } } }).ok).toBe(false);
			expect(
				preFinish({
					...context,
					event: {
						...event,
						manifest: { validation: { failed_unit: "verify_runtime", error: artifact.error, published: true } },
					},
				}).ok,
			).toBe(false);
			expect(
				preFinish({
					...context,
					event: {
						...event,
						manifest: { validation: { failed_unit: "publish", error: artifact.error, published: false } },
					},
				}).ok,
			).toBe(false);
			writeFileSync(
				join(runDir, "artifacts", "runtime_verification.json"),
				JSON.stringify({ ...artifact, error: "changed" }),
			);
			expect(preFinish(context).ok).toBe(false);
		} finally {
			rmSync(runDir, { recursive: true, force: true });
		}
	});

	it("enforces the approval gate retry limit", () => {
		const initial = initialCheckpoint("audit");
		const first = bumpRetry(initial, "digest-1");
		const second = bumpRetry(first, "digest-2");
		const third = bumpRetry(second, "digest-3");
		expect(retryExhausted(second)).toBe(false);
		expect(retryExhausted(third)).toBe(true);
	});

	it("keeps counting blocks after the blocked unit passes", () => {
		const initial = initialCheckpoint("audit");
		expect(gateBlocks(initial.data)).toBe(0);

		const blocked = bumpRetry(bumpRetry(initial, "digest-1"), "digest-2");
		expect(gateBlocks(blocked.data)).toBe(2);

		// advance() clears the unit's retry entry, which is right for the retry
		// budget and wrong for a run-long tally: the counters used to report zero
		// blocks for every gate that blocked and then passed.
		expect(gateBlocks(advance(blocked).data)).toBe(2);
	});
});
