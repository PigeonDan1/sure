import { evaluatePathBoundary, type ResolvedRoot } from "../contracts/path-boundary.ts";
import type { ExecutionRequest, ExecutorIdentity } from "../contracts/types.ts";
import { type SitePolicy, validateSitePolicy } from "../policy/site.ts";
import { validatePolicySnapshot } from "../policy/snapshot.ts";
import type { PolicySnapshot } from "../policy/types.ts";
import { type ExecutionAdapterSurface, parseExecutionAdapterRoute, parseExecutionAdapterTimeouts } from "./adapter.ts";
import {
	admitExternalAdapterManifest,
	type ExternalAdapterAdmission,
	type ExternalAdapterAdmissionContext,
	type ExternalAdapterAuthorization,
	type ExternalAdapterManifest,
	type ExternalAdapterOutputBinding,
	validateExternalAdapterManifest,
} from "./adapter-manifest.ts";

const DIGEST = /^(?:sha256:)?[0-9a-f]{64}$/i;

function object(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function sameDigest(left: string, right: string): boolean {
	return left.replace(/^sha256:/i, "").toLowerCase() === right.replace(/^sha256:/i, "").toLowerCase();
}

function validDigest(value: unknown): value is string {
	return typeof value === "string" && DIGEST.test(value);
}

/** Host-observed values that an adapter manifest is not allowed to self-assert. */
export interface ExternalAdapterHostBinding {
	readonly manifest: ExternalAdapterManifest;
	readonly policy_snapshot: PolicySnapshot;
	readonly runtime_identity_digest: string;
	readonly container_image_digest?: string;
	readonly output_scope: ExternalAdapterOutputBinding;
}

export interface ExternalAdapterPolicyProjection {
	readonly policy_snapshot: PolicySnapshot;
	readonly site_policy: SitePolicy;
	readonly policy_snapshot_digest: string;
	readonly policy_surfaces: readonly ExecutionAdapterSurface[];
	readonly policy_authorization: ExternalAdapterAuthorization;
	readonly allowed_output_roots: readonly ResolvedRoot[];
	readonly forbidden_output_roots: readonly ResolvedRoot[];
}

export interface ExternalAdapterPolicyProjectionResult {
	readonly valid: boolean;
	readonly errors: readonly string[];
	readonly projection?: ExternalAdapterPolicyProjection;
}

export interface ExternalAdapterRegistrationValidation extends ExternalAdapterAdmission {
	readonly policy_snapshot?: PolicySnapshot;
}

export interface ExternalAdapterRequestAdmission extends ExternalAdapterAdmission {
	readonly context?: ExternalAdapterAdmissionContext;
	readonly policy_snapshot?: PolicySnapshot;
	readonly policy_projection?: ExternalAdapterPolicyProjection;
}

interface VerifiedSitePolicySnapshot {
	policy_snapshot?: PolicySnapshot;
	site_policy?: SitePolicy;
	errors: string[];
}

function projectPathBinding(
	policySnapshot: PolicySnapshot,
	path: string,
	role: "controlled_publication" | "runtime_cache" | "forbidden_output",
	errors: string[],
): ResolvedRoot | undefined {
	const matches = policySnapshot.path_bindings.filter((binding) => binding.path === path && binding.role === role);
	if (matches.length !== 1) {
		errors.push(`adapter policy snapshot requires exactly one ${role} binding for ${path}`);
		return undefined;
	}
	return { path: matches[0].path, resolved_path: matches[0].resolved_path };
}

function verifySitePolicySnapshot(value: unknown): VerifiedSitePolicySnapshot {
	let policySnapshot: PolicySnapshot;
	try {
		policySnapshot = validatePolicySnapshot(value);
	} catch (error) {
		return {
			errors: [`adapter policy snapshot is invalid: ${error instanceof Error ? error.message : String(error)}`],
		};
	}
	let sitePolicy: SitePolicy;
	try {
		sitePolicy = validateSitePolicy(policySnapshot.policy);
	} catch (error) {
		return {
			policy_snapshot: policySnapshot,
			errors: [`adapter site policy is invalid: ${error instanceof Error ? error.message : String(error)}`],
		};
	}
	const errors: string[] = [];
	if (sitePolicy.site_id !== policySnapshot.site_id) {
		errors.push("adapter site policy site_id does not match policy snapshot");
	}
	if (sitePolicy.policy_version !== policySnapshot.policy_version) {
		errors.push("adapter site policy policy_version does not match policy snapshot");
	}
	return { policy_snapshot: policySnapshot, site_policy: sitePolicy, errors };
}

/**
 * Derive the only authoritative external-adapter authorization understood by
 * site-policy v1. Remote and trusted surfaces remain fail-closed until the
 * site-policy contract can describe their authorization roots explicitly.
 */
export function projectExternalAdapterPolicy(
	value: unknown,
	surface: ExecutionAdapterSurface,
): ExternalAdapterPolicyProjectionResult {
	const verified = verifySitePolicySnapshot(value);
	if (verified.errors.length > 0 || verified.policy_snapshot === undefined || verified.site_policy === undefined) {
		return { valid: false, errors: verified.errors };
	}
	const { policy_snapshot: policySnapshot, site_policy: sitePolicy } = verified;
	if (surface !== "vc") {
		return {
			valid: false,
			errors: [`site policy v1 cannot authorize execution surface ${surface}`],
		};
	}
	const errors: string[] = [];
	if (!sitePolicy.execution.surfaces.includes("vc")) {
		errors.push("site policy does not enable execution surface vc");
	}
	if (typeof sitePolicy.execution.vc_project !== "string" || sitePolicy.execution.vc_project.length === 0) {
		errors.push("site policy vc execution requires execution.vc_project");
	}
	if (!Array.isArray(sitePolicy.execution.vc_partitions) || sitePolicy.execution.vc_partitions.length === 0) {
		errors.push("site policy vc execution requires non-empty execution.vc_partitions");
	}
	const allowedOutputRoots = [
		...sitePolicy.storage.approved_results_roots.map((path) =>
			projectPathBinding(policySnapshot, path, "controlled_publication", errors),
		),
		projectPathBinding(policySnapshot, sitePolicy.storage.runtime_root, "runtime_cache", errors),
	].filter((root): root is ResolvedRoot => root !== undefined);
	const forbiddenOutputRoots = sitePolicy.storage.forbidden_output_roots
		.map((path) => projectPathBinding(policySnapshot, path, "forbidden_output", errors))
		.filter((root): root is ResolvedRoot => root !== undefined);
	if (errors.length > 0) return { valid: false, errors };
	const projection: ExternalAdapterPolicyProjection = {
		policy_snapshot: policySnapshot,
		site_policy: sitePolicy,
		policy_snapshot_digest: policySnapshot.snapshot_digest,
		policy_surfaces: ["vc"],
		policy_authorization: {
			allowed_projects: [sitePolicy.execution.vc_project as string],
			allowed_partitions: [...(sitePolicy.execution.vc_partitions as string[])].sort(),
		},
		allowed_output_roots: allowedOutputRoots,
		forbidden_output_roots: forbiddenOutputRoots,
	};
	return { valid: true, errors: [], projection };
}

/** Validate immutable registration data without interpreting it as policy authorization. */
export function validateExternalAdapterRegistration(
	executor: ExecutorIdentity,
	binding: ExternalAdapterHostBinding,
): ExternalAdapterRegistrationValidation {
	if (!object(binding)) return { valid: false, errors: ["external adapter host binding must be an object"] };
	const verified = verifySitePolicySnapshot(binding.policy_snapshot);
	if (verified.errors.length > 0 || verified.policy_snapshot === undefined) {
		return { valid: false, errors: verified.errors, policy_snapshot: verified.policy_snapshot };
	}
	const policySnapshot = verified.policy_snapshot;
	const manifest = binding.manifest;
	if (!object(manifest)) {
		return { valid: false, errors: ["adapter manifest must be an object"], policy_snapshot: policySnapshot };
	}
	const manifestValidation = validateExternalAdapterManifest(manifest);
	if (!manifestValidation.valid) {
		return { ...manifestValidation, policy_snapshot: policySnapshot };
	}
	if (
		!validDigest(manifest.policy_snapshot_digest) ||
		!sameDigest(manifest.policy_snapshot_digest, policySnapshot.snapshot_digest)
	) {
		return {
			valid: false,
			errors: ["adapter manifest policy_snapshot_digest does not match verified policy snapshot"],
			policy_snapshot: policySnapshot,
		};
	}
	if (manifest.runtime?.container === undefined && binding.container_image_digest !== undefined) {
		return {
			valid: false,
			errors: ["adapter host container_image_digest is not allowed when the manifest has no container"],
			policy_snapshot: policySnapshot,
		};
	}
	const staticContext: ExternalAdapterAdmissionContext = {
		policy_snapshot_digest: policySnapshot.snapshot_digest,
		policy_authorization: manifest.authorization,
		policy_surfaces: [manifest.surface],
		surface: manifest.surface,
		executor,
		runtime_identity_digest: binding.runtime_identity_digest,
		...(binding.container_image_digest === undefined
			? {}
			: { container_image_digest: binding.container_image_digest }),
		output_scope: binding.output_scope,
		...(manifest.surface !== "vc"
			? {}
			: {
					project: manifest.authorization.allowed_projects[0],
					partition: manifest.authorization.allowed_partitions?.[0],
				}),
	};
	const admission = admitExternalAdapterManifest(manifest, staticContext);
	return {
		...admission,
		policy_snapshot: policySnapshot,
	};
}

/**
 * Bind a validated request to a registered manifest and the policy projection.
 * Callers must complete this admission before capability probing or execution.
 */
export function admitExternalAdapterRequest(
	executor: ExecutorIdentity,
	binding: ExternalAdapterHostBinding,
	request: ExecutionRequest,
): ExternalAdapterRequestAdmission {
	const registration = validateExternalAdapterRegistration(executor, binding);
	if (!registration.valid || registration.manifest === undefined || registration.policy_snapshot === undefined) {
		return registration;
	}
	const manifest = registration.manifest;
	const policySnapshot = registration.policy_snapshot;
	const routeValidation = parseExecutionAdapterRoute(request.runtime_requirements);
	if (!routeValidation.valid) {
		return { ...registration, valid: false, errors: routeValidation.errors };
	}
	if (routeValidation.route === undefined) {
		return {
			...registration,
			valid: false,
			errors: ["external adapter admission requires an execution surface"],
		};
	}
	const route = routeValidation.route;
	const errors: string[] = [];
	if (route.surface !== manifest.surface) {
		errors.push("request execution surface does not match adapter manifest");
	}
	if (!validDigest(request.adapter_manifest_digest)) {
		errors.push("external adapter admission requires request.adapter_manifest_digest");
	} else if (!sameDigest(request.adapter_manifest_digest, manifest.manifest_digest)) {
		errors.push("request.adapter_manifest_digest does not match registered adapter manifest");
	}
	if (!validDigest(request.policy_snapshot_digest)) {
		errors.push("external adapter admission requires request.policy_snapshot_digest");
	} else if (!sameDigest(request.policy_snapshot_digest, policySnapshot.snapshot_digest)) {
		errors.push("request.policy_snapshot_digest does not match registered policy snapshot");
	}
	if (!validDigest(request.policy_digest) || !sameDigest(request.policy_digest, policySnapshot.policy_digest)) {
		errors.push("request.policy_digest does not match registered policy snapshot");
	}
	if (
		!object(request.output_root) ||
		!validDigest(request.output_root.policy_digest) ||
		!sameDigest(request.output_root.policy_digest, policySnapshot.policy_digest)
	) {
		errors.push("request.output_root.policy_digest does not match registered policy snapshot");
	}
	if (errors.length > 0) return { ...registration, valid: false, errors };

	const projectionResult = projectExternalAdapterPolicy(policySnapshot, route.surface);
	if (!projectionResult.valid || projectionResult.projection === undefined) {
		return { ...registration, valid: false, errors: projectionResult.errors };
	}
	const timeoutResult = parseExecutionAdapterTimeouts(request.runtime_requirements);
	if (!timeoutResult.valid) {
		return { ...registration, valid: false, errors: timeoutResult.errors };
	}
	const resources =
		route.surface === "vc"
			? {
					...(typeof request.runtime_requirements.vc_gpus === "number"
						? { gpus: request.runtime_requirements.vc_gpus }
						: {}),
					...(typeof request.runtime_requirements.vc_memory_gb === "number"
						? { memory_gb: request.runtime_requirements.vc_memory_gb }
						: {}),
					...(typeof request.runtime_requirements.vc_cpus === "number"
						? { cpus: request.runtime_requirements.vc_cpus }
						: {}),
				}
			: undefined;
	const projection = projectionResult.projection;
	const outputBoundary = evaluatePathBoundary({
		candidate_path: request.output_root.path,
		candidate_resolved_path: request.output_root.resolved_path,
		allowed_roots: projection.allowed_output_roots,
		forbidden_roots: projection.forbidden_output_roots,
	});
	if (!outputBoundary.admitted) {
		return {
			...registration,
			valid: false,
			errors: [
				outputBoundary.blocking_outcome?.diagnostics[0]?.message ?? "request output root is outside site policy",
			],
		};
	}
	const context: ExternalAdapterAdmissionContext = {
		policy_snapshot_digest: projection.policy_snapshot_digest,
		policy_authorization: projection.policy_authorization,
		policy_surfaces: projection.policy_surfaces,
		surface: route.surface,
		executor,
		runtime_identity_digest: binding.runtime_identity_digest,
		...(binding.container_image_digest === undefined
			? {}
			: { container_image_digest: binding.container_image_digest }),
		output_scope: binding.output_scope,
		...(route.surface !== "vc"
			? {}
			: {
					project: request.runtime_requirements.vc_project as string,
					partition: request.runtime_requirements.vc_partition as string,
				}),
		...(resources === undefined ? {} : { resources }),
		...(timeoutResult.timeouts === undefined ? {} : { timeouts: timeoutResult.timeouts }),
	};
	const admission = admitExternalAdapterManifest(manifest, context);
	return {
		...admission,
		context,
		policy_snapshot: policySnapshot,
		policy_projection: projection,
	};
}
