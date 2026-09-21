#!/usr/bin/env tsx
import { existsSync, mkdirSync, rmSync } from "node:fs";
import { isAbsolute, join, relative, sep } from "node:path";
import { requireSitePolicy } from "../sure/site/loader.ts";

// Deliberately no separator folding: node:path is win32 on Windows, where
// relative() already reads "/" and "\" as the same separator, and posix
// elsewhere, where a backslash is an ordinary file-name character. Rewriting
// "\" to "/" would change no Windows answer and would let an approved root
// /srv/a\b pass as protected by the forbidden root /srv/a/b on Linux and macOS.
function isWithin(path: string, root: string): boolean {
	const candidate = relative(root, path);
	return candidate === "" || (!candidate.startsWith(`..${sep}`) && candidate !== ".." && !isAbsolute(candidate));
}

// accessSync(W_OK) only reports the read-only attribute on Windows: it calls an
// ACL-denied directory, and a path that is a plain file, writable. Creating and
// removing a directory is the one question all three platforms answer honestly.
function isWritable(root: string): boolean {
	const probe = join(root, `.sure-site-check-${process.pid}`);
	try {
		mkdirSync(probe);
		return true;
	} catch {
		return false;
	} finally {
		// Removing the probe must never fail the check: force only swallows
		// ENOENT, so a handle held by a scanner or an indexer, or a drop-box ACL
		// that grants add-subdirectory but denies delete-child, raises EPERM
		// here. Retry a few times, then leave the probe behind rather than abort
		// a command whose whole contract is that it does not fail.
		try {
			rmSync(probe, { recursive: true, force: true, maxRetries: 3 });
		} catch {
			// Deliberately ignored; see above.
		}
	}
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
	if (projectionRoot && storage.forbidden_output_roots.some((forbidden) => isWithin(projectionRoot, forbidden))) {
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
	if (execution.surfaces.includes("vc") && !execution.vc_project) {
		failures.push("execution.vc_project is required when the vc surface is enabled");
	}
	if (!execution.surfaces.includes("vc") && execution.vc_partitions !== undefined) {
		failures.push("execution.vc_partitions requires the vc surface");
	}
	if (failures.length > 0) throw new Error(failures.join("\n"));

	// Directory state is a warning, never a failure: on a fresh machine none of
	// these exist yet, and the commands that need them create their own.
	for (const [kind, roots] of [
		["approved model root", storage.approved_models_roots],
		["approved result root", storage.approved_results_roots],
		["runtime root", [storage.runtime_root]],
		["dataset projection root", projectionRoot ? [projectionRoot] : []],
	] as const) {
		for (const root of roots) {
			if (!existsSync(root)) console.log(`warn ${kind} does not exist yet: ${root}`);
			else if (!isWritable(root)) console.log(`warn ${kind} is not writable: ${root}`);
		}
	}
	// Dataset source roots are read-only by design, so only existence matters.
	for (const [key, root] of Object.entries(datasets.allowed_source_roots)) {
		if (!existsSync(root)) console.log(`warn dataset source root ${key} does not exist yet: ${root}`);
	}
	console.log(`ok   site policy: ${resolved.policy.site_id} (${resolved.source}, sha256 ${resolved.sha256})`);
} catch (error) {
	console.error(error instanceof Error ? error.message : String(error));
	process.exit(1);
}
