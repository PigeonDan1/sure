import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type { AssuranceProfile, FrozenSubjectRef, JsonValue } from "../contracts/types.ts";

const DIGEST = /^(?:sha256:)?[0-9a-f]{64}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

/**
 * The subject is the immutable hand-off between agent-dependent adaptation and
 * agent-independent evaluation.  `subject_digest` is calculated over every
 * other field, so a consumer can verify the file without trusting its path.
 */
export interface FrozenEvaluationSubject extends FrozenSubjectRef {
	schema: "sure.evaluation_subject.v1";
	subject_id: string;
	prediction_path: string;
	prediction_digest: string;
	execution_receipt_digest: string;
	evaluator_engine_digest: string;
	evaluator_route_digest: string;
	workflow_digest: string;
	validator_digest: string;
	executor_digest: string;
	policy_digest: string;
	reference_snapshot_digest: string;
	assurance_profile: AssuranceProfile;
	/** True means this is a readable legacy projection, never formal evidence. */
	legacy_unverified: boolean;
	approval_event_digest?: string;
	frozen_at: string;
	subject_digest: string;
}

type FrozenSubjectInput = Omit<FrozenEvaluationSubject, "schema" | "subject_digest"> & {
	subject_digest?: string;
};

function validDigest(value: unknown): value is string {
	return typeof value === "string" && DIGEST.test(value);
}

function absolutePath(value: unknown): value is string {
	return typeof value === "string" && value.startsWith("/") && value.length > 1;
}

function normalizeDigest(value: string): string {
	return value.startsWith("sha256:") ? value.toLowerCase() : `sha256:${value.toLowerCase()}`;
}

function withoutDigest(subject: FrozenEvaluationSubject | FrozenSubjectInput): Record<string, unknown> {
	const value = { ...subject } as Record<string, unknown>;
	delete value.subject_digest;
	return value;
}

export function frozenSubjectDigest(subject: FrozenEvaluationSubject | FrozenSubjectInput): string {
	return canonicalJsonDigest(withoutDigest(subject) as JsonValue);
}

export function validateFrozenEvaluationSubject(subject: unknown): string[] {
	const errors: string[] = [];
	if (typeof subject !== "object" || subject === null || Array.isArray(subject))
		return ["evaluation subject must be an object"];
	const value = subject as Record<string, unknown>;
	const allowedKeys = new Set([
		"schema",
		"subject_id",
		"bundle_manifest_path",
		"bundle_digest",
		"runtime_identity_digest",
		"inference_protocol_digest",
		"dataset_identity_digest",
		"scoring_protocol_digest",
		"prediction_path",
		"prediction_digest",
		"execution_receipt_digest",
		"evaluator_engine_digest",
		"evaluator_route_digest",
		"workflow_digest",
		"validator_digest",
		"executor_digest",
		"policy_digest",
		"reference_snapshot_digest",
		"assurance_profile",
		"legacy_unverified",
		"approval_event_digest",
		"frozen_at",
		"subject_digest",
	]);
	for (const key of Object.keys(value))
		if (!allowedKeys.has(key)) errors.push(`evaluation subject has unknown field ${key}`);
	if (value.schema !== "sure.evaluation_subject.v1") errors.push("evaluation subject schema is unsupported");
	if (typeof value.subject_id !== "string" || value.subject_id.length === 0)
		errors.push("evaluation subject subject_id is missing");
	for (const field of ["bundle_manifest_path", "prediction_path"] as const)
		if (!absolutePath(value[field])) errors.push(`evaluation subject ${field} must be an absolute path`);
	if (typeof value.frozen_at !== "string" || value.frozen_at.length === 0)
		errors.push("evaluation subject frozen_at is missing");
	else if (Number.isNaN(Date.parse(value.frozen_at)))
		errors.push("evaluation subject frozen_at must be an ISO date-time");
	if (typeof value.subject_id === "string" && !ID.test(value.subject_id))
		errors.push("evaluation subject subject_id is invalid");
	for (const field of [
		"bundle_digest",
		"runtime_identity_digest",
		"inference_protocol_digest",
		"dataset_identity_digest",
		"scoring_protocol_digest",
		"prediction_digest",
		"execution_receipt_digest",
		"evaluator_engine_digest",
		"evaluator_route_digest",
		"workflow_digest",
		"validator_digest",
		"executor_digest",
		"policy_digest",
		"reference_snapshot_digest",
		"subject_digest",
	] as const) {
		if (!validDigest(value[field])) errors.push(`evaluation subject ${field} must be a SHA-256 digest`);
	}
	if (
		value.assurance_profile !== "cooperative" &&
		value.assurance_profile !== "pi_enforced" &&
		value.assurance_profile !== "trusted"
	)
		errors.push("evaluation subject assurance_profile is invalid");
	if (typeof value.legacy_unverified !== "boolean")
		errors.push("evaluation subject legacy_unverified must be boolean");
	if (value.approval_event_digest !== undefined && !validDigest(value.approval_event_digest))
		errors.push("evaluation subject approval_event_digest must be a SHA-256 digest");
	if (
		errors.length === 0 &&
		!sameDigest(value.subject_digest, frozenSubjectDigest(value as unknown as FrozenEvaluationSubject))
	)
		errors.push("evaluation subject subject_digest does not match its contents");
	return errors;
}

export function sameDigest(left: unknown, right: unknown): boolean {
	return validDigest(left) && validDigest(right) && normalizeDigest(left) === normalizeDigest(right);
}

export function createFrozenEvaluationSubject(input: FrozenSubjectInput): FrozenEvaluationSubject {
	const subject = {
		...input,
		schema: "sure.evaluation_subject.v1" as const,
		subject_digest: "",
	};
	const normalized = { ...subject } as FrozenEvaluationSubject;
	for (const field of [
		"bundle_digest",
		"runtime_identity_digest",
		"inference_protocol_digest",
		"dataset_identity_digest",
		"scoring_protocol_digest",
		"prediction_digest",
		"execution_receipt_digest",
		"evaluator_engine_digest",
		"evaluator_route_digest",
		"workflow_digest",
		"validator_digest",
		"executor_digest",
		"policy_digest",
		"reference_snapshot_digest",
	] as const) {
		normalized[field] = normalizeDigest(String(normalized[field]));
	}
	if (normalized.approval_event_digest !== undefined)
		normalized.approval_event_digest = normalizeDigest(normalized.approval_event_digest);
	normalized.subject_digest = frozenSubjectDigest(normalized);
	return normalized;
}
