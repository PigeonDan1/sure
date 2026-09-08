import type {
	ExecutionLifecycle,
	PublicOutcome,
	ReasonCode,
	ValidatorVerdict,
	WorkflowDisposition,
} from "../workflow/outcome.ts";

export type JsonPrimitive = boolean | number | string | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export const ARTIFACT_ORIGINS = ["local_staging", "read_only_reference", "generated", "external"] as const;
export type ArtifactOrigin = (typeof ARTIFACT_ORIGINS)[number];

export const EXECUTION_ARTIFACT_MODES = ["preexisting", "mutating", "producing"] as const;
export type ExecutionArtifactMode = (typeof EXECUTION_ARTIFACT_MODES)[number];

export const EXECUTION_OUTPUT_KINDS = ["file", "directory"] as const;
export type ExecutionOutputKind = (typeof EXECUTION_OUTPUT_KINDS)[number];

export const EXECUTION_INPUT_LOCATOR_KINDS = ["run_artifact", "run_path", "resolved_input_field"] as const;
export type ExecutionInputLocatorKind = (typeof EXECUTION_INPUT_LOCATOR_KINDS)[number];

export const EXECUTION_DIGEST_KINDS = ["file_sha256", "tree_sha256"] as const;
export type ExecutionDigestKind = (typeof EXECUTION_DIGEST_KINDS)[number];

export interface ArtifactRef {
	artifact_id: string;
	path: string;
	resolved_path: string;
	sha256: string;
	size: number;
	media_type: string;
	origin: ArtifactOrigin;
	source_root: string;
	/** Omitted for legacy file artifacts; directory outputs must set this explicitly. */
	kind?: ExecutionOutputKind;
	/** Omitted for legacy file artifacts; directory outputs use a deterministic tree digest. */
	digest_kind?: ExecutionDigestKind;
	reference_snapshot_digest?: string;
}

/** A resolved input-to-artifact mapping recorded in an execution request. */
export interface ExecutionInputBindingEntry {
	input_id: string;
	locator_kind: ExecutionInputLocatorKind;
	path: string;
	artifact: ArtifactRef;
}

/**
 * Host-neutral proof that one immutable input context selected one operation
 * branch and that every declared input was bound before execution.
 */
export interface ExecutionInputBinding {
	schema: "sure.execution_input_binding.v1";
	contract_digest: string;
	selector_id: string;
	context_artifact: string;
	context_digest: string;
	inputs: ExecutionInputBindingEntry[];
	binding_digest: string;
}

/** A path is relative to ExecutionRequest.output_root.resolved_path. */
export interface ExecutionOutputSpec {
	artifact_id: string;
	path: string;
	kind: ExecutionOutputKind;
	required: boolean;
}

/**
 * Declares the output boundary for an executor operation. The request binds
 * this object; the receipt binds its digest and the observed output set.
 */
export interface ExecutionOutputContract {
	schema: "sure.execution_output_contract.v1";
	mode: ExecutionArtifactMode;
	outputs: ExecutionOutputSpec[];
	/** Relative paths (or directory roots) where failed-run residue may remain. */
	temporary_paths: string[];
	/** Required outputs may be absent on a non-success lifecycle when true. */
	allow_missing_on_failure: boolean;
	/** Whether the receipt may retain and describe temporary residue. */
	retain_failed_outputs: boolean;
}

export interface ExecutionOutputResidual {
	path: string;
	resolved_path: string;
	kind: ExecutionOutputKind;
	status: "present" | "missing";
	sha256?: string;
	digest_kind?: ExecutionDigestKind;
	size?: number;
}

export interface ReferenceSnapshot {
	schema: "sure.reference_snapshot.v1";
	snapshot_id: string;
	digest: string;
	root: string;
	resolved_root: string;
	observed_at: string;
}

export const CAPABILITY_CLASSES = ["agent_capability", "execution_capability"] as const;
export type CapabilityClass = (typeof CAPABILITY_CLASSES)[number];

export const CAPABILITY_STATUSES = ["AVAILABLE", "MISSING", "UNKNOWN", "DENIED"] as const;
export type CapabilityStatus = (typeof CAPABILITY_STATUSES)[number];

export const CAPABILITY_EVIDENCE_SOURCES = [
	"agent",
	"host_probe",
	"executor",
	"site_policy",
	"trusted_attestation",
] as const;
export type CapabilityEvidenceSource = (typeof CAPABILITY_EVIDENCE_SOURCES)[number];

export interface CapabilityRequirement {
	capability_id: string;
	capability_class: CapabilityClass;
	required: boolean;
	constraints?: Record<string, JsonValue>;
}

export interface CapabilityEvidence {
	capability_id: string;
	capability_class: CapabilityClass;
	status: CapabilityStatus;
	source: CapabilityEvidenceSource;
	observed_at: string;
	evidence_digest?: string;
	details?: Record<string, JsonValue>;
}

export interface CapabilityReport {
	schema: "sure.capability_report.v1";
	requirements: CapabilityRequirement[];
	evidence: CapabilityEvidence[];
	checked_at: string;
}

export const EXECUTION_OPERATIONS = ["validation", "inference", "formal_evaluation", "package", "publication"] as const;
export type ExecutionOperation = (typeof EXECUTION_OPERATIONS)[number];

