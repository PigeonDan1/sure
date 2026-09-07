import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type { ExecutorIdentity, JsonValue } from "../contracts/types.ts";
import { EXECUTION_ADAPTER_SURFACES, type ExecutionAdapterSurface } from "./adapter.ts";

/** A self-bound description of an external execution adapter. */
export const EXTERNAL_ADAPTER_MANIFEST_SCHEMA = "sure.execution.adapter_manifest.v1" as const;

export const ADAPTER_ATTESTATION_MODES = ["none", "receipt_digest", "signed_receipt", "trusted_attestation"] as const;
export type AdapterAttestationMode = (typeof ADAPTER_ATTESTATION_MODES)[number];

export const ADAPTER_CANCELLATION_STRATEGIES = ["job_delete", "cooperative_signal", "none"] as const;
export type AdapterCancellationStrategy = (typeof ADAPTER_CANCELLATION_STRATEGIES)[number];

export const ADAPTER_CANCELLATION_CONFIRMATIONS = ["best_effort", "confirmed", "not_applicable"] as const;
export type AdapterCancellationConfirmation = (typeof ADAPTER_CANCELLATION_CONFIRMATIONS)[number];

export const ADAPTER_TIMEOUT_OUTCOMES = ["CANCELLED", "BLOCKED"] as const;
export type AdapterTimeoutOutcome = (typeof ADAPTER_TIMEOUT_OUTCOMES)[number];

export const ADAPTER_WRITE_POLICIES = ["declared_outputs_only", "output_root"] as const;
export type AdapterWritePolicy = (typeof ADAPTER_WRITE_POLICIES)[number];

export interface ExternalAdapterAuthorization {
	allowed_projects: readonly string[];
	/** Queue/partition authorization is required for VC and optional elsewhere. */
	allowed_partitions?: readonly string[];
}

export interface ExternalAdapterResourceLimits {
	max_gpus: number;
	max_memory_gb: number;
	max_cpus: number;
}

export interface ExternalAdapterRequestedResources {
	gpus?: number;
	memory_gb?: number;
	cpus?: number;
}

export interface ExternalAdapterTimeouts {
	submit_seconds: number;
	wait_seconds: number;
	command_seconds: number;
	cancel_seconds: number;
	poll_seconds: number;
}

export interface ExternalAdapterCancellation {
	supported: boolean;
	strategy: AdapterCancellationStrategy;
	confirmation: AdapterCancellationConfirmation;
	timeout_outcome: AdapterTimeoutOutcome;
}

export interface ExternalAdapterOutputScope {
	output_root: string;
	logs_root: string;
	write_policy: AdapterWritePolicy;
	logs_retained: boolean;
}

export interface ExternalAdapterOutputBinding {
	output_root: string;
	logs_root: string;
}

export interface ExternalAdapterContainer {
	image: string;
	image_digest: string;
}

export interface ExternalAdapterRuntime {
	runtime_identity_digest: string;
	container?: ExternalAdapterContainer;
}

export interface ExternalAdapterAttestation {
	mode: AdapterAttestationMode;
}

export interface ExternalAdapterManifest {
	schema: typeof EXTERNAL_ADAPTER_MANIFEST_SCHEMA;
	manifest_id: string;
	manifest_version: string;
	surface: ExecutionAdapterSurface;
	executor: ExecutorIdentity;
	policy_snapshot_digest: string;
	authorization: ExternalAdapterAuthorization;
	resource_limits: ExternalAdapterResourceLimits;
	timeouts: ExternalAdapterTimeouts;
	cancellation: ExternalAdapterCancellation;
	output_scope: ExternalAdapterOutputScope;
	runtime: ExternalAdapterRuntime;
	attestation: ExternalAdapterAttestation;
	manifest_digest: string;
}

export type ExternalAdapterManifestInput = Omit<ExternalAdapterManifest, "schema" | "manifest_digest"> & {
	manifest_digest?: string;
};

export interface ExternalAdapterManifestValidation {
	valid: boolean;
	errors: readonly string[];
	digest?: string;
}

