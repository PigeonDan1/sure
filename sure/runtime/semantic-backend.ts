import { createHash } from "node:crypto";
import {
	existsSync,
	lstatSync,
	readdirSync,
	readFileSync,
	realpathSync,
} from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { canonicalJsonDigest } from "../../packages/sure-core/src/contracts/canonical-json.ts";
import type { JsonValue } from "../../packages/sure-core/src/contracts/types.ts";

export interface SemanticBackendOperation {
	operation_id: string;
	description: string;
	entrypoint: string;
	consumer_skill_ids: readonly string[];
	kind: "execute" | "validate" | "resolve";
	timeout_ms: number;
	deterministic: boolean;
	canonical_resource_digest?: string;
	legacy_resource_digest?: string;
}

export interface SemanticBackendBundle {
	schema: "sure.semantic.backend.bundle.v1";
	bundle_id: string;
	version: string;
	description: string;
	canonical_root: string;
	legacy_root: string;
	canonical_tree_digest?: string;
	legacy_tree_digest?: string;
	operations: readonly SemanticBackendOperation[];
}

export interface SemanticBackendManifest {
	schema: "sure.semantic.backend.manifest.v1";
	registry_digest: string;
	bundles: readonly SemanticBackendBundle[];
}

export interface SemanticBackendResolveOptions {
	environment?: NodeJS.ProcessEnv;
	manifestPath?: string;
	/** Verify the complete implementation tree in addition to the entrypoint. */
	verifyTree?: boolean;
	/** Refuse a manifest that is not the expected immutable registry. */
	expectedRegistryDigest?: string;
	/** Restrict an operation to a known bundle digest. */
	expectedBundleDigest?: string;
}

export interface ResolvedSemanticBackend {
	operation_id: string;
	bundle_id: string;
	bundle_version: string;
	path: string;
	source: "package" | "semantic-backend-root" | "canonical" | "legacy";
	resource_digest: string;
	bundle_digest?: string;
	registry_digest: string;
	timeout_ms: number;
	deterministic: boolean;
}

export class SemanticBackendResolutionError extends Error {
	readonly code = "SURE_SEMANTIC_BACKEND_UNAVAILABLE";
}

function record(value: unknown): Record<string, unknown> | undefined {
	return typeof value === "object" && value !== null && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: undefined;
}

function requiredString(value: unknown, field: string): string {
	if (typeof value !== "string" || value.trim() === "") throw new SemanticBackendResolutionError(`${field} must be a non-empty string`);
	return value;
}

function relativeResource(value: string, field: string): string {
	const normalized = value.replaceAll("\\", "/");
	if (!normalized || isAbsolute(normalized) || normalized.split("/").includes("..")) {
		throw new SemanticBackendResolutionError(`${field} must be a relative non-escaping path: ${value}`);
	}
	return normalized;
}

function digestFile(path: string): string {
	return `sha256:${createHash("sha256").update(readFileSync(path)).digest("hex")}`;
}

function treeFiles(root: string, prefix = ""): string[] {
	const entries = readdirSync(root, { withFileTypes: true }).sort((left, right) =>
		left.name < right.name ? -1 : left.name > right.name ? 1 : 0,
	);
	const files: string[] = [];
	for (const entry of entries) {
		if (entry.name === "__pycache__" || entry.name === "node_modules") continue;
		const relativePath = prefix ? `${prefix}/${entry.name}` : entry.name;
		const path = join(root, entry.name);
		if (entry.isSymbolicLink()) {
			throw new SemanticBackendResolutionError(`semantic backend tree contains a symlink: ${path}`);
		}
		if (entry.isDirectory()) files.push(...treeFiles(path, relativePath));
		else if (entry.isFile() && !/\.(?:pyc|js|d\.ts|map)$/.test(entry.name)) files.push(relativePath);
		else throw new SemanticBackendResolutionError(`semantic backend tree contains a non-regular entry: ${path}`);
	}
	return files;
}

function treeDigest(root: string): string {
	const rows = treeFiles(root).map((path) => `${path}\0${digestFile(join(root, path))}`).join("\n");
	return `sha256:${createHash("sha256").update(rows).digest("hex")}`;
}

