import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { createFrozenEvaluationSubject } from "../src/conformance/frozen.ts";
import {
	canonicalJson,
	canonicalJsonDigest,
	canonicalJsonSha256,
	evaluateCapabilityRequirements,
	evaluatePathBoundary,
	parseAndValidateJson,
	validateCapabilityEvidence,
	validateCapabilityEvidenceList,
	validateJsonSchema,
} from "../src/contracts/index.ts";
import type {
	CapabilityEvidence,
	CapabilityRequirement,
	ConformanceRecord,
	ExecutionReceipt,
	ExecutionRequest,
	JsonValue,
} from "../src/contracts/types.ts";
import {
	createOutcome,
	outcomeFromExecutionLifecycle,
	PUBLIC_OUTCOMES,
	REASON_CODES,
	VALIDATOR_VERDICTS,
	WORKFLOW_DISPOSITIONS,
} from "../src/workflow/outcome.ts";

const DIGEST_A = "a".repeat(64);
const DIGEST_B = "b".repeat(64);
const DIGEST_C = "c".repeat(64);
const NOW = "2026-09-06T00:00:00.000Z";

function readSchema(name: string): Record<string, unknown> {
	const path = fileURLToPath(new URL(`../../../sure/core/contracts/${name}.schema.json`, import.meta.url));
	return JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
}

function outputRoot() {
	return {
		path: "/tmp/sure-dev/runs/run-1",
		resolved_path: "/tmp/sure-dev/runs/run-1",
		scope_id: "run-1",
		policy_digest: DIGEST_A,
		writable: true as const,
	};
}

function executionRequest(): ExecutionRequest {
	return {
		schema: "sure.execution_request.v1",
		request_id: "request-1",
		semantic_request_digest: DIGEST_A,
		run_id: "run-1",
		unit_id: "execute",
		attempt: 1,
		operation: "inference",
		subject: {
			bundle_manifest_path: "/tmp/sure-dev/bundle.json",
			bundle_digest: DIGEST_A,
			runtime_identity_digest: DIGEST_B,
			inference_protocol_digest: DIGEST_C,
		},
		inputs: [
			{
				artifact_id: "dataset-1",
				path: "/hpc/reference/dataset.jsonl",
				resolved_path: "/hpc/reference/dataset.jsonl",
				sha256: DIGEST_B,
				size: 12,
				media_type: "application/x-ndjson",
				origin: "read_only_reference",
				source_root: "/hpc/reference",
				reference_snapshot_digest: DIGEST_C,
			},
		],
		entrypoint: { executable: "python3", argv: ["-s", "/tmp/sure-dev/run.py", "--input", "dataset.jsonl"] },
		runtime_requirements: { python: "3.11" },
		capability_requirements: [
			{ capability_id: "sure.resource.gpu", capability_class: "execution_capability", required: true },
		],
		reference_snapshot_digest: DIGEST_C,
		output_root: outputRoot(),
		policy_digest: DIGEST_A,
		policy_snapshot_digest: DIGEST_B,
		created_at: NOW,
	};
}

function executionReceipt(): ExecutionReceipt {
	return {
		schema: "sure.execution_receipt.v1",
		receipt_id: "receipt-1",
		request_id: "request-1",
		request_digest: DIGEST_A,
		semantic_request_digest: DIGEST_B,
		run_id: "run-1",
		unit_id: "execute",
		attempt: 1,
		executor: {
			executor_id: "local-python-1",
			kind: "python",
			version: "1.0.0",
			digest: DIGEST_A,
			trust_level: "host_enforced",
		},
		lifecycle: "SUCCEEDED",
		capability_evidence: [
			{
				capability_id: "sure.resource.gpu",
				capability_class: "execution_capability",
				status: "AVAILABLE",
				source: "host_probe",
				observed_at: NOW,
				evidence_digest: DIGEST_B,
			},
		],
		outputs: [],
		reference_snapshot_digest: DIGEST_C,
		output_root: outputRoot(),
		policy_digest: DIGEST_A,
		started_at: NOW,
		finished_at: NOW,
		exit_code: 0,
	};
}