export interface ExternalAdapterAdmissionContext {
	policy_snapshot_digest: string;
	/** Authorization extracted from the already-verified policy snapshot. */
	policy_authorization: ExternalAdapterAuthorization;
	/** Execution surfaces extracted from the already-verified policy snapshot. */
	policy_surfaces: readonly ExecutionAdapterSurface[];
	surface: ExecutionAdapterSurface;
	executor: ExecutorIdentity;
	/** Adapter runtime identity independently observed by the host. */
	runtime_identity_digest: string;
	container_image_digest?: string;
	output_scope: ExternalAdapterOutputBinding;
	project?: string;
	partition?: string;
	resources?: ExternalAdapterRequestedResources;
	timeouts?: Partial<ExternalAdapterTimeouts>;
}

export interface ExternalAdapterAdmission extends ExternalAdapterManifestValidation {
	manifest?: ExternalAdapterManifest;
}

const DIGEST = /^(?:sha256:)?[0-9a-f]{64}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const VERSION = /^[0-9]+\.[0-9]+\.[0-9]+$/;
const TOKEN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const RELATIVE_PATH = /^(?!\/)(?!.*(?:^|\/)\.\.?(?:\/|$))(?!.*\/\/)(?!.*\/$)(?!.*\\).+/;
const MAX_RESOURCE = 1_000_000;
const MAX_TIMEOUT_SECONDS = 604_800;

const ALLOWED_EXECUTOR_KINDS: Readonly<Record<ExecutionAdapterSurface, readonly string[]>> = {
	vc: ["remote", "trusted"],
	remote: ["remote"],
	trusted: ["trusted"],
};

function object(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, allowed: readonly string[], field: string, errors: string[]): void {
	for (const key of Object.keys(value).sort()) {
		if (!allowed.includes(key)) errors.push(`${field} has unknown field ${key}`);
	}
}

function requiredString(value: unknown, field: string, errors: string[]): value is string {
	if (typeof value !== "string" || value.trim() === "") {
		errors.push(`${field} must be a non-empty string`);
		return false;
	}
	return true;
}

function validDigest(value: unknown): value is string {
	return typeof value === "string" && DIGEST.test(value);
}

function sameDigest(left: string, right: string): boolean {
	return left.replace(/^sha256:/, "").toLowerCase() === right.replace(/^sha256:/, "").toLowerCase();
}

function validateDigest(value: unknown, field: string, errors: string[]): void {
	if (!validDigest(value)) errors.push(`${field} must be a SHA-256 digest`);
}

function validateExecutor(value: unknown, surface: unknown, errors: string[]): void {
	if (!object(value)) {
		errors.push("adapter manifest executor must be an object");
		return;
	}
	exactKeys(value, ["executor_id", "kind", "version", "digest", "trust_level"], "adapter manifest executor", errors);
	if (
		requiredString(value.executor_id, "adapter manifest executor.executor_id", errors) &&
		!ID.test(value.executor_id)
	) {
		errors.push("adapter manifest executor.executor_id is invalid");
	}
	if (requiredString(value.version, "adapter manifest executor.version", errors) && !VERSION.test(value.version)) {
		errors.push("adapter manifest executor.version is invalid");
	}
	validateDigest(value.digest, "adapter manifest executor.digest", errors);
	if (!["local", "python", "docker", "remote", "trusted"].includes(String(value.kind))) {
		errors.push("adapter manifest executor.kind is invalid");
	}
	if (!["cooperative", "host_enforced", "attested"].includes(String(value.trust_level))) {
		errors.push("adapter manifest executor.trust_level is invalid");
	}
	if (
		typeof surface === "string" &&
		EXECUTION_ADAPTER_SURFACES.includes(surface as ExecutionAdapterSurface) &&
		typeof value.kind === "string" &&
		!ALLOWED_EXECUTOR_KINDS[surface as ExecutionAdapterSurface].includes(value.kind)
	) {
		errors.push(`adapter manifest executor.kind is not allowed for surface=${surface}`);
	}
}

