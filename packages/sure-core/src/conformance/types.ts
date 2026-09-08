import type {
	ArtifactRef,
	AssuranceProfile,
	ExecutionReceipt,
	ExecutionRequest,
	FrozenSubjectRef,
} from "../contracts/types.ts";
import type { ExecutionContractHistoryValidation } from "../execution/bundle.ts";
import type { ExecutionReceiptValidation } from "../execution/types.ts";
import type { CoreOutcome, ValidatorVerdict, WorkflowDisposition } from "../workflow/outcome.ts";
import type { VerifiedAssurance } from "./assurance.ts";
import type { FrozenEvaluationSubject } from "./frozen.ts";

export interface FrozenFormalSubject extends FrozenSubjectRef {
	dataset_identity_digest: string;
	scoring_protocol_digest: string;
}

export interface FormalEligibilityInput {
	request: ExecutionRequest;
	receipt: ExecutionReceipt;
	receipt_validation: ExecutionReceiptValidation;
	assurance_profile: AssuranceProfile;
	/** Persisted host-neutral Core implementation version used by this run. */
	core_version: string;
	validator_verdict: ValidatorVerdict;
	workflow_disposition: WorkflowDisposition;
	subject: FrozenFormalSubject;
	workflow_digest: string;
	validator_digest: string;
	executor_digest: string;
	policy_digest: string;
	/** Digest of the immutable resolved site-policy snapshot. */
	policy_snapshot_digest?: string;
	reference_snapshot_digest: string;
	/** Digest over the latest and immutable execution bundle re-audited by the consumer. */
	execution_history_digest?: string;
	/** Core audit result for the complete admission-v1 latest/history pair. */
	execution_history_validation?: ExecutionContractHistoryValidation;
	/** Digest of the durable validator evidence selected by the formal consumer. */
	validation_evidence_digest?: string;
	/** Run binding fixed before execution; never inferred from the attestation itself. */
	run_binding_digest?: string;
	evidence?: readonly ArtifactRef[];
	/** Self-digesting subject manifest required for formal evaluation operations. */
	frozen_subject?: FrozenEvaluationSubject;
	/** Digest of the receipt file bound by the subject manifest. */
	receipt_digest?: string;
	/** Digest of the admission trace reloaded from the execution bundle. */
	admission_digest?: string;
	/** Process-local result returned by a host-injected AssuranceVerifierPort. */
	verified_assurance?: VerifiedAssurance;
}

export interface FormalEligibilityResult {
	eligible: boolean;
	outcome: CoreOutcome;
	diagnostics: readonly string[];
	evidence: readonly ArtifactRef[];
}
