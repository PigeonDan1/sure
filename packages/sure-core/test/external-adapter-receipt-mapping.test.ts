import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
	canonicalJsonDigest,
	type ExecutionLifecycle,
	type ExecutionOutputContract,
	type ExecutionReceipt,
	type ExecutionRequest,
	executionOutputContractDigest,
	executionOutputSetDigest,
	validateExecutionReceipt,
} from "../src/index.ts";

interface MappingFixture {
	schema: string;
	version: number;
	request_profile: {
		execution_surface: string;
		executor_kind: string;
		executor: {
			executor_id: string;
			version: string;
			digest: string;
			trust_level: "cooperative" | "host_enforced" | "attested";
		};
		policy_snapshot_digest: string;
		adapter_manifest_digest: string;
		capability_ids: string[];
		output_contract: ExecutionOutputContract;
	};
	cases: Array<{
		id: string;
		result: { exit_code: number | null; timed_out: boolean; partial?: boolean };
		capability_available?: boolean;
		submitted?: boolean;
		outputs?: Record<string, unknown>[];
		residuals?: Record<string, unknown>[];
		expected: { lifecycle: string; contract_valid: boolean; diagnostic_codes: string[] };
	}>;
}

const fixture = JSON.parse(
	readFileSync(
		new URL("../../../sure/canonical/fixtures/external-adapter-receipt-mapping.v1.json", import.meta.url),
		"utf8",
	),
) as MappingFixture;

describe("external adapter receipt mapping fixture", () => {
	it("keeps the normalized VC outcomes host-neutral", () => {
		expect(fixture.schema).toBe("sure.external_adapter.receipt_mapping.v1");
		expect(fixture.version).toBe(1);
		expect(fixture.request_profile.execution_surface).toBe("vc");
		expect(["remote", "trusted"]).toContain(fixture.request_profile.executor_kind);
		expect(fixture.request_profile.capability_ids).toEqual(expect.arrayContaining(["sure.execution.vc"]));
	});

	it("never classifies timeout, capability absence, or boundary escape as success", () => {
		for (const current of fixture.cases) {
			if (
				current.result.timed_out ||
				current.id === "capability-missing" ||
				current.id === "output-boundary-escape"
			) {
				expect(current.expected.contract_valid && current.expected.lifecycle === "SUCCEEDED").toBe(false);
			}
		}
		expect(fixture.cases.map((current) => current.id)).toEqual([
			"success",
			"executor-failure",
			"partial-output",
			"submission-failure",
			"timeout-cancel-unconfirmed",
			"capability-missing",
			"capability-missing-before-submit",
			"output-boundary-escape",
		]);
	});

	it("keeps Core receipt validation aligned for success, failure, timeout, partial, and boundary cases", () => {
		const profile = fixture.request_profile;
		const root = "/tmp/sure-mapping";
		const digestA = `sha256:${"a".repeat(64)}`;
		const digestB = `sha256:${"b".repeat(64)}`;
		const request: ExecutionRequest = {
			schema: "sure.execution_request.v1",
			request_id: "mapping-request",
			semantic_request_digest: digestA,
			run_id: "mapping-run",
			unit_id: "execute",
			attempt: 1,
			operation: "inference",
			subject: {
				bundle_manifest_path: `${root}/bundle.json`,
				bundle_digest: digestA,
				runtime_identity_digest: digestB,
			},
			inputs: [],
			entrypoint: { executable: "adapter-entrypoint", argv: [] },
			runtime_requirements: {
				execution_surface: "vc",
				executor_kind: profile.executor_kind,
				vc_project: "example-project",
				vc_partition: "gpu-test",
				vc_gpus: 1,
				vc_memory_gb: 32,
				vc_cpus: 8,
				adapter_timeouts: {
					submit_seconds: 300,
					wait_seconds: 1800,
					command_seconds: 1200,
					cancel_seconds: 120,
					poll_seconds: 15,
				},
			},
			capability_requirements: profile.capability_ids.map((capability_id) => ({
				capability_id,
				capability_class: "execution_capability" as const,
				required: true,
			})),
			reference_snapshot_digest: digestB,
			output_root: {
				path: root,
				resolved_path: root,
				scope_id: "mapping-run",
				policy_digest: digestB,
				writable: true,
			},
			policy_digest: digestB,
			policy_snapshot_digest: profile.policy_snapshot_digest,
			adapter_manifest_digest: profile.adapter_manifest_digest,
			created_at: "2026-09-07T00:00:00.000Z",
			output_contract: profile.output_contract,
		};

		for (const current of fixture.cases) {
			const lifecycle = current.expected.lifecycle as ExecutionLifecycle;
			const residuals = (current.residuals ?? []) as unknown as ExecutionReceipt["residuals"];
			const outputs = (current.outputs ?? []) as unknown as ExecutionReceipt["outputs"];
			const receipt: ExecutionReceipt = {
				schema: "sure.execution_receipt.v1",
				receipt_id: `receipt-${current.id}`,
				request_id: request.request_id,
				request_digest: canonicalJsonDigest(request as never),
				semantic_request_digest: request.semantic_request_digest,
				run_id: request.run_id,
				unit_id: request.unit_id,
				attempt: request.attempt,
				executor: { ...profile.executor, kind: profile.executor_kind as "remote" },
				lifecycle,
				capability_evidence: profile.capability_ids.map((capability_id) => ({
					capability_id,
					capability_class: "execution_capability" as const,
					status: current.capability_available === false ? "MISSING" : "AVAILABLE",
					source: "executor" as const,
					observed_at: "2026-09-07T00:00:00.000Z",
					evidence_digest: digestB,
				})),
				outputs,
				reference_snapshot_digest: request.reference_snapshot_digest,
				output_root: request.output_root,
				policy_digest: request.policy_digest,
				policy_snapshot_digest: request.policy_snapshot_digest,
				adapter_manifest_digest: request.adapter_manifest_digest,
				started_at: "2026-09-07T00:00:00.000Z",
				...(lifecycle === "SUCCEEDED" ||
				lifecycle === "FAILED" ||
				lifecycle === "PARTIAL" ||
				lifecycle === "CANCELLED"
					? { finished_at: "2026-09-07T00:00:01.000Z" }
					: {}),
				...(lifecycle === "SUCCEEDED" || lifecycle === "FAILED" || lifecycle === "PARTIAL"
					? { exit_code: current.result.exit_code ?? 0 }
					: {}),
			};
			if (request.output_contract !== undefined) {
				receipt.output_contract_digest = executionOutputContractDigest(request.output_contract);
				receipt.residuals = residuals;
				receipt.output_set_digest = executionOutputSetDigest(receipt.outputs, residuals ?? []);
			}
			const validation = validateExecutionReceipt(request, receipt);
			expect(validation.valid, current.id).toBe(current.expected.contract_valid);
			expect(receipt.lifecycle, current.id).toBe(current.expected.lifecycle);
		}
	});
});