function validateTokenList(value: unknown, field: string, errors: string[]): void {
	if (!Array.isArray(value)) {
		errors.push(`${field} must be an array`);
		return;
	}
	if (value.length === 0) errors.push(`${field} must contain at least one entry`);
	const seen = new Set<string>();
	for (const [index, item] of value.entries()) {
		if (typeof item !== "string" || item.trim() === "") {
			errors.push(`${field}[${index}] must be a non-empty string`);
			continue;
		}
		if (!TOKEN.test(item)) errors.push(`${field}[${index}] is invalid`);
		if (seen.has(item)) errors.push(`${field}[${index}] is duplicated`);
		seen.add(item);
	}
	const sorted = [...value].filter((item): item is string => typeof item === "string").sort();
	if (sorted.length === value.length && sorted.some((item, index) => item !== value[index])) {
		errors.push(`${field} must be sorted`);
	}
}

function validateAuthorization(value: unknown, surface: unknown, errors: string[]): void {
	if (!object(value)) {
		errors.push("adapter manifest authorization must be an object");
		return;
	}
	exactKeys(value, ["allowed_projects", "allowed_partitions"], "adapter manifest authorization", errors);
	validateTokenList(value.allowed_projects, "adapter manifest authorization.allowed_projects", errors);
	if (value.allowed_partitions === undefined) {
		if (surface === "vc") errors.push("adapter manifest authorization.allowed_partitions must be an array");
	} else {
		validateTokenList(value.allowed_partitions, "adapter manifest authorization.allowed_partitions", errors);
	}
}

function validatePositiveBound(value: unknown, field: string, maximum: number, errors: string[]): void {
	if (!Number.isSafeInteger(value) || (value as number) <= 0) {
		errors.push(`${field} must be a positive integer`);
		return;
	}
	if ((value as number) > maximum) errors.push(`${field} exceeds the maximum allowed value`);
}

function validateResources(value: unknown, errors: string[]): void {
	if (!object(value)) {
		errors.push("adapter manifest resource_limits must be an object");
		return;
	}
	exactKeys(value, ["max_gpus", "max_memory_gb", "max_cpus"], "adapter manifest resource_limits", errors);
	validatePositiveBound(value.max_gpus, "adapter manifest resource_limits.max_gpus", MAX_RESOURCE, errors);
	validatePositiveBound(value.max_memory_gb, "adapter manifest resource_limits.max_memory_gb", MAX_RESOURCE, errors);
	validatePositiveBound(value.max_cpus, "adapter manifest resource_limits.max_cpus", MAX_RESOURCE, errors);
}

function validateTimeouts(value: unknown, errors: string[]): void {
	if (!object(value)) {
		errors.push("adapter manifest timeouts must be an object");
		return;
	}
	exactKeys(
		value,
		["submit_seconds", "wait_seconds", "command_seconds", "cancel_seconds", "poll_seconds"],
		"adapter manifest timeouts",
		errors,
	);
	for (const field of [
		"submit_seconds",
		"wait_seconds",
		"command_seconds",
		"cancel_seconds",
		"poll_seconds",
	] as const) {
		validatePositiveBound(value[field], `adapter manifest timeouts.${field}`, MAX_TIMEOUT_SECONDS, errors);
	}
	if (
		Number.isSafeInteger(value.command_seconds) &&
		Number.isSafeInteger(value.wait_seconds) &&
		(value.command_seconds as number) > (value.wait_seconds as number)
	) {
		errors.push("adapter manifest timeouts.command_seconds must not exceed wait_seconds");
	}
	if (
		Number.isSafeInteger(value.poll_seconds) &&
		Number.isSafeInteger(value.wait_seconds) &&
		(value.poll_seconds as number) > (value.wait_seconds as number)
	) {
		errors.push("adapter manifest timeouts.poll_seconds must not exceed wait_seconds");
	}
}

