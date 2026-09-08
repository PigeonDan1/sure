import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type { ExecutionReceipt, ExecutionRequest, JsonValue } from "../contracts/types.ts";
import { type CoreOutcome, createOutcome } from "../workflow/outcome.ts";
import { validateExecutionAdmissionReceiptBinding, validateExecutionAdmissionTrace } from "./admission.ts";
import { validateExecutionReceipt, validateExecutionRequest } from "./receipt.ts";
import type {
	ExecutionAdmissionTrace,
	ExecutionBoundaryOptions,
	ExecutionReceiptValidation,
	ExecutionRequestValidation,
} from "./types.ts";

export const EXECUTION_COMPATIBILITY_SCHEMA = "sure.execution_compatibility.v1" as const;

export interface ExecutionContractRecord {
	readonly schema?: unknown;
	readonly version?: unknown;
	readonly request_digest?: unknown;
	readonly receipt_digest?: unknown;
	readonly admission_digest?: unknown;
	readonly contract_valid?: unknown;
	readonly admission_instrumentation?: unknown;
	readonly [key: string]: unknown;
}

export interface ExecutionContractBundle {
	readonly request: ExecutionRequest;
	readonly receipt?: ExecutionReceipt;
	readonly admission?: ExecutionAdmissionTrace;
	readonly contract?: ExecutionContractRecord;
}

export interface ExecutionContractBundleOptions extends ExecutionBoundaryOptions {
	/** A final consumer normally requires a receipt; prepare mode may omit it. */
	require_receipt?: boolean;
	/** Formal/fully instrumented consumers may require an admission trace. */
	require_admission?: boolean;
	/** A generated execution_contract.json is required for this consumer. */
	require_contract_record?: boolean;
	/** Historical bundles without admission-v1 remain readable by default. */
	accept_legacy_uninstrumented?: boolean;
}

export interface ExecutionContractBundleValidation {
	valid: boolean;
	errors: readonly string[];
	outcome: CoreOutcome;
	request_validation: ExecutionRequestValidation;
	receipt_validation?: ExecutionReceiptValidation;
	admission_errors: readonly string[];
	contract_errors: readonly string[];
}

export interface ExecutionContractHistory {
	readonly latest: ExecutionContractBundle;
	readonly immutable: ExecutionContractBundle;
}

export interface ExecutionContractHistoryValidation {
	valid: boolean;
	errors: readonly string[];
	history_errors: readonly string[];
	outcome: CoreOutcome;
	latest_validation: ExecutionContractBundleValidation;
	immutable_validation: ExecutionContractBundleValidation;
}

function digestableBundle(bundle: ExecutionContractBundle): JsonValue {
	return {
		request: bundle.request as unknown as JsonValue,
		receipt: (bundle.receipt ?? null) as unknown as JsonValue,
		admission: (bundle.admission ?? null) as unknown as JsonValue,
		contract: (bundle.contract ?? null) as unknown as JsonValue,
	};
}

/** Stable content identity for one persisted execution contract bundle. */
export function executionContractBundleDigest(bundle: ExecutionContractBundle): string {
	return canonicalJsonDigest({
		schema: "sure.execution_contract_bundle.digest.v1",
		bundle: digestableBundle(bundle),
	});
}

/**
 * Content identity over both the latest compatibility view and the immutable
 * request-id view. Embedded paths remain evidence only, but their exact bytes
 * are covered so a verified host attestation cannot be replayed over edits.
 */
export function executionContractHistoryDigest(history: ExecutionContractHistory): string {
	return canonicalJsonDigest({
		schema: "sure.execution_contract_history.digest.v1",
		latest: digestableBundle(history.latest),
		immutable: digestableBundle(history.immutable),
	});
}

function invalidOutcome(errors: readonly string[]): CoreOutcome {
	return createOutcome({
		validatorVerdict: "NOT_EXECUTED",
		workflowDisposition: "BLOCK",
		reasonCode: "INVALID_CONTRACT",
		diagnostics: errors.map((message) => ({ code: "EXECUTION_BUNDLE_INVALID", message })),
	});
}

function capabilityMissingOutcome(): CoreOutcome {
	return createOutcome({
		validatorVerdict: "NOT_EXECUTED",
		workflowDisposition: "BLOCK",
		reasonCode: "CAPABILITY_MISSING",
		executionLifecycle: "NOT_STARTED",
		diagnostics: [
			{
				code: "CAPABILITY_MISSING",
				message: "Execution adapter capability was not available; no receipt was produced.",
			},
		],
	});
}

