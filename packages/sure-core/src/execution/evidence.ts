import type { ExecutionArtifactMode } from "../contracts/types.ts";
import type { ExecutionLifecycle } from "../workflow/outcome.ts";

export const OPERATION_EXECUTION_EVIDENCE_SCHEMA = "sure.operation.execution.v1" as const;
export const OPERATION_EXECUTION_PROJECTION_VERSION = 2 as const;

export type OperationExecutionEvidenceSource = "surectl" | "pi_hook" | "registered_operation";
export type OperationExecutionVerdict = "PASS" | "FAIL" | "NOT_EXECUTED";

/**
 * Durable state projection for one registered execution operation. Paths and
 * host source are provenance; operation, mode, verdict, and artifact digests
 * are the host-neutral semantic fields.
 */
export interface OperationExecutionEvidence {
	schema: typeof OPERATION_EXECUTION_EVIDENCE_SCHEMA;
	/** Absent only on legacy-v1 state written before the shared projection. */
	projection_version?: typeof OPERATION_EXECUTION_PROJECTION_VERSION;
	source: OperationExecutionEvidenceSource;
	operation_id: string;
	artifact_mode?: ExecutionArtifactMode;
	verdict: OperationExecutionVerdict;
	reason_code: string;
	diagnostics: readonly string[];
	artifact_input_digest: string;
	artifact_input_path?: string;
	artifact_output_digest?: string;
	artifact_output_path?: string;
	runtime_digest?: string;
	backend_registry_digest?: string;
	backend_bundle_digest?: string;
	backend_resource_digest?: string;
	input_contract_digest?: string;
	input_selector_id?: string;
	input_context_digest?: string;
	input_binding_digest?: string;
	request_path?: string;
	request_digest?: string;
	admission_path?: string;
	admission_digest?: string;
	receipt_path?: string;
	receipt_digest?: string;
	contract_path?: string;
	contract_digest?: string;
	execution_history_digest?: string;
	branch_id?: string;
	unit_id?: string;
	attempt?: number;
	outcome?: unknown;
}

export type OperationEvidenceCompatibility = "current-v2" | "legacy-v1";

export type OperationExecutionEvidenceDecodeResult =
	| {
			ok: true;
			evidence: OperationExecutionEvidence;
			compatibility: OperationEvidenceCompatibility;
			errors: readonly [];
	  }
	| {
			ok: false;
			compatibility: "invalid";
			errors: readonly string[];
	  };

export interface AdmitOperationExecutionEvidenceOptions {
	expected_operation_id: string;
	expected_artifact_mode: ExecutionArtifactMode;
	/** Digest independently read from the immutable execution receipt. */
	receipt_output_digest?: string;
}

export interface AdmittedOperationExecutionEvidence extends OperationExecutionEvidence {
	artifact_mode: ExecutionArtifactMode;
	compatibility: OperationEvidenceCompatibility;
	artifact_output_digest_source: "state" | "receipt" | "absent";
}

export type OperationExecutionEvidenceAdmissionResult =
	| { ok: true; evidence: AdmittedOperationExecutionEvidence; errors: readonly [] }
	| { ok: false; errors: readonly string[] };

export interface OperationExecutionSemanticProjection {
	operation_id: string;
	artifact_mode: ExecutionArtifactMode;
	verdict: OperationExecutionVerdict;
	reason_code: string;
	artifact_input_digest: string;
	artifact_output_digest?: string;
}

export interface ExecutionEvidenceProjectionInput {
	lifecycle?: ExecutionLifecycle;
	receipt_valid: boolean;
	capability_admitted: boolean;
	missing_output?: boolean;
	outcome_reason_code: string;
}

export interface ExecutionEvidenceProjection {
	verdict: OperationExecutionVerdict;
	reason_code: string;
}

const DIGEST = /^(?:sha256:)?[0-9a-f]{64}$/;
const MODES = new Set<ExecutionArtifactMode>(["preexisting", "mutating", "producing"]);
const SOURCES = new Set<OperationExecutionEvidenceSource>(["surectl", "pi_hook", "registered_operation"]);
const VERDICTS = new Set<OperationExecutionVerdict>(["PASS", "FAIL", "NOT_EXECUTED"]);

