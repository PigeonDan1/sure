import { createHash } from "node:crypto";
import { existsSync, lstatSync, readdirSync, readFileSync, realpathSync } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type {
	CapabilityRequirement,
	ExecutionArtifactMode,
	ExecutionOutputContract,
	JsonValue,
} from "../contracts/types.ts";
import { validateExecutionOutputContract } from "../execution/output-contract.ts";

export interface SemanticBackendOperation {
	operation_id: string;
	description: string;
	entrypoint: string;
	consumer_skill_ids: readonly string[];
	kind: "execute" | "validate" | "resolve";
	timeout_ms: number;
	deterministic: boolean;
	requires_policy_snapshot?: boolean;
	/** Relationship between an execute operation and its gate artifact. */
	artifact_mode?: ExecutionArtifactMode;
	/** Declarative output boundary for producer/mutating execution adapters. */
	output_contract?: ExecutionOutputContract;
	/** Static execution capabilities; input-dependent capabilities stay in the adapter. */
	capability_requirements?: readonly CapabilityRequirement[];
	canonical_resource_digest?: string;
	legacy_resource_digest?: string;
}

export type SemanticBackendRootKind = "skill" | "repository";

export interface SemanticBackendBundle {
	schema: "sure.semantic.backend.bundle.v1";
	bundle_id: string;
	version: string;
	description: string;
	canonical_root: string;
	canonical_root_kind?: SemanticBackendRootKind;
	legacy_root: string;
	legacy_root_kind?: SemanticBackendRootKind;
	integrity_root?: string;
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
	bundle_root: string;
	integrity_root: string;
	source: "package" | "semantic-backend-root" | "canonical" | "legacy";
	resource_digest: string;
	bundle_digest?: string;
	registry_digest: string;
	timeout_ms: number;
	deterministic: boolean;
	requires_policy_snapshot: boolean;
	artifact_mode?: SemanticBackendOperation["artifact_mode"];
	output_contract?: ExecutionOutputContract;
	capability_requirements?: readonly CapabilityRequirement[];
	kind: SemanticBackendOperation["kind"];
	consumer_skill_ids: readonly string[];
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
	if (typeof value !== "string" || value.trim() === "")
		throw new SemanticBackendResolutionError(`${field} must be a non-empty string`);
	return value;
}

function relativeResource(value: string, field: string): string {
	const normalized = value.replaceAll("\\", "/");
	if (!normalized || isAbsolute(normalized) || normalized.split("/").includes("..")) {
		throw new SemanticBackendResolutionError(`${field} must be a relative non-escaping path: ${value}`);
	}
	return normalized;
}

function backendRootKind(value: unknown, field: string): SemanticBackendRootKind | undefined {
	if (value === undefined) return undefined;
	if (value !== "skill" && value !== "repository") {
		throw new SemanticBackendResolutionError(`${field} must be skill or repository`);
	}
	return value;
}