export function repositoryRootForPackage(packageDir: string, environment: NodeJS.ProcessEnv = process.env): string {
	const configured = environment.SURE_REPOSITORY_ROOT?.trim();
	if (configured) {
		const root = resolve(configured);
		if (!existsSync(root)) throw new SemanticBackendResolutionError(`repository root does not exist: ${root}`);
		return root;
	}
	let current = resolve(packageDir);
	for (;;) {
		if (existsSync(join(current, "sure", "canonical")) && existsSync(join(current, "sure", "skills"))) return current;
		const parent = dirname(current);
		if (parent === current) return resolve(packageDir);
		current = parent;
	}
}

function manifestCandidates(packageDir: string, options: SemanticBackendResolveOptions): string[] {
	const env = options.environment ?? process.env;
	const root = repositoryRootForPackage(packageDir, env);
	const explicit = options.manifestPath ?? env.SURE_SEMANTIC_BACKEND_MANIFEST;
	return [
		...(explicit ? [resolve(explicit)] : []),
		join(resolve(packageDir), "semantic-backends.json"),
		join(root, "sure", "canonical", "shared", "evaluation", "backend-manifest.json"),
	].filter((path, index, paths) => paths.indexOf(path) === index);
}

function parseManifest(value: unknown, path: string): SemanticBackendManifest {
	const root = record(value);
	if (!root || (root.schema !== "sure.semantic.backend.manifest.v1" && root.schema !== "sure.semantic.backends.v1")) {
		throw new SemanticBackendResolutionError(`unsupported semantic backend manifest: ${path}`);
	}
	const registryDigest = requiredString(root.registry_digest, "registry_digest");
	if (!/^sha256:[0-9a-f]{64}$/.test(registryDigest)) throw new SemanticBackendResolutionError("registry_digest is invalid");
	if (!Array.isArray(root.bundles) || root.bundles.length === 0) throw new SemanticBackendResolutionError("backend manifest has no bundles");
	const bundleIds = new Set<string>();
	const operationIds = new Set<string>();
	const digest = (value: unknown, field: string): string | undefined => {
		if (value === undefined) return undefined;
		if (typeof value !== "string" || !/^sha256:[0-9a-f]{64}$/.test(value)) {
			throw new SemanticBackendResolutionError(`${field} is invalid`);
		}
		return value;
	};
	const bundles: SemanticBackendBundle[] = root.bundles.map((rawBundle, bundleIndex) => {
		const bundle = record(rawBundle);
		if (!bundle) throw new SemanticBackendResolutionError(`bundles[${bundleIndex}] must be an object`);
		const schema = requiredString(bundle.schema, `bundles[${bundleIndex}].schema`);
		if (schema !== "sure.semantic.backend.bundle.v1") throw new SemanticBackendResolutionError(`unsupported backend bundle schema: ${schema}`);
		const canonicalRoot = relativeResource(requiredString(bundle.canonical_root, "canonical_root"), "canonical_root");
		const legacyRoot = relativeResource(requiredString(bundle.legacy_root, "legacy_root"), "legacy_root");
		const bundleId = requiredString(bundle.bundle_id, "bundle_id");
		if (bundleIds.has(bundleId)) throw new SemanticBackendResolutionError(`duplicate backend bundle: ${bundleId}`);
		bundleIds.add(bundleId);
		const canonicalTreeDigest = digest(bundle.canonical_tree_digest, `bundles[${bundleIndex}].canonical_tree_digest`);
		const legacyTreeDigest = digest(bundle.legacy_tree_digest, `bundles[${bundleIndex}].legacy_tree_digest`);
		if (!Array.isArray(bundle.operations) || bundle.operations.length === 0) throw new SemanticBackendResolutionError(`bundles[${bundleIndex}] has no operations`);
		const operations = bundle.operations.map((rawOperation, operationIndex) => {
			const operation = record(rawOperation);
			if (!operation) throw new SemanticBackendResolutionError(`operations[${operationIndex}] must be an object`);
			const operationId = requiredString(operation.operation_id, "operation_id");
			if (operationIds.has(operationId)) throw new SemanticBackendResolutionError(`duplicate backend operation: ${operationId}`);
			operationIds.add(operationId);
			const entrypoint = relativeResource(requiredString(operation.entrypoint, "entrypoint"), "entrypoint");
			if (!Array.isArray(operation.consumer_skill_ids) || operation.consumer_skill_ids.some((id) => typeof id !== "string")) {
				throw new SemanticBackendResolutionError(`${operationId}.consumer_skill_ids must be a string array`);
			}
			if (!["execute", "validate", "resolve"].includes(String(operation.kind))) throw new SemanticBackendResolutionError(`${operationId}.kind is invalid`);
			if (!Number.isSafeInteger(operation.timeout_ms) || Number(operation.timeout_ms) <= 0) throw new SemanticBackendResolutionError(`${operationId}.timeout_ms is invalid`);
			if (typeof operation.deterministic !== "boolean") throw new SemanticBackendResolutionError(`${operationId}.deterministic is invalid`);
			const canonicalResourceDigest = digest(operation.canonical_resource_digest, `${operationId}.canonical_resource_digest`);
			const legacyResourceDigest = digest(operation.legacy_resource_digest, `${operationId}.legacy_resource_digest`);
			return {
				operation_id: operationId,
				description: requiredString(operation.description, `${operationId}.description`),
				entrypoint,
				consumer_skill_ids: operation.consumer_skill_ids as string[],
				kind: operation.kind as SemanticBackendOperation["kind"],
				timeout_ms: Number(operation.timeout_ms),
				deterministic: operation.deterministic,
				...(canonicalResourceDigest === undefined ? {} : { canonical_resource_digest: canonicalResourceDigest }),
				...(legacyResourceDigest === undefined ? {} : { legacy_resource_digest: legacyResourceDigest }),
			};
		});
		return {
			schema: "sure.semantic.backend.bundle.v1",
			bundle_id: bundleId,
			version: requiredString(bundle.version, "version"),
			description: requiredString(bundle.description, "description"),
			canonical_root: canonicalRoot,
			legacy_root: legacyRoot,
			...(canonicalTreeDigest === undefined ? {} : { canonical_tree_digest: canonicalTreeDigest }),
			...(legacyTreeDigest === undefined ? {} : { legacy_tree_digest: legacyTreeDigest }),
			operations,
		} satisfies SemanticBackendBundle;
	});
	const unsigned = { schema: "sure.semantic.backend.manifest.v1", bundles } as unknown as JsonValue;
	if (canonicalJsonDigest(unsigned) !== registryDigest) throw new SemanticBackendResolutionError(`semantic backend registry digest mismatch: ${path}`);
	return { schema: "sure.semantic.backend.manifest.v1", registry_digest: registryDigest, bundles };
}

