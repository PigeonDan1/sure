import { validateDockerRuntimeEvidence } from "../execution/docker.ts";
import { type CoreOutcome, createOutcome } from "../workflow/outcome.ts";
import { sameDigest as sameFrozenDigest, validateFrozenEvaluationSubject } from "./frozen.ts";
import type { FormalEligibilityInput, FormalEligibilityResult } from "./types.ts";

const DIGEST = /^(?:sha256:)?[0-9a-f]{64}$/;

function validDigest(value: unknown): value is string {
	return typeof value === "string" && DIGEST.test(value);
}

function sameDigest(left: unknown, right: unknown): boolean {
	return (
		validDigest(left) &&
		validDigest(right) &&
		left.replace(/^sha256:/, "").toLowerCase() === right.replace(/^sha256:/, "").toLowerCase()
	);
}

function blocked(
	reasonCode:
		| "UPGRADE_REQUIRED"
		| "DIGEST_MISMATCH"
		| "VALIDATION_FAILED"
		| "VALIDATION_PENDING"
		| "CAPABILITY_MISSING"
		| "UNKNOWN_CAPABILITY"
		| "POLICY_DENIED",
	messages: readonly string[],
): CoreOutcome {
	return createOutcome({
		validatorVerdict: reasonCode === "VALIDATION_FAILED" ? "FAIL" : "NOT_EXECUTED",
		workflowDisposition: reasonCode === "VALIDATION_FAILED" ? "BLOCK" : "BLOCK",
		reasonCode,
		diagnostics: messages.map((message) => ({ code: reasonCode, message })),
	});
}

/**
 * Formal evaluation is a second boundary after execution and artifact
 * validation. A cooperative host may produce useful evidence, but it cannot
 * self-attest the lifecycle needed for a reproducible leaderboard result.
 */