function conformanceRecord(): ConformanceRecord {
	return {
		schema: "sure.conformance.v1",
		conformance_id: "conformance-1",
		run_id: "run-1",
		unit_id: "formal-evaluation",
		attempt: 1,
		request_digest: DIGEST_A,
		receipt_digest: DIGEST_B,
		subject_bundle_digest: DIGEST_C,
		subject_manifest_digest: DIGEST_A,
		prediction_digest: DIGEST_B,
		evaluator_engine_digest: DIGEST_C,
		evaluator_route_digest: DIGEST_A,
		approval_event_digest: DIGEST_B,
		legacy_unverified: false,
		runtime_identity_digest: DIGEST_A,
		inference_protocol_digest: DIGEST_B,
		dataset_identity_digest: DIGEST_C,
		scoring_protocol_digest: DIGEST_A,
		workflow_digest: DIGEST_B,
		validator_digest: DIGEST_C,
		executor_digest: DIGEST_A,
		policy_digest: DIGEST_B,
		reference_snapshot_digest: DIGEST_C,
		output_root: outputRoot(),
		validator_verdict: "PASS",
		workflow_disposition: "TERMINATE",
		outcome: "PASS",
		reason_code: "VALIDATION_PASSED",
		assurance_profile: "pi_enforced",
		formal_evaluation_eligible: true,
		checked_at: NOW,
		evidence: [],
		diagnostics: [],
	};
}

function frozenEvaluationSubject() {
	return createFrozenEvaluationSubject({
		subject_id: "subject-1",
		bundle_manifest_path: "/tmp/sure-dev/bundle.json",
		bundle_digest: DIGEST_A,
		runtime_identity_digest: DIGEST_B,
		inference_protocol_digest: DIGEST_C,
		dataset_identity_digest: DIGEST_A,
		scoring_protocol_digest: DIGEST_B,
		prediction_path: "/tmp/sure-dev/predictions.jsonl",
		prediction_digest: DIGEST_C,
		execution_receipt_digest: DIGEST_A,
		evaluator_engine_digest: DIGEST_B,
		evaluator_route_digest: DIGEST_C,
		workflow_digest: DIGEST_A,
		validator_digest: DIGEST_B,
		executor_digest: DIGEST_C,
		policy_digest: DIGEST_A,
		reference_snapshot_digest: DIGEST_B,
		assurance_profile: "pi_enforced",
		legacy_unverified: false,
		approval_event_digest: DIGEST_C,
		frozen_at: NOW,
	});
}

describe("canonical JSON", () => {
	it("sorts object keys recursively and produces a stable SHA-256", () => {
		const left: JsonValue = { b: 2, a: { z: true, y: [3, "x"] } };
		const right: JsonValue = { a: { y: [3, "x"], z: true }, b: 2 };
		expect(canonicalJson(left)).toBe('{"a":{"y":[3,"x"],"z":true},"b":2}');
		expect(canonicalJsonSha256(left)).toBe(canonicalJsonSha256(right));
		expect(canonicalJsonDigest(left)).toBe(`sha256:${canonicalJsonSha256(left)}`);
	});

	it("rejects values outside the JSON data model", () => {
		expect(() => canonicalJson(Number.NaN as never)).toThrow(/non-finite/);
		expect(() => canonicalJson({ value: undefined } as never)).toThrow(/undefined/);
		const cyclic: { self?: unknown } = {};
		cyclic.self = cyclic;
		expect(() => canonicalJson(cyclic as never)).toThrow(/cyclic/);
	});
});

