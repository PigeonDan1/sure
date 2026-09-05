import { describe, expect, it } from "vitest";
import {
	assessFormalEligibility,
	canonicalJsonDigest,
	type ExecutionReceipt,
	type ExecutionRequest,
	type FrozenFormalSubject,
	type JsonValue,
	validateExecutionReceipt,
} from "../src/index.ts";

const A = `sha256:${"a".repeat(64)}`;
const B = `sha256:${"b".repeat(64)}`;
const C = `sha256:${"c".repeat(64)}`;
const D = `sha256:${"d".repeat(64)}`;
const NOW = "2026-09-06T00:00:00.000Z";

function request(): ExecutionRequest {
	return {
		schema: "sure.execution_request.v1",
		request_id: "formal-request",
		semantic_request_digest: A,
		run_id: "formal-run",
		unit_id: "execute_inference",
		attempt: 1,
		operation: "inference",
		subject: {
			bundle_manifest_path: "/tmp/sure/bundle.json",
			bundle_digest: A,
			runtime_identity_digest: B,
			inference_protocol_digest: C,
		},
		inputs: [],
		entrypoint: { executable: "python3", argv: ["/tmp/sure/run.py"] },
		runtime_requirements: {},
		capability_requirements: [
			{ capability_id: "sure.execution.local-python", capability_class: "execution_capability", required: true },
		],
		reference_snapshot_digest: D,
		output_root: {
			path: "/tmp/sure/formal-run/artifacts",
			resolved_path: "/tmp/sure/formal-run/artifacts",
			scope_id: "formal-run",
			policy_digest: A,
			writable: true,
		},
		policy_digest: A,
		created_at: NOW,
	};
}

function receipt(input: ExecutionRequest): ExecutionReceipt {
	return {
		schema: "sure.execution_receipt.v1",
		receipt_id: "formal-receipt",
		request_id: input.request_id,
		request_digest: canonicalJsonDigest(input as unknown as JsonValue),
		semantic_request_digest: input.semantic_request_digest,
		run_id: input.run_id,
		unit_id: input.unit_id,
		attempt: input.attempt,
		executor: {
			executor_id: "pi-python",
			kind: "python",
			version: "1",
			digest: B,
			trust_level: "host_enforced",
		},
		lifecycle: "SUCCEEDED",
		capability_evidence: [
			{
				capability_id: "sure.execution.local-python",
				capability_class: "execution_capability",
				status: "AVAILABLE",
				source: "executor",
				observed_at: NOW,
				evidence_digest: C,
			},
		],
		outputs: [],
		reference_snapshot_digest: input.reference_snapshot_digest,
		output_root: input.output_root,
		policy_digest: input.policy_digest,
		started_at: NOW,
		finished_at: NOW,
		exit_code: 0,
	};
}

function subject(input: ExecutionRequest): FrozenFormalSubject {
	return {
		...input.subject,
		dataset_identity_digest: C,
		scoring_protocol_digest: D,
	};
}

function eligibility(overrides: Partial<Parameters<typeof assessFormalEligibility>[0]> = {}) {
	const input = request();
	const output = assessFormalEligibility({
		request: input,
		receipt: receipt(input),
		receipt_validation: validateExecutionReceipt(input, receipt(input)),
		assurance_profile: "pi_enforced",
		validator_verdict: "PASS",
		workflow_disposition: "TERMINATE",
		subject: subject(input),
		workflow_digest: A,
		validator_digest: B,
		executor_digest: B,
		policy_digest: A,
		reference_snapshot_digest: D,
		...overrides,
	});
	return output;
}

describe("formal conformance boundary", () => {
	it("admits only a frozen, host-enforced successful subject", () => {
		const result = eligibility();
		expect(result.eligible).toBe(true);
		expect(result.outcome).toMatchObject({ outcome: "PASS", reason_code: "VALIDATION_PASSED" });
	});

	it("normalizes optional digest prefixes at the assurance boundary", () => {
		const result = eligibility({
			executor_digest: B.slice("sha256:".length),
		});
		expect(result.eligible).toBe(true);
	});

	it("keeps cooperative portable evidence useful but non-formal", () => {
		const result = eligibility({ assurance_profile: "cooperative" });
		expect(result.eligible).toBe(false);
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "UPGRADE_REQUIRED" });
	});

	it("requires dataset and scoring identities", () => {
		const input = request();
		const result = eligibility({
			subject: { ...subject(input), dataset_identity_digest: "missing" },
		});
		expect(result.eligible).toBe(false);
		expect(result.outcome.reason_code).toBe("DIGEST_MISMATCH");
	});

	it("requires formal request identities to match the frozen subject", () => {
		const input = request();
		input.operation = "formal_evaluation";
		input.subject = {
			...input.subject,
			dataset_identity_digest: C,
			scoring_protocol_digest: D,
		};
		const result = eligibility({
			request: input,
			subject: { ...subject(input), dataset_identity_digest: A },
			receipt: receipt(input),
			receipt_validation: validateExecutionReceipt(input, receipt(input)),
		});
		expect(result.eligible).toBe(false);
		expect(result.outcome.reason_code).toBe("DIGEST_MISMATCH");
	});

	it("propagates a missing execution capability as non-executed", () => {
		const input = request();
		const failedReceipt = receipt(input);
		failedReceipt.capability_evidence = [];
		const result = eligibility({
			receipt: failedReceipt,
			receipt_validation: validateExecutionReceipt(input, failedReceipt),
		});
		expect(result.eligible).toBe(false);
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "CAPABILITY_MISSING" });
	});

	it("keeps unknown and denied capabilities distinct", () => {
		const input = request();
		const unknown = {
			...input,
			capability_requirements: [
				{ ...input.capability_requirements[0], capability_id: "sure.execution.not-registered" },
			],
		};
		const unknownReceipt = receipt(unknown);
		unknownReceipt.capability_evidence = [];
		const unknownResult = eligibility({
			request: unknown,
			receipt: unknownReceipt,
			receipt_validation: validateExecutionReceipt(unknown, unknownReceipt),
		});
		expect(unknownResult.outcome.reason_code).toBe("UNKNOWN_CAPABILITY");

		const denied = request();
		const deniedReceipt = receipt(denied);
		deniedReceipt.capability_evidence[0].status = "DENIED";
		const deniedResult = eligibility({
			request: denied,
			receipt: deniedReceipt,
			receipt_validation: validateExecutionReceipt(denied, deniedReceipt),
		});
		expect(deniedResult.outcome.reason_code).toBe("POLICY_DENIED");
	});

	it("rejects a validator result that is not PASS", () => {
		const result = eligibility({ validator_verdict: "FAIL" });
		expect(result.eligible).toBe(false);
		expect(result.outcome).toMatchObject({ outcome: "BLOCKED", reason_code: "VALIDATION_FAILED" });
	});
});