function resourceWithin(path: string, root: string): boolean {
	return root === "." || path === root || path.startsWith(`${root}/`);
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
	const rows = treeFiles(root)
		.map((path) => `${path}\0${digestFile(join(root, path))}`)
		.join("\n");
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
	if (!/^sha256:[0-9a-f]{64}$/.test(registryDigest))
		throw new SemanticBackendResolutionError("registry_digest is invalid");
	if (!Array.isArray(root.bundles) || root.bundles.length === 0)
		throw new SemanticBackendResolutionError("backend manifest has no bundles");
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
		if (schema !== "sure.semantic.backend.bundle.v1")
			throw new SemanticBackendResolutionError(`unsupported backend bundle schema: ${schema}`);
		const canonicalRoot = relativeResource(requiredString(bundle.canonical_root, "canonical_root"), "canonical_root");
		const legacyRoot = relativeResource(requiredString(bundle.legacy_root, "legacy_root"), "legacy_root");
		const canonicalRootKind = backendRootKind(bundle.canonical_root_kind, "canonical_root_kind");
		const legacyRootKind = backendRootKind(bundle.legacy_root_kind, "legacy_root_kind");
		const integrityRoot =
			bundle.integrity_root === undefined
				? undefined
				: relativeResource(requiredString(bundle.integrity_root, "integrity_root"), "integrity_root");
		const bundleId = requiredString(bundle.bundle_id, "bundle_id");
		if (bundleIds.has(bundleId)) throw new SemanticBackendResolutionError(`duplicate backend bundle: ${bundleId}`);
		bundleIds.add(bundleId);
		const canonicalTreeDigest = digest(bundle.canonical_tree_digest, `bundles[${bundleIndex}].canonical_tree_digest`);
		const legacyTreeDigest = digest(bundle.legacy_tree_digest, `bundles[${bundleIndex}].legacy_tree_digest`);
		if (!Array.isArray(bundle.operations) || bundle.operations.length === 0)
			throw new SemanticBackendResolutionError(`bundles[${bundleIndex}] has no operations`);
		const operations = bundle.operations.map((rawOperation, operationIndex) => {
			const operation = record(rawOperation);
			if (!operation) throw new SemanticBackendResolutionError(`operations[${operationIndex}] must be an object`);
			const operationId = requiredString(operation.operation_id, "operation_id");
			if (operationIds.has(operationId))
				throw new SemanticBackendResolutionError(`duplicate backend operation: ${operationId}`);
			operationIds.add(operationId);
			const entrypoint = relativeResource(requiredString(operation.entrypoint, "entrypoint"), "entrypoint");
			if (integrityRoot !== undefined && !resourceWithin(entrypoint, integrityRoot)) {
				throw new SemanticBackendResolutionError(`${operationId}.entrypoint is outside integrity_root`);
			}
			if (
				!Array.isArray(operation.consumer_skill_ids) ||
				operation.consumer_skill_ids.some((id) => typeof id !== "string")
			) {
				throw new SemanticBackendResolutionError(`${operationId}.consumer_skill_ids must be a string array`);
			}
			if (!["execute", "validate", "resolve"].includes(String(operation.kind)))
				throw new SemanticBackendResolutionError(`${operationId}.kind is invalid`);
			if (!Number.isSafeInteger(operation.timeout_ms) || Number(operation.timeout_ms) <= 0)
				throw new SemanticBackendResolutionError(`${operationId}.timeout_ms is invalid`);
			if (typeof operation.deterministic !== "boolean")
				throw new SemanticBackendResolutionError(`${operationId}.deterministic is invalid`);
			if (
				operation.requires_policy_snapshot !== undefined &&
				typeof operation.requires_policy_snapshot !== "boolean"
			) {
				throw new SemanticBackendResolutionError(`${operationId}.requires_policy_snapshot is invalid`);
			}
			if (
				operation.artifact_mode !== undefined &&
				!["preexisting", "mutating", "producing"].includes(String(operation.artifact_mode))
			) {
				throw new SemanticBackendResolutionError(`${operationId}.artifact_mode is invalid`);
			}
			if (operation.artifact_mode !== undefined && operation.kind !== "execute") {
				throw new SemanticBackendResolutionError(
					`${operationId}.artifact_mode is only valid for execute operations`,
				);
			}
			let outputContract: ExecutionOutputContract | undefined;
			if (operation.output_contract !== undefined) {
				const rawOutputContract = record(operation.output_contract);
				if (!rawOutputContract) {
					throw new SemanticBackendResolutionError(`${operationId}.output_contract must be an object`);
				}
				const contractValidation = validateExecutionOutputContract(rawOutputContract);
				if (!contractValidation.valid) {
					throw new SemanticBackendResolutionError(
						`${operationId}.output_contract is invalid: ${contractValidation.errors.join("; ")}`,
					);
				}
				if (operation.kind !== "execute") {
					throw new SemanticBackendResolutionError(
						`${operationId}.output_contract is only valid for execute operations`,
					);
				}
				if (operation.artifact_mode !== undefined && rawOutputContract.mode !== operation.artifact_mode) {
					throw new SemanticBackendResolutionError(`${operationId}.output_contract.mode must match artifact_mode`);
				}
				outputContract = rawOutputContract as unknown as ExecutionOutputContract;
			}
			let capabilityRequirements: readonly CapabilityRequirement[] | undefined;
			if (operation.capability_requirements !== undefined) {
				if (!Array.isArray(operation.capability_requirements)) {
					throw new SemanticBackendResolutionError(`${operationId}.capability_requirements must be an array`);
				}
				for (const [index, rawRequirement] of operation.capability_requirements.entries()) {
					const requirement = record(rawRequirement);
					if (
						!requirement ||
						typeof requirement.capability_id !== "string" ||
						!/^sure\.[a-z0-9][a-z0-9.-]*$/.test(requirement.capability_id) ||
						requirement.capability_class !== "execution_capability" ||
						typeof requirement.required !== "boolean"
					) {
						throw new SemanticBackendResolutionError(
							`${operationId}.capability_requirements[${index}] is invalid`,
						);
					}
				}
				capabilityRequirements = operation.capability_requirements as CapabilityRequirement[];
			}
			const canonicalResourceDigest = digest(
				operation.canonical_resource_digest,
				`${operationId}.canonical_resource_digest`,
			);
			const legacyResourceDigest = digest(operation.legacy_resource_digest, `${operationId}.legacy_resource_digest`);
			return {
				operation_id: operationId,
				description: requiredString(operation.description, `${operationId}.description`),
				entrypoint,
				consumer_skill_ids: operation.consumer_skill_ids as string[],
				kind: operation.kind as SemanticBackendOperation["kind"],
				timeout_ms: Number(operation.timeout_ms),
				deterministic: operation.deterministic,
				...(operation.requires_policy_snapshot === undefined
					? {}
					: { requires_policy_snapshot: operation.requires_policy_snapshot }),
				...(operation.artifact_mode === undefined
					? {}
					: { artifact_mode: operation.artifact_mode as "preexisting" | "mutating" | "producing" }),
				...(outputContract === undefined ? {} : { output_contract: outputContract }),
				...(capabilityRequirements === undefined ? {} : { capability_requirements: capabilityRequirements }),
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
			...(canonicalRootKind === undefined ? {} : { canonical_root_kind: canonicalRootKind }),
			legacy_root: legacyRoot,
			...(legacyRootKind === undefined ? {} : { legacy_root_kind: legacyRootKind }),
			...(integrityRoot === undefined ? {} : { integrity_root: integrityRoot }),
			...(canonicalTreeDigest === undefined ? {} : { canonical_tree_digest: canonicalTreeDigest }),
			...(legacyTreeDigest === undefined ? {} : { legacy_tree_digest: legacyTreeDigest }),
			operations,
		} satisfies SemanticBackendBundle;
	});
	const unsigned = { schema: "sure.semantic.backend.manifest.v1", bundles } as unknown as JsonValue;
	if (canonicalJsonDigest(unsigned) !== registryDigest)
		throw new SemanticBackendResolutionError(`semantic backend registry digest mismatch: ${path}`);
	return { schema: "sure.semantic.backend.manifest.v1", registry_digest: registryDigest, bundles };
}