export function loadSemanticBackendManifest(packageDir: string, options: SemanticBackendResolveOptions = {}): SemanticBackendManifest {
	let lastError: unknown;
	for (const candidate of manifestCandidates(packageDir, options)) {
		try {
			if (!existsSync(candidate)) continue;
			const parsed = parseManifest(JSON.parse(readFileSync(candidate, "utf8")) as unknown, candidate);
			if (options.expectedRegistryDigest && parsed.registry_digest !== options.expectedRegistryDigest) {
				throw new SemanticBackendResolutionError(`semantic backend registry is not the expected digest: ${parsed.registry_digest}`);
			}
			return parsed;
		} catch (error) {
			lastError = error;
			break;
		}
	}
	if (lastError instanceof Error) throw lastError;
	throw new SemanticBackendResolutionError("semantic backend manifest is not available");
}

interface Candidate {
	path: string;
	root: string;
	source: ResolvedSemanticBackend["source"];
	resourceDigest?: string;
	treeDigest?: string;
}

function regularCandidate(candidate: Candidate): boolean {
	try {
		const stat = lstatSync(candidate.path);
		if (!stat.isFile() || stat.isSymbolicLink()) return false;
		const real = realpathSync.native(candidate.path);
		const realRoot = realpathSync.native(candidate.root);
		const relativePath = relative(realRoot, real);
		return relativePath === "" || (relativePath !== ".." && !relativePath.startsWith(`..${sep}`) && !isAbsolute(relativePath));
	} catch {
		return false;
	}
}

function rootCandidate(path: string, root: string, source: Candidate["source"], resourceDigest?: string, treeDigestValue?: string): Candidate {
	return { path: resolve(path), root: resolve(root), source, resourceDigest, treeDigest: treeDigestValue };
}