export function assessFormalEligibility(input: FormalEligibilityInput): FormalEligibilityResult {
	const diagnostics: string[] = [];
	const formalOperation = input.request.operation === "formal_evaluation";
	if (formalOperation) {
		if (input.frozen_subject === undefined) {
			diagnostics.push("formal evaluation requires an immutable evaluation subject");
		} else {
			for (const error of validateFrozenEvaluationSubject(input.frozen_subject)) diagnostics.push(error);
			if (input.frozen_subject.legacy_unverified) {
				diagnostics.push("legacy_unverified evaluation subjects are not eligible for formal evaluation");
			}
			if (!input.frozen_subject.approval_event_digest) {
				diagnostics.push("formal evaluation requires a digest-bound human approval event");
			}
			if (
				!input.receipt_digest ||
				!sameFrozenDigest(input.receipt_digest, input.frozen_subject.execution_receipt_digest)
			) {
				diagnostics.push("execution receipt digest does not match the frozen evaluation subject");
			}
			const bindings: Array<[string, unknown, unknown]> = [
				["bundle", input.request.subject.bundle_digest, input.frozen_subject.bundle_digest],
				[
					"runtime identity",
					input.request.subject.runtime_identity_digest,
					input.frozen_subject.runtime_identity_digest,
				],
				[
					"inference protocol",
					input.request.subject.inference_protocol_digest,
					input.frozen_subject.inference_protocol_digest,
				],
				[
					"dataset identity",
					input.request.subject.dataset_identity_digest,
					input.frozen_subject.dataset_identity_digest,
				],
				[
					"scoring protocol",
					input.request.subject.scoring_protocol_digest,
					input.frozen_subject.scoring_protocol_digest,
				],
				["workflow", input.workflow_digest, input.frozen_subject.workflow_digest],
				["validator", input.validator_digest, input.frozen_subject.validator_digest],
				["executor", input.executor_digest, input.frozen_subject.executor_digest],
				["policy", input.policy_digest, input.frozen_subject.policy_digest],
				["reference snapshot", input.reference_snapshot_digest, input.frozen_subject.reference_snapshot_digest],
			];
			for (const [name, current, frozen] of bindings) {
				if (!sameFrozenDigest(current, frozen))
					diagnostics.push(`${name} digest does not match the frozen evaluation subject`);
			}
		}
	}
	if (input.assurance_profile === "cooperative") {
		diagnostics.push("cooperative host enforcement is insufficient for formal evaluation");
	}
	if (input.assurance_profile === "pi_enforced" && input.receipt.executor.trust_level === "cooperative") {
		diagnostics.push("pi_enforced assurance requires a host-enforced or attested executor receipt");
	}
	if (input.assurance_profile === "trusted" && input.receipt.executor.trust_level !== "attested") {
		diagnostics.push("trusted assurance requires an attested executor receipt");
	}
	if (!input.receipt_validation.valid) diagnostics.push(...input.receipt_validation.errors);
	if (formalOperation && input.request.runtime_requirements.executor_kind === "docker") {
		for (const error of validateDockerRuntimeEvidence(input.request, input.receipt)) diagnostics.push(error);
	}
	if (input.receipt.lifecycle !== "SUCCEEDED")
		diagnostics.push(`execution lifecycle ${input.receipt.lifecycle} is not SUCCEEDED`);
	if (!input.receipt_validation.capability.admitted) {
		diagnostics.push(
			...input.receipt_validation.capability.missing.map((id) => `required capability ${id} is missing`),
		);
		diagnostics.push(
			...input.receipt_validation.capability.unknown.map((id) => `required capability ${id} is unknown`),
		);
		diagnostics.push(
			...input.receipt_validation.capability.denied.map((id) => `required capability ${id} is denied`),
		);
		diagnostics.push(
			...input.receipt_validation.capability.invalid_evidence.map((id) => `capability evidence ${id} is invalid`),
		);
	}
	if (input.validator_verdict !== "PASS") diagnostics.push(`formal validator verdict is ${input.validator_verdict}`);
	if (!(["ADVANCE", "TERMINATE"] as readonly string[]).includes(input.workflow_disposition)) {
		diagnostics.push(`workflow disposition ${input.workflow_disposition} is not terminally admissible`);
	}
	const requiredDigests: Array<[string, unknown]> = [
		["subject.bundle_digest", input.subject.bundle_digest],
		["subject.runtime_identity_digest", input.subject.runtime_identity_digest],
		["subject.inference_protocol_digest", input.subject.inference_protocol_digest],
		["subject.dataset_identity_digest", input.subject.dataset_identity_digest],
		["subject.scoring_protocol_digest", input.subject.scoring_protocol_digest],
		["workflow_digest", input.workflow_digest],
		["validator_digest", input.validator_digest],
		["executor_digest", input.executor_digest],
		["policy_digest", input.policy_digest],
		["reference_snapshot_digest", input.reference_snapshot_digest],
	];
	for (const [name, value] of requiredDigests)
		if (!validDigest(value)) diagnostics.push(`${name} is not a SHA-256 digest`);
	for (const [name, requestValue, frozenValue] of [
		["bundle", input.request.subject.bundle_digest, input.subject.bundle_digest],
		["runtime identity", input.request.subject.runtime_identity_digest, input.subject.runtime_identity_digest],
		["inference protocol", input.request.subject.inference_protocol_digest, input.subject.inference_protocol_digest],
		["dataset identity", input.request.subject.dataset_identity_digest, input.subject.dataset_identity_digest],
		["scoring protocol", input.request.subject.scoring_protocol_digest, input.subject.scoring_protocol_digest],
	] as const) {
		if (input.request.operation === "formal_evaluation" && !validDigest(requestValue)) {
			diagnostics.push(`formal request ${name} digest is missing`);
			continue;
		}
		if (requestValue === undefined) continue;
		if (!sameDigest(requestValue, frozenValue))
			diagnostics.push(`request ${name} digest does not match the frozen subject`);
	}
	if (!sameDigest(input.receipt.executor.digest, input.executor_digest))
		diagnostics.push("receipt executor digest does not match conformance executor digest");
	if (!sameDigest(input.request.policy_digest, input.policy_digest))
		diagnostics.push("request policy digest does not match conformance policy digest");
	if (!sameDigest(input.receipt.policy_digest, input.policy_digest))
		diagnostics.push("receipt policy digest does not match conformance policy digest");
	if (!sameDigest(input.request.reference_snapshot_digest, input.reference_snapshot_digest))
		diagnostics.push("request reference snapshot does not match conformance snapshot");
	const reasonCode =
		input.receipt_validation.capability.unknown.length > 0
			? "UNKNOWN_CAPABILITY"
			: input.receipt_validation.capability.denied.length > 0
				? "POLICY_DENIED"
				: diagnostics.some((message) => message.includes("capability"))
					? "CAPABILITY_MISSING"
					: diagnostics.some((message) => message.includes("digest") || message.includes("SHA-256"))
						? "DIGEST_MISMATCH"
						: input.validator_verdict === "FAIL"
							? "VALIDATION_FAILED"
							: input.validator_verdict === "NOT_EXECUTED" || input.receipt.lifecycle !== "SUCCEEDED"
								? "VALIDATION_PENDING"
								: input.assurance_profile === "cooperative"
									? "UPGRADE_REQUIRED"
									: "VALIDATION_FAILED";
	if (diagnostics.length > 0) {
		return {
			eligible: false,
			outcome: blocked(reasonCode, diagnostics),
			diagnostics,
			evidence: input.evidence ?? [],
		};
	}
	return {
		eligible: true,
		outcome: createOutcome({
			validatorVerdict: "PASS",
			workflowDisposition: input.workflow_disposition,
			reasonCode: "VALIDATION_PASSED",
			executionLifecycle: input.receipt.lifecycle,
		}),
		diagnostics: [],
		evidence: input.evidence ?? [],
	};
}
