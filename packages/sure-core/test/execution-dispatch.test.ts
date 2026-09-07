import { describe, expect, it, vi } from "vitest";
import {
	canonicalJsonDigest,
	dispatchExecutor,
	type ExecutionReceipt,
	type ExecutionRequest,
	type ExecutorKind,
	type ExecutorPort,
	ExecutorRegistry,
	type ExecutorTrustLevel,
	type JsonValue,
	parseExecutionAdapterRoute,
} from "../src/index.ts";

const A = `sha256:${"a".repeat(64)}`;
const B = `sha256:${"b".repeat(64)}`;
const NOW = "2026-09-07T00:00:00.000Z";

function request(kind: ExecutorKind, runtime: Record<string, JsonValue> = {}): ExecutionRequest {
	const formal = kind === "trusted";
	return {
		schema: "sure.execution_request.v1",
		request_id: `dispatch-${kind}`,
		semantic_request_digest: A,
		run_id: "dispatch-run",
		unit_id: "execute",
		attempt: 1,
		operation: formal ? "formal_evaluation" : "inference",
		subject: {
			bundle_manifest_path: "/tmp/sure-dispatch/bundle.json",
			bundle_digest: A,
			runtime_identity_digest: B,
			...(formal
				? {
						inference_protocol_digest: A,
						dataset_identity_digest: B,
						scoring_protocol_digest: A,
					}
				: {}),
		},
		inputs: [],
		entrypoint: { executable: "adapter-entrypoint", argv: [] },
		runtime_requirements: { executor_kind: kind, ...runtime },
		capability_requirements: [
			{
				capability_id: `sure.execution.${kind}`,
				capability_class: "execution_capability",
				required: true,
			},
		],
		reference_snapshot_digest: B,
		output_root: {
			path: "/tmp/sure-dispatch/results",
			resolved_path: "/tmp/sure-dispatch/results",
			scope_id: "dispatch-run",
			policy_digest: A,
			writable: true,
		},
		policy_digest: A,
		created_at: NOW,
	};
}

function portFor(
	kind: "remote" | "trusted",
	trustLevel: ExecutorTrustLevel,
	overrides: Partial<Pick<ExecutorPort, "probe" | "execute">> = {},
): ExecutorPort {
	const identity = {
		executor_id: `test.${kind}`,
		kind,
		version: "1.0.0",
		digest: A,
		trust_level: trustLevel,
	} as const;
	const probe =
		overrides.probe ??
		(((requirements) =>
			requirements.map((requirement) => ({
				capability_id: requirement.capability_id,
				capability_class: requirement.capability_class,
				status: "AVAILABLE" as const,
				source: kind === "trusted" ? ("trusted_attestation" as const) : ("executor" as const),
				observed_at: NOW,
				evidence_digest: B,
			}))) as ExecutorPort["probe"]);
	const execute =
		overrides.execute ??
		(((input) => {
			const evidence = input.capability_requirements.map((requirement) => ({
				capability_id: requirement.capability_id,
				capability_class: requirement.capability_class,
				status: "AVAILABLE" as const,
				source: kind === "trusted" ? ("trusted_attestation" as const) : ("executor" as const),
				observed_at: NOW,
				evidence_digest: B,
			}));
			return {
				schema: "sure.execution_receipt.v1" as const,
				receipt_id: `receipt-${kind}`,
				request_id: input.request_id,
				request_digest: canonicalJsonDigest(input as unknown as JsonValue),
				semantic_request_digest: input.semantic_request_digest,
				run_id: input.run_id,
				unit_id: input.unit_id,
				attempt: input.attempt,
				executor: { ...identity },
				lifecycle: "SUCCEEDED" as const,
				capability_evidence: evidence,
				outputs: [],
				reference_snapshot_digest: input.reference_snapshot_digest,
				output_root: input.output_root,
				policy_digest: input.policy_digest,
				started_at: NOW,
				finished_at: NOW,
				exit_code: 0,
			} satisfies ExecutionReceipt;
		}) as ExecutorPort["execute"]);
	return { identity, probe, execute };
}