/** Resolve and verify a semantic operation without exposing sibling-skill paths to callers. */
export function resolveSemanticBackendOperation(
	packageDir: string,
	operationId: string,
	options: SemanticBackendResolveOptions = {},
): ResolvedSemanticBackend {
	const manifest = loadSemanticBackendManifest(packageDir, options);
	const found = manifest.bundles.flatMap((bundle) =>
		bundle.operations
			.filter((operation) => operation.operation_id === operationId)
			.map((operation) => ({ bundle, operation })),
	)[0];
	if (!found) throw new SemanticBackendResolutionError(`semantic backend operation is not registered: ${operationId}`);
	const { bundle, operation } = found;
	const env = options.environment ?? process.env;
	const root = repositoryRootForPackage(packageDir, env);
	const canonicalSkill = bundle.canonical_root.replace(/^skills\//, "");
	const legacySkill = bundle.legacy_root.replace(/^skills\//, "");
	const candidates: Candidate[] = [];
	const backendRoot = env.SURE_SEMANTIC_BACKEND_ROOT;
	if (backendRoot) {
		const base = resolve(backendRoot);
		candidates.push(rootCandidate(join(base, bundle.bundle_id, operation.entrypoint), join(base, bundle.bundle_id), "semantic-backend-root", operation.canonical_resource_digest, bundle.canonical_tree_digest));
		candidates.push(rootCandidate(join(base, operation.entrypoint), base, "semantic-backend-root", operation.canonical_resource_digest, bundle.canonical_tree_digest));
	}
	// A portable package may carry a backend under an explicit subdirectory.
	candidates.push(rootCandidate(join(packageDir, "backends", bundle.bundle_id, operation.entrypoint), join(packageDir, "backends", bundle.bundle_id), "package", operation.canonical_resource_digest, bundle.canonical_tree_digest));
	const canonicalRoot = env.SURE_CANONICAL_SKILLS_ROOT ? resolve(env.SURE_CANONICAL_SKILLS_ROOT) : join(root, "sure", "canonical", "skills");
	candidates.push(rootCandidate(join(canonicalRoot, canonicalSkill, operation.entrypoint), join(canonicalRoot, canonicalSkill), "canonical", operation.canonical_resource_digest, bundle.canonical_tree_digest));
	const legacyRoot = env.SURE_LEGACY_SKILLS_ROOT ? resolve(env.SURE_LEGACY_SKILLS_ROOT) : join(root, "sure", "skills");
	candidates.push(rootCandidate(join(legacyRoot, legacySkill, operation.entrypoint), join(legacyRoot, legacySkill), "legacy", operation.legacy_resource_digest, bundle.legacy_tree_digest));
	for (const candidate of candidates) {
		let candidateExists = false;
		try {
			lstatSync(candidate.path);
			candidateExists = true;
		} catch {
			candidateExists = false;
		}
		if (!candidateExists) continue;
		if (!regularCandidate(candidate)) throw new SemanticBackendResolutionError(`semantic backend entrypoint is not a contained regular file: ${candidate.path}`);
		if (candidate.resourceDigest && digestFile(candidate.path) !== candidate.resourceDigest) {
			throw new SemanticBackendResolutionError(`semantic backend entrypoint digest mismatch: ${operationId}`);
		}
		if (options.expectedBundleDigest && candidate.treeDigest && candidate.treeDigest !== options.expectedBundleDigest) {
			throw new SemanticBackendResolutionError(`semantic backend bundle digest mismatch: ${operationId}`);
		}
		if (options.verifyTree !== false && candidate.treeDigest) {
			if (!existsSync(candidate.root) || treeDigest(candidate.root) !== candidate.treeDigest) {
				throw new SemanticBackendResolutionError(`semantic backend tree digest mismatch: ${operationId}`);
			}
		}
		return {
			operation_id: operation.operation_id,
			bundle_id: bundle.bundle_id,
			bundle_version: bundle.version,
			path: candidate.path,
			source: candidate.source,
			resource_digest: candidate.resourceDigest ?? digestFile(candidate.path),
			...(candidate.treeDigest === undefined ? {} : { bundle_digest: candidate.treeDigest }),
			registry_digest: manifest.registry_digest,
			timeout_ms: operation.timeout_ms,
			deterministic: operation.deterministic,
		};
	}
	throw new SemanticBackendResolutionError(`semantic backend operation is unavailable: ${operationId}`);
}