function validateCancellation(value: unknown, surface: unknown, errors: string[]): void {
	if (!object(value)) {
		errors.push("adapter manifest cancellation must be an object");
		return;
	}
	exactKeys(
		value,
		["supported", "strategy", "confirmation", "timeout_outcome"],
		"adapter manifest cancellation",
		errors,
	);
	if (typeof value.supported !== "boolean") errors.push("adapter manifest cancellation.supported must be boolean");
	if (!ADAPTER_CANCELLATION_STRATEGIES.includes(value.strategy as AdapterCancellationStrategy)) {
		errors.push("adapter manifest cancellation.strategy is invalid");
	}
	if (!ADAPTER_CANCELLATION_CONFIRMATIONS.includes(value.confirmation as AdapterCancellationConfirmation)) {
		errors.push("adapter manifest cancellation.confirmation is invalid");
	}
	if (!ADAPTER_TIMEOUT_OUTCOMES.includes(value.timeout_outcome as AdapterTimeoutOutcome)) {
		errors.push("adapter manifest cancellation.timeout_outcome is invalid");
	}
	if (value.supported === false) {
		if (value.strategy !== "none") errors.push("unsupported cancellation must use strategy=none");
		if (value.confirmation !== "not_applicable")
			errors.push("unsupported cancellation must use confirmation=not_applicable");
		if (value.timeout_outcome !== "BLOCKED") errors.push("unsupported cancellation must use timeout_outcome=BLOCKED");
	}
	if (value.supported === true && value.strategy === "none")
		errors.push("supported cancellation cannot use strategy=none");
	if (value.supported === true && value.confirmation === "not_applicable") {
		errors.push("supported cancellation cannot use confirmation=not_applicable");
	}
	if (value.confirmation === "confirmed" && value.timeout_outcome !== "CANCELLED") {
		errors.push("confirmed cancellation must use timeout_outcome=CANCELLED");
	}
	if (value.confirmation === "best_effort" && value.timeout_outcome !== "BLOCKED") {
		errors.push("best_effort cancellation must use timeout_outcome=BLOCKED");
	}
	if (surface === "vc" && value.supported !== true) {
		errors.push("vc adapter manifest requires supported cancellation");
	}
}

function validateRelativePath(value: unknown, field: string, errors: string[]): void {
	if (typeof value !== "string" || !RELATIVE_PATH.test(value)) {
		errors.push(`${field} must be a normalized non-escaping relative POSIX path`);
	}
}

function validateOutputScope(value: unknown, errors: string[]): void {
	if (!object(value)) {
		errors.push("adapter manifest output_scope must be an object");
		return;
	}
	exactKeys(
		value,
		["output_root", "logs_root", "write_policy", "logs_retained"],
		"adapter manifest output_scope",
		errors,
	);
	validateRelativePath(value.output_root, "adapter manifest output_scope.output_root", errors);
	validateRelativePath(value.logs_root, "adapter manifest output_scope.logs_root", errors);
	if (typeof value.output_root === "string" && typeof value.logs_root === "string") {
		if (value.logs_root !== value.output_root && !value.logs_root.startsWith(`${value.output_root}/`)) {
			errors.push("adapter manifest output_scope.logs_root must be inside output_root");
		}
	}
	if (!ADAPTER_WRITE_POLICIES.includes(value.write_policy as AdapterWritePolicy)) {
		errors.push("adapter manifest output_scope.write_policy is invalid");
	}
	if (typeof value.logs_retained !== "boolean")
		errors.push("adapter manifest output_scope.logs_retained must be boolean");
}

function validateRuntime(value: unknown, surface: unknown, errors: string[]): void {
	if (!object(value)) {
		errors.push("adapter manifest runtime must be an object");
		return;
	}
	exactKeys(value, ["runtime_identity_digest", "container"], "adapter manifest runtime", errors);
	validateDigest(value.runtime_identity_digest, "adapter manifest runtime.runtime_identity_digest", errors);
	if (value.container !== undefined) {
		if (!object(value.container)) {
			errors.push("adapter manifest runtime.container must be an object");
		} else {
			exactKeys(value.container, ["image", "image_digest"], "adapter manifest runtime.container", errors);
			if (requiredString(value.container.image, "adapter manifest runtime.container.image", errors)) {
				const embedded = /@sha256:([0-9a-f]{64})$/i.exec(value.container.image);
				if (embedded === null) errors.push("adapter manifest runtime.container.image must be digest-pinned");
				else if (
					!validDigest(value.container.image_digest) ||
					!sameDigest(value.container.image_digest, `sha256:${embedded[1]}`)
				) {
					errors.push("adapter manifest runtime.container.image_digest does not match image");
				}
			}
			validateDigest(value.container.image_digest, "adapter manifest runtime.container.image_digest", errors);
		}
	}
	if (surface === "vc" && (value.container === undefined || value.container === null)) {
		errors.push("vc adapter manifest requires runtime.container");
	}
}

