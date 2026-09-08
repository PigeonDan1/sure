import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type { ExecutionReceipt, ExecutionRequest, JsonValue } from "../contracts/types.ts";
import type { CoreOutcome } from "../workflow/outcome.ts";
import { EXECUTION_ADMISSION_STATUSES, type ExecutionAdmissionTrace } from "./types.ts";

const DIGEST = /^(?:sha256:)?[0-9a-f]{64}$/i;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function sameDigest(value: string, expected: string): boolean {
	return value.replace(/^sha256:/i, "").toLowerCase() === expected.replace(/^sha256:/i, "").toLowerCase();
}

function admissionStatus(
	outcome: CoreOutcome,
	probeInvoked: boolean,
	executeInvoked: boolean,
): ExecutionAdmissionTrace["status"] {
	if (outcome.reason_code === "CAPABILITY_MISSING") return "CAPABILITY_MISSING";
	if (!probeInvoked && !executeInvoked) return "REJECTED";
	if (outcome.reason_code === "INVALID_CONTRACT" && !executeInvoked) return "REJECTED";
	return "ADMITTED";
}

/** Build a host-neutral preflight trace from a dispatch result. */
export function createExecutionAdmissionTrace(
	request: ExecutionRequest,
	observedAt: string,
	outcome: CoreOutcome,
	options: {
		probe_invoked: boolean;
		execute_invoked: boolean;
		receipt?: ExecutionReceipt;
		receipt_valid?: boolean;
	},
): ExecutionAdmissionTrace {
	const runtime = isRecord(request.runtime_requirements) ? request.runtime_requirements : undefined;
	const requestedExecutorKind = runtime?.executor_kind;
	const surface = runtime?.execution_surface;
	const receiptValid = options.receipt_valid === true;
	return {
		schema: "sure.execution_admission.v1",
		request_digest: canonicalJsonDigest(request as unknown as JsonValue),
		...(typeof request.request_id === "string" && ID.test(request.request_id)
			? { request_id: request.request_id }
			: {}),
		...(typeof requestedExecutorKind === "string" ? { requested_executor_kind: requestedExecutorKind } : {}),
		...(surface === "vc" || surface === "remote" || surface === "trusted" ? { execution_surface: surface } : {}),
		...(typeof request.adapter_manifest_digest === "string" && DIGEST.test(request.adapter_manifest_digest)
			? { adapter_manifest_digest: request.adapter_manifest_digest }
			: {}),
		status: admissionStatus(outcome, options.probe_invoked, options.execute_invoked),
		reason_code: outcome.reason_code,
		observed_at: observedAt,
		probe_invoked: options.probe_invoked,
		execute_invoked: options.execute_invoked,
		receipt_present: options.receipt !== undefined,
		receipt_valid: receiptValid,
	};
}

/** Validate the trace wire shape before it is persisted or compared. */
export function validateExecutionAdmissionTrace(value: unknown): string[] {
	if (!isRecord(value)) return ["execution admission trace must be an object"];
	const errors: string[] = [];
	if (value.schema !== "sure.execution_admission.v1") errors.push("admission.schema is unsupported");
	if (typeof value.request_digest !== "string" || !DIGEST.test(value.request_digest))
		errors.push("admission.request_digest must be a SHA-256 digest");
	if (value.request_id !== undefined && (typeof value.request_id !== "string" || !ID.test(value.request_id)))
		errors.push("admission.request_id is invalid");
	if (value.requested_executor_kind !== undefined && typeof value.requested_executor_kind !== "string")
		errors.push("admission.requested_executor_kind must be a string");
	if (
		value.execution_surface !== undefined &&
		!(
			value.execution_surface === "vc" ||
			value.execution_surface === "remote" ||
			value.execution_surface === "trusted"
		)
	)
		errors.push("admission.execution_surface is invalid");
	if (
		value.adapter_manifest_digest !== undefined &&
		(typeof value.adapter_manifest_digest !== "string" || !DIGEST.test(value.adapter_manifest_digest))
	)
		errors.push("admission.adapter_manifest_digest must be a SHA-256 digest");
	if (!(EXECUTION_ADMISSION_STATUSES as readonly unknown[]).includes(value.status))
		errors.push("admission.status is invalid");
	if (typeof value.reason_code !== "string" || value.reason_code.length === 0)
		errors.push("admission.reason_code must be a non-empty string");
	if (typeof value.observed_at !== "string" || value.observed_at.length === 0)
		errors.push("admission.observed_at must be a non-empty string");
	for (const field of ["probe_invoked", "execute_invoked", "receipt_present", "receipt_valid"] as const) {
		if (typeof value[field] !== "boolean") errors.push(`admission.${field} must be boolean`);
	}
	if (value.receipt_valid === true && value.receipt_present !== true)
		errors.push("admission.receipt_valid requires receipt_present");
	if (value.status === "CAPABILITY_MISSING" && value.reason_code !== "CAPABILITY_MISSING")
		errors.push("CAPABILITY_MISSING admission must use reason_code CAPABILITY_MISSING");
	if (value.status === "CAPABILITY_MISSING" && value.execute_invoked === true)
		errors.push("CAPABILITY_MISSING admission cannot invoke execute");
	if (value.status === "REJECTED" && value.reason_code === "CAPABILITY_MISSING")
		errors.push("REJECTED admission cannot use reason_code CAPABILITY_MISSING");
	if (value.status === "REJECTED" && value.execute_invoked === true)
		errors.push("REJECTED admission cannot invoke execute");
	if (value.status === "ADMITTED" && value.probe_invoked !== true && value.execute_invoked !== true)
		errors.push("ADMITTED admission requires probe_invoked or execute_invoked");
	return errors;
}

