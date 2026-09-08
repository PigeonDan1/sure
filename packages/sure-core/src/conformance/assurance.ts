import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type { AssuranceProfile, JsonValue } from "../contracts/types.ts";

export const ASSURANCE_ATTESTATION_SCHEMA = "sure.assurance_attestation.v1" as const;

export const ASSURANCE_ISSUER_KINDS = ["portable_host", "pi_harness", "trusted_service"] as const;
export type AssuranceIssuerKind = (typeof ASSURANCE_ISSUER_KINDS)[number];

export const ASSURANCE_PROOF_KINDS = ["host_store", "signature", "trusted_attestation"] as const;
export type AssuranceProofKind = (typeof ASSURANCE_PROOF_KINDS)[number];

export interface AssuranceIssuerIdentity {
	issuer_id: string;
	kind: AssuranceIssuerKind;
	version: string;
	digest: string;
}

export interface AssuranceProof {
	kind: AssuranceProofKind;
	verification_material_id: string;
	value: string;
}

/**
 * Host-issued evidence over the complete formal-evaluation boundary.
 *
 * This document is only a claim until an out-of-band AssuranceVerifierPort
 * verifies it against a host-owned trust anchor. A self-digest or a file in the
 * run directory never grants an assurance profile by itself.
 */
export interface AssuranceAttestation {
	schema: typeof ASSURANCE_ATTESTATION_SCHEMA;
	attestation_id: string;
	issuer: AssuranceIssuerIdentity;
	assurance_profile: AssuranceProfile;
	core_version: string;
	run_id: string;
	unit_id: string;
	attempt: number;
	request_digest: string;
	admission_digest: string;
	receipt_digest: string;
	execution_history_digest: string;
	validation_evidence_digest: string;
	subject_digest: string;
	approval_event_digest: string;
	workflow_digest: string;
	validator_digest: string;
	executor_digest: string;
	policy_digest: string;
	policy_snapshot_digest: string;
	reference_snapshot_digest: string;
	run_binding_digest: string;
	event_sequence: number;
	previous_event_digest?: string;
	issued_at: string;
	statement_digest: string;
	proof: AssuranceProof;
	attestation_digest: string;
}

export type AssuranceAttestationInput = Omit<
	AssuranceAttestation,
	"schema" | "statement_digest" | "attestation_digest"
>;

export interface AssuranceExpectedBinding {
	assurance_profile: AssuranceProfile;
	core_version: string;
	run_id: string;
	unit_id: string;
	attempt: number;
	request_digest: string;
	admission_digest: string;
	receipt_digest: string;
	execution_history_digest: string;
	validation_evidence_digest: string;
	subject_digest: string;
	approval_event_digest: string;
	workflow_digest: string;
	validator_digest: string;
	executor_digest: string;
	policy_digest: string;
	policy_snapshot_digest: string;
	reference_snapshot_digest: string;
	run_binding_digest: string;
}

export interface AssuranceTrustAnchor {
	issuer_id: string;
	issuer_kind: AssuranceIssuerKind;
	issuer_digest: string;
	maximum_profile: AssuranceProfile;
	status: "active" | "revoked";
	proof_kinds: readonly AssuranceProofKind[];
}

export interface AssuranceProofResult {
	verified: boolean;
	diagnostics?: readonly string[];
}

/** Host-owned registry/proof adapter. Implementations must not trust run artifacts. */
export interface AssuranceVerifierPort {
	resolveTrustAnchor(issuerId: string): AssuranceTrustAnchor | undefined;
	verifyProof(attestation: AssuranceAttestation, anchor: AssuranceTrustAnchor): AssuranceProofResult;
}

export interface VerifiedAssurance {
	readonly profile: AssuranceProfile;
	readonly attestation: AssuranceAttestation;
	readonly trust_anchor: AssuranceTrustAnchor;
}

export type AssuranceVerificationResult =
	| { verified: true; assurance: VerifiedAssurance; diagnostics: readonly [] }
	| { verified: false; diagnostics: readonly string[] };

const DIGEST = /^(?:sha256:)?[0-9a-f]{64}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const verifiedResults = new WeakMap<object, { attestation_digest: string; profile: AssuranceProfile }>();

const PROFILE_RANK: Readonly<Record<AssuranceProfile, number>> = {
	cooperative: 0,
	pi_enforced: 1,
	trusted: 2,
};

const ISSUER_CEILING: Readonly<Record<AssuranceIssuerKind, AssuranceProfile>> = {
	portable_host: "cooperative",
	pi_harness: "pi_enforced",
	trusted_service: "trusted",
};

