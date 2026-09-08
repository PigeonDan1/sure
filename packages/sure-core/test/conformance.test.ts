import { describe, expect, it } from "vitest";
import {
	type AssuranceExpectedBinding,
	type AssuranceVerifierPort,
	assessFormalEligibility,
	canonicalJsonDigest,
	createAssuranceAttestation,
	createFrozenEvaluationSubject,
	type ExecutionAdmissionTrace,
	type ExecutionContractHistory,
	type ExecutionReceipt,
	type ExecutionRequest,
	executionContractHistoryDigest,
	type FrozenFormalSubject,
	type JsonValue,
	runBindingDigest,
	validateDockerRuntimeEvidence,
	validateExecutionContractHistory,
	validateExecutionReceipt,
	verifyAssuranceAttestation,
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
		operation: "formal_evaluation",
		subject: {
			bundle_manifest_path: "/tmp/sure/bundle.json",
			bundle_digest: A,
			runtime_identity_digest: B,
			inference_protocol_digest: C,
			dataset_identity_digest: C,
			scoring_protocol_digest: D,
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
		policy_snapshot_digest: C,
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
		policy_snapshot_digest: input.policy_snapshot_digest,
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

function frozenSubject(input: ExecutionRequest, currentReceipt: ExecutionReceipt) {
	return createFrozenEvaluationSubject({
		subject_id: "subject-formal",
		bundle_manifest_path: input.subject.bundle_manifest_path,
		bundle_digest: input.subject.bundle_digest,
		runtime_identity_digest: input.subject.runtime_identity_digest,
		inference_protocol_digest: input.subject.inference_protocol_digest!,
		dataset_identity_digest: input.subject.dataset_identity_digest!,
		scoring_protocol_digest: input.subject.scoring_protocol_digest!,
		prediction_path: "/tmp/sure/predictions",
		prediction_digest: A,
		execution_receipt_digest: canonicalJsonDigest(currentReceipt as unknown as JsonValue),
		evaluator_engine_digest: A,
		evaluator_route_digest: B,
		workflow_digest: A,
		validator_digest: B,
		executor_digest: B,
		policy_digest: A,
		reference_snapshot_digest: D,
		assurance_profile: "pi_enforced",
		legacy_unverified: false,
		approval_event_digest: C,
		frozen_at: NOW,
	});
}

function executionHistory(input: ExecutionRequest, currentReceipt: ExecutionReceipt): ExecutionContractHistory {
	const admission: ExecutionAdmissionTrace = {
		schema: "sure.execution_admission.v1",
		request_digest: canonicalJsonDigest(input as unknown as JsonValue),
		request_id: input.request_id,
		status: "ADMITTED",
		reason_code: "VALIDATION_PENDING",
		observed_at: NOW,
		probe_invoked: true,
		execute_invoked: true,
		receipt_present: true,
		receipt_valid: true,
	};
	const contract = {
		schema: "sure.execution_compatibility.v1",
		version: 1,
		request_digest: canonicalJsonDigest(input as unknown as JsonValue),
		receipt_digest: canonicalJsonDigest(currentReceipt as unknown as JsonValue),
		admission_digest: canonicalJsonDigest(admission as unknown as JsonValue),
		admission_instrumentation: "admission-v1",
		contract_valid: true,
	};
	return {
		latest: { request: input, receipt: currentReceipt, admission, contract },
		immutable: { request: input, receipt: currentReceipt, admission, contract },
	};
}

const verifier: AssuranceVerifierPort = {
	resolveTrustAnchor: () => ({
		issuer_id: "pi-test",
		issuer_kind: "pi_harness",
		issuer_digest: D,
		maximum_profile: "pi_enforced",
		status: "active",
		proof_kinds: ["host_store"],
	}),
	verifyProof: (attestation) => ({ verified: attestation.proof.value === "test-host-proof" }),
};

function verifiedAssurance(
	input: ExecutionRequest,
	frozen: ReturnType<typeof frozenSubject>,
	values: {
		assurance_profile: "cooperative" | "pi_enforced" | "trusted";
		core_version: string;
		workflow_digest: string;
		validator_digest: string;
		executor_digest: string;
		policy_digest: string;
		policy_snapshot_digest: string;
		reference_snapshot_digest: string;
		admission_digest: string;
		receipt_digest: string;
		execution_history_digest: string;
		validation_evidence_digest: string;
		run_binding_digest: string;
	},
) {
	const expected: AssuranceExpectedBinding = {
		assurance_profile: values.assurance_profile,
		core_version: values.core_version,
		run_id: input.run_id,
		unit_id: input.unit_id,
		attempt: input.attempt,
		request_digest: canonicalJsonDigest(input as unknown as JsonValue),
		admission_digest: values.admission_digest,
		receipt_digest: values.receipt_digest,
		execution_history_digest: values.execution_history_digest,
		validation_evidence_digest: values.validation_evidence_digest,
		subject_digest: frozen.subject_digest,
		approval_event_digest: frozen.approval_event_digest!,
		workflow_digest: values.workflow_digest,
		validator_digest: values.validator_digest,
		executor_digest: values.executor_digest,
		policy_digest: values.policy_digest,
		policy_snapshot_digest: values.policy_snapshot_digest,
		reference_snapshot_digest: values.reference_snapshot_digest,
		run_binding_digest: values.run_binding_digest,
	};
	const attestation = createAssuranceAttestation({
		attestation_id: "attestation-formal",
		issuer: { issuer_id: "pi-test", kind: "pi_harness", version: "1", digest: D },
		...expected,
		event_sequence: 1,
		issued_at: NOW,
		proof: { kind: "host_store", verification_material_id: "pi-test-store", value: "test-host-proof" },
	});
	const result = verifyAssuranceAttestation(attestation, expected, verifier);
	if (!result.verified) throw new Error(result.diagnostics.join("; "));
	return result.assurance;
}

function eligibility(overrides: Partial<Parameters<typeof assessFormalEligibility>[0]> = {}) {
	const input = request();
	const currentReceipt = receipt(input);
	const frozen = frozenSubject(input, currentReceipt);
	const history = executionHistory(input, currentReceipt);
	const policySnapshotDigest = C;
	const bindingDigest = runBindingDigest({
		workflowDigest: A,
		validatorDigest: B,
		executorDigest: B,
		policyDigest: A,
		policySnapshotDigest,
	});
	const base = {
		request: input,
		receipt: currentReceipt,
		receipt_validation: validateExecutionReceipt(input, currentReceipt),
		assurance_profile: "pi_enforced",
		core_version: "0.80.3",
		validator_verdict: "PASS",
		workflow_disposition: "TERMINATE",
		subject: subject(input),
		workflow_digest: A,
		validator_digest: B,
		executor_digest: B,
		policy_digest: A,
		policy_snapshot_digest: policySnapshotDigest,
		reference_snapshot_digest: D,
		frozen_subject: frozen,
		receipt_digest: canonicalJsonDigest(currentReceipt as unknown as JsonValue),
		admission_digest: A,
		execution_history_digest: executionContractHistoryDigest(history),
		execution_history_validation: validateExecutionContractHistory(history, {
			require_receipt: true,
			require_admission: true,
			require_contract_record: true,
			accept_legacy_uninstrumented: false,
		}),
		validation_evidence_digest: C,
		run_binding_digest: bindingDigest,
		...overrides,
	} satisfies Parameters<typeof assessFormalEligibility>[0];
	const withAssurance = Object.hasOwn(overrides, "verified_assurance")
		? base
		: {
				...base,
				verified_assurance: verifiedAssurance(base.request, base.frozen_subject!, {
					assurance_profile: base.assurance_profile,
					core_version: base.core_version,
					workflow_digest: base.workflow_digest,
					validator_digest: base.validator_digest,
					executor_digest: base.executor_digest,
					policy_digest: base.policy_digest,
					policy_snapshot_digest: base.policy_snapshot_digest,
					reference_snapshot_digest: base.reference_snapshot_digest,
					admission_digest: base.admission_digest!,
					receipt_digest: base.receipt_digest!,
					execution_history_digest: base.execution_history_digest!,
					validation_evidence_digest: base.validation_evidence_digest!,
					run_binding_digest: base.run_binding_digest!,
				}),
			};
	return assessFormalEligibility(withAssurance);
}

describe("formal conformance boundary", () => {
	it("requires independently verified Docker image evidence for formal execution", () => {
		const input = request();
		const imageDigest = `sha256:${"e".repeat(64)}`;
		const image = `registry.example/sure/trans@${imageDigest}`;
		input.operation = "formal_evaluation";
		input.subject = {
			...input.subject,
			dataset_identity_digest: C,
			scoring_protocol_digest: D,
		};
		input.runtime_requirements = {
			executor_kind: "docker",
			docker_image: image,
			docker_image_digest: imageDigest,
			docker_mounts: [],
		};
		const currentReceipt = receipt(input);
		currentReceipt.executor = { ...currentReceipt.executor, kind: "docker" };
		currentReceipt.capability_evidence = [
			{
				capability_id: "sure.execution.docker",
				capability_class: "execution_capability",
				status: "AVAILABLE",
				source: "executor",
				observed_at: NOW,
				details: { image_ref: image, image_digest: imageDigest, image_verified: true },
			},
		];
		expect(validateDockerRuntimeEvidence(input, currentReceipt)).toEqual([]);
		currentReceipt.capability_evidence[0]!.details = {
			image_ref: image,
			image_digest: imageDigest,
			image_verified: false,
		};
		expect(validateDockerRuntimeEvidence(input, currentReceipt)).toContain(
			"Docker capability evidence is not independently image-verified",
		);
	});

	it("admits only a frozen, host-enforced successful subject", () => {
		const result = eligibility();
		expect(result.eligible).toBe(true);
		expect(result.outcome).toMatchObject({ outcome: "PASS", reason_code: "VALIDATION_PASSED" });
	});

	it("does not let a caller-supplied profile or receipt trust grant formal assurance", () => {
		const result = eligibility({ verified_assurance: undefined });
		expect(result.eligible).toBe(false);
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "UPGRADE_REQUIRED" });
		expect(result.diagnostics).toContain("formal evaluation requires host-verified assurance");
	});

	it("requires the complete execution history to be re-audited", () => {
		const result = eligibility({ execution_history_validation: undefined });
		expect(result.eligible).toBe(false);
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		expect(result.diagnostics).toContain("formal evaluation requires a complete execution history audit");
	});

	it("never treats a non-formal execution request as formally eligible", () => {
		const input = request();
		input.operation = "inference";
		const currentReceipt = receipt(input);
		const result = eligibility({
			request: input,
			receipt: currentReceipt,
			receipt_validation: validateExecutionReceipt(input, currentReceipt),
			verified_assurance: undefined,
		});
		expect(result.eligible).toBe(false);
		expect(result.outcome.reason_code).toBe("INVALID_CONTRACT");
	});

	it("normalizes optional digest prefixes at the assurance boundary", () => {
		const unprefixedExecutor = B.slice("sha256:".length);
		const result = eligibility({
			executor_digest: unprefixedExecutor,
			run_binding_digest: runBindingDigest({
				workflowDigest: A,
				validatorDigest: B,
				executorDigest: unprefixedExecutor,
				policyDigest: A,
				policySnapshotDigest: C,
			}),
		});
		expect(result.eligible).toBe(true);
	});

	it("rejects a caller-selected run binding that does not match its components", () => {
		const result = eligibility({ run_binding_digest: D });
		expect(result.eligible).toBe(false);
		expect(result.outcome.reason_code).toBe("DIGEST_MISMATCH");
		expect(result.diagnostics).toContain("run binding digest does not match the canonical binding components");
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

	it("requires a frozen subject for a formal evaluation operation", () => {
		const input = request();
		input.operation = "formal_evaluation";
		input.subject = {
			...input.subject,
			dataset_identity_digest: C,
			scoring_protocol_digest: D,
		};
		const result = eligibility({
			request: input,
			receipt: receipt(input),
			receipt_validation: validateExecutionReceipt(input, receipt(input)),
			frozen_subject: undefined,
			verified_assurance: undefined,
		});
		expect(result.eligible).toBe(false);
		expect(result.diagnostics).toContain("formal evaluation requires an immutable evaluation subject");
	});

	it("admits a fully bound frozen formal subject", () => {
		const input = request();
		input.operation = "formal_evaluation";
		input.subject = {
			...input.subject,
			dataset_identity_digest: C,
			scoring_protocol_digest: D,
		};
		const currentReceipt = receipt(input);
		const frozen = frozenSubject(input, currentReceipt);
		const result = eligibility({
			request: input,
			receipt: currentReceipt,
			receipt_validation: validateExecutionReceipt(input, currentReceipt),
			frozen_subject: frozen,
			receipt_digest: canonicalJsonDigest(currentReceipt as unknown as JsonValue),
		});
		expect(result.eligible).toBe(true);
	});
});