function rejectedAdmissionOutcome(trace: ExecutionAdmissionTrace): CoreOutcome {
	return createOutcome({
		validatorVerdict: "NOT_EXECUTED",
		workflowDisposition: "BLOCK",
		reasonCode: trace.reason_code,
		diagnostics: [
			{
				code: trace.reason_code,
				message: "Execution request was rejected before an executor was invoked.",
			},
		],
	});
}

function digestMatches(value: unknown, expected: string): boolean {
	return (
		typeof value === "string" &&
		value.replace(/^sha256:/i, "").toLowerCase() === expected.replace(/^sha256:/i, "").toLowerCase()
	);
}

function valuesMatch(left: unknown, right: unknown): boolean {
	if (left === undefined || right === undefined) return left === right;
	return canonicalJsonDigest(left as JsonValue) === canonicalJsonDigest(right as JsonValue);
}

function stableContractRecord(record: ExecutionContractRecord | undefined): Record<string, unknown> | undefined {
	if (record === undefined) return undefined;
	const { request_path: _requestPath, receipt_path: _receiptPath, admission_path: _admissionPath, ...stable } = record;
	return stable;
}

function contractRecordErrors(
	bundle: ExecutionContractBundle,
	options: ExecutionContractBundleOptions,
	baseValid: boolean,
): string[] {
	const record = bundle.contract;
	if (record === undefined) {
		return options.require_contract_record ? ["execution contract record is required"] : [];
	}
	const errors: string[] = [];
	if (record.schema !== EXECUTION_COMPATIBILITY_SCHEMA) errors.push("execution contract schema is unsupported");
	if (record.version !== 1) errors.push("execution contract version is unsupported");
	if (
		record.admission_instrumentation !== "admission-v1" &&
		record.admission_instrumentation !== "legacy-uninstrumented"
	) {
		errors.push("execution contract admission_instrumentation is invalid");
	}
	const requestDigest = canonicalJsonDigest(bundle.request as unknown as JsonValue);
	if (!digestMatches(record.request_digest, requestDigest)) {
		errors.push("execution contract request_digest does not match request bytes");
	}
	if (bundle.receipt !== undefined) {
		const receiptDigest = canonicalJsonDigest(bundle.receipt as unknown as JsonValue);
		if (!digestMatches(record.receipt_digest, receiptDigest)) {
			errors.push("execution contract receipt_digest does not match receipt bytes");
		}
	} else if (record.receipt_digest !== undefined) {
		errors.push("execution contract receipt_digest is present without a receipt");
	}
	if (bundle.admission !== undefined) {
		const admissionDigest = canonicalJsonDigest(bundle.admission as unknown as JsonValue);
		if (!digestMatches(record.admission_digest, admissionDigest)) {
			errors.push("execution contract admission_digest does not match admission bytes");
		}
		if (record.admission_instrumentation !== "admission-v1") {
			errors.push("execution contract admission_instrumentation must be admission-v1");
		}
	} else if (record.admission_digest !== undefined || record.admission_instrumentation === "admission-v1") {
		errors.push("execution contract declares admission-v1 without an admission trace");
	}
	if (record.contract_valid !== undefined && typeof record.contract_valid !== "boolean") {
		errors.push("execution contract contract_valid must be boolean");
	} else if (record.contract_valid !== undefined && record.contract_valid !== baseValid) {
		errors.push("execution contract contract_valid does not match current validation");
	}
	if (record.admission_instrumentation === "legacy-uninstrumented" && options.accept_legacy_uninstrumented === false) {
		errors.push("legacy-uninstrumented execution contract is not accepted by this consumer");
	}
	return errors;
}

/**
 * Re-audit a persisted request/receipt/admission bundle at the consumer.
 *
 * The bundle is evidence, not workflow authority.  This function deliberately
 * composes the existing request, receipt and admission validators instead of
 * introducing a second lifecycle reducer.  A missing receipt is only valid
 * for an explicit prepare path or a rejected/capability-missing admission.
 */
