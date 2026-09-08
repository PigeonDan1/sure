import { describe, expect, it } from "vitest";
import { canonicalJsonDigest } from "../src/contracts/canonical-json.ts";
import type { ArtifactRef, ExecutionOutputContract, JsonValue } from "../src/contracts/types.ts";
import {
	createRegisteredOperationRequest,
	type ResolvedSemanticBackend,
	registeredOperationCapabilityRequirements,
	registeredOperationCapabilityRequirementsDigest,
	registeredOperationSemanticDigest,
} from "../src/evaluation/index.ts";
import { executionOutputContractDigest, executionOutputSetDigest } from "../src/execution/output-contract.ts";
import { createBoundExecutionReceipt } from "../src/execution/receipt-builder.ts";

const A = `sha256:${"a".repeat(64)}`;
const B = `sha256:${"b".repeat(64)}`;
const C = `sha256:${"c".repeat(64)}`;
const D = `sha256:${"d".repeat(64)}`;
const E = `sha256:${"e".repeat(64)}`;
const F = `sha256:${"f".repeat(64)}`;
const NOW = "2026-09-11T12:00:00.000Z";
const CAPABILITY_DIGEST = "sha256:fe31743e9eda4f4d064a6b874e21a4f9a41a0389cd10135e9f37c9e40b5fd235";
const OUTPUT_CONTRACT_DIGEST = "sha256:c7d1a3692101e8d41d1e70151eb870fb83372240365c5e31459bf19cd6080f4a";
const SEMANTIC_DIGEST = "sha256:85b1cafc236c9862f9d613bd01462a98d23403278029498076a6772c950e5620";
const REQUEST_DIGEST = "sha256:58500d32ac81cb7423c3fd77cf1f4fe5e9a59577353c02bc7e999661523488e1";
const OUTPUT_SET_DIGEST = "sha256:176fd57304595d4542b38bdccc8f07e1ff07bf4155879f58491621656781d54e";
const RECEIPT_DIGEST = "sha256:060fdbc57567f479b8959e88e94d527a62dfe6c4d6d152eb7f974e028ea0c0a3";

const artifact: ArtifactRef = {
	artifact_id: "model",
	path: "/work/run/artifacts/model.json",
	resolved_path: "/work/run/artifacts/model.json",
	sha256: A,
	size: 42,
	media_type: "application/json",
	origin: "generated",
	source_root: "/work/run/artifacts",
};

const outputContract: ExecutionOutputContract = {
	schema: "sure.execution_output_contract.v1",
	mode: "producing",
	outputs: [{ artifact_id: "result", path: "result.json", kind: "file", required: true }],
	temporary_paths: [".staging"],
	allow_missing_on_failure: true,
	retain_failed_outputs: true,
};

function backend(overrides: Partial<ResolvedSemanticBackend> = {}): ResolvedSemanticBackend {
	return {
		operation_id: "sure.test.execute",
		bundle_id: "sure-test-execution",
		bundle_version: "1.0.0",
		path: "/opt/sure/run.py",
		bundle_root: "/opt/sure",
		integrity_root: "scripts",
		source: "canonical",
		resource_digest: B,
		bundle_digest: C,
		registry_digest: D,
		timeout_ms: 30_000,
		deterministic: true,
		requires_policy_snapshot: true,
		artifact_mode: "producing",
		output_contract: outputContract,
		capability_requirements: [
			{
				capability_id: "sure.execution.harness-python",
				capability_class: "execution_capability",
				required: true,
				constraints: { ignored_duplicate: true },
			},
			{
				capability_id: "sure.execution.gpu",
				capability_class: "execution_capability",
				required: true,
				constraints: { count: 1 },
			},
		],
		kind: "execute",
		consumer_skill_ids: ["sure_test"],
		...overrides,
	};
}

function request() {
	return createRegisteredOperationRequest({
		request_id: "operation-fixed-vector",
		run_id: "run-fixed-vector",
		run_dir: "/work/run",
		workflow_digest: E,
		policy_snapshot_digest: F,
		branch_id: "default",
		unit_id: "produce_result",
		attempt: 2,
		request_operation: "package",
		backend: backend(),
		script_args: ["--profile", "strict"],
		artifact,
		python_executable: "/venv/bin/python",
		package_dir: "/work/package",
		artifacts_root: "/work/run/artifacts",
		artifacts_resolved_root: "/work/run/artifacts",
		semantic_runtime_digest: E,
		reference_snapshot_digest: F,
		policy_digest: D,
		created_at: NOW,
	});
}

