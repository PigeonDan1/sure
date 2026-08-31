#!/usr/bin/env tsx
import { isAbsolute, relative, sep } from "node:path";
import { requireSitePolicy } from "../sure/site/loader.ts";

function isWithin(path: string, root: string): boolean {
	const candidate = relative(root, path);
	return candidate === "" || (!candidate.startsWith(`..${sep}`) && candidate !== ".." && !isAbsolute(candidate));
}

try {
	const resolved = requireSitePolicy();
	const { datasets, execution, storage } = resolved.policy;
	const projectionRoot = datasets.projection_root;
	const failures: string[] = [];
	for (const [kind, roots] of [
		["approved model", storage.approved_models_roots],
		["approved result", storage.approved_results_roots],
	] as const) {
		for (const root of roots) {
			if (!storage.forbidden_output_roots.some((forbidden) => isWithin(root, forbidden))) {
				failures.push(`${kind} root must be protected by a forbidden output root: ${root}`);
			}
		}
	}
	if (storage.forbidden_output_roots.some((forbidden) => isWithin(storage.runtime_root, forbidden))) {
		failures.push(`runtime root must stay outside forbidden output roots: ${storage.runtime_root}`);
	}
	if (
		projectionRoot &&
		storage.forbidden_output_roots.some((forbidden) => isWithin(projectionRoot, forbidden))
	) {
		failures.push(`dataset projection root must stay outside forbidden output roots: ${projectionRoot}`);
	}
	if (
		projectionRoot &&
		Object.values(datasets.allowed_source_roots).some(
			(source) => isWithin(projectionRoot, source) || isWithin(source, projectionRoot),
		)
	) {
		failures.push(`dataset projection root must not overlap an allowed source root: ${projectionRoot}`);
	}
	if (execution.surfaces.includes("vc") && !execution.vc_partitions?.length) {
		failures.push("execution.vc_partitions is required when the vc surface is enabled");
	}
	if (!execution.surfaces.includes("vc") && execution.vc_partitions !== undefined) {
		failures.push("execution.vc_partitions requires the vc surface");
	}
	if (failures.length > 0) throw new Error(failures.join("\n"));
	console.log(`ok   site policy: ${resolved.policy.site_id} (${resolved.source}, sha256 ${resolved.sha256})`);
} catch (error) {
	console.error(error instanceof Error ? error.message : String(error));
	process.exit(1);
}
