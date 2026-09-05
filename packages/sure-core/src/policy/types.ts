import type { JsonValue } from "../contracts/types.ts";

export const POLICY_PATH_ROLES = [
	"read_only_reference",
	"controlled_publication",
	"dataset_source",
	"runtime_cache",
	"forbidden_output",
] as const;
export type PolicyPathRole = (typeof POLICY_PATH_ROLES)[number];

export interface PolicyPathBinding {
	root_id: string;
	role: PolicyPathRole;
	path: string;
	resolved_path: string;
}

export interface PolicySnapshotSource {
	kind: string;
	path?: string;
	raw_sha256: string;
}

export interface PolicySnapshotInput {
	site_id: string;
	policy_version: number;
	policy: JsonValue;
	source: PolicySnapshotSource;
	path_bindings: readonly PolicyPathBinding[];
}

export interface PolicySnapshot {
	schema: "sure.policy.snapshot.v1";
	site_id: string;
	policy_version: number;
	policy: JsonValue;
	source: PolicySnapshotSource;
	path_bindings: readonly PolicyPathBinding[];
	policy_digest: string;
	bindings_digest: string;
	snapshot_digest: string;
}
