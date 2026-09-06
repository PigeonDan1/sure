import { posix } from "node:path";
import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type { JsonValue } from "../contracts/types.ts";
import { POLICY_PATH_ROLES, type PolicyPathBinding, type PolicySnapshot, type PolicySnapshotInput } from "./types.ts";

const ROOT_ID_PATTERN = /^[a-z0-9][a-z0-9._-]*$/;
const DIGEST_PATTERN = /^(?:sha256:)?[0-9a-f]{64}$/;

function object(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(value: unknown, field: string): string {
	if (typeof value !== "string" || value.length === 0) throw new Error(`${field} must be a non-empty string.`);
	return value;
}

function normalizedDigest(value: unknown, field: string): string {
	const digest = requiredString(value, field);
	if (!DIGEST_PATTERN.test(digest)) throw new Error(`${field} must be SHA-256.`);
	return digest.startsWith("sha256:") ? digest : `sha256:${digest}`;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], field: string): void {
	for (const key of Object.keys(value)) {
		if (!keys.includes(key)) throw new Error(`${field} has unknown field: ${key}`);
	}
}

function assertBinding(binding: PolicyPathBinding): void {
	if (!object(binding)) throw new Error("Policy path binding must be an object.");
	exactKeys(binding, ["root_id", "role", "path", "resolved_path"], "Policy path binding");
	requiredString(binding.root_id, "Policy binding root_id");
	requiredString(binding.role, "Policy binding role");
	requiredString(binding.path, "Policy binding path");
	requiredString(binding.resolved_path, "Policy binding resolved_path");
	if (!ROOT_ID_PATTERN.test(binding.root_id)) throw new Error(`Invalid policy root id: ${binding.root_id}`);
	if (!POLICY_PATH_ROLES.includes(binding.role)) throw new Error(`Invalid policy path role: ${binding.role}`);
	for (const [name, value] of [
		["path", binding.path],
		["resolved_path", binding.resolved_path],
	] as const) {
		if (!posix.isAbsolute(value)) throw new Error(`Policy binding ${binding.root_id} ${name} must be absolute.`);
		if (posix.normalize(value) !== value) {
			throw new Error(`Policy binding ${binding.root_id} ${name} must be normalized.`);
		}
	}
}

function snapshotPayload(input: PolicySnapshotInput, policyDigest: string, bindingsDigest: string): JsonValue {
	return {
		schema: "sure.policy.snapshot.v1",
		site_id: input.site_id,
		policy_version: input.policy_version,
		policy: input.policy,
		source: input.source as unknown as JsonValue,
		path_bindings: input.path_bindings as unknown as JsonValue,
		policy_digest: policyDigest,
		bindings_digest: bindingsDigest,
	};
}

/** Create the immutable policy evidence that is bound to a run. */
export function createPolicySnapshot(input: PolicySnapshotInput): PolicySnapshot {
	if (!ROOT_ID_PATTERN.test(input.site_id)) throw new Error(`Invalid site id: ${input.site_id}`);
	if (!Number.isSafeInteger(input.policy_version) || input.policy_version < 1) {
		throw new Error("Policy version must be a positive integer.");
	}
	if (!input.source.kind.trim()) throw new Error("Policy snapshot source kind is required.");
	if (!DIGEST_PATTERN.test(input.source.raw_sha256)) throw new Error("Policy source digest must be SHA-256.");
	if (input.source.path !== undefined && !posix.isAbsolute(input.source.path)) {
		throw new Error("Policy source path must be absolute when present.");
	}

	const ids = new Set<string>();
	for (const binding of input.path_bindings) {
		assertBinding(binding);
		if (ids.has(binding.root_id)) throw new Error(`Duplicate policy root id: ${binding.root_id}`);
		ids.add(binding.root_id);
	}
	const bindings = [...input.path_bindings].sort((left, right) => left.root_id.localeCompare(right.root_id));
	const source = {
		...input.source,
		raw_sha256: input.source.raw_sha256.startsWith("sha256:")
			? input.source.raw_sha256
			: `sha256:${input.source.raw_sha256}`,
	};
	const normalized: PolicySnapshotInput = { ...input, source, path_bindings: bindings };
	const policyDigest = canonicalJsonDigest(input.policy);
	const bindingsDigest = canonicalJsonDigest(bindings as unknown as JsonValue);
	const payload = snapshotPayload(normalized, policyDigest, bindingsDigest);
	return {
		...(payload as unknown as Omit<PolicySnapshot, "snapshot_digest">),
		snapshot_digest: canonicalJsonDigest(payload),
	};
}

