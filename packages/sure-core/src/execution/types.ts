import type { CapabilityEvaluation } from "../contracts/capability.ts";
import type {
	CapabilityEvidence,
	CapabilityRequirement,
	ExecutionReceipt,
	ExecutionRequest,
	ExecutorIdentity,
} from "../contracts/types.ts";
import type { CoreOutcome, ReasonCode } from "../workflow/outcome.ts";

/** ADMITTED means the adapter boundary was reached; it does not make a receipt valid. */
export const EXECUTION_ADMISSION_STATUSES = ["ADMITTED", "CAPABILITY_MISSING", "REJECTED"] as const;
export type ExecutionAdmissionStatus = (typeof EXECUTION_ADMISSION_STATUSES)[number];

/**
 * A preflight trace is deliberately not an execution receipt. It records why
 * a request did or did not reach an executor without inventing executor
 * identity for a missing or rejected adapter.
 */
export interface ExecutionAdmissionTrace {
	schema: "sure.execution_admission.v1";
	request_digest: string;
	request_id?: string;
	requested_executor_kind?: string;
	execution_surface?: "vc" | "remote" | "trusted";
	adapter_manifest_digest?: string;
	status: ExecutionAdmissionStatus;
	reason_code: ReasonCode;
	observed_at: string;
	probe_invoked: boolean;
	execute_invoked: boolean;
	receipt_present: boolean;
	receipt_valid: boolean;
}

/** Host-neutral executor port. Adapters may be synchronous or asynchronous. */
export interface ExecutorPort {
	readonly identity: ExecutorIdentity;
	probe(requirements: readonly CapabilityRequirement[]): readonly CapabilityEvidence[];
	execute(request: ExecutionRequest): ExecutionReceipt | Promise<ExecutionReceipt>;
}

export interface ExecutionBoundaryOptions {
	/** Additional registered capability ids accepted by this deployment. */
	known_capability_ids?: ReadonlySet<string>;
	/** Roots that are writable for the request outputs. */
	allowed_output_roots?: readonly string[];
	/** Roots that must never receive executor output. */
	forbidden_output_roots?: readonly string[];
	/** A formal caller can require an attested executor. */
	require_attested_executor?: boolean;
}

export interface ExecutionRequestValidation {
	valid: boolean;
	errors: readonly string[];
	outcome: CoreOutcome;
}

export interface ExecutionReceiptValidation {
	valid: boolean;
	errors: readonly string[];
	outcome: CoreOutcome;
	capability: CapabilityEvaluation;
}