describe("wire schemas", () => {
	it("round-trips valid capability, request, receipt, and conformance records", () => {
		const capability = {
			schema: "sure.capability_report.v1",
			requirements: executionRequest().capability_requirements,
			evidence: executionReceipt().capability_evidence,
			checked_at: NOW,
		};
		const cases: Array<[string, unknown]> = [
			["capability", capability],
			["execution_request", executionRequest()],
			["execution_receipt", executionReceipt()],
			["conformance", conformanceRecord()],
			["evaluation_subject", frozenEvaluationSubject()],
			[
				"operation_execution_evidence",
				{
					schema: "sure.operation.execution.v1",
					projection_version: 2,
					source: "registered_operation",
					operation_id: "sure.onboard.execute_import",
					artifact_mode: "mutating",
					verdict: "PASS",
					reason_code: "EXECUTION_SUCCEEDED",
					diagnostics: [],
					artifact_input_digest: DIGEST_A,
					artifact_output_digest: DIGEST_B,
				},
			],
		];
		for (const [name, value] of cases) {
			const serialized = JSON.stringify(value);
			expect(parseAndValidateJson(readSchema(name), serialized)).toEqual({
				ok: true,
				value,
				issues: [],
			});
			expect(canonicalJsonSha256(value as JsonValue)).toBe(canonicalJsonSha256(JSON.parse(serialized)));
		}
	});

	it("keeps operation evidence legacy-readable but current PASS fail-closed", () => {
		const schema = readSchema("operation_execution_evidence");
		const legacy = {
			schema: "sure.operation.execution.v1",
			source: "registered_operation",
			operation_id: "sure.onboard.execute_import",
			verdict: "PASS",
			reason_code: "EXECUTION_SUCCEEDED",
			diagnostics: [],
			artifact_input_digest: DIGEST_A,
		};
		expect(validateJsonSchema(schema, legacy).ok).toBe(true);
		expect(validateJsonSchema(schema, { ...legacy, projection_version: 2 }).ok).toBe(false);
	});

	it("rejects shell strings and unbound read-only references", () => {
		const schema = readSchema("execution_request");
		const shellRequest = { ...executionRequest(), command: "python3 run.py --input dataset.jsonl" };
		expect(validateJsonSchema(schema, shellRequest).ok).toBe(false);

		const missingSnapshot = executionRequest();
		delete missingSnapshot.inputs[0].reference_snapshot_digest;
		expect(validateJsonSchema(schema, missingSnapshot).ok).toBe(false);
	});

	it("requires the structured Docker runtime shape when Docker is selected", () => {
		const schema = readSchema("execution_request");
		const dockerRequest = {
			...executionRequest(),
			runtime_requirements: {
				executor_kind: "docker",
				docker_image: `registry.example/sure/test@sha256:${DIGEST_A}`,
				docker_mounts: [{ source: "/tmp/sure-dev/runs/run-1", target: "/work", read_only: true }],
			},
			capability_requirements: [
				{ capability_id: "sure.execution.docker", capability_class: "execution_capability", required: true },
			],
		};
		expect(validateJsonSchema(schema, dockerRequest).ok).toBe(true);
		const missingMounts = {
			...dockerRequest,
			runtime_requirements: { executor_kind: "docker", docker_image: "image" },
		};
		expect(validateJsonSchema(schema, missingMounts).ok).toBe(false);
	});

	it("requires a digest-pinned Docker image for formal evaluation", () => {
		const schema = readSchema("execution_request");
		const base = executionRequest();
		const formalDocker = {
			...base,
			operation: "formal_evaluation",
			subject: {
				...base.subject,
				dataset_identity_digest: DIGEST_A,
				scoring_protocol_digest: DIGEST_B,
			},
			runtime_requirements: {
				executor_kind: "docker",
				docker_image: `registry.example/sure/trans@sha256:${DIGEST_A}`,
				docker_image_digest: `sha256:${DIGEST_A}`,
				docker_mounts: [],
			},
		};
		expect(validateJsonSchema(schema, formalDocker).ok).toBe(true);
		expect(
			validateJsonSchema(schema, {
				...formalDocker,
				runtime_requirements: {
					...formalDocker.runtime_requirements,
					docker_image: "registry.example/sure/trans:latest",
				},
			}).ok,
		).toBe(false);
	});

	it("requires an explicit registered transport for external execution routes", () => {
		const schema = readSchema("execution_request");
		const base = executionRequest();
		const vcRequest = {
			...base,
			runtime_requirements: {
				execution_surface: "vc",
				executor_kind: "remote",
				vc_project: "sure-test",
				vc_partition: "gpu-test",
			},
			capability_requirements: [
				{ capability_id: "sure.execution.remote", capability_class: "execution_capability", required: true },
				{ capability_id: "sure.execution.vc", capability_class: "execution_capability", required: true },
			],
		};
		expect(validateJsonSchema(schema, vcRequest).ok).toBe(true);
		expect(
			validateJsonSchema(schema, {
				...vcRequest,
				runtime_requirements: { ...vcRequest.runtime_requirements, executor_kind: "python" },
			}).ok,
		).toBe(false);
		expect(
			validateJsonSchema(schema, {
				...vcRequest,
				runtime_requirements: { ...vcRequest.runtime_requirements, vc_partition: undefined },
			}).ok,
		).toBe(false);
	});

	it("validates the shared output contract across request and receipt schemas", () => {
		const outputContract = {
			schema: "sure.execution_output_contract.v1",
			mode: "producing",
			outputs: [
				{ artifact_id: "manifest", path: "manifest.json", kind: "file", required: true },
				{ artifact_id: "bundle", path: "bundle", kind: "directory", required: false },
			],
			temporary_paths: [".staging"],
			allow_missing_on_failure: true,
			retain_failed_outputs: true,
		};
		expect(validateJsonSchema(readSchema("execution_output_contract"), outputContract).ok).toBe(true);
		expect(
			validateJsonSchema(readSchema("execution_request"), { ...executionRequest(), output_contract: outputContract })
				.ok,
		).toBe(true);
		const receipt = {
			...executionReceipt(),
			outputs: [
				{
					artifact_id: "bundle",
					path: "/tmp/sure-dev/runs/run-1/bundle",
					resolved_path: "/tmp/sure-dev/runs/run-1/bundle",
					sha256: DIGEST_A,
					size: 12,
					media_type: "inode/directory",
					origin: "generated",
					source_root: "/tmp/sure-dev/runs/run-1",
					kind: "directory",
					digest_kind: "tree_sha256",
				},
			],
			residuals: [
				{
					path: "/tmp/sure-dev/runs/run-1/.staging",
					resolved_path: "/tmp/sure-dev/runs/run-1/.staging",
					kind: "directory",
					status: "present",
					sha256: DIGEST_B,
					digest_kind: "tree_sha256",
					size: 2,
				},
			],
			output_contract_digest: DIGEST_C,
			output_set_digest: DIGEST_A,
		};
		expect(validateJsonSchema(readSchema("execution_receipt"), receipt).ok).toBe(true);
		const noRequiredOutput = {
			...outputContract,
			outputs: outputContract.outputs.map((output) => ({ ...output, required: false })),
		};
		expect(validateJsonSchema(readSchema("execution_output_contract"), noRequiredOutput).ok).toBe(false);
		expect(
			validateJsonSchema(readSchema("execution_output_contract"), {
				...outputContract,
				outputs: [{ ...outputContract.outputs[0], path: "manifest/" }],
			}).ok,
		).toBe(false);
	});

	it("validates conditional execution input contracts with locator-specific paths", () => {
		const contract = {
			schema: "sure.execution_input_contract.v1",
			context_artifact: "trans_input_resolved.json",
			selection: "exactly_one",
			selectors: [
				{
					selector_id: "python-source",
					match: { source_kind: "python" },
					inputs: [
						{ input_id: "source", locator_kind: "run_artifact", path: "source.json", required: true },
						{ input_id: "lock", locator_kind: "resolved_input_field", path: "lockfile", required: true },
					],
				},
			],
		};
		const schema = readSchema("execution_input_contract");
		expect(validateJsonSchema(schema, contract).ok).toBe(true);
		expect(
			validateJsonSchema(schema, {
				...contract,
				selectors: [
					{
						...contract.selectors[0],
						inputs: [{ ...contract.selectors[0].inputs[0], path: "../outside.json" }],
					},
				],
			}).ok,
		).toBe(false);
		expect(
			validateJsonSchema(schema, {
				...contract,
				selectors: [
					{
						...contract.selectors[0],
						inputs: [{ ...contract.selectors[0].inputs[1], path: "../lockfile" }],
					},
				],
			}).ok,
		).toBe(false);
	});

	it("validates locator-specific paths in an execution input binding", () => {
		const request = executionRequest();
		const artifact = request.inputs[0];
		const binding = {
			schema: "sure.execution_input_binding.v1",
			contract_digest: DIGEST_A,
			selector_id: "python-source",
			context_artifact: "context.json",
			context_digest: DIGEST_B,
			inputs: [
				{
					input_id: "dataset",
					locator_kind: "run_artifact",
					path: "dataset.jsonl",
					artifact,
				},
			],
			binding_digest: DIGEST_C,
		};
		expect(validateJsonSchema(readSchema("execution_request"), { ...request, input_binding: binding }).ok).toBe(true);
		expect(
			validateJsonSchema(readSchema("execution_request"), {
				...request,
				input_binding: {
					...binding,
					inputs: [{ ...binding.inputs[0], path: "../outside.json" }],
				},
			}).ok,
		).toBe(false);
		expect(
			validateJsonSchema(readSchema("execution_request"), {
				...request,
				input_binding: {
					...binding,
					inputs: [{ ...binding.inputs[0], locator_kind: "resolved_input_field", path: "lockfile" }],
				},
			}).ok,
		).toBe(true);
		expect(
			validateJsonSchema(readSchema("execution_request"), {
				...request,
				input_binding: {
					...binding,
					inputs: [{ ...binding.inputs[0], locator_kind: "resolved_input_field", path: "../lockfile" }],
				},
			}).ok,
		).toBe(false);
	});

	it("rejects fake formal PASS and capability-missing PASS", () => {
		const schema = readSchema("conformance");
		const missingFreeze = conformanceRecord();
		delete missingFreeze.dataset_identity_digest;
		expect(validateJsonSchema(schema, missingFreeze).ok).toBe(false);

		const fakePass = { ...conformanceRecord(), reason_code: "CAPABILITY_MISSING" };
		expect(validateJsonSchema(schema, fakePass).ok).toBe(false);

		const executorPass = { ...conformanceRecord(), formal_evaluation_eligible: false, validator_verdict: "FAIL" };
		expect(validateJsonSchema(schema, executorPass).ok).toBe(false);
	});

	it("validates the portable runtime lock contract", () => {
		const lock = {
			schema: "sure.portable.runtime.lock.v1",
			runtime_version: "portable-v1",
			core_package_version: "0.80.3",
			semantic_backend_registry_digest: `sha256:${DIGEST_A}`,
			executor_registry_digest: `sha256:${DIGEST_B}`,
			operation_ids: ["sure.eval.run"],
			files: [{ path: "backends/eval/scripts/run.py", size_bytes: 12, sha256: `sha256:${DIGEST_A}` }],
			runtime_digest: `sha256:${DIGEST_B}`,
		};
		const schema = readSchema("portable_runtime_lock");
		expect(validateJsonSchema(schema, lock).ok).toBe(true);
		expect(validateJsonSchema(schema, { ...lock, files: [{ ...lock.files[0], path: "../run.py" }] }).ok).toBe(false);
	});

	it("keeps TypeScript outcome enums aligned with the conformance schema", () => {
		const schema = readSchema("conformance") as {
			properties: Record<string, { enum?: string[] }>;
		};
		expect(schema.properties.validator_verdict.enum).toEqual([...VALIDATOR_VERDICTS]);
		expect(schema.properties.workflow_disposition.enum).toEqual([...WORKFLOW_DISPOSITIONS]);
		expect(schema.properties.outcome.enum).toEqual([...PUBLIC_OUTCOMES]);
		expect(schema.properties.reason_code.enum).toEqual([...REASON_CODES]);
	});
});

