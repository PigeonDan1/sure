import type {
	ArtifactRef,
	AssuranceProfile,
	ExecutionReceipt,
	ExecutionRequest,
	FrozenSubjectRef,
} from "../contracts/types.ts";
import type { ExecutionReceiptValidation } from "../execution/types.ts";
import type { FrozenEvaluationSubject } from "./frozen.ts";
import type { CoreOutcome, ValidatorVerdict, WorkflowDisposition } from "../workflow/outcome.ts";

export interface FrozenFormalSubject extends FrozenSubjectRef {
	dataset_identity_digest: string;
	scoring_protocol_digest: string;
}

export interface FormalEligibilityInput {
	request: ExecutionRequest;
	receipt: ExecutionReceipt;
	receipt_validation: ExecutionReceiptValidation;
	assurance_profile: AssuranceProfile;
	validator_verdict: ValidatorVerdict;
	workflow_disposition: WorkflowDisposition;
	subject: FrozenFormalSubject;
	workflow_digest: string;
	validator_digest: string;
	executor_digest: string;
	policy_digest: string;
	reference_snapshot_digest: string;
	evidence?: readonly ArtifactRef[];
	/** Self-digesting subject manifest required for formal evaluation operations. */
	frozen_subject?: FrozenEvaluationSubject;
	/** Digest of the receipt file bound by the subject manifest. */
	receipt_digest?: string;
}

export interface FormalEligibilityResult {
	eligible: boolean;
	outcome: CoreOutcome;
	diagnostics: readonly string[];
	evidence: readonly ArtifactRef[];
}