export function loadSemanticBackendManifest(
	packageDir: string,
	options: SemanticBackendResolveOptions = {},
): SemanticBackendManifest {
	let lastError: unknown;
	for (const candidate of manifestCandidates(packageDir, options)) {
		try {
			if (!existsSync(candidate)) continue;
			const parsed = parseManifest(JSON.parse(readFileSync(candidate, "utf8")) as unknown, candidate);
			if (options.expectedRegistryDigest && parsed.registry_digest !== options.expectedRegistryDigest) {
				throw new SemanticBackendResolutionError(
					`semantic backend registry is not the expected digest: ${parsed.registry_digest}`,
				);
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
	integrityRoot: string;
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
		return (
			relativePath === "" ||
			(relativePath !== ".." && !relativePath.startsWith(`..${sep}`) && !isAbsolute(relativePath))
		);
	} catch {
		return false;
	}
}

function rootCandidate(
	path: string,
	root: string,
	source: Candidate["source"],
	resourceDigest?: string,
	treeDigestValue?: string,
	integrityRoot = ".",
): Candidate {
	return {
		path: resolve(path),
		root: resolve(root),
		integrityRoot,
		source,
		resourceDigest,
		treeDigest: treeDigestValue,
	};
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
	const integrityRoot = bundle.integrity_root ?? ".";
	const candidates: Candidate[] = [];
	const backendRoot = env.SURE_SEMANTIC_BACKEND_ROOT;
	if (backendRoot) {
		const base = resolve(backendRoot);
		candidates.push(
			rootCandidate(
				join(base, bundle.bundle_id, operation.entrypoint),
				join(base, bundle.bundle_id),
				"semantic-backend-root",
				operation.canonical_resource_digest,
				bundle.canonical_tree_digest,
				integrityRoot,
			),
		);
		candidates.push(
			rootCandidate(
				join(base, operation.entrypoint),
				base,
				"semantic-backend-root",
				operation.canonical_resource_digest,
				bundle.canonical_tree_digest,
				integrityRoot,
			),
		);
	}
	// A portable package may carry a backend under an explicit subdirectory.
	candidates.push(
		rootCandidate(
			join(packageDir, "backends", bundle.bundle_id, operation.entrypoint),
			join(packageDir, "backends", bundle.bundle_id),
			"package",
			operation.canonical_resource_digest,
			bundle.canonical_tree_digest,
			integrityRoot,
		),
	);
	const canonicalRoot =
		bundle.canonical_root_kind === "repository"
			? join(root, bundle.canonical_root)
			: join(
					env.SURE_CANONICAL_SKILLS_ROOT
						? resolve(env.SURE_CANONICAL_SKILLS_ROOT)
						: join(root, "sure", "canonical", "skills"),
					bundle.canonical_root.replace(/^skills\//, ""),
				);
	candidates.push(
		rootCandidate(
			join(canonicalRoot, operation.entrypoint),
			canonicalRoot,
			"canonical",
			operation.canonical_resource_digest,
			bundle.canonical_tree_digest,
			integrityRoot,
		),
	);
	const legacyRoot =
		bundle.legacy_root_kind === "repository"
			? join(root, bundle.legacy_root)
			: join(
					env.SURE_LEGACY_SKILLS_ROOT ? resolve(env.SURE_LEGACY_SKILLS_ROOT) : join(root, "sure", "skills"),
					bundle.legacy_root.replace(/^skills\//, ""),
				);
	candidates.push(
		rootCandidate(
			join(legacyRoot, operation.entrypoint),
			legacyRoot,
			"legacy",
			operation.legacy_resource_digest,
			bundle.legacy_tree_digest,
			integrityRoot,
		),
	);
	for (const candidate of candidates) {
		let candidateExists = false;
		try {
			lstatSync(candidate.path);
			candidateExists = true;
		} catch {
			candidateExists = false;
		}
		if (!candidateExists) continue;
		if (!regularCandidate(candidate))
			throw new SemanticBackendResolutionError(
				`semantic backend entrypoint is not a contained regular file: ${candidate.path}`,
			);
		if (candidate.resourceDigest && digestFile(candidate.path) !== candidate.resourceDigest) {
			throw new SemanticBackendResolutionError(`semantic backend entrypoint digest mismatch: ${operationId}`);
		}
		if (
			options.expectedBundleDigest &&
			candidate.treeDigest &&
			candidate.treeDigest !== options.expectedBundleDigest
		) {
			throw new SemanticBackendResolutionError(`semantic backend bundle digest mismatch: ${operationId}`);
		}
		if (options.verifyTree !== false && candidate.treeDigest) {
			const integrityPath = join(candidate.root, candidate.integrityRoot);
			if (!existsSync(integrityPath) || treeDigest(integrityPath) !== candidate.treeDigest) {
				throw new SemanticBackendResolutionError(`semantic backend tree digest mismatch: ${operationId}`);
			}
		}
		return {
			operation_id: operation.operation_id,
			bundle_id: bundle.bundle_id,
			bundle_version: bundle.version,
			path: candidate.path,
			bundle_root: candidate.root,
			integrity_root: candidate.integrityRoot,
			source: candidate.source,
			resource_digest: candidate.resourceDigest ?? digestFile(candidate.path),
			...(candidate.treeDigest === undefined ? {} : { bundle_digest: candidate.treeDigest }),
			registry_digest: manifest.registry_digest,
			timeout_ms: operation.timeout_ms,
			deterministic: operation.deterministic,
			requires_policy_snapshot: operation.requires_policy_snapshot ?? false,
			...(operation.artifact_mode === undefined ? {} : { artifact_mode: operation.artifact_mode }),
			...(operation.output_contract === undefined ? {} : { output_contract: operation.output_contract }),
			...(operation.capability_requirements === undefined
				? {}
				: { capability_requirements: operation.capability_requirements }),
			kind: operation.kind,
			consumer_skill_ids: [...operation.consumer_skill_ids],
		};
	}
	throw new SemanticBackendResolutionError(`semantic backend operation is unavailable: ${operationId}`);
}