describe("outcome invariants", () => {
	it("never maps missing execution capability to PASS", () => {
		const outcome = createOutcome({
			validatorVerdict: "NOT_EXECUTED",
			workflowDisposition: "BLOCK",
			reasonCode: "CAPABILITY_MISSING",
		});
		expect(outcome.outcome).toBe("NOT_EXECUTED");
		expect(outcome.retryable).toBe(false);
	});

	it("does not allow PARTIAL, failed, cancelled, or running execution to pass", () => {
		for (const lifecycle of ["SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "RUNNING"] as const) {
			expect(outcomeFromExecutionLifecycle(lifecycle).outcome).not.toBe("PASS");
		}
		expect(outcomeFromExecutionLifecycle("SUCCEEDED")).toMatchObject({
			validator_verdict: "NOT_EXECUTED",
			workflow_disposition: "WAIT",
			reason_code: "VALIDATION_PENDING",
		});
		expect(outcomeFromExecutionLifecycle("PARTIAL")).toMatchObject({
			validator_verdict: "FAIL",
			workflow_disposition: "BLOCK",
			reason_code: "EXECUTION_PARTIAL",
		});
	});

	it("rejects contradictory outcome construction", () => {
		expect(() =>
			createOutcome({
				validatorVerdict: "PASS",
				workflowDisposition: "ADVANCE",
				reasonCode: "VALIDATION_PASSED",
				executionLifecycle: "PARTIAL",
			}),
		).toThrow(/cannot produce PASS/);
		expect(() =>
			createOutcome({
				validatorVerdict: "FAIL",
				workflowDisposition: "BLOCK",
				reasonCode: "CAPABILITY_MISSING",
			}),
		).toThrow(/NOT_EXECUTED/);
	});
});

describe("capability admission", () => {
	const gpuRequirement: CapabilityRequirement = {
		capability_id: "sure.resource.gpu",
		capability_class: "execution_capability",
		required: true,
	};
	const evidenceBase = {
		capability_id: "sure.resource.gpu",
		status: "AVAILABLE" as const,
		observed_at: NOW,
		evidence_digest: DIGEST_A,
	};

	it("accepts authoritative execution evidence", () => {
		const evidence: CapabilityEvidence = {
			...evidenceBase,
			capability_class: "execution_capability",
			source: "executor",
		};
		expect(evaluateCapabilityRequirements([gpuRequirement], [evidence])).toMatchObject({ admitted: true });
	});

	it("does not let an agent capability satisfy an execution requirement", () => {
		const evidence: CapabilityEvidence = {
			...evidenceBase,
			capability_class: "agent_capability",
			source: "agent",
		};
		const result = evaluateCapabilityRequirements([gpuRequirement], [evidence]);
		expect(result).toMatchObject({
			admitted: false,
			missing: ["sure.resource.gpu"],
			blocking_outcome: {
				validator_verdict: "NOT_EXECUTED",
				outcome: "NOT_EXECUTED",
				reason_code: "CAPABILITY_MISSING",
			},
		});
	});

	it("blocks unknown and policy-denied capabilities without PASS", () => {
		const unknown = evaluateCapabilityRequirements(
			[{ capability_id: "sure.unknown.future", capability_class: "execution_capability", required: true }],
			[],
		);
		expect(unknown.blocking_outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "UNKNOWN_CAPABILITY" });

		const denied: CapabilityEvidence = {
			...evidenceBase,
			capability_class: "execution_capability",
			status: "DENIED",
			source: "site_policy",
		};
		expect(evaluateCapabilityRequirements([gpuRequirement], [denied]).blocking_outcome).toMatchObject({
			outcome: "NOT_EXECUTED",
			reason_code: "POLICY_DENIED",
		});
	});

	it("rejects an unregistered evidence source instead of treating it as authoritative", () => {
		const evidence: CapabilityEvidence = {
			...evidenceBase,
			capability_class: "execution_capability",
			source: "executor",
		};
		const forged = { ...evidence, source: "remote_daemon" };
		expect(validateCapabilityEvidence(forged)).toContain("capability_evidence.source is invalid");
		expect(validateCapabilityEvidenceList([forged])).toContain("capability_evidence[0].source is invalid");
	});

	it("requires a digest for available evidence and forbids agent execution evidence", () => {
		const available = {
			...evidenceBase,
			capability_class: "execution_capability",
			source: "host_probe",
			evidence_digest: undefined,
		};
		expect(validateCapabilityEvidence(available)).toContain(
			"capability_evidence.evidence_digest is required for AVAILABLE evidence",
		);
		const agentExecution = {
			...evidenceBase,
			capability_class: "execution_capability",
			source: "agent",
		};
		expect(validateCapabilityEvidence(agentExecution)).toContain(
			"capability_evidence.source agent cannot satisfy an execution capability",
		);
	});
});

