import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";

export const SITE_POLICY_ENV = "SURE_SITE_POLICY";
export const SITE_POLICY_SCHEMA = "sure.site.policy.v1";

export type ExecutionSurface = "local" | "vc";
export type SitePolicySource = "environment" | "bundled" | "local";

export interface SitePolicy {
	schema: typeof SITE_POLICY_SCHEMA;
	site_id: string;
	policy_version: 1;
	storage: {
		approved_models_roots: string[];
		approved_results_roots: string[];
		forbidden_output_roots: string[];
		runtime_root: string;
	};
	datasets: {
		allowed_source_roots: string[];
	};
	execution: {
		surfaces: ExecutionSurface[];
		vc_partitions?: string[];
		vc_partition_priority?: Record<string, number>;
	};
	network?: {
		internal_git_host?: string;
		gateway_portal?: string;
	};
}

export interface ResolvedSitePolicy {
	policy: SitePolicy;
	path: string;
	source: SitePolicySource;
	sha256: string;
}

export interface SitePolicyLoadOptions {
	environment?: NodeJS.ProcessEnv;
	repositoryRoot?: string;
}

const repositoryRoot = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const missingPolicyMessage =
	"SURE site policy is not configured.\n" +
	"Missing: config/site.bundled.yaml (bundled distribution) or config/site.local.yaml (local configuration).\n" +
	"Fix: cp config/site.example.yaml config/site.local.yaml and edit the model, result, dataset and runtime paths.\n" +
	"Verify: npm run sure:site-check\n" +
	"See README.md#publicself-hosted-site-policy and docs/site-configuration.md.";

function expectRecord(value: unknown, location: string): Record<string, unknown> {
	if (typeof value !== "object" || value === null || Array.isArray(value)) {
		throw new Error(`${location} must be a mapping`);
	}
	return value as Record<string, unknown>;
}

function rejectUnknown(record: Record<string, unknown>, allowed: readonly string[], location: string): void {
	const unknown = Object.keys(record).filter((key) => !allowed.includes(key));
	if (unknown.length > 0) throw new Error(`${location} has unknown field: ${unknown[0]}`);
}

function expectString(value: unknown, location: string): string {
	if (typeof value !== "string" || value.length === 0) throw new Error(`${location} must be a non-empty string`);
	return value;
}

function expectAbsolutePath(value: unknown, location: string): string {
	const path = expectString(value, location);
	if (!isAbsolute(path)) throw new Error(`${location} must be an absolute path`);
	return path;
}

function expectUniqueStrings(value: unknown, location: string, absolute: boolean): string[] {
	if (!Array.isArray(value) || value.length === 0) throw new Error(`${location} must be a non-empty list`);
	if (absolute && value.length !== 1) throw new Error(`${location} must contain exactly one path in policy v1`);
	const items = value.map((item, index) =>
		absolute ? expectAbsolutePath(item, `${location}[${index}]`) : expectString(item, `${location}[${index}]`),
	);
	if (new Set(items).size !== items.length) throw new Error(`${location} must not contain duplicates`);
	return items;
}

