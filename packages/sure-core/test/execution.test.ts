import { describe, expect, it } from "vitest";
import {
	canonicalJsonDigest,
	type ExecutionReceipt,
	type ExecutionRequest,
	executionInputBindingDigest,
	type JsonValue,
	validateExecutionReceipt,
	validateExecutionRequest,
} from "../src/index.ts";

const DIGEST_A = `sha256:${"a".repeat(64)}`;
const DIGEST_B = `sha256:${"b".repeat(64)}`;
const NOW = "2026-09-06T00:00:00.000Z";

function request(): ExecutionRequest {
	return {
		schema: "sure.execution_request.v1",
		request_id: "request-1",
		semantic_request_digest: DIGEST_A,
		run_id: "run-1",
		unit_id: "execute_inference",
		attempt: 1,
		operation: "inference",
		subject: {
			bundle_manifest_path: "/tmp/sure-dev/bundle.json",
			bundle_digest: DIGEST_A,
			runtime_identity_digest: DIGEST_B,
			inference_protocol_digest: DIGEST_A,
		},
		inputs: [],
		entrypoint: { executable: "python3", argv: ["/tmp/sure-dev/run.py"] },
		runtime_requirements: { python: "3.11" },
		capability_requirements: [
			{ capability_id: "sure.execution.local-python", capability_class: "execution_capability", required: true },
		],
		reference_snapshot_digest: DIGEST_B,
		output_root: {
			path: "/tmp/sure-dev/runs/run-1/artifacts",
			resolved_path: "/tmp/sure-dev/runs/run-1/artifacts",
			scope_id: "run-1",
			policy_digest: DIGEST_A,
			writable: true,
		},
		policy_digest: DIGEST_A,
		created_at: NOW,
	};
}

function receipt(input: ExecutionRequest, lifecycle: ExecutionReceipt["lifecycle"] = "SUCCEEDED"): ExecutionReceipt {
	return {
		schema: "sure.execution_receipt.v1",
		receipt_id: "receipt-1",
		request_id: input.request_id,
		request_digest: canonicalJsonDigest(input as unknown as JsonValue),
		semantic_request_digest: input.semantic_request_digest,
		run_id: input.run_id,
		unit_id: input.unit_id,
		attempt: input.attempt,
		executor: {
			executor_id: "local-python-1",
			kind: "python",
			version: "1.0.0",
			digest: DIGEST_A,
			trust_level: "host_enforced",
		},
		lifecycle,
		capability_evidence: [
			{
				capability_id: "sure.execution.local-python",
				capability_class: "execution_capability",
				status: "AVAILABLE",
				source: "executor",
				observed_at: NOW,
				evidence_digest: DIGEST_B,
			},
		],
		outputs: [],
		reference_snapshot_digest: input.reference_snapshot_digest,
		output_root: input.output_root,
		policy_digest: input.policy_digest,
		started_at: NOW,
		finished_at: NOW,
		exit_code: lifecycle === "SUCCEEDED" ? 0 : 1,
	};
}