function object(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

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

function frozenAttestation(value: AssuranceAttestation): AssuranceAttestation {
	return Object.freeze({
		...value,
		issuer: Object.freeze({ ...value.issuer }),
		proof: Object.freeze({ ...value.proof }),
	});
}

function frozenTrustAnchor(value: AssuranceTrustAnchor): AssuranceTrustAnchor {
	return Object.freeze({ ...value, proof_kinds: Object.freeze([...value.proof_kinds]) });
}

function trustAnchorDiagnostics(value: unknown): string[] {
	if (!object(value)) return ["assurance trust anchor must be an object"];
	const errors: string[] = [];
	if (typeof value.issuer_id !== "string" || !ID.test(value.issuer_id))
		errors.push("assurance trust anchor issuer_id is invalid");
	if (!ASSURANCE_ISSUER_KINDS.includes(value.issuer_kind as AssuranceIssuerKind))
		errors.push("assurance trust anchor issuer_kind is invalid");
	if (!validDigest(value.issuer_digest)) errors.push("assurance trust anchor issuer_digest is invalid");
	if (
		value.maximum_profile !== "cooperative" &&
		value.maximum_profile !== "pi_enforced" &&
		value.maximum_profile !== "trusted"
	)
		errors.push("assurance trust anchor maximum_profile is invalid");
	if (value.status !== "active" && value.status !== "revoked") errors.push("assurance trust anchor status is invalid");
	if (
		!Array.isArray(value.proof_kinds) ||
		value.proof_kinds.length === 0 ||
		value.proof_kinds.some((kind) => !ASSURANCE_PROOF_KINDS.includes(kind as AssuranceProofKind))
	) {
		errors.push("assurance trust anchor proof_kinds are invalid");
	}
	if (
		ASSURANCE_ISSUER_KINDS.includes(value.issuer_kind as AssuranceIssuerKind) &&
		(value.maximum_profile === "cooperative" ||
			value.maximum_profile === "pi_enforced" ||
			value.maximum_profile === "trusted") &&
		PROFILE_RANK[value.maximum_profile] > PROFILE_RANK[ISSUER_CEILING[value.issuer_kind as AssuranceIssuerKind]]
	) {
		errors.push("assurance trust anchor profile exceeds the issuer-kind ceiling");
	}
	return errors;
}

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

function statementValue(attestation: AssuranceAttestation | AssuranceAttestationInput): Record<string, unknown> {
	const value = { ...attestation } as Record<string, unknown>;
	delete value.schema;
	delete value.statement_digest;
	delete value.proof;
	delete value.attestation_digest;
	return value;
}

function attestationValue(attestation: AssuranceAttestation): Record<string, unknown> {
	const value = { ...attestation } as Record<string, unknown>;
	delete value.attestation_digest;
	return value;
}

export function assuranceStatementDigest(attestation: AssuranceAttestation | AssuranceAttestationInput): string {
	return canonicalJsonDigest({
		schema: "sure.assurance_statement.v1",
		...statementValue(attestation),
	} as JsonValue);
}

export function assuranceAttestationDigest(attestation: AssuranceAttestation): string {
	return canonicalJsonDigest(attestationValue(attestation) as JsonValue);
}

export function createAssuranceAttestation(input: AssuranceAttestationInput): AssuranceAttestation {
	const statementDigest = assuranceStatementDigest(input);
	const attestation: AssuranceAttestation = {
		schema: ASSURANCE_ATTESTATION_SCHEMA,
		...input,
		statement_digest: statementDigest,
		attestation_digest: "",
	};
	attestation.attestation_digest = assuranceAttestationDigest(attestation);
	return attestation;
}

export function validateAssuranceAttestation(value: unknown): string[] {
	if (!object(value)) return ["assurance attestation must be an object"];
	const errors: string[] = [];
	const allowed = new Set([
		"schema",
		"attestation_id",
		"issuer",
		"assurance_profile",
		"core_version",
		"run_id",
		"unit_id",
		"attempt",
		"request_digest",
		"admission_digest",
		"receipt_digest",
		"execution_history_digest",
		"validation_evidence_digest",
		"subject_digest",
		"approval_event_digest",
		"workflow_digest",
		"validator_digest",
		"executor_digest",
		"policy_digest",
		"policy_snapshot_digest",
		"reference_snapshot_digest",
		"run_binding_digest",
		"event_sequence",
		"previous_event_digest",
		"issued_at",
		"statement_digest",
		"proof",
		"attestation_digest",
	]);
	for (const key of Object.keys(value))
		if (!allowed.has(key)) errors.push(`assurance attestation has unknown field ${key}`);
	if (value.schema !== ASSURANCE_ATTESTATION_SCHEMA) errors.push("assurance attestation schema is unsupported");
	for (const field of ["attestation_id", "run_id", "unit_id"] as const) {
		if (typeof value[field] !== "string" || !ID.test(value[field]))
			errors.push(`assurance attestation ${field} is invalid`);
	}
	if (!Number.isSafeInteger(value.attempt) || Number(value.attempt) < 1)
		errors.push("assurance attestation attempt must be a positive integer");
	if (!Number.isSafeInteger(value.event_sequence) || Number(value.event_sequence) < 1)
		errors.push("assurance attestation event_sequence must be a positive integer");
	if (
		value.assurance_profile !== "cooperative" &&
		value.assurance_profile !== "pi_enforced" &&
		value.assurance_profile !== "trusted"
	)
		errors.push("assurance attestation assurance_profile is invalid");
	if (typeof value.core_version !== "string" || value.core_version.length === 0)
		errors.push("assurance attestation core_version is missing");
	for (const field of [
		"request_digest",
		"admission_digest",
		"receipt_digest",
		"execution_history_digest",
		"validation_evidence_digest",
		"subject_digest",
		"approval_event_digest",
		"workflow_digest",
		"validator_digest",
		"executor_digest",
		"policy_digest",
		"policy_snapshot_digest",
		"reference_snapshot_digest",
		"run_binding_digest",
		"statement_digest",
		"attestation_digest",
	] as const) {
		if (typeof value[field] !== "string" || !DIGEST.test(value[field]))
			errors.push(`assurance attestation ${field} must be a SHA-256 digest`);
	}
	if (value.previous_event_digest !== undefined && !validDigest(value.previous_event_digest))
		errors.push("assurance attestation previous_event_digest must be a SHA-256 digest");
	if (value.event_sequence === 1 && value.previous_event_digest !== undefined)
		errors.push("first assurance event cannot declare previous_event_digest");
	if (
		typeof value.event_sequence === "number" &&
		value.event_sequence > 1 &&
		value.previous_event_digest === undefined
	)
		errors.push("subsequent assurance event requires previous_event_digest");
	if (typeof value.issued_at !== "string" || Number.isNaN(Date.parse(value.issued_at)))
		errors.push("assurance attestation issued_at must be an ISO date-time");
	if (!object(value.issuer)) {
		errors.push("assurance attestation issuer must be an object");
	} else {
		const issuerKeys = new Set(["issuer_id", "kind", "version", "digest"]);
		for (const key of Object.keys(value.issuer))
			if (!issuerKeys.has(key)) errors.push(`assurance attestation issuer has unknown field ${key}`);
		if (typeof value.issuer.issuer_id !== "string" || !ID.test(value.issuer.issuer_id))
			errors.push("assurance attestation issuer_id is invalid");
		if (!ASSURANCE_ISSUER_KINDS.includes(value.issuer.kind as AssuranceIssuerKind))
			errors.push("assurance attestation issuer kind is invalid");
		if (typeof value.issuer.version !== "string" || value.issuer.version.length === 0)
			errors.push("assurance attestation issuer version is missing");
		if (typeof value.issuer.digest !== "string" || !DIGEST.test(value.issuer.digest))
			errors.push("assurance attestation issuer digest must be a SHA-256 digest");
	}
	if (!object(value.proof)) {
		errors.push("assurance attestation proof must be an object");
	} else {
		const proofKeys = new Set(["kind", "verification_material_id", "value"]);
		for (const key of Object.keys(value.proof))
			if (!proofKeys.has(key)) errors.push(`assurance attestation proof has unknown field ${key}`);
		if (!ASSURANCE_PROOF_KINDS.includes(value.proof.kind as AssuranceProofKind))
			errors.push("assurance attestation proof kind is invalid");
		if (typeof value.proof.verification_material_id !== "string" || !ID.test(value.proof.verification_material_id))
			errors.push("assurance attestation proof verification_material_id is invalid");
		if (typeof value.proof.value !== "string" || value.proof.value.length === 0)
			errors.push("assurance attestation proof value is missing");
	}
	if (
		object(value.issuer) &&
		ASSURANCE_ISSUER_KINDS.includes(value.issuer.kind as AssuranceIssuerKind) &&
		(value.assurance_profile === "cooperative" ||
			value.assurance_profile === "pi_enforced" ||
			value.assurance_profile === "trusted") &&
		PROFILE_RANK[value.assurance_profile] > PROFILE_RANK[ISSUER_CEILING[value.issuer.kind as AssuranceIssuerKind]]
	) {
		errors.push("assurance profile exceeds the issuer-kind ceiling");
	}
	if (errors.length === 0) {
		const attestation = value as unknown as AssuranceAttestation;
		if (!sameDigest(attestation.statement_digest, assuranceStatementDigest(attestation)))
			errors.push("assurance attestation statement_digest does not match its contents");
		if (!sameDigest(attestation.attestation_digest, assuranceAttestationDigest(attestation)))
			errors.push("assurance attestation attestation_digest does not match its contents");
	}
	return errors;
}

function bindingDiagnostics(attestation: AssuranceAttestation, expected: AssuranceExpectedBinding): string[] {
	const errors: string[] = [];
	for (const field of ["run_id", "unit_id", "attempt", "assurance_profile", "core_version"] as const) {
		if (attestation[field] !== expected[field]) errors.push(`assurance attestation ${field} does not match`);
	}
	for (const field of [
		"request_digest",
		"admission_digest",
		"receipt_digest",
		"execution_history_digest",
		"validation_evidence_digest",
		"subject_digest",
		"approval_event_digest",
		"workflow_digest",
		"validator_digest",
		"executor_digest",
		"policy_digest",
		"policy_snapshot_digest",
		"reference_snapshot_digest",
		"run_binding_digest",
	] as const) {
		if (!sameDigest(attestation[field], expected[field]))
			errors.push(`assurance attestation ${field} does not match`);
	}
	return errors;
}

/**
 * Verify an attestation with a host-provided trust service. The branded result
 * is intentionally process-local: deserializing a JSON object never recreates
 * verified authority and every consumer must invoke its verifier again.
 */
export function verifyAssuranceAttestation(
	value: unknown,
	expected: AssuranceExpectedBinding,
	verifier: AssuranceVerifierPort,
): AssuranceVerificationResult {
	const errors = validateAssuranceAttestation(value);
	if (errors.length > 0) return { verified: false, diagnostics: errors };
	const attestation = frozenAttestation(value as AssuranceAttestation);
	errors.push(...bindingDiagnostics(attestation, expected));
	let resolvedAnchor: AssuranceTrustAnchor | undefined;
	try {
		resolvedAnchor = verifier.resolveTrustAnchor(attestation.issuer.issuer_id);
	} catch (error) {
		errors.push(`assurance trust anchor resolution failed: ${errorMessage(error)}`);
	}
	const anchorErrors = resolvedAnchor === undefined ? [] : trustAnchorDiagnostics(resolvedAnchor);
	errors.push(...anchorErrors);
	const anchor =
		resolvedAnchor === undefined || anchorErrors.length > 0 ? undefined : frozenTrustAnchor(resolvedAnchor);
	if (anchor === undefined) {
		if (
			resolvedAnchor === undefined &&
			!errors.some((message) => message.startsWith("assurance trust anchor resolution failed"))
		)
			errors.push("assurance issuer is not registered by the host");
	} else {
		if (anchor.status !== "active") errors.push("assurance issuer is revoked");
		if (anchor.issuer_id !== attestation.issuer.issuer_id)
			errors.push("assurance issuer id does not match trust anchor");
		if (anchor.issuer_kind !== attestation.issuer.kind)
			errors.push("assurance issuer kind does not match trust anchor");
		if (!sameDigest(anchor.issuer_digest, attestation.issuer.digest))
			errors.push("assurance issuer digest does not match trust anchor");
		if (PROFILE_RANK[attestation.assurance_profile] > PROFILE_RANK[anchor.maximum_profile])
			errors.push("assurance profile exceeds the trust anchor ceiling");
		if (PROFILE_RANK[attestation.assurance_profile] > PROFILE_RANK[ISSUER_CEILING[attestation.issuer.kind]])
			errors.push("assurance profile exceeds the issuer-kind ceiling");
		if (!anchor.proof_kinds.includes(attestation.proof.kind))
			errors.push("assurance proof kind is not admitted by the trust anchor");
		if (errors.length === 0) {
			try {
				const proof = verifier.verifyProof(attestation, anchor);
				if (!object(proof) || proof.verified !== true) {
					const suppliedDiagnostics =
						object(proof) &&
						Array.isArray(proof.diagnostics) &&
						proof.diagnostics.every((message) => typeof message === "string")
							? proof.diagnostics
							: [];
					const diagnostics =
						suppliedDiagnostics.length > 0 ? suppliedDiagnostics : ["assurance proof verification failed"];
					errors.push(...diagnostics);
				}
			} catch (error) {
				errors.push(`assurance proof verification failed: ${errorMessage(error)}`);
			}
		}
	}
	if (errors.length > 0 || anchor === undefined) return { verified: false, diagnostics: errors };
	const assurance: VerifiedAssurance = Object.freeze({
		profile: attestation.assurance_profile,
		attestation,
		trust_anchor: anchor,
	});
	verifiedResults.set(assurance, {
		attestation_digest: attestation.attestation_digest,
		profile: attestation.assurance_profile,
	});
	return { verified: true, assurance, diagnostics: [] };
}

export function isVerifiedAssurance(value: unknown): value is VerifiedAssurance {
	if (!object(value)) return false;
	const verified = verifiedResults.get(value);
	if (verified === undefined) return false;
	const candidate = value as unknown as VerifiedAssurance;
	return (
		Object.isFrozen(candidate) &&
		candidate.profile === verified.profile &&
		sameDigest(candidate.attestation?.attestation_digest, verified.attestation_digest)
	);
}