function validateAttestation(value: unknown, surface: unknown, executor: unknown, errors: string[]): void {
	if (!object(value)) {
		errors.push("adapter manifest attestation must be an object");
		return;
	}
	exactKeys(value, ["mode"], "adapter manifest attestation", errors);
	if (!ADAPTER_ATTESTATION_MODES.includes(value.mode as AdapterAttestationMode)) {
		errors.push("adapter manifest attestation.mode is invalid");
	}
	const trust = object(executor) ? executor.trust_level : undefined;
	if (trust === "attested" && value.mode === "none")
		errors.push("attested executor requires a non-none attestation mode");
	if (value.mode === "trusted_attestation" && trust !== "attested") {
		errors.push("trusted_attestation requires an attested executor");
	}
	if (surface === "trusted" && trust !== "attested") errors.push("trusted surface requires an attested executor");
	if (surface === "trusted" && value.mode !== "trusted_attestation" && value.mode !== "signed_receipt") {
		errors.push("trusted surface requires signed_receipt or trusted_attestation");
	}
}

function withoutDigest(value: ExternalAdapterManifest | Record<string, unknown>): Record<string, unknown> {
	const copy = { ...value };
	delete copy.manifest_digest;
	return copy;
}

/** Compute the content identity of a manifest, excluding its self-binding field. */
export function externalAdapterManifestDigest(value: ExternalAdapterManifest | ExternalAdapterManifestInput): string {
	return canonicalJsonDigest(withoutDigest(value as ExternalAdapterManifest) as unknown as JsonValue);
}

/** Build a normalized, self-bound manifest from an unsigned input object. */
export function createExternalAdapterManifest(input: ExternalAdapterManifestInput): ExternalAdapterManifest {
	const authorization = {
		allowed_projects: [...input.authorization.allowed_projects].sort(),
		...(input.authorization.allowed_partitions === undefined
			? {}
			: { allowed_partitions: [...input.authorization.allowed_partitions].sort() }),
	};
	const manifest: ExternalAdapterManifest = {
		...input,
		schema: EXTERNAL_ADAPTER_MANIFEST_SCHEMA,
		executor: { ...input.executor, digest: normalizeDigest(input.executor.digest) },
		policy_snapshot_digest: normalizeDigest(input.policy_snapshot_digest),
		authorization,
		runtime: {
			...input.runtime,
			runtime_identity_digest: normalizeDigest(input.runtime.runtime_identity_digest),
			...(input.runtime.container === undefined
				? {}
				: {
						container: {
							...input.runtime.container,
							image_digest: normalizeDigest(input.runtime.container.image_digest),
						},
					}),
		},
		manifest_digest: "",
	};
	manifest.manifest_digest = externalAdapterManifestDigest(manifest);
	return manifest;
}