export function validateExecutionContractBundle(
	bundle: ExecutionContractBundle,
	options: ExecutionContractBundleOptions = {},
): ExecutionContractBundleValidation {
	const requestValidation = validateExecutionRequest(bundle.request, options);
	const errors = [...requestValidation.errors];
	let receiptValidation: ExecutionReceiptValidation | undefined;
	let admissionErrors: string[] = [];
	if (bundle.receipt !== undefined) {
		receiptValidation = validateExecutionReceipt(bundle.request, bundle.receipt, options);
		errors.push(...receiptValidation.errors);
	}
	if (bundle.admission !== undefined) {
		admissionErrors = validateExecutionAdmissionTrace(bundle.admission);
		errors.push(...admissionErrors);
		if (admissionErrors.length === 0) {
			admissionErrors = validateExecutionAdmissionReceiptBinding(bundle.request, bundle.admission, {
				receipt: bundle.receipt,
				receipt_valid: receiptValidation?.valid,
				capability_admitted: receiptValidation?.capability.admitted,
			});
			errors.push(...admissionErrors);
		}
	} else if (options.require_admission) {
		admissionErrors = ["execution admission trace is required"];
		errors.push(...admissionErrors);
	}
	const admittedWithoutReceipt =
		bundle.admission?.status === "CAPABILITY_MISSING" || bundle.admission?.status === "REJECTED";
	const receiptRequirementValid =
		bundle.receipt !== undefined || options.require_receipt === false || admittedWithoutReceipt;
	if (bundle.receipt === undefined) {
		if (!receiptRequirementValid) errors.push("execution receipt is required for final bundle validation");
	}
	const baseValid =
		requestValidation.valid &&
		(receiptValidation === undefined || receiptValidation.valid) &&
		admissionErrors.length === 0 &&
		receiptRequirementValid;
	const contractErrors = contractRecordErrors(bundle, options, baseValid);
	errors.push(...contractErrors);
	const valid = baseValid && errors.length === 0;
	let outcome: CoreOutcome;
	if (contractErrors.length > 0) outcome = invalidOutcome(errors);
	else if (!requestValidation.valid) outcome = requestValidation.outcome;
	else if (receiptValidation !== undefined && !receiptValidation.valid) outcome = receiptValidation.outcome;
	else if (admissionErrors.length > 0) outcome = invalidOutcome(errors);
	else if (receiptValidation !== undefined) outcome = receiptValidation.outcome;
	else if (bundle.admission?.status === "CAPABILITY_MISSING") outcome = capabilityMissingOutcome();
	else if (bundle.admission?.status === "REJECTED") outcome = rejectedAdmissionOutcome(bundle.admission);
	else if (errors.length > 0) outcome = invalidOutcome(errors);
	else outcome = requestValidation.outcome;
	return {
		valid,
		errors,
		outcome,
		request_validation: requestValidation,
		...(receiptValidation === undefined ? {} : { receipt_validation: receiptValidation }),
		admission_errors: admissionErrors,
		contract_errors: contractErrors,
	};
}

/**
 * Bind a latest compatibility view to its immutable-by-request-id history.
 *
 * Callers resolve the immutable filenames from the already validated request
 * id; paths embedded in execution_contract.json are never authority.  The
 * contract records may differ only in their top-level latest/history aliases.
 */
export function validateExecutionContractHistory(
	history: ExecutionContractHistory,
	options: ExecutionContractBundleOptions = {},
): ExecutionContractHistoryValidation {
	const auditOptions = { ...options, require_contract_record: options.require_contract_record ?? true };
	const latestValidation = validateExecutionContractBundle(history.latest, auditOptions);
	const immutableValidation = validateExecutionContractBundle(history.immutable, auditOptions);
	const historyErrors: string[] = [];
	if (!valuesMatch(history.latest.request, history.immutable.request))
		historyErrors.push("immutable execution request does not match latest execution request");
	if (!valuesMatch(history.latest.receipt, history.immutable.receipt))
		historyErrors.push("immutable execution receipt does not match latest execution receipt");
	if (!valuesMatch(history.latest.admission, history.immutable.admission))
		historyErrors.push("immutable execution admission does not match latest execution admission");
	if (!valuesMatch(stableContractRecord(history.latest.contract), stableContractRecord(history.immutable.contract)))
		historyErrors.push("immutable execution contract metadata does not match latest execution contract metadata");
	if (
		latestValidation.outcome.reason_code !== immutableValidation.outcome.reason_code ||
		latestValidation.outcome.outcome !== immutableValidation.outcome.outcome
	) {
		historyErrors.push("immutable execution outcome does not match latest execution outcome");
	}
	const errors = [
		...latestValidation.errors.map((error) => `latest bundle: ${error}`),
		...immutableValidation.errors.map((error) => `immutable bundle: ${error}`),
		...historyErrors,
	];
	return {
		valid: latestValidation.valid && immutableValidation.valid && historyErrors.length === 0,
		errors,
		history_errors: historyErrors,
		outcome: historyErrors.length > 0 ? invalidOutcome(errors) : latestValidation.outcome,
		latest_validation: latestValidation,
		immutable_validation: immutableValidation,
	};
}
