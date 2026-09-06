import { describe, expect, it } from "vitest";
import { createFrozenEvaluationSubject, frozenSubjectDigest, validateFrozenEvaluationSubject } from "../src/index.ts";

const DIGEST = (letter: string) => `sha256:${letter.repeat(64)}`;

function subject() {
	return createFrozenEvaluationSubject({
		subject_id: "subject-1",
		bundle_manifest_path: "/tmp/sure/bundle.json",
		bundle_digest: DIGEST("a"),
		runtime_identity_digest: DIGEST("b"),
		inference_protocol_digest: DIGEST("c"),
		dataset_identity_digest: DIGEST("d"),
		scoring_protocol_digest: DIGEST("e"),
		prediction_path: "/tmp/sure/predictions",
		prediction_digest: DIGEST("f"),
		execution_receipt_digest: DIGEST("a"),
		evaluator_engine_digest: DIGEST("b"),
		evaluator_route_digest: DIGEST("c"),
		workflow_digest: DIGEST("d"),
		validator_digest: DIGEST("e"),
		executor_digest: DIGEST("f"),
		policy_digest: DIGEST("a"),
		reference_snapshot_digest: DIGEST("b"),
		assurance_profile: "pi_enforced",
		legacy_unverified: false,
		approval_event_digest: DIGEST("c"),
		frozen_at: "2026-09-06T00:00:00.000Z",
	});
}

describe("frozen evaluation subject", () => {
	it("self-binds all formal identities", () => {
		const value = subject();
		expect(validateFrozenEvaluationSubject(value)).toEqual([]);
		expect(value.subject_digest).toBe(frozenSubjectDigest(value));
	});

	it("detects content tampering even when the file remains parseable", () => {
		const value = subject();
		value.prediction_digest = DIGEST("9");
		expect(validateFrozenEvaluationSubject(value)).toContain(
			"evaluation subject subject_digest does not match its contents",
		);
	});

	it("requires a digest-bound human approval for formal eligibility at the caller", () => {
		const value = subject();
		delete value.approval_event_digest;
		value.subject_digest = frozenSubjectDigest(value);
		expect(validateFrozenEvaluationSubject(value)).toEqual([]);
		// The field is optional for readable legacy projections; conformance
		// applies the stricter formal requirement.
		expect(value.legacy_unverified).toBe(false);
	});
});
