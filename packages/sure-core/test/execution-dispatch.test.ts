import { describe, expect, it, vi } from "vitest";
import {
	createExternalAdapterManifest,
	dispatchExecutor,
	type ExecutionReceipt,
	ExecutorRegistry,
	parseExecutionAdapterRoute,
} from "../src/index.ts";
import {
	adapterBinding,
	externalPort,
	externalRequest,
	FIXTURE_DIGEST_B,
	FIXTURE_NOW,
	vcAdapterFixture,
	vcManifest,
	vcPolicySnapshot,
} from "./external-adapter-fixture.ts";

function registerFixture(fixture: ReturnType<typeof vcAdapterFixture>): ExecutorRegistry {
	const registry = new ExecutorRegistry();
	registry.registerExternal(fixture.port, fixture.binding);
	return registry;
}

describe("host-neutral executor dispatch", () => {
	it("keeps an unregistered external manifest unavailable without local fallback", async () => {
		const fixture = vcAdapterFixture();
		const result = await dispatchExecutor(new ExecutorRegistry(), fixture.request, { now: () => FIXTURE_NOW });

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
		expect(result.admission_trace).toMatchObject({
			status: "CAPABILITY_MISSING",
			reason_code: "CAPABILITY_MISSING",
			probe_invoked: false,
			execute_invoked: false,
			receipt_present: false,
			receipt_valid: false,
		});
	});

	it("admits the registered manifest before probing and keeps success validation-pending", async () => {
		const defaults = vcAdapterFixture();
		const probe = vi.fn(defaults.port.probe);
		const execute = vi.fn(defaults.port.execute);
		const fixture = vcAdapterFixture({ probe, execute });
		const result = await dispatchExecutor(registerFixture(fixture), fixture.request);

		expect(probe).toHaveBeenCalledOnce();
		expect(execute).toHaveBeenCalledOnce();
		expect(result.adapter_admission).toMatchObject({
			valid: true,
			digest: fixture.manifest.manifest_digest,
		});
		expect(result.receipt_validation?.valid).toBe(true);
		expect(result.receipt?.adapter_manifest_digest).toBe(fixture.manifest.manifest_digest);
		expect(result.receipt?.executor).toEqual(fixture.port.identity);
		expect(result.outcome).toMatchObject({
			outcome: "NOT_EXECUTED",
			workflow_disposition: "WAIT",
			reason_code: "VALIDATION_PENDING",
		});
		expect(result.admission_trace).toMatchObject({
			status: "ADMITTED",
			probe_invoked: true,
			execute_invoked: true,
			receipt_present: true,
			receipt_valid: true,
		});
	});

	it("keeps capability absence distinct after successful manifest admission", async () => {
		const defaults = vcAdapterFixture();
		const execute = vi.fn(defaults.port.execute);
		const probe = vi.fn((requirements: Parameters<typeof defaults.port.probe>[0]) =>
			requirements.map((requirement) => ({
				capability_id: requirement.capability_id,
				capability_class: requirement.capability_class,
				status: "MISSING" as const,
				source: "executor" as const,
				observed_at: FIXTURE_NOW,
			})),
		);
		const fixture = vcAdapterFixture({ probe, execute });
		const result = await dispatchExecutor(registerFixture(fixture), fixture.request);

		expect(result.adapter_admission?.valid).toBe(true);
		expect(probe).toHaveBeenCalledOnce();
		expect(execute).not.toHaveBeenCalled();
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "CAPABILITY_MISSING" });
	});

	it.each([
		{
			name: "policy digest",
			mutate: (request: ReturnType<typeof vcAdapterFixture>["request"]) => {
				request.policy_digest = FIXTURE_DIGEST_B;
			},
			expected: "request.policy_digest does not match registered policy snapshot",
		},
		{
			name: "output policy digest",
			mutate: (request: ReturnType<typeof vcAdapterFixture>["request"]) => {
				request.output_root.policy_digest = FIXTURE_DIGEST_B;
			},
			expected: "request.output_root.policy_digest does not match registered policy snapshot",
		},
		{
			name: "output path authorization",
			mutate: (request: ReturnType<typeof vcAdapterFixture>["request"]) => {
				request.output_root.path = "/outside/results";
				request.output_root.resolved_path = "/outside/results";
			},
			expected: "Path /outside/results is outside every allowed root",
		},
		{
			name: "policy snapshot digest",
			mutate: (request: ReturnType<typeof vcAdapterFixture>["request"]) => {
				request.policy_snapshot_digest = FIXTURE_DIGEST_B;
			},
			expected: "request.policy_snapshot_digest does not match registered policy snapshot",
		},
		{
			name: "project authorization",
			mutate: (request: ReturnType<typeof vcAdapterFixture>["request"]) => {
				request.runtime_requirements.vc_project = "other-project";
			},
			expected: "project is not allowed by adapter manifest",
		},
		{
			name: "resource limit",
			mutate: (request: ReturnType<typeof vcAdapterFixture>["request"]) => {
				request.runtime_requirements.vc_gpus = 5;
			},
			expected: "admission resources.gpus exceeds adapter manifest limit",
		},
		{
			name: "timeout limit",
			mutate: (request: ReturnType<typeof vcAdapterFixture>["request"]) => {
				request.runtime_requirements.adapter_timeouts = {
					submit_seconds: 300,
					wait_seconds: 1800,
					command_seconds: 1201,
					cancel_seconds: 120,
					poll_seconds: 15,
				};
			},
			expected: "admission timeouts.command_seconds exceeds adapter manifest limit",
		},
	])("rejects $name drift before probe", async ({ mutate, expected }) => {
		const defaults = vcAdapterFixture();
		const probe = vi.fn(defaults.port.probe);
		const execute = vi.fn(defaults.port.execute);
		const fixture = vcAdapterFixture({ probe, execute });
		const request = structuredClone(fixture.request);
		mutate(request);

		const result = await dispatchExecutor(registerFixture(fixture), request);
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		expect(result.adapter_admission?.errors).toContain(expected);
		expect(probe).not.toHaveBeenCalled();
		expect(execute).not.toHaveBeenCalled();
		expect(result.admission_trace).toMatchObject({
			status: "REJECTED",
			probe_invoked: false,
			execute_invoked: false,
			receipt_present: false,
			receipt_valid: false,
		});
	});

	it("rejects post-registration executor identity drift before probe", async () => {
		const defaults = vcAdapterFixture();
		const probe = vi.fn(defaults.port.probe);
		const execute = vi.fn(defaults.port.execute);
		const fixture = vcAdapterFixture({ probe, execute });
		const registry = registerFixture(fixture);
		(fixture.port.identity as { executor_id: string }).executor_id = "retargeted.remote";

		const result = await dispatchExecutor(registry, fixture.request);
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		expect(result.outcome.diagnostics[0]?.message).toContain(
			"registered port.identity.executor_id does not match its pinned executor identity",
		);
		expect(probe).not.toHaveBeenCalled();
		expect(execute).not.toHaveBeenCalled();
	});

	it("does not let an adapter manifest widen site-policy authorization", async () => {
		const policySnapshot = vcPolicySnapshot();
		const defaults = externalPort();
		const probe = vi.fn(defaults.probe);
		const execute = vi.fn(defaults.execute);
		const port = externalPort("remote", { probe, execute });
		const base = vcManifest(port, policySnapshot);
		const manifest = createExternalAdapterManifest({
			...base,
			authorization: {
				allowed_projects: ["other-project", "sure-test"],
				allowed_partitions: ["gpu-test"],
			},
		});
		const binding = adapterBinding(manifest, policySnapshot);
		const registry = new ExecutorRegistry();
		registry.registerExternal(port, binding);
		const result = await dispatchExecutor(registry, externalRequest(manifest, policySnapshot));

		expect(result.adapter_admission?.errors).toContain(
			"adapter manifest project allowlist exceeds policy authorization",
		);
		expect(result.outcome.reason_code).toBe("INVALID_CONTRACT");
		expect(probe).not.toHaveBeenCalled();
		expect(execute).not.toHaveBeenCalled();
		expect(result.admission_trace.status).toBe("REJECTED");
	});

	it("fails closed when site-policy v1 cannot authorize the requested external surface", async () => {
		const policySnapshot = vcPolicySnapshot();
		const defaults = externalPort();
		const probe = vi.fn(defaults.probe);
		const execute = vi.fn(defaults.execute);
		const port = externalPort("remote", { probe, execute });
		const vc = vcManifest(port, policySnapshot);
		const manifest = createExternalAdapterManifest({
			...vc,
			surface: "remote",
			authorization: { allowed_projects: ["sure-test"] },
			cancellation: {
				supported: false,
				strategy: "none",
				confirmation: "not_applicable",
				timeout_outcome: "BLOCKED",
			},
			runtime: { runtime_identity_digest: vc.runtime.runtime_identity_digest },
		});
		const binding = {
			...adapterBinding(manifest, policySnapshot),
			container_image_digest: undefined,
		};
		const request = externalRequest(manifest, policySnapshot);
		request.runtime_requirements = { execution_surface: "remote", executor_kind: "remote" };
		request.capability_requirements = [
			{ capability_id: "sure.execution.remote", capability_class: "execution_capability", required: true },
		];
		const registry = new ExecutorRegistry();
		registry.registerExternal(port, binding);
		const result = await dispatchExecutor(registry, request);

		expect(result.adapter_admission?.errors).toEqual(["site policy v1 cannot authorize execution surface remote"]);
		expect(result.outcome.reason_code).toBe("INVALID_CONTRACT");
		expect(probe).not.toHaveBeenCalled();
		expect(execute).not.toHaveBeenCalled();
	});

	it("rejects incomplete routes and overlarge timeout budgets before probing", async () => {
		const defaults = vcAdapterFixture();
		const probe = vi.fn(defaults.port.probe);
		const fixture = vcAdapterFixture({ probe });
		const incomplete = structuredClone(fixture.request);
		delete incomplete.runtime_requirements.vc_partition;
		expect(parseExecutionAdapterRoute(incomplete.runtime_requirements).errors).toContain(
			"runtime_requirements.vc_partition must be a non-empty string",
		);
		const rejected = await dispatchExecutor(registerFixture(fixture), incomplete);
		expect(rejected.request_validation.valid).toBe(false);
		expect(rejected.outcome.reason_code).toBe("INVALID_CONTRACT");

		const overlarge = structuredClone(fixture.request);
		overlarge.runtime_requirements.adapter_timeouts = { wait_seconds: 604_801 };
		const timeoutRejected = await dispatchExecutor(registerFixture(fixture), overlarge);
		expect(timeoutRejected.request_validation.errors).toContain(
			"runtime_requirements.adapter_timeouts.wait_seconds exceeds the maximum allowed value",
		);
		expect(
			parseExecutionAdapterRoute({
				execution_surface: "remote",
				executor_kind: "remote",
				adapter_timeouts: { wait_seconds: 10, command_seconds: 604_801 },
			}).errors,
		).toEqual(["runtime_requirements.adapter_timeouts.command_seconds exceeds the maximum allowed value"]);
		expect(probe).not.toHaveBeenCalled();
	});

	it("rejects forged capability evidence before execute", async () => {
		const defaults = vcAdapterFixture();
		const execute = vi.fn(defaults.port.execute);
		const fixture = vcAdapterFixture({
			execute,
			probe: (requirements) =>
				requirements.map((requirement) => ({
					capability_id: requirement.capability_id,
					capability_class: requirement.capability_class,
					status: "AVAILABLE" as const,
					source: "remote_daemon" as never,
					observed_at: FIXTURE_NOW,
					evidence_digest: FIXTURE_DIGEST_B,
				})),
		});
		const result = await dispatchExecutor(registerFixture(fixture), fixture.request);
		expect(result.adapter_admission?.valid).toBe(true);
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		expect(execute).not.toHaveBeenCalled();
	});

	it.each([
		{
			name: "adapter manifest",
			mutate: (receipt: ExecutionReceipt) => {
				receipt.adapter_manifest_digest = FIXTURE_DIGEST_B;
			},
			expected: "adapter_manifest_digest",
		},
		{
			name: "policy snapshot",
			mutate: (receipt: ExecutionReceipt) => {
				receipt.policy_snapshot_digest = FIXTURE_DIGEST_B;
			},
			expected: "policy_snapshot_digest",
		},
		{
			name: "executor identity",
			mutate: (receipt: ExecutionReceipt) => {
				receipt.executor = { ...receipt.executor, executor_id: "forged.remote" };
			},
			expected: "registered executor",
		},
	])("rejects receipt $name tampering", async ({ mutate, expected }) => {
		const defaults = externalPort();
		const fixture = vcAdapterFixture({
			execute: (request) => {
				const receipt = defaults.execute(request) as ExecutionReceipt;
				mutate(receipt);
				return receipt;
			},
		});
		const result = await dispatchExecutor(registerFixture(fixture), fixture.request);
		expect(result.adapter_admission?.valid).toBe(true);
		expect(result.receipt_validation?.valid).toBe(false);
		expect(result.receipt_validation?.errors.join(" ")).toContain(expected);
		expect(result.outcome.reason_code).toBe("INVALID_CONTRACT");
		expect(result.admission_trace).toMatchObject({
			status: "ADMITTED",
			probe_invoked: true,
			execute_invoked: true,
			receipt_present: true,
			receipt_valid: false,
		});
	});

	it("does not let an executor exception become PASS", async () => {
		const fixture = vcAdapterFixture({
			execute: () => {
				throw new Error("queue rejected");
			},
		});
		const result = await dispatchExecutor(registerFixture(fixture), fixture.request);
		expect(result.receipt).toBeUndefined();
		expect(result.outcome).toMatchObject({
			validator_verdict: "FAIL",
			workflow_disposition: "RETRY",
			outcome: "RETRY",
			reason_code: "EXECUTION_FAILED",
		});
		expect(result.admission_trace).toMatchObject({
			status: "ADMITTED",
			probe_invoked: true,
			execute_invoked: true,
			receipt_present: false,
			receipt_valid: false,
		});
	});
});
