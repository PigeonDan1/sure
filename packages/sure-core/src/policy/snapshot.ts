import { posix } from "node:path";
import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type { JsonValue } from "../contracts/types.ts";
import { POLICY_PATH_ROLES, type PolicyPathBinding, type PolicySnapshot, type PolicySnapshotInput } from "./types.ts";

const ROOT_ID_PATTERN = /^[a-z0-9][a-z0-9._-]*$/;
const DIGEST_PATTERN = /^(?:sha256:)?[0-9a-f]{64}$/;

function assertBinding(binding: PolicyPathBinding): void {
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