export function validateSitePolicy(value: unknown): SitePolicy {
	const root = expectRecord(value, "site policy");
	rejectUnknown(root, ["schema", "site_id", "policy_version", "storage", "datasets", "execution", "network"], "site policy");
	if (root.schema !== SITE_POLICY_SCHEMA) throw new Error(`schema must be ${SITE_POLICY_SCHEMA}`);
	const siteId = expectString(root.site_id, "site_id");
	if (!/^[a-z0-9][a-z0-9._-]*$/.test(siteId)) throw new Error("site_id has an invalid format");
	if (root.policy_version !== 1) throw new Error("policy_version must be 1");

	const storage = expectRecord(root.storage, "storage");
	rejectUnknown(storage, ["approved_models_roots", "approved_results_roots", "forbidden_output_roots", "runtime_root"], "storage");
	const datasets = expectRecord(root.datasets, "datasets");
	rejectUnknown(datasets, ["allowed_source_roots"], "datasets");
	const execution = expectRecord(root.execution, "execution");
	rejectUnknown(execution, ["surfaces", "vc_partitions", "vc_partition_priority"], "execution");
	const surfaces = expectUniqueStrings(execution.surfaces, "execution.surfaces", false);
	if (surfaces.some((surface) => surface !== "local" && surface !== "vc")) {
		throw new Error("execution.surfaces contains an unsupported value");
	}

	let network: SitePolicy["network"];
	if (root.network !== undefined) {
		const source = expectRecord(root.network, "network");
		rejectUnknown(source, ["internal_git_host", "gateway_portal"], "network");
		network = {};
		if (source.internal_git_host !== undefined) {
			network.internal_git_host = expectString(source.internal_git_host, "network.internal_git_host");
		}
		if (source.gateway_portal !== undefined) {
			network.gateway_portal = expectString(source.gateway_portal, "network.gateway_portal");
			try {
				const portal = new URL(network.gateway_portal);
				if (!portal.hostname || (portal.protocol !== "http:" && portal.protocol !== "https:")) throw new Error();
			} catch {
				throw new Error("network.gateway_portal must be a valid HTTP(S) URL");
			}
		}
	}

	const policy: SitePolicy = {
		schema: SITE_POLICY_SCHEMA,
		site_id: siteId,
		policy_version: 1,
		storage: {
			approved_models_roots: expectUniqueStrings(storage.approved_models_roots, "storage.approved_models_roots", true),
			approved_results_roots: expectUniqueStrings(storage.approved_results_roots, "storage.approved_results_roots", true),
			forbidden_output_roots: expectUniqueStrings(storage.forbidden_output_roots, "storage.forbidden_output_roots", true),
			runtime_root: expectAbsolutePath(storage.runtime_root, "storage.runtime_root"),
		},
		datasets: {
			allowed_source_roots: expectUniqueStrings(datasets.allowed_source_roots, "datasets.allowed_source_roots", true),
		},
		execution: {
			surfaces: surfaces as ExecutionSurface[],
		},
	};
	if (execution.vc_partitions !== undefined) {
		policy.execution.vc_partitions = expectUniqueStrings(execution.vc_partitions, "execution.vc_partitions", false);
	}
	if (execution.vc_partition_priority !== undefined) {
		const priority = expectRecord(execution.vc_partition_priority, "execution.vc_partition_priority");
		const parsed: Record<string, number> = {};
		for (const [name, value] of Object.entries(priority)) {
			if (!/^\S+$/.test(name) || typeof value !== "number" || !Number.isInteger(value) || value < 0) {
				throw new Error(`execution.vc_partition_priority.${name} must be a non-negative integer`);
			}
			parsed[name] = value;
		}
		policy.execution.vc_partition_priority = parsed;
	}
	if (network !== undefined) policy.network = network;
	return policy;
}

function loadPolicy(path: string, source: SitePolicySource): ResolvedSitePolicy {
	let content: Buffer;
	try {
		content = readFileSync(path);
	} catch (error) {
		const detail = error instanceof Error ? error.message : String(error);
		throw new Error(`Cannot read ${source} site policy ${path}: ${detail}`);
	}
	let decoded: unknown;
	try {
		decoded = parse(content.toString("utf8"));
	} catch (error) {
		const detail = error instanceof Error ? error.message : String(error);
		throw new Error(`Cannot parse ${source} site policy ${path}: ${detail}`);
	}
	try {
		return {
			policy: validateSitePolicy(decoded),
			path,
			source,
			sha256: createHash("sha256").update(content).digest("hex"),
		};
	} catch (error) {
		const detail = error instanceof Error ? error.message : String(error);
		throw new Error(`Invalid ${source} site policy ${path}: ${detail}`);
	}
}

export function resolveSitePolicy(options: SitePolicyLoadOptions = {}): ResolvedSitePolicy | undefined {
	const root = resolve(options.repositoryRoot ?? repositoryRoot);
	const environment = options.environment ?? process.env;
	const explicit = environment[SITE_POLICY_ENV]?.trim();
	if (explicit) {
		if (!isAbsolute(explicit)) throw new Error(`${SITE_POLICY_ENV} must be an absolute path`);
		return loadPolicy(resolve(explicit), "environment");
	}
	const candidates: Array<[string, SitePolicySource]> = [
		[resolve(root, "config/site.bundled.yaml"), "bundled"],
		[resolve(root, "config/site.local.yaml"), "local"],
	];
	for (const [path, source] of candidates) {
		if (existsSync(path)) return loadPolicy(path, source);
	}
	return undefined;
}

export function requireSitePolicy(options: SitePolicyLoadOptions = {}): ResolvedSitePolicy {
	const resolved = resolveSitePolicy(options);
	if (!resolved) throw new Error(missingPolicyMessage);
	return resolved;
}

export function sitePolicyMissingMessage(): string {
	return missingPolicyMessage;
}