describe("registered operation request planning", () => {
	it("fixes the request wire shape and semantic binding independently of the host", () => {
		const operation = backend();
		const capabilityRequirements = [
			{
				capability_id: "sure.execution.harness-python",
				capability_class: "execution_capability" as const,
				required: true,
			},
			{
				capability_id: "sure.execution.gpu",
				capability_class: "execution_capability" as const,
				required: true,
				constraints: { count: 1 },
			},
		];
		const capabilityDigest = canonicalJsonDigest(capabilityRequirements as unknown as JsonValue);
		const outputContractDigest = executionOutputContractDigest(outputContract);
		const semanticDigest = registeredOperationSemanticDigest({
			run_id: "run-fixed-vector",
			branch_id: "default",
			unit_id: "produce_result",
			attempt: 2,
			operation_id: "sure.test.execute",
			request_operation: "package",
			artifact_mode: "producing",
			artifact_input_digest: A,
			workflow_digest: E,
			runtime_digest: E,
			backend_registry_digest: D,
			backend_bundle_digest: C,
			backend_resource_digest: B,
			reference_snapshot_digest: F,
			script_args: ["--profile", "strict"],
			policy_digest: D,
			policy_snapshot_digest: F,
			artifact_output_path: "/work/run/artifacts/result.json",
			output_contract_digest: outputContractDigest,
			artifact_input_path: artifact.path,
			capability_requirements_digest: capabilityDigest,
		});
		const planned = request();

		expect(capabilityDigest).toBe(CAPABILITY_DIGEST);
		expect(outputContractDigest).toBe(OUTPUT_CONTRACT_DIGEST);
		expect(semanticDigest).toBe(SEMANTIC_DIGEST);
		expect(registeredOperationCapabilityRequirements(operation)).toEqual(capabilityRequirements);
		expect(registeredOperationCapabilityRequirementsDigest(operation)).toBe(capabilityDigest);
		expect(planned).toEqual({
			schema: "sure.execution_request.v1",
			request_id: "operation-fixed-vector",
			semantic_request_digest: semanticDigest,
			run_id: "run-fixed-vector",
			unit_id: "produce_result",
			attempt: 2,
			operation: "package",
			subject: {
				bundle_manifest_path: artifact.path,
				bundle_digest: A,
				runtime_identity_digest: E,
			},
			inputs: [artifact],
			entrypoint: {
				executable: "/venv/bin/python",
				argv: [
					"/opt/sure/run.py",
					"--run-dir",
					"/work/run",
					"--produces",
					"/work/run/artifacts/result.json",
					"--profile",
					"strict",
				],
				working_directory: "/work/package",
			},
			runtime_requirements: {
				executor_kind: "python",
				harness_python_executable: "/venv/bin/python",
				semantic_backend_operation_id: "sure.test.execute",
				semantic_backend_registry_digest: D,
				semantic_backend_bundle_digest: C,
				semantic_backend_resource_digest: B,
				portable_runtime_digest: E,
				workflow_digest: E,
				branch_id: "default",
				artifact_mode: "producing",
				script_args: ["--profile", "strict"],
				artifact_input_digest: A,
				artifact_input_path: artifact.path,
				artifact_output_path: "/work/run/artifacts/result.json",
				output_contract_digest: outputContractDigest,
				capability_requirements_digest: capabilityDigest,
			},
			capability_requirements: capabilityRequirements,
			reference_snapshot_digest: F,
			output_root: {
				path: "/work/run/artifacts",
				resolved_path: "/work/run/artifacts",
				scope_id: "run-fixed-vector",
				policy_digest: D,
				writable: true,
			},
			policy_digest: D,
			policy_snapshot_digest: F,
			created_at: NOW,
			output_contract: outputContract,
		});
		expect(request()).toEqual(planned);
		expect(canonicalJsonDigest(planned as unknown as JsonValue)).toBe(REQUEST_DIGEST);
	});

	it("preserves legacy capability digest omission and rejects ambiguous outputs", () => {
		const noExtraCapabilities = backend({ capability_requirements: undefined });
		expect(registeredOperationCapabilityRequirements(noExtraCapabilities)).toEqual([
			{
				capability_id: "sure.execution.harness-python",
				capability_class: "execution_capability",
				required: true,
			},
		]);
		expect(registeredOperationCapabilityRequirementsDigest(noExtraCapabilities)).toBeUndefined();
		expect(() =>
			createRegisteredOperationRequest({
				request_id: "invalid-output",
				run_id: "run-fixed-vector",
				run_dir: "/work/run",
				workflow_digest: E,
				branch_id: "default",
				unit_id: "produce_result",
				attempt: 2,
				request_operation: "package",
				backend: backend(),
				script_args: [],
				artifact,
				output_path: "/work/run/artifacts/other.json",
				python_executable: "/venv/bin/python",
				package_dir: "/work/package",
				artifacts_root: "/work/run/artifacts",
				artifacts_resolved_root: "/work/run/artifacts",
				semantic_runtime_digest: E,
				reference_snapshot_digest: F,
				policy_digest: D,
				created_at: NOW,
			}),
		).toThrow("sure.test.execute output_path does not match its output contract");
		expect(() =>
			createRegisteredOperationRequest({
				request_id: "missing-mode",
				run_id: "run-fixed-vector",
				run_dir: "/work/run",
				workflow_digest: E,
				branch_id: "default",
				unit_id: "produce_result",
				attempt: 2,
				request_operation: "package",
				backend: backend({ artifact_mode: undefined }),
				script_args: [],
				artifact,
				python_executable: "/venv/bin/python",
				package_dir: "/work/package",
				artifacts_root: "/work/run/artifacts",
				artifacts_resolved_root: "/work/run/artifacts",
				semantic_runtime_digest: E,
				reference_snapshot_digest: F,
				policy_digest: D,
				created_at: NOW,
			}),
		).toThrow("sure.test.execute does not declare an artifact_mode");
	});
});