describe("host-neutral executor dispatch", () => {
	it("keeps an unregistered remote executor unavailable without local fallback", async () => {
		const registry = new ExecutorRegistry();
		const result = await dispatchExecutor(registry, request("remote"));

		expect(result.registered).toBe(false);
		expect(result.receipt).toBeUndefined();
		expect(result.capability.admitted).toBe(false);
		expect(result.capability.missing).toContain("sure.execution.remote");
		expect(result.outcome).toMatchObject({
			validator_verdict: "NOT_EXECUTED",
			workflow_disposition: "BLOCK",
			outcome: "NOT_EXECUTED",
			reason_code: "CAPABILITY_MISSING",
			execution_lifecycle: "NOT_STARTED",
		});
	});

	it("requires an adapter probe before dispatching a registered remote request", async () => {
		const registry = new ExecutorRegistry();
		const execute = vi.fn(portFor("remote", "host_enforced").execute);
		const probe = vi.fn(
			portFor("remote", "host_enforced", {
				probe: (requirements) =>
					requirements.map((requirement) => ({
						capability_id: requirement.capability_id,
						capability_class: requirement.capability_class,
						status: "MISSING" as const,
						source: "executor" as const,
						observed_at: NOW,
					})),
			}).probe,
		);
		registry.register({ ...portFor("remote", "host_enforced"), probe, execute });

		const result = await dispatchExecutor(registry, request("remote"));
		expect(probe).toHaveBeenCalledOnce();
		expect(execute).not.toHaveBeenCalled();
		expect(result.registered).toBe(true);
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "CAPABILITY_MISSING" });
	});

	it("binds a successful remote receipt to the registered identity", async () => {
		const registry = new ExecutorRegistry();
		const port = portFor("remote", "host_enforced");
		registry.register(port);

		const result = await dispatchExecutor(registry, request("remote"));
		expect(result.receipt_validation?.valid).toBe(true);
		expect(result.receipt?.executor).toEqual(port.identity);
		expect(result.outcome).toMatchObject({
			outcome: "NOT_EXECUTED",
			workflow_disposition: "WAIT",
			reason_code: "VALIDATION_PENDING",
		});
	});

	it("preserves a VC transport declaration while requiring the registered adapter", async () => {
		const registry = new ExecutorRegistry();
		const port = portFor("remote", "host_enforced");
		const execute = vi.fn(port.execute);
		registry.register({ ...port, execute });
		const input = request("remote", { execution_surface: "vc", vc_project: "sure-test", vc_partition: "gpu-test" });

		const result = await dispatchExecutor(registry, input);
		expect(execute).toHaveBeenCalledOnce();
		expect(result.request_validation.valid).toBe(true);
		expect(result.outcome.reason_code).toBe("VALIDATION_PENDING");
	});

	it("rejects an incomplete or locally disguised VC route before probing", async () => {
		const incomplete = request("remote", { execution_surface: "vc", vc_project: "sure-test" });
		const parsed = parseExecutionAdapterRoute(incomplete.runtime_requirements);
		expect(parsed.valid).toBe(false);
		expect(parsed.errors).toContain("runtime_requirements.vc_partition must be a non-empty string");
		const registry = new ExecutorRegistry();
		const port = portFor("remote", "host_enforced");
		const probe = vi.fn(port.probe);
		registry.register({ ...port, probe });
		const rejected = await dispatchExecutor(registry, incomplete);
		expect(rejected.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		expect(probe).not.toHaveBeenCalled();

		const disguised = request("local", {
			execution_surface: "vc",
			vc_project: "sure-test",
			vc_partition: "gpu-test",
		});
		const localResult = await dispatchExecutor(registry, disguised);
		expect(localResult.outcome.reason_code).toBe("INVALID_CONTRACT");
	});

	it("rejects unknown external surface values instead of treating them as local", () => {
		const result = parseExecutionAdapterRoute({ execution_surface: "hpc" });
		expect(result).toEqual({
			valid: false,
			errors: ["runtime_requirements.execution_surface must be vc, remote, or trusted"],
		});
	});

	it("rejects a receipt that impersonates another executor", async () => {
		const registry = new ExecutorRegistry();
		const port = portFor("remote", "host_enforced", {
			execute: (input) => {
				const receipt = portFor("remote", "host_enforced").execute(input) as ExecutionReceipt;
				return { ...receipt, executor: { ...receipt.executor, executor_id: "forged.remote" } };
			},
		});
		registry.register(port);

		const result = await dispatchExecutor(registry, request("remote"));
		expect(result.receipt).toBeDefined();
		expect(result.receipt_validation?.valid).toBe(false);
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		expect(result.receipt_validation?.errors.join(" ")).toContain("registered executor");
	});

	it("does not let an executor exception become PASS", async () => {
		const registry = new ExecutorRegistry();
		const port = portFor("remote", "host_enforced", {
			execute: () => {
				throw new Error("queue rejected");
			},
		});
		registry.register(port);

		const result = await dispatchExecutor(registry, request("remote"));
		expect(result.receipt).toBeUndefined();
		expect(result.outcome).toMatchObject({
			validator_verdict: "FAIL",
			workflow_disposition: "RETRY",
			outcome: "RETRY",
			reason_code: "EXECUTION_FAILED",
		});
	});

	it("keeps the trusted registry floor enforced", () => {
		const registry = new ExecutorRegistry();
		expect(() => registry.register(portFor("trusted", "cooperative"))).toThrow(/does not meet attested trust/);
	});
});