describe("path admission", () => {
	const allowed = [{ path: "/tmp/sure-dev", resolved_path: "/tmp/sure-dev" }];
	const reference = [{ path: "/hpc/reference", resolved_path: "/hpc/reference" }];

	it("accepts a local resolved output path", () => {
		expect(
			evaluatePathBoundary({
				candidate_path: "/tmp/sure-dev/runs/one",
				candidate_resolved_path: "/tmp/sure-dev/runs/one",
				allowed_roots: allowed,
				forbidden_roots: reference,
			}),
		).toEqual({ admitted: true });
	});

	it("blocks lexical traversal, symlink escape, and read-only reference output", () => {
		const traversal = evaluatePathBoundary({
			candidate_path: "/tmp/sure-dev/../outside",
			candidate_resolved_path: "/tmp/outside",
			allowed_roots: allowed,
		});
		expect(traversal).toMatchObject({ admitted: false, reason_code: "PATH_OUT_OF_SCOPE" });

		const symlink = evaluatePathBoundary({
			candidate_path: "/tmp/sure-dev/link/output",
			candidate_resolved_path: "/hpc/reference/output",
			allowed_roots: allowed,
			forbidden_roots: reference,
		});
		expect(symlink).toMatchObject({ admitted: false, reason_code: "READ_ONLY_REFERENCE" });

		const symlinkEscape = evaluatePathBoundary({
			candidate_path: "/tmp/sure-dev/link/output",
			candidate_resolved_path: "/tmp/other/output",
			allowed_roots: allowed,
		});
		expect(symlinkEscape).toMatchObject({ admitted: false, reason_code: "SYMLINK_ESCAPE" });

		const production = evaluatePathBoundary({
			candidate_path: "/hpc/reference/new-output",
			candidate_resolved_path: "/hpc/reference/new-output",
			allowed_roots: reference,
			forbidden_roots: reference,
		});
		expect(production.blocking_outcome).toMatchObject({
			outcome: "NOT_EXECUTED",
			reason_code: "READ_ONLY_REFERENCE",
		});
	});
});