/**
 * Parse and verify a persisted policy snapshot.
 *
 * A snapshot is evidence, not merely configuration: all derived digests are
 * recomputed from the canonical payload so a caller cannot make a modified
 * policy look like the original one by retaining its old digest fields.
 */
export function validatePolicySnapshot(value: unknown): PolicySnapshot {
	if (!object(value)) throw new Error("Policy snapshot must be an object.");
	exactKeys(
		value,
		[
			"schema",
			"site_id",
			"policy_version",
			"policy",
			"source",
			"path_bindings",
			"policy_digest",
			"bindings_digest",
			"snapshot_digest",
		],
		"Policy snapshot",
	);
	if (value.schema !== "sure.policy.snapshot.v1") throw new Error("Policy snapshot schema is unsupported.");
	const policyVersion = value.policy_version;
	if (typeof policyVersion !== "number" || !Number.isSafeInteger(policyVersion) || policyVersion < 1)
		throw new Error("Policy snapshot policy_version must be a positive integer.");
	const sourceValue = value.source;
	if (!object(sourceValue)) throw new Error("Policy snapshot source must be an object.");
	exactKeys(sourceValue, ["kind", "path", "raw_sha256"], "Policy snapshot source");
	const sourceKind = requiredString(sourceValue.kind, "Policy snapshot source.kind");
	const sourceDigest = normalizedDigest(sourceValue.raw_sha256, "Policy snapshot source.raw_sha256");
	let sourcePath: string | undefined;
	if (sourceValue.path !== undefined) {
		sourcePath = requiredString(sourceValue.path, "Policy snapshot source.path");
		if (!posix.isAbsolute(sourcePath) || posix.normalize(sourcePath) !== sourcePath)
			throw new Error("Policy snapshot source.path must be an absolute normalized path.");
	}
	if (!Array.isArray(value.path_bindings)) throw new Error("Policy snapshot path_bindings must be an array.");
	const bindings: PolicyPathBinding[] = value.path_bindings.map((raw, index) => {
		if (!object(raw)) throw new Error(`Policy snapshot path_bindings[${index}] must be an object.`);
		exactKeys(raw, ["root_id", "role", "path", "resolved_path"], `Policy snapshot path_bindings[${index}]`);
		return {
			root_id: requiredString(raw.root_id, `Policy snapshot path_bindings[${index}].root_id`),
			role: requiredString(raw.role, `Policy snapshot path_bindings[${index}].role`) as PolicyPathBinding["role"],
			path: requiredString(raw.path, `Policy snapshot path_bindings[${index}].path`),
			resolved_path: requiredString(raw.resolved_path, `Policy snapshot path_bindings[${index}].resolved_path`),
		};
	});
	const normalized = createPolicySnapshot({
		site_id: requiredString(value.site_id, "Policy snapshot site_id"),
		policy_version: policyVersion,
		policy: value.policy as JsonValue,
		source: { kind: sourceKind, ...(sourcePath === undefined ? {} : { path: sourcePath }), raw_sha256: sourceDigest },
		path_bindings: bindings,
	});
	for (const [field, supplied, computed] of [
		["policy_digest", value.policy_digest, normalized.policy_digest],
		["bindings_digest", value.bindings_digest, normalized.bindings_digest],
		["snapshot_digest", value.snapshot_digest, normalized.snapshot_digest],
	] as const) {
		if (normalizedDigest(supplied, `Policy snapshot ${field}`) !== computed)
			throw new Error(`Policy snapshot ${field} does not match its canonical contents.`);
	}
	return normalized;
}

/** Backwards-compatible name for callers that treat parsing as validation. */
export const parsePolicySnapshot = validatePolicySnapshot;
