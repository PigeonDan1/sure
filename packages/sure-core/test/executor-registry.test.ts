import { describe, expect, it, vi } from "vitest";
import {
	ExecutorRegistry,
	executorDescriptor,
	executorDescriptors,
	executorRegistrySnapshot,
	validateExecutionReceipt,
} from "../src/index.ts";
import {
	adapterBinding,
	externalPort,
	FIXTURE_DIGEST_B,
	vcAdapterFixture,
	vcManifest,
	vcPolicySnapshot,
} from "./external-adapter-fixture.ts";

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

	it("requires external executors to register an immutable manifest binding", () => {
		const fixture = vcAdapterFixture();
		const registry = new ExecutorRegistry();
		expect(() => registry.register(fixture.port)).toThrow(/registerExternal/);
		registry.registerExternal(fixture.port, fixture.binding);
		expect(registry.resolve("remote")).toBeUndefined();
		expect(registry.resolveExternal("remote", fixture.manifest.manifest_digest)?.port).toBe(fixture.port);
		expect(registry.resolveExternal("remote", fixture.manifest.manifest_digest)?.identity).toEqual(
			fixture.port.identity,
		);
		expect(registry.resolveExternal("trusted", fixture.manifest.manifest_digest)).toBeUndefined();
		expect(registry.resolveExternal("remote", FIXTURE_DIGEST_B)).toBeUndefined();
	});

	it("routes multiple external manifests by digest without weakening trust floors", async () => {
		const registry = new ExecutorRegistry();
		const remote = vcAdapterFixture({}, "remote");
		const trusted = vcAdapterFixture({}, "trusted");
		registry.registerExternal(remote.port, remote.binding);
		registry.registerExternal(trusted.port, trusted.binding);
		expect(registry.registeredIdentities().map((identity) => identity.kind)).toEqual(["remote", "trusted"]);

		for (const fixture of [remote, trusted]) {
			const registration = registry.resolveExternal(fixture.port.identity.kind, fixture.manifest.manifest_digest);
			const receipt = await registration?.port.execute(fixture.request);
			if (!receipt) throw new Error(`missing ${fixture.port.identity.kind} receipt`);
			expect(validateExecutionReceipt(fixture.request, receipt).valid).toBe(true);
		}

		const policySnapshot = vcPolicySnapshot();
		const insufficientPort = externalPort("trusted", {}, "cooperative");
		const insufficientManifest = vcManifest(insufficientPort, policySnapshot);
		expect(() =>
			new ExecutorRegistry().registerExternal(
				insufficientPort,
				adapterBinding(insufficientManifest, policySnapshot),
			),
		).toThrow(/does not meet attested trust/);
	});

	it("rejects registration drift without probing the adapter", () => {
		const probe = vi.fn(vcAdapterFixture().port.probe);
		const fixture = vcAdapterFixture({ probe });
		const mutations: Array<[string, (binding: typeof fixture.binding) => void, RegExp]> = [
			[
				"runtime identity",
				(binding) => {
					(binding as { runtime_identity_digest: string }).runtime_identity_digest = FIXTURE_DIGEST_B;
				},
				/runtime_identity_digest/,
			],
			[
				"container identity",
				(binding) => {
					(binding as { container_image_digest?: string }).container_image_digest = FIXTURE_DIGEST_B;
				},
				/container image digest/,
			],
			[
				"output scope",
				(binding) => {
					(binding.output_scope as { logs_root: string }).logs_root = "artifacts/other_logs";
				},
				/logs_root/,
			],
			[
				"policy snapshot",
				(binding) => {
					(binding.policy_snapshot as { site_id: string }).site_id = "forged-site";
				},
				/policy snapshot is invalid/,
			],
			[
				"manifest contents",
				(binding) => {
					(binding.manifest as { manifest_version: string }).manifest_version = "2.0.0";
				},
				/manifest_digest does not match/,
			],
		];
		for (const [_name, mutate, expected] of mutations) {
			const binding = structuredClone(fixture.binding);
			mutate(binding);
			expect(() => new ExecutorRegistry().registerExternal(fixture.port, binding)).toThrow(expected);
		}
		expect(probe).not.toHaveBeenCalled();
	});

	it("copies registration bindings so caller mutation cannot retarget a port", () => {
		const fixture = vcAdapterFixture();
		const binding = structuredClone(fixture.binding);
		const registry = new ExecutorRegistry();
		registry.registerExternal(fixture.port, binding);
		(binding as { runtime_identity_digest: string }).runtime_identity_digest = FIXTURE_DIGEST_B;
		const stored = registry.resolveExternal("remote", fixture.manifest.manifest_digest);
		expect(stored?.binding.runtime_identity_digest).toBe(fixture.binding.runtime_identity_digest);
		if (stored !== undefined) {
			(stored.binding as { runtime_identity_digest: string }).runtime_identity_digest = FIXTURE_DIGEST_B;
		}
		expect(
			registry.resolveExternal("remote", fixture.manifest.manifest_digest)?.binding.runtime_identity_digest,
		).toBe(fixture.binding.runtime_identity_digest);
	});

	it("pins external identities and does not expose the stored identity by reference", () => {
		const fixture = vcAdapterFixture();
		const registeredIdentity = structuredClone(fixture.port.identity);
		const registry = new ExecutorRegistry();
		registry.registerExternal(fixture.port, fixture.binding);
		(fixture.port.identity as { executor_id: string }).executor_id = "retargeted.remote";

		const resolved = registry.resolveExternal("remote", fixture.manifest.manifest_digest);
		expect(resolved?.identity).toEqual(registeredIdentity);
		if (resolved !== undefined) {
			(resolved.identity as { executor_id: string }).executor_id = "caller.mutation";
		}
		expect(registry.resolveExternal("remote", fixture.manifest.manifest_digest)?.identity).toEqual(
			registeredIdentity,
		);
		expect(registry.registeredIdentities()).toEqual([registeredIdentity]);
	});

	it("has a stable registry digest independent of host probes and registrations", () => {
		const snapshot = executorRegistrySnapshot();
		const fixture = vcAdapterFixture();
		const registry = new ExecutorRegistry();
		registry.registerExternal(fixture.port, fixture.binding);
		expect(snapshot.schema).toBe("sure.executor.registry.v1");
		expect(snapshot.registry_digest).toMatch(/^sha256:[0-9a-f]{64}$/);
		expect(executorRegistrySnapshot()).toEqual(snapshot);
	});
});