function object(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validDigest(value: unknown): value is string {
	return typeof value === "string" && DIGEST.test(value);
}

function sameDigest(left: string, right: string): boolean {
	return left.replace(/^sha256:/, "").toLowerCase() === right.replace(/^sha256:/, "").toLowerCase();
}

function optionalString(value: unknown, field: string, errors: string[]): void {
	if (value !== undefined && (typeof value !== "string" || value.length === 0)) {
		errors.push(`${field} must be a non-empty string when present`);
	}
}

function optionalDigest(value: unknown, field: string, errors: string[]): void {
	if (value !== undefined && !validDigest(value)) errors.push(`${field} must be a SHA-256 digest when present`);
}

/** Write only the current projection; legacy shape is read-only compatibility. */
export function createOperationExecutionEvidence(
	input: Omit<OperationExecutionEvidence, "schema" | "projection_version">,
): OperationExecutionEvidence {
	return {
		schema: OPERATION_EXECUTION_EVIDENCE_SCHEMA,
		projection_version: OPERATION_EXECUTION_PROJECTION_VERSION,
		...input,
	};
}

/** Parse state without consulting mutable artifacts or rewriting legacy data. */
export function decodeOperationExecutionEvidence(value: unknown): OperationExecutionEvidenceDecodeResult {
	const errors: string[] = [];
	if (!object(value)) return { ok: false, compatibility: "invalid", errors: ["operation evidence must be an object"] };
	if (value.schema !== OPERATION_EXECUTION_EVIDENCE_SCHEMA) errors.push("operation evidence schema is unsupported");
	if (!SOURCES.has(value.source as OperationExecutionEvidenceSource))
		errors.push("operation evidence source is invalid");
	if (typeof value.operation_id !== "string" || value.operation_id.length === 0)
		errors.push("operation evidence operation_id must be a non-empty string");
	if (!VERDICTS.has(value.verdict as OperationExecutionVerdict)) errors.push("operation evidence verdict is invalid");
	if (typeof value.reason_code !== "string" || value.reason_code.length === 0)
		errors.push("operation evidence reason_code must be a non-empty string");
	if (!Array.isArray(value.diagnostics) || value.diagnostics.some((item) => typeof item !== "string"))
		errors.push("operation evidence diagnostics must be a string array");
	if (!validDigest(value.artifact_input_digest))
		errors.push("operation evidence artifact_input_digest must be a SHA-256 digest");
	if (value.projection_version !== undefined && value.projection_version !== OPERATION_EXECUTION_PROJECTION_VERSION)
		errors.push("operation evidence projection_version is unsupported");
	if (value.artifact_mode !== undefined && !MODES.has(value.artifact_mode as ExecutionArtifactMode))
		errors.push("operation evidence artifact_mode is invalid");
	for (const field of [
		"artifact_input_path",
		"artifact_output_path",
		"request_path",
		"receipt_path",
		"admission_path",
		"contract_path",
		"branch_id",
		"unit_id",
	] as const) {
		optionalString(value[field], `operation evidence ${field}`, errors);
	}
	for (const field of [
		"artifact_output_digest",
		"runtime_digest",
		"backend_registry_digest",
		"backend_bundle_digest",
		"backend_resource_digest",
		"input_contract_digest",
		"input_context_digest",
		"input_binding_digest",
		"request_digest",
		"admission_digest",
		"receipt_digest",
		"contract_digest",
		"execution_history_digest",
	] as const) {
		optionalDigest(value[field], `operation evidence ${field}`, errors);
	}
	optionalString(value.input_selector_id, "operation evidence input_selector_id", errors);
	if (value.attempt !== undefined && (!Number.isSafeInteger(value.attempt) || Number(value.attempt) < 1))
		errors.push("operation evidence attempt must be a positive integer when present");
	if (
		value.projection_version === OPERATION_EXECUTION_PROJECTION_VERSION &&
		value.verdict === "PASS" &&
		value.artifact_mode === undefined
	)
		errors.push("current PASS operation evidence is missing artifact_mode");
	if (
		value.projection_version === OPERATION_EXECUTION_PROJECTION_VERSION &&
		value.verdict === "PASS" &&
		value.artifact_output_digest === undefined
	)
		errors.push("current PASS operation evidence is missing artifact_output_digest");
	if (errors.length > 0) return { ok: false, compatibility: "invalid", errors };
	return {
		ok: true,
		evidence: value as unknown as OperationExecutionEvidence,
		compatibility: value.projection_version === OPERATION_EXECUTION_PROJECTION_VERSION ? "current-v2" : "legacy-v1",
		errors: [],
	};
}

/**
 * Bind a decoded state projection to the locked operation and receipt. Legacy
 * output digests may be derived in memory from that receipt, never written
 * back into historical state or receipt bytes.
 */
export function admitOperationExecutionEvidence(
	decoded: Extract<OperationExecutionEvidenceDecodeResult, { ok: true }>,
	options: AdmitOperationExecutionEvidenceOptions,
): OperationExecutionEvidenceAdmissionResult {
	const errors: string[] = [];
	const persisted = decoded.evidence;
	if (persisted.operation_id !== options.expected_operation_id)
		errors.push("operation evidence does not match the locked operation");
	if (persisted.artifact_mode !== undefined && persisted.artifact_mode !== options.expected_artifact_mode)
		errors.push("operation evidence artifact_mode does not match the locked operation");
	const stateOutputDigest = persisted.artifact_output_digest;
	const receiptOutputDigest = options.receipt_output_digest;
	if (
		stateOutputDigest !== undefined &&
		receiptOutputDigest !== undefined &&
		!sameDigest(stateOutputDigest, receiptOutputDigest)
	)
		errors.push("operation evidence artifact_output_digest does not match the receipt");
	if (persisted.verdict === "PASS" && stateOutputDigest === undefined) {
		if (decoded.compatibility === "current-v2") {
			errors.push("current PASS operation evidence cannot omit artifact_output_digest");
		} else if (receiptOutputDigest === undefined) {
			errors.push("legacy PASS operation evidence has no receipt-bound output digest");
		}
	}
	if (errors.length > 0) return { ok: false, errors };
	const artifactOutputDigest = stateOutputDigest ?? receiptOutputDigest;
	return {
		ok: true,
		evidence: {
			...persisted,
			artifact_mode: options.expected_artifact_mode,
			...(artifactOutputDigest === undefined ? {} : { artifact_output_digest: artifactOutputDigest }),
			compatibility: decoded.compatibility,
			artifact_output_digest_source:
				stateOutputDigest !== undefined ? "state" : receiptOutputDigest !== undefined ? "receipt" : "absent",
		},
		errors: [],
	};
}

/** Compare host behavior without conflating host-specific paths or trust. */
export function operationExecutionSemanticProjection(
	evidence: AdmittedOperationExecutionEvidence,
): OperationExecutionSemanticProjection {
	return {
		operation_id: evidence.operation_id,
		artifact_mode: evidence.artifact_mode,
		verdict: evidence.verdict,
		reason_code: evidence.reason_code,
		artifact_input_digest: evidence.artifact_input_digest,
		...(evidence.artifact_output_digest === undefined
			? {}
			: { artifact_output_digest: evidence.artifact_output_digest }),
	};
}

/**
 * Map an executor result to the registered-operation evidence vocabulary.
 * Execution success remains PASS at this projection layer, while the Core
 * outcome stays VALIDATION_PENDING until an independent validator runs.
 */
export function projectExecutionEvidence(input: ExecutionEvidenceProjectionInput): ExecutionEvidenceProjection {
	if (input.missing_output) return { verdict: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" };
	if (!input.receipt_valid || !input.capability_admitted) {
		return { verdict: "NOT_EXECUTED", reason_code: input.outcome_reason_code };
	}
	switch (input.lifecycle) {
		case "SUCCEEDED":
			return { verdict: "PASS", reason_code: "EXECUTION_SUCCEEDED" };
		case "FAILED":
			return { verdict: "FAIL", reason_code: "EXECUTION_FAILED" };
		case "PARTIAL":
			return { verdict: "FAIL", reason_code: "EXECUTION_PARTIAL" };
		case "CANCELLED":
			return { verdict: "NOT_EXECUTED", reason_code: "EXECUTION_CANCELLED" };
		default:
			return { verdict: "NOT_EXECUTED", reason_code: input.outcome_reason_code };
	}
}