/** Validate structure, semantic relations and the manifest self-binding. */
export function validateExternalAdapterManifest(value: unknown): ExternalAdapterManifestValidation {
	if (!object(value)) return { valid: false, errors: ["adapter manifest must be an object"] };
	const errors: string[] = [];
	exactKeys(
		value,
		[
			"schema",
			"manifest_id",
			"manifest_version",
			"surface",
			"executor",
			"policy_snapshot_digest",
			"authorization",
			"resource_limits",
			"timeouts",
			"cancellation",
			"output_scope",
			"runtime",
			"attestation",
			"manifest_digest",
		],
		"adapter manifest",
		errors,
	);
	if (value.schema !== EXTERNAL_ADAPTER_MANIFEST_SCHEMA) errors.push("adapter manifest schema is unsupported");
	if (requiredString(value.manifest_id, "adapter manifest manifest_id", errors) && !ID.test(value.manifest_id)) {
		errors.push("adapter manifest manifest_id is invalid");
	}
	if (
		requiredString(value.manifest_version, "adapter manifest manifest_version", errors) &&
		!VERSION.test(value.manifest_version)
	) {
		errors.push("adapter manifest manifest_version is invalid");
	}
	if (!EXECUTION_ADAPTER_SURFACES.includes(value.surface as ExecutionAdapterSurface)) {
		errors.push("adapter manifest surface is invalid");
	}
	validateDigest(value.policy_snapshot_digest, "adapter manifest policy_snapshot_digest", errors);
	validateExecutor(value.executor, value.surface, errors);
	validateAuthorization(value.authorization, value.surface, errors);
	validateResources(value.resource_limits, errors);
	validateTimeouts(value.timeouts, errors);
	validateCancellation(value.cancellation, value.surface, errors);
	validateOutputScope(value.output_scope, errors);
	validateRuntime(value.runtime, value.surface, errors);
	validateAttestation(value.attestation, value.surface, value.executor, errors);
	validateDigest(value.manifest_digest, "adapter manifest manifest_digest", errors);
	let digest: string | undefined;
	if (errors.length === 0) {
		digest = externalAdapterManifestDigest(value as unknown as ExternalAdapterManifest);
		if (!sameDigest(String(value.manifest_digest), digest)) {
			errors.push("adapter manifest manifest_digest does not match its contents");
		}
	}
	return errors.length === 0 ? { valid: true, errors: [], digest } : { valid: false, errors, digest };
}