/** Rebind a trace to a request digest when loading persisted host data. */
export function validateExecutionAdmissionBinding(request: ExecutionRequest, trace: ExecutionAdmissionTrace): string[] {
	const errors = validateExecutionAdmissionTrace(trace);
	const expected = canonicalJsonDigest(request as unknown as JsonValue);
	if (DIGEST.test(trace.request_digest) && !sameDigest(trace.request_digest, expected)) {
		errors.push("admission.request_digest does not match request");
	}
	if (typeof request.request_id === "string" && ID.test(request.request_id)) {
		if (trace.request_id === undefined) errors.push("admission.request_id is missing from request binding");
		else if (trace.request_id !== request.request_id) errors.push("admission.request_id does not match request");
	}
	const runtime = isRecord(request.runtime_requirements) ? request.runtime_requirements : undefined;
	if (typeof runtime?.executor_kind === "string") {
		if (trace.requested_executor_kind === undefined)
			errors.push("admission.requested_executor_kind is missing from request binding");
		else if (trace.requested_executor_kind !== runtime.executor_kind)
			errors.push("admission.requested_executor_kind does not match request");
	}
	if (
		runtime?.execution_surface === "vc" ||
		runtime?.execution_surface === "remote" ||
		runtime?.execution_surface === "trusted"
	) {
		if (trace.execution_surface === undefined)
			errors.push("admission.execution_surface is missing from request binding");
		else if (trace.execution_surface !== runtime.execution_surface)
			errors.push("admission.execution_surface does not match request");
	}
	if (request.adapter_manifest_digest !== undefined && DIGEST.test(request.adapter_manifest_digest)) {
		if (trace.adapter_manifest_digest === undefined)
			errors.push("admission.adapter_manifest_digest is missing from request binding");
		else if (!sameDigest(trace.adapter_manifest_digest, request.adapter_manifest_digest))
			errors.push("admission.adapter_manifest_digest does not match request");
	}
	return errors;
}

/**
 * Bind a persisted admission trace to the receipt that the host actually
 * produced.  The trace is a preflight record, so it never replaces receipt
 * validation; this helper only prevents the two records from making
 * contradictory claims.
 */
export function validateExecutionAdmissionReceiptBinding(
	request: ExecutionRequest,
	trace: ExecutionAdmissionTrace,
	options: {
		receipt?: ExecutionReceipt;
		receipt_valid?: boolean;
		capability_admitted?: boolean;
	} = {},
): string[] {
	const errors = validateExecutionAdmissionBinding(request, trace);
	const receipt = options.receipt;
	const receiptPresent = receipt !== undefined;
	if (trace.receipt_present !== receiptPresent) {
		errors.push("admission.receipt_present does not match the persisted receipt");
	}
	if (options.receipt_valid !== undefined && trace.receipt_valid !== options.receipt_valid) {
		errors.push("admission.receipt_valid does not match receipt validation");
	}
	if (options.capability_admitted === false && trace.status === "ADMITTED") {
		errors.push("admission.status ADMITTED conflicts with a non-admitted capability result");
	}
	if (options.capability_admitted === true && trace.status === "CAPABILITY_MISSING") {
		errors.push("admission.status CAPABILITY_MISSING conflicts with an admitted capability result");
	}
	if (trace.receipt_valid && !receiptPresent) {
		errors.push("admission.receipt_valid requires a persisted receipt");
	}
	if (receipt === undefined) return errors;

	const lifecycle = receipt.lifecycle;
	if (lifecycle === "SUCCEEDED") {
		if (trace.status !== "ADMITTED") errors.push("successful receipt requires ADMITTED admission status");
		if (!trace.execute_invoked) errors.push("successful receipt requires admission.execute_invoked");
		if (!trace.receipt_valid) errors.push("successful receipt requires admission.receipt_valid");
		if (options.receipt_valid === false) errors.push("successful receipt cannot have invalid receipt validation");
		if (options.capability_admitted === false) errors.push("successful receipt cannot have missing capability");
	}
	if (trace.status === "CAPABILITY_MISSING" && lifecycle !== "NOT_STARTED") {
		errors.push("CAPABILITY_MISSING admission cannot carry an executing receipt");
	}
	if (trace.status === "REJECTED" && lifecycle !== "NOT_STARTED") {
		errors.push("REJECTED admission cannot carry an executing receipt");
	}
	if (lifecycle !== "NOT_STARTED" && !trace.execute_invoked) {
		errors.push("an executing receipt requires admission.execute_invoked");
	}
	return errors;
}
