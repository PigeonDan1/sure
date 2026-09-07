import type { JsonValue } from "../src/contracts/types.ts";
import {
	canonicalJsonDigest,
	createExternalAdapterManifest,
	type ExecutionReceipt,
	type ExecutionRequest,
	type ExecutorPort,
	type ExecutorTrustLevel,
	type ExternalAdapterHostBinding,
	type ExternalAdapterManifest,
} from "../src/index.ts";
import { snapshotResolvedSitePolicy, validateSitePolicy } from "../src/policy/site.ts";

export const FIXTURE_NOW = "2026-09-07T00:00:00.000Z";
export const FIXTURE_DIGEST_A = `sha256:${"a".repeat(64)}`;
export const FIXTURE_DIGEST_B = `sha256:${"b".repeat(64)}`;
export const FIXTURE_RUNTIME_DIGEST = `sha256:${"c".repeat(64)}`;
export const FIXTURE_CONTAINER_DIGEST = `sha256:${"d".repeat(64)}`;

export function vcPolicySnapshot(options: { vcEnabled?: boolean; partitions?: string[] } = {}) {
	const vcEnabled = options.vcEnabled ?? true;
	const policy = validateSitePolicy({
		schema: "sure.site.policy.v1",
		site_id: "dispatch-test",
		policy_version: 1,
		storage: {
			approved_models_roots: ["/reference/models"],
			approved_results_roots: ["/tmp/sure-dispatch/results"],
			forbidden_output_roots: ["/reference"],
			runtime_root: "/tmp/sure-dispatch/runtime",
		},
		datasets: { allowed_source_roots: { default: "/reference/datasets" } },
		execution: {
			surfaces: vcEnabled ? ["local", "vc"] : ["local"],
			local_runtimes: ["container"],
			...(vcEnabled ? { vc_project: "sure-test", vc_partitions: options.partitions ?? ["gpu-test"] } : {}),
		},
	});
	return snapshotResolvedSitePolicy(
		{
			policy,
			path: "/config/site.test.yaml",
			source: "bundled",
			sha256: FIXTURE_DIGEST_B,
		},
		{ resolvePath: (path) => path },
	);
}

export function externalPort(
	kind: "remote" | "trusted" = "remote",
	overrides: Partial<Pick<ExecutorPort, "probe" | "execute">> = {},
	trustLevel: ExecutorTrustLevel = kind === "trusted" ? "attested" : "host_enforced",
): ExecutorPort {
	const identity = {
		executor_id: `sure.external.vc.mock.${kind}`,
		kind,
		version: "1.0.0",
		digest: FIXTURE_DIGEST_A,
		trust_level: trustLevel,
	} as const;
	const probe: ExecutorPort["probe"] =
		overrides.probe ??
		((requirements) =>
			requirements.map((requirement) => ({
				capability_id: requirement.capability_id,
				capability_class: requirement.capability_class,
				status: "AVAILABLE",
				source: kind === "trusted" ? "trusted_attestation" : "executor",
				observed_at: FIXTURE_NOW,
				evidence_digest: FIXTURE_DIGEST_B,
			})));
	const execute: ExecutorPort["execute"] =
		overrides.execute ??
		((request) =>
			({
				schema: "sure.execution_receipt.v1",
				receipt_id: `receipt-${kind}`,
				request_id: request.request_id,
				request_digest: canonicalJsonDigest(request as unknown as JsonValue),
				semantic_request_digest: request.semantic_request_digest,
				run_id: request.run_id,
				unit_id: request.unit_id,
				attempt: request.attempt,
				executor: { ...identity },
				lifecycle: "SUCCEEDED",
				capability_evidence: request.capability_requirements.map((requirement) => ({
					capability_id: requirement.capability_id,
					capability_class: requirement.capability_class,
					status: "AVAILABLE",
					source: kind === "trusted" ? "trusted_attestation" : "executor",
					observed_at: FIXTURE_NOW,
					evidence_digest: FIXTURE_DIGEST_B,
				})),
				outputs: [],
				reference_snapshot_digest: request.reference_snapshot_digest,
				output_root: request.output_root,
				policy_digest: request.policy_digest,
				...(request.policy_snapshot_digest === undefined
					? {}
					: { policy_snapshot_digest: request.policy_snapshot_digest }),
				...(request.adapter_manifest_digest === undefined
					? {}
					: { adapter_manifest_digest: request.adapter_manifest_digest }),
				started_at: FIXTURE_NOW,
				finished_at: FIXTURE_NOW,
				exit_code: 0,
			}) satisfies ExecutionReceipt);
	return { identity, probe, execute };
}