/** Validate a manifest and bind it to the run's policy/request context. */
export function admitExternalAdapterManifest(
	value: unknown,
	context: ExternalAdapterAdmissionContext,
): ExternalAdapterAdmission {
	if (!object(context)) return { valid: false, errors: ["adapter admission context must be an object"] };
	const validation = validateExternalAdapterManifest(value);
	if (!validation.valid) return validation;
	const manifest = value as ExternalAdapterManifest;
	const errors: string[] = [];
	if (!validDigest(context.policy_snapshot_digest))
		errors.push("adapter admission policy_snapshot_digest must be a SHA-256 digest");
	else if (!sameDigest(manifest.policy_snapshot_digest, context.policy_snapshot_digest)) {
		errors.push("adapter manifest policy_snapshot_digest does not match admission context");
	}
	if (manifest.surface !== context.surface) errors.push("adapter manifest surface does not match admission context");
	if (!Array.isArray(context.policy_surfaces)) {
		errors.push("adapter admission policy_surfaces must be an array");
	} else {
		const invalidSurface = context.policy_surfaces.find(
			(surface) => !EXECUTION_ADAPTER_SURFACES.includes(surface as ExecutionAdapterSurface),
		);
		if (invalidSurface !== undefined) errors.push("adapter admission policy_surfaces contains an invalid surface");
		if (!context.policy_surfaces.includes(manifest.surface)) {
			errors.push("adapter manifest surface is not enabled by policy");
		}
	}
	if (!object(context.executor)) {
		errors.push("adapter admission executor must be an object");
	} else {
		for (const field of ["executor_id", "kind", "version", "trust_level"] as const) {
			if (context.executor[field] !== manifest.executor[field]) {
				errors.push(`adapter manifest executor.${field} does not match admission context`);
			}
		}
		if (!validDigest(context.executor.digest) || !sameDigest(context.executor.digest, manifest.executor.digest)) {
			errors.push("adapter manifest executor.digest does not match admission context");
		}
	}
	if (!validDigest(context.runtime_identity_digest)) {
		errors.push("adapter admission runtime_identity_digest must be a SHA-256 digest");
	} else if (!sameDigest(manifest.runtime.runtime_identity_digest, context.runtime_identity_digest)) {
		errors.push("adapter manifest runtime_identity_digest does not match admission context");
	}
	if (manifest.runtime.container !== undefined) {
		if (!validDigest(context.container_image_digest)) {
			errors.push("adapter admission container_image_digest must be a SHA-256 digest");
		} else if (!sameDigest(manifest.runtime.container.image_digest, context.container_image_digest)) {
			errors.push("adapter manifest container image digest does not match admission context");
		}
	}
	if (!object(context.output_scope)) {
		errors.push("adapter admission output_scope must be an object");
	} else {
		if (context.output_scope.output_root !== manifest.output_scope.output_root) {
			errors.push("adapter manifest output_root does not match admission context");
		}
		if (context.output_scope.logs_root !== manifest.output_scope.logs_root) {
			errors.push("adapter manifest logs_root does not match admission context");
		}
	}
	if (!object(context.policy_authorization)) {
		errors.push("adapter admission policy_authorization must be an object");
	} else {
		const policyErrors: string[] = [];
		validateTokenList(
			context.policy_authorization.allowed_projects,
			"adapter admission policy_authorization.allowed_projects",
			policyErrors,
		);
		if (context.policy_authorization.allowed_partitions !== undefined) {
			validateTokenList(
				context.policy_authorization.allowed_partitions,
				"adapter admission policy_authorization.allowed_partitions",
				policyErrors,
			);
		} else if (manifest.surface === "vc") {
			policyErrors.push("adapter admission policy_authorization.allowed_partitions must be an array");
		}
		errors.push(...policyErrors);
		if (policyErrors.length === 0) {
			if (
				manifest.authorization.allowed_projects.some(
					(project) => !context.policy_authorization.allowed_projects.includes(project),
				)
			) {
				errors.push("adapter manifest project allowlist exceeds policy authorization");
			}
			if (manifest.authorization.allowed_partitions !== undefined) {
				if (
					context.policy_authorization.allowed_partitions === undefined ||
					manifest.authorization.allowed_partitions.some(
						(partition) => !context.policy_authorization.allowed_partitions?.includes(partition),
					)
				) {
					errors.push("adapter manifest partition allowlist exceeds policy authorization");
				}
			}
		}
	}
	if (manifest.surface === "vc") {
		if (typeof context.project !== "string" || context.project.length === 0)
			errors.push("vc adapter admission requires project");
		else if (!manifest.authorization.allowed_projects.includes(context.project))
			errors.push("project is not allowed by adapter manifest");
		if (typeof context.partition !== "string" || context.partition.length === 0)
			errors.push("vc adapter admission requires partition");
		else if (!manifest.authorization.allowed_partitions?.includes(context.partition))
			errors.push("partition is not allowed by adapter manifest");
	}
	if (context.resources !== undefined) {
		for (const [field, limitField] of [
			["gpus", "max_gpus"],
			["memory_gb", "max_memory_gb"],
			["cpus", "max_cpus"],
		] as const) {
			const requested = context.resources[field];
			if (requested === undefined) continue;
			if (!Number.isSafeInteger(requested) || requested <= 0)
				errors.push(`admission resources.${field} must be a positive integer`);
			else if (requested > manifest.resource_limits[limitField])
				errors.push(`admission resources.${field} exceeds adapter manifest limit`);
		}
	}
	if (context.timeouts !== undefined) {
		for (const field of [
			"submit_seconds",
			"wait_seconds",
			"command_seconds",
			"cancel_seconds",
			"poll_seconds",
		] as const) {
			const requested = context.timeouts[field];
			if (requested === undefined) continue;
			if (!Number.isSafeInteger(requested) || requested <= 0)
				errors.push(`admission timeouts.${field} must be a positive integer`);
			else if (requested > manifest.timeouts[field])
				errors.push(`admission timeouts.${field} exceeds adapter manifest limit`);
		}
	}
	return errors.length === 0
		? { valid: true, errors: [], digest: validation.digest, manifest }
		: { valid: false, errors, digest: validation.digest, manifest };
}

function normalizeDigest(value: string): string {
	return value.startsWith("sha256:") ? value.toLowerCase() : `sha256:${value.toLowerCase()}`;
}