describe("bound execution receipt construction", () => {
	it("copies every request binding and fixes the observed output digest", () => {
		const planned = request();
		const output: ArtifactRef = {
			artifact_id: "result",
			path: "/work/run/artifacts/result.json",
			resolved_path: "/work/run/artifacts/result.json",
			sha256: B,
			size: 7,
			media_type: "application/json",
			origin: "generated",
			source_root: "/work/run/artifacts",
		};
		const capabilityEvidence = [
			{
				capability_id: "sure.execution.harness-python",
				capability_class: "execution_capability" as const,
				status: "AVAILABLE" as const,
				source: "executor" as const,
				observed_at: NOW,
				evidence_digest: C,
			},
		];
		const executor = {
			executor_id: "surectl.python",
			kind: "python" as const,
			version: "0.80.3",
			digest: D,
			trust_level: "cooperative" as const,
		};
		const receipt = createBoundExecutionReceipt(planned, {
			receipt_id: "receipt-fixed-vector",
			executor,
			lifecycle: "SUCCEEDED",
			capability_evidence: capabilityEvidence,
			outputs: [output],
			residuals: [],
			started_at: NOW,
			finished_at: NOW,
			exit_code: 0,
		});
		const outputSetDigest = executionOutputSetDigest([output], []);
		expect(receipt).toEqual({
			schema: "sure.execution_receipt.v1",
			receipt_id: "receipt-fixed-vector",
			request_id: planned.request_id,
			request_digest: canonicalJsonDigest(planned as unknown as JsonValue),
			semantic_request_digest: planned.semantic_request_digest,
			run_id: planned.run_id,
			unit_id: planned.unit_id,
			attempt: planned.attempt,
			executor,
			lifecycle: "SUCCEEDED",
			capability_evidence: capabilityEvidence,
			outputs: [output],
			reference_snapshot_digest: F,
			output_root: planned.output_root,
			policy_digest: D,
			policy_snapshot_digest: F,
			started_at: NOW,
			finished_at: NOW,
			exit_code: 0,
			output_contract_digest: executionOutputContractDigest(outputContract),
			output_set_digest: outputSetDigest,
			residuals: [],
		});
		expect(outputSetDigest).toBe(OUTPUT_SET_DIGEST);
		expect(canonicalJsonDigest(receipt as unknown as JsonValue)).toBe(RECEIPT_DIGEST);
	});
});