describe("execution boundary", () => {
	it("accepts a receipt contract but leaves PASS to artifact validators", () => {
		const input = request();
		const result = validateExecutionReceipt(input, receipt(input));
		expect(result.valid).toBe(true);
		expect(result.outcome).toMatchObject({
			outcome: "NOT_EXECUTED",
			workflow_disposition: "WAIT",
			reason_code: "VALIDATION_PENDING",
		});
	});

	it("turns missing execution capability into NOT_EXECUTED", () => {
		const input = request();
		const failed = receipt(input);
		failed.capability_evidence = [];
		const result = validateExecutionReceipt(input, failed);
		expect(result.valid).toBe(false);
		expect(result.capability.missing).toEqual(["sure.execution.local-python"]);
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "CAPABILITY_MISSING" });
	});

	it("rejects an invalid capability source in a persisted receipt", () => {
		const input = request();
		const forged = receipt(input);
		(forged.capability_evidence[0] as unknown as Record<string, unknown>).source = "remote_daemon";
		const result = validateExecutionReceipt(input, forged);
		expect(result.valid).toBe(false);
		expect(result.errors).toContain("receipt.capability_evidence[0].source is invalid");
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
	});

	it("keeps a failed executor distinct from a validator failure", () => {
		const input = request();
		const result = validateExecutionReceipt(input, receipt(input, "FAILED"));
		expect(result.valid).toBe(true);
		expect(result.outcome).toMatchObject({ outcome: "RETRY", reason_code: "EXECUTION_FAILED" });
	});

	it("preserves an explicit preflight contract failure when a NOT_STARTED receipt is revalidated", () => {
		const input = request();
		const failed = receipt(input, "NOT_STARTED");
		delete failed.exit_code;
		failed.diagnostics = [{ code: "INVALID_CONTRACT", message: "mount source is outside the admitted boundary" }];
		const result = validateExecutionReceipt(input, failed);
		expect(result.valid).toBe(false);
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		expect(result.errors).toContain("mount source is outside the admitted boundary");
	});

	it("requires digest-pinned Docker identities for formal execution", () => {
		const input = request();
		input.operation = "formal_evaluation";
		input.subject = {
			...input.subject,
			dataset_identity_digest: DIGEST_A,
			scoring_protocol_digest: DIGEST_B,
		};
		input.runtime_requirements = {
			executor_kind: "docker",
			docker_image: "registry.example/sure/trans:latest",
			docker_mounts: [],
		};
		const result = validateExecutionRequest(input);
		expect(result.valid).toBe(false);
		expect(result.errors).toContain("formal Docker execution requires a digest-pinned docker_image");
	});

	it("rejects tampered request identity and output escape", () => {
		const input = request();
		const tampered = receipt(input);
		tampered.semantic_request_digest = DIGEST_B;
		tampered.outputs = [
			{
				artifact_id: "result",
				path: "/tmp/outside/result.json",
				resolved_path: "/tmp/outside/result.json",
				sha256: DIGEST_A,
				size: 1,
				media_type: "application/json",
				origin: "generated",
				source_root: "/tmp/outside",
			},
		];
		const result = validateExecutionReceipt(input, tampered);
		expect(result.valid).toBe(false);
		expect(result.errors.join(" ")).toMatch(/does not match request|outside every allowed root/);
	});

	it("checks request output root against an explicit site write boundary", () => {
		const result = validateExecutionRequest(request(), { allowed_output_roots: ["/tmp/another-root"] });
		expect(result.valid).toBe(false);
		expect(result.outcome.reason_code).toBe("PATH_OUT_OF_SCOPE");
	});

	it("requires the receipt to repeat the request input binding digest", () => {
		const input = request();
		const artifact = {
			artifact_id: "context",
			path: "/tmp/sure-dev/runs/run-1/artifacts/context.json",
			resolved_path: "/tmp/sure-dev/runs/run-1/artifacts/context.json",
			sha256: DIGEST_A,
			size: 1,
			media_type: "application/json",
			origin: "local_staging" as const,
			source_root: "/tmp/sure-dev/runs/run-1",
		};
		input.inputs = [artifact];
		input.input_binding = {
			schema: "sure.execution_input_binding.v1",
			contract_digest: DIGEST_B,
			selector_id: "python-source",
			context_artifact: "context.json",
			context_digest: DIGEST_A,
			inputs: [{ input_id: "context", locator_kind: "run_artifact", path: "context.json", artifact }],
			binding_digest: "",
		};
		input.input_binding.binding_digest = executionInputBindingDigest(input.input_binding);
		const missing = receipt(input);
		delete missing.input_binding_digest;
		expect(validateExecutionReceipt(input, missing).valid).toBe(false);
		missing.input_binding_digest = input.input_binding.binding_digest;
		expect(validateExecutionReceipt(input, missing).valid).toBe(true);
		missing.input_binding_digest = DIGEST_B;
		expect(validateExecutionReceipt(input, missing).errors.join(" ")).toMatch(/input_binding_digest/);
	});
});
