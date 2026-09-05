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

export interface ArtifactRef {
	artifact_id: string;
	path: string;
	resolved_path: string;
	sha256: string;
	size: number;
	media_type: string;
	origin: ArtifactOrigin;
	source_root: string;
	reference_snapshot_digest?: string;
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
	entrypoint: ExecutionEntrypoint;
	runtime_requirements: Record<string, JsonValue>;
	capability_requirements: CapabilityRequirement[];
	reference_snapshot_digest: string;
	output_root: OutputRootBinding;
	policy_digest: string;
	created_at: string;
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
	reference_snapshot_digest: string;
	output_root: OutputRootBinding;
	policy_digest: string;
	started_at: string;
	finished_at?: string;
	exit_code?: number;
	diagnostics?: Record<string, JsonValue>[];
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
