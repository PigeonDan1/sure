import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type {
	ArtifactRef,
	CapabilityEvidence,
	ExecutionOutputResidual,
	ExecutionReceipt,
	ExecutionRequest,
	ExecutorIdentity,
	JsonValue,
} from "../contracts/types.ts";
import { executionOutputContractDigest, executionOutputSetDigest } from "./output-contract.ts";

export interface BoundExecutionReceiptOptions {
	receipt_id: string;
	executor: ExecutorIdentity;
	lifecycle: ExecutionReceipt["lifecycle"];
	capability_evidence: readonly CapabilityEvidence[];
	outputs?: readonly ArtifactRef[];
	residuals?: readonly ExecutionOutputResidual[];
	started_at: string;
	finished_at?: string;
	exit_code?: number;
	diagnostics?: readonly Record<string, JsonValue>[];
}

/** Construct a receipt whose immutable request bindings cannot drift by host. */
export function createBoundExecutionReceipt(
	request: ExecutionRequest,
	options: BoundExecutionReceiptOptions,
): ExecutionReceipt {
	const outputs = [...(options.outputs ?? [])];
	const outputContract = request.output_contract;
	const residuals = outputContract === undefined ? undefined : [...(options.residuals ?? [])];
	return {
		schema: "sure.execution_receipt.v1",
		receipt_id: options.receipt_id,
		request_id: request.request_id,
		request_digest: canonicalJsonDigest(request as unknown as JsonValue),
		semantic_request_digest: request.semantic_request_digest,
		run_id: request.run_id,
		unit_id: request.unit_id,
		attempt: request.attempt,
		executor: options.executor,
		lifecycle: options.lifecycle,
		capability_evidence: [...options.capability_evidence],
		outputs,
		...(request.input_binding === undefined ? {} : { input_binding_digest: request.input_binding.binding_digest }),
		reference_snapshot_digest: request.reference_snapshot_digest,
		output_root: request.output_root,
		policy_digest: request.policy_digest,
		...(request.policy_snapshot_digest === undefined
			? {}
			: { policy_snapshot_digest: request.policy_snapshot_digest }),
		...(request.adapter_manifest_digest === undefined
			? {}
			: { adapter_manifest_digest: request.adapter_manifest_digest }),
		started_at: options.started_at,
		...(options.finished_at === undefined ? {} : { finished_at: options.finished_at }),
		...(options.exit_code === undefined ? {} : { exit_code: options.exit_code }),
		...(options.diagnostics === undefined ? {} : { diagnostics: [...options.diagnostics] }),
		...(outputContract === undefined
			? {}
			: {
					output_contract_digest: executionOutputContractDigest(outputContract),
					output_set_digest: executionOutputSetDigest(outputs, residuals),
					residuals: residuals!,
				}),
	};
}