export interface ExecutionEntrypoint {
	executable: string;
	argv: string[];
	working_directory?: string;
}

export interface OutputRootBinding {
	path: string;
	resolved_path: string;
	scope_id: string;
	policy_digest: string;
	writable: true;
}

export interface FrozenSubjectRef {
	bundle_manifest_path: string;
	bundle_digest: string;
	runtime_identity_digest: string;
	inference_protocol_digest?: string;
	dataset_identity_digest?: string;
	scoring_protocol_digest?: string;
}

export interface ExecutionRequest {
	schema: "sure.execution_request.v1";
	request_id: string;
	semantic_request_digest: string;
	run_id: string;
	unit_id: string;
	attempt: number;
	operation: ExecutionOperation;
	subject: FrozenSubjectRef;
	inputs: ArtifactRef[];
	/** Present for operations selected through an input contract. */
	input_binding?: ExecutionInputBinding;
	entrypoint: ExecutionEntrypoint;
	runtime_requirements: Record<string, JsonValue>;
	capability_requirements: CapabilityRequirement[];
	reference_snapshot_digest: string;
	output_root: OutputRootBinding;
	policy_digest: string;
	/** Digest of the immutable site-policy snapshot captured for this run. */
	policy_snapshot_digest?: string;
	/** Content digest of the admitted external adapter manifest. */
	adapter_manifest_digest?: string;
	created_at: string;
	/** Optional for backwards compatibility; required for producer operations. */
	output_contract?: ExecutionOutputContract;
}

export const EXECUTOR_KINDS = ["local", "python", "docker", "remote", "trusted"] as const;
export type ExecutorKind = (typeof EXECUTOR_KINDS)[number];

export const EXECUTOR_TRUST_LEVELS = ["cooperative", "host_enforced", "attested"] as const;
export type ExecutorTrustLevel = (typeof EXECUTOR_TRUST_LEVELS)[number];

export interface ExecutorIdentity {
	executor_id: string;
	kind: ExecutorKind;
	version: string;
	digest: string;
	trust_level: ExecutorTrustLevel;
}

export interface ExecutionReceipt {
	schema: "sure.execution_receipt.v1";
	receipt_id: string;
	request_id: string;
	request_digest: string;
	semantic_request_digest: string;
	run_id: string;
	unit_id: string;
	attempt: number;
	executor: ExecutorIdentity;
	lifecycle: ExecutionLifecycle;
	capability_evidence: CapabilityEvidence[];
	outputs: ArtifactRef[];
	/** Digest of request.input_binding when conditional inputs were bound. */
	input_binding_digest?: string;
	reference_snapshot_digest: string;
	output_root: OutputRootBinding;
	policy_digest: string;
	/** Digest of the immutable site-policy snapshot used by the executor. */
	policy_snapshot_digest?: string;
	/** Echo of the external adapter manifest selected by the request. */
	adapter_manifest_digest?: string;
	started_at: string;
	finished_at?: string;
	exit_code?: number;
	diagnostics?: Record<string, JsonValue>[];
	/** Digest of request.output_contract when an output contract is present. */
	output_contract_digest?: string;
	/** Digest of the canonical observed outputs and failure residuals. */
	output_set_digest?: string;
	residuals?: ExecutionOutputResidual[];
}

export const ASSURANCE_PROFILES = ["cooperative", "pi_enforced", "trusted"] as const;
export type AssuranceProfile = (typeof ASSURANCE_PROFILES)[number];

export interface ConformanceRecord {
	schema: "sure.conformance.v1";
	conformance_id: string;
	run_id: string;
	unit_id: string;
	attempt: number;
	request_digest: string;
	receipt_digest: string;
	subject_bundle_digest: string;
	/** Digest of the self-binding evaluation_subject.v1 manifest, when present. */
	subject_manifest_digest?: string;
	prediction_digest?: string;
	evaluator_engine_digest?: string;
	evaluator_route_digest?: string;
	approval_event_digest?: string;
	/** Host-neutral Core implementation version used for the conformance decision. */
	core_version?: string;
	admission_digest?: string;
	validation_evidence_digest?: string;
	run_binding_digest?: string;
	policy_snapshot_digest?: string;
	/** Digest of a host-verified assurance attestation; required for formal eligibility. */
	assurance_attestation_digest?: string;
	/** Digest over the re-audited latest and immutable execution contract bundles. */
	execution_history_digest?: string;
	legacy_unverified?: boolean;
	runtime_identity_digest?: string;
	inference_protocol_digest?: string;
	dataset_identity_digest?: string;
	scoring_protocol_digest?: string;
	workflow_digest: string;
	validator_digest: string;
	executor_digest: string;
	policy_digest: string;
	reference_snapshot_digest: string;
	output_root: OutputRootBinding;
	validator_verdict: ValidatorVerdict;
	workflow_disposition: WorkflowDisposition;
	outcome: PublicOutcome;
	reason_code: ReasonCode;
	assurance_profile: AssuranceProfile;
	formal_evaluation_eligible: boolean;
	checked_at: string;
	evidence: ArtifactRef[];
	diagnostics: Record<string, JsonValue>[];
}
