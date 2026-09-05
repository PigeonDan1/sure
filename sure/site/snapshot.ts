import { resolve } from "node:path";
import {
	createPolicySnapshot,
	type JsonValue,
	type PolicyPathBinding,
	type PolicySnapshot,
} from "../../packages/sure-core/src/index.ts";
import type { ResolvedSitePolicy } from "./loader.ts";

export interface SitePolicySnapshotOptions {
	resolvePath?: (path: string) => string;
}

/** Adapt a validated site policy into immutable, host-neutral run evidence. */
export function snapshotResolvedSitePolicy(
	resolved: ResolvedSitePolicy,
	options: SitePolicySnapshotOptions = {},
): PolicySnapshot {
	const resolvePath = options.resolvePath ?? ((path: string) => resolve(path));
	const bindings: PolicyPathBinding[] = [];
	const add = (rootId: string, role: PolicyPathBinding["role"], path: string): void => {
		bindings.push({ root_id: rootId, role, path, resolved_path: resolvePath(path) });
	};
	for (const [index, path] of resolved.policy.storage.approved_models_roots.entries()) {
		add(`approved-models.${index}`, "read_only_reference", path);
	}
	for (const [index, path] of resolved.policy.storage.approved_results_roots.entries()) {
		add(`approved-results.${index}`, "controlled_publication", path);
	}
	for (const [key, path] of Object.entries(resolved.policy.datasets.allowed_source_roots)) {
		add(`dataset.${key}`, "dataset_source", path);
	}
	if (resolved.policy.datasets.projection_root !== undefined) {
		add("dataset-projection", "controlled_publication", resolved.policy.datasets.projection_root);
	}
	add("runtime", "runtime_cache", resolved.policy.storage.runtime_root);
	for (const [index, path] of resolved.policy.storage.forbidden_output_roots.entries()) {
		add(`forbidden-output.${index}`, "forbidden_output", path);
	}
	return createPolicySnapshot({
		site_id: resolved.policy.site_id,
		policy_version: resolved.policy.policy_version,
		policy: resolved.policy as unknown as JsonValue,
		source: { kind: resolved.source, path: resolved.path, raw_sha256: resolved.sha256 },
		path_bindings: bindings,
	});
}