export function vcManifest(port: ExecutorPort, policySnapshot = vcPolicySnapshot()): ExternalAdapterManifest {
	return createExternalAdapterManifest({
		manifest_id: `sure-vc-mock-${port.identity.kind}`,
		manifest_version: "1.0.0",
		surface: "vc",
		executor: port.identity,
		policy_snapshot_digest: policySnapshot.snapshot_digest,
		authorization: { allowed_projects: ["sure-test"], allowed_partitions: ["gpu-test"] },
		resource_limits: { max_gpus: 4, max_memory_gb: 128, max_cpus: 32 },
		timeouts: {
			submit_seconds: 300,
			wait_seconds: 1800,
			command_seconds: 1200,
			cancel_seconds: 120,
			poll_seconds: 15,
		},
		cancellation: {
			supported: true,
			strategy: "job_delete",
			confirmation: "best_effort",
			timeout_outcome: "BLOCKED",
		},
		output_scope: {
			output_root: "artifacts",
			logs_root: "artifacts/vc_logs",
			write_policy: "declared_outputs_only",
			logs_retained: true,
		},
		runtime: {
			runtime_identity_digest: FIXTURE_RUNTIME_DIGEST,
			container: {
				image: `registry.example/sure/trans:1.0.0@${FIXTURE_CONTAINER_DIGEST}`,
				image_digest: FIXTURE_CONTAINER_DIGEST,
			},
		},
		attestation: { mode: port.identity.kind === "trusted" ? "trusted_attestation" : "receipt_digest" },
	});
}

export function adapterBinding(
	manifest: ExternalAdapterManifest,
	policySnapshot = vcPolicySnapshot(),
): ExternalAdapterHostBinding {
	return {
		manifest,
		policy_snapshot: policySnapshot,
		runtime_identity_digest: FIXTURE_RUNTIME_DIGEST,
		container_image_digest: FIXTURE_CONTAINER_DIGEST,
		output_scope: { output_root: "artifacts", logs_root: "artifacts/vc_logs" },
	};
}

export function externalRequest(
	manifest: ExternalAdapterManifest,
	policySnapshot = vcPolicySnapshot(),
): ExecutionRequest {
	const formal = manifest.executor.kind === "trusted";
	return {
		schema: "sure.execution_request.v1",
		request_id: `dispatch-${manifest.executor.kind}`,
		semantic_request_digest: FIXTURE_DIGEST_A,
		run_id: "dispatch-run",
		unit_id: "execute",
		attempt: 1,
		operation: formal ? "formal_evaluation" : "inference",
		subject: {
			bundle_manifest_path: "/tmp/sure-dispatch/bundle.json",
			bundle_digest: FIXTURE_DIGEST_A,
			runtime_identity_digest: FIXTURE_DIGEST_B,
			...(formal
				? {
						inference_protocol_digest: FIXTURE_DIGEST_A,
						dataset_identity_digest: FIXTURE_DIGEST_B,
						scoring_protocol_digest: FIXTURE_DIGEST_A,
					}
				: {}),
		},
		inputs: [],
		entrypoint: { executable: "adapter-entrypoint", argv: [] },
		runtime_requirements: {
			execution_surface: "vc",
			executor_kind: manifest.executor.kind,
			vc_project: "sure-test",
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
		capability_requirements: [
			{
				capability_id: `sure.execution.${manifest.executor.kind}`,
				capability_class: "execution_capability",
				required: true,
			},
			{ capability_id: "sure.execution.vc", capability_class: "execution_capability", required: true },
		],
		reference_snapshot_digest: FIXTURE_DIGEST_B,
		output_root: {
			path: "/tmp/sure-dispatch/results/dispatch-run",
			resolved_path: "/tmp/sure-dispatch/results/dispatch-run",
			scope_id: "dispatch-run",
			policy_digest: policySnapshot.policy_digest,
			writable: true,
		},
		policy_digest: policySnapshot.policy_digest,
		policy_snapshot_digest: policySnapshot.snapshot_digest,
		adapter_manifest_digest: manifest.manifest_digest,
		created_at: FIXTURE_NOW,
	};
}

export interface ExternalAdapterFixture {
	port: ExecutorPort;
	policySnapshot: ReturnType<typeof vcPolicySnapshot>;
	manifest: ExternalAdapterManifest;
	binding: ExternalAdapterHostBinding;
	request: ExecutionRequest;
}

export function vcAdapterFixture(
	overrides: Partial<Pick<ExecutorPort, "probe" | "execute">> = {},
	kind: "remote" | "trusted" = "remote",
): ExternalAdapterFixture {
	const policySnapshot = vcPolicySnapshot();
	const port = externalPort(kind, overrides);
	const manifest = vcManifest(port, policySnapshot);
	return {
		port,
		policySnapshot,
		manifest,
		binding: adapterBinding(manifest, policySnapshot),
		request: externalRequest(manifest, policySnapshot),
	};
}
