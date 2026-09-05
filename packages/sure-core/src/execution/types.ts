import type { CapabilityEvaluation } from "../contracts/capability.ts";
import type {
	CapabilityEvidence,
	CapabilityRequirement,
	ExecutionReceipt,
	ExecutionRequest,
	ExecutorIdentity,
} from "../contracts/types.ts";
import type { CoreOutcome } from "../workflow/outcome.ts";

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
