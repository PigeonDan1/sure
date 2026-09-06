import { describe, expect, it } from "vitest";
import {
	canonicalJsonDigest,
	type ExecutionRequest,
	type ExecutorPort,
	ExecutorRegistry,
	type ExecutorTrustLevel,
	executorDescriptor,
	executorDescriptors,
	executorRegistrySnapshot,
	type JsonValue,
	validateExecutionReceipt,
} from "../src/index.ts";

const DIGEST_A = `sha256:${"a".repeat(64)}`;
const DIGEST_B = `sha256:${"b".repeat(64)}`;
const NOW = "2026-09-06T00:00:00.000Z";

function request(kind: "remote" | "trusted"): ExecutionRequest {
	return {
		schema: "sure.execution_request.v1",
		request_id: `request-${kind}`,
		semantic_request_digest: DIGEST_A,
		run_id: "run-registry",
		unit_id: "execute",
		attempt: 1,
		operation: "formal_evaluation",
		subject: {
			bundle_manifest_path: "/tmp/sure/bundle.json",
			bundle_digest: DIGEST_A,
			runtime_identity_digest: DIGEST_B,
			inference_protocol_digest: DIGEST_A,
			dataset_identity_digest: DIGEST_B,
			scoring_protocol_digest: DIGEST_A,
		},
		inputs: [],
		entrypoint: { executable: "registered-adapter", argv: [] },
		runtime_requirements: {},
		capability_requirements: [
			{ capability_id: `sure.execution.${kind}`, capability_class: "execution_capability", required: true },
		],
		reference_snapshot_digest: DIGEST_B,
		output_root: {
			path: "/tmp/sure/results",
			resolved_path: "/tmp/sure/results",
			scope_id: "run-registry",
			policy_digest: DIGEST_A,
			writable: true,
		},
		policy_digest: DIGEST_A,
		created_at: NOW,
	};
}

function mockPort(kind: "remote" | "trusted", trustLevel: ExecutorTrustLevel): ExecutorPort {
	return {
		identity: {
			executor_id: `test.${kind}`,
			kind,
			version: "1.0.0",
			digest: DIGEST_A,
			trust_level: trustLevel,
		},
		probe: (requirements) =>
			requirements.map((requirement) => ({
				capability_id: requirement.capability_id,
				capability_class: requirement.capability_class,
				status: "AVAILABLE",
				source: kind === "trusted" ? "trusted_attestation" : "executor",
				observed_at: NOW,
				evidence_digest: DIGEST_B,
			})),
		execute: (input) => ({
			schema: "sure.execution_receipt.v1",
			receipt_id: `receipt-${kind}`,
			request_id: input.request_id,
			request_digest: canonicalJsonDigest(input as unknown as JsonValue),
			semantic_request_digest: input.semantic_request_digest,
			run_id: input.run_id,
			unit_id: input.unit_id,
			attempt: input.attempt,
			executor: {
				executor_id: `test.${kind}`,
				kind,
				version: "1.0.0",
				digest: DIGEST_A,
				trust_level: trustLevel,
			},
			lifecycle: "SUCCEEDED",
			capability_evidence: input.capability_requirements.map((requirement) => ({
				capability_id: requirement.capability_id,
				capability_class: requirement.capability_class,
				status: "AVAILABLE",
				source: kind === "trusted" ? "trusted_attestation" : "executor",
				observed_at: NOW,
				evidence_digest: DIGEST_B,
			})),
			outputs: [],
			reference_snapshot_digest: input.reference_snapshot_digest,
			output_root: input.output_root,
			policy_digest: input.policy_digest,
			started_at: NOW,
			finished_at: NOW,
			exit_code: 0,
		}),
	};
}

describe("host-neutral executor registry", () => {
	it("declares every wire executor kind exactly once", () => {
		const descriptors = executorDescriptors();
		expect(descriptors.map((descriptor) => descriptor.kind)).toEqual([
			"local",
			"python",
			"docker",
			"remote",
			"trusted",
		]);
		expect(new Set(descriptors.map((descriptor) => descriptor.executor_id)).size).toBe(descriptors.length);
	});

	it("does not turn external or trusted declarations into builtin capability claims", () => {
		expect(executorDescriptor("remote")).toMatchObject({
			implementation: "external_registration_required",
			minimum_trust_level: "cooperative",
		});
		expect(executorDescriptor("trusted")).toMatchObject({
			implementation: "external_registration_required",
			minimum_trust_level: "attested",
		});
		expect(executorDescriptor("python")).toMatchObject({
			implementation: "builtin",
			minimum_trust_level: "cooperative",
		});
	});

	it("routes registered remote and trusted mocks without weakening their trust floor", async () => {
		const registry = new ExecutorRegistry();
		registry.register(mockPort("remote", "host_enforced"));
		registry.register(mockPort("trusted", "attested"));
		expect(registry.registeredIdentities().map((identity) => identity.kind)).toEqual(["remote", "trusted"]);

		for (const kind of ["remote", "trusted"] as const) {
			const input = request(kind);
			const receipt = await registry.resolve(kind)?.execute(input);
			if (!receipt) throw new Error(`missing ${kind} receipt`);
			expect(validateExecutionReceipt(input, receipt).valid).toBe(true);
		}

		const insufficient = new ExecutorRegistry();
		expect(() => insufficient.register(mockPort("trusted", "cooperative"))).toThrow(/does not meet attested trust/);
	});

	it("has a stable registry digest independent of host probes", () => {
		const snapshot = executorRegistrySnapshot();
		expect(snapshot.schema).toBe("sure.executor.registry.v1");
		expect(snapshot.registry_digest).toMatch(/^sha256:[0-9a-f]{64}$/);
		expect(executorRegistrySnapshot()).toEqual(snapshot);
	});
});
