import { accessSync, constants, existsSync, mkdirSync, realpathSync } from "node:fs";
import { basename, dirname, isAbsolute, join, resolve, sep } from "node:path";
import { requireSitePolicy, resolveSitePolicy } from "../../../../../sure/site/loader.ts";

// Approved models and promoted results both live below this root. Runs never
// write inside it; a human promotes reviewed products there.
export const NFS_ROOT = resolveSitePolicy()?.policy.storage.forbidden_output_roots[0] ?? "<site-policy-required>";

export interface OutputDirResolution {
	ok: boolean;
	dir?: string;
	error?: string;
}

interface SplitArgs {
	requested?: string;
	rest: string;
}

function splitOutputDir(args: string): SplitArgs {
	const tokens = args.trim().split(/\s+/).filter(Boolean);
	const rest: string[] = [];
	let requested: string | undefined;
	for (let i = 0; i < tokens.length; i++) {
		const token = tokens[i];
		const eq = token.indexOf("=");
		if (eq >= 0) {
			if (token.slice(0, eq) === "output_dir") {
				requested = requested ?? token.slice(eq + 1);
				continue;
			}
			rest.push(token);
			continue;
		}
		if (token.replace(/^--?/, "") !== "output_dir") {
			rest.push(token);
			continue;
		}
		const next = tokens[i + 1];
		if (next !== undefined && !next.startsWith("-")) {
			requested = requested ?? next;
			i++;
		}
	}
	return { requested, rest: rest.join(" ") };
}

/**
 * Drop `output_dir` from the arguments handed to the agent.
 *
 * The harness owns that directory: it writes `result.json` there and copies the
 * run's control artifacts into it. An agent that also sees the path treats it as
 * "put the products here" and redirects skill output — a `/sure_feed` run did
 * exactly that, publishing its handoff outside `sure/handoffs/` where
 * `/sure_onboard model=<name>` could no longer find it. Hooks still receive the
 * full argument string, so `/sure_eval` keeps staging its products there.
 */
export function stripOutputDir(args: string): string {
	return splitOutputDir(args).rest;
}

// Resolve symlinks as far as the path exists, then re-attach the rest. A check
// that only compares spelled paths would miss alternate names for shared roots.
function realPathish(path: string): string {
	let current = resolve(path);
	const tail: string[] = [];
	while (!existsSync(current)) {
		const parent = dirname(current);
		if (parent === current) {
			return resolve(path);
		}
		tail.unshift(basename(current));
		current = parent;
	}
	return tail.length > 0 ? join(realpathSync(current), ...tail) : realpathSync(current);
}

function insideNfs(dir: string): boolean {
	const configuredRoots = requireSitePolicy().policy.storage.forbidden_output_roots;
	const roots = new Set(configuredRoots.map((root) => resolve(root)));
	for (const root of configuredRoots) {
		if (existsSync(root)) roots.add(realpathSync(root));
	}
	const candidates = new Set([resolve(dir), realPathish(dir)]);
	for (const root of roots) {
		for (const candidate of candidates) {
			if (candidate === root || candidate.startsWith(root + sep)) {
				return true;
			}
		}
	}
	return false;
}

/**
 * Resolve the output directory a Sure command asked for.
 *
 * Cloud callers read one directory per invocation, so the directory has to be
 * usable before the run starts: absolute, outside NFS, and writable.
 */
export function resolveOutputDir(args: string): OutputDirResolution {
	const { requested } = splitOutputDir(args);
	if (!requested) {
		return { ok: true, dir: undefined };
	}
	if (!isAbsolute(requested)) {
		return {
			ok: false,
			error: `output_dir must be an absolute path, for example output_dir=${join(dirname(NFS_ROOT), "my_results")} (got "${requested}")`,
		};
	}
	const dir = resolve(requested);
	try {
		if (insideNfs(dir)) {
			return { ok: false, error: `output_dir must stay outside ${NFS_ROOT}: promotion into NFS is a human step` };
		}
	} catch (error) {
		return { ok: false, error: error instanceof Error ? error.message : String(error) };
	}
	try {
		mkdirSync(dir, { recursive: true });
	} catch (error) {
		const detail = error instanceof Error ? error.message : String(error);
		return { ok: false, error: `output_dir cannot be created: ${dir} (${detail})` };
	}
	try {
		accessSync(dir, constants.W_OK);
	} catch {
		return { ok: false, error: `output_dir is not writable: ${dir}` };
	}
	return { ok: true, dir };
}
