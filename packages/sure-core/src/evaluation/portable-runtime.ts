import { createHash } from "node:crypto";
import { lstatSync, readdirSync, readFileSync, realpathSync } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type { JsonValue } from "../contracts/types.ts";
import { loadSemanticBackendManifest, resolveSemanticBackendOperation } from "./semantic-backend.ts";

const DIGEST_PATTERN = /^sha256:[0-9a-f]{64}$/;
const OPERATION_PATTERN = /^sure\.[a-z0-9][a-z0-9._-]+$/;
const RUNTIME_LOCK = "runtime-support.lock.json";

export interface PortableRuntimeFileLock {
	path: string;
	size_bytes: number;
	sha256: string;
}

export interface PortableRuntimeLock {
	schema: "sure.portable.runtime.lock.v1";
	runtime_version: "portable-v1";
	core_package_version: string;
	semantic_backend_registry_digest: string;
	executor_registry_digest: string;
	operation_ids: readonly string[];
	files: readonly PortableRuntimeFileLock[];
	runtime_digest: string;
}

export interface PortableRuntimeVerificationOptions {
	expected_runtime_digest?: string;
	expected_core_package_version?: string;
	expected_semantic_backend_registry_digest?: string;
	expected_executor_registry_digest?: string;
}

export interface VerifiedPortableRuntime {
	root: string;
	lock_path: string;
	lock: PortableRuntimeLock;
	verified_file_count: number;
}

export class PortableRuntimeVerificationError extends Error {
	readonly code = "SURE_PORTABLE_RUNTIME_INVALID";
}

function invalid(message: string): never {
	throw new PortableRuntimeVerificationError(message);
}

function record(value: unknown, label: string): Record<string, unknown> {
	if (typeof value !== "object" || value === null || Array.isArray(value)) invalid(`${label} must be an object`);
	return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[], label: string): void {
	const actual = Object.keys(value).sort();
	const canonical = [...expected].sort();
	if (actual.length !== canonical.length || actual.some((key, index) => key !== canonical[index])) {
		invalid(`${label} has unexpected or missing fields`);
	}
}

function stringValue(value: unknown, label: string): string {
	if (typeof value !== "string" || value.length === 0) invalid(`${label} must be a non-empty string`);
	return value;
}

function digestValue(value: unknown, label: string): string {
	const digest = stringValue(value, label);
	if (!DIGEST_PATTERN.test(digest)) invalid(`${label} must be a canonical SHA-256 digest`);
	return digest;
}

function runtimePath(value: unknown, label: string): string {
	const path = stringValue(value, label);
	const segments = path.split("/");
	if (
		isAbsolute(path) ||
		path.includes("\\") ||
		!/^[A-Za-z0-9._/-]+$/.test(path) ||
		segments.some((segment) => segment === "" || segment === "." || segment === "..")
	) {
		invalid(`${label} must be a normalized, non-escaping POSIX path`);
	}
	return path;
}

function parseRuntimeLock(value: unknown): PortableRuntimeLock {
	const raw = record(value, "portable runtime lock");
	exactKeys(
		raw,
		[
			"schema",
			"runtime_version",
			"core_package_version",
			"semantic_backend_registry_digest",
			"executor_registry_digest",
			"operation_ids",
			"files",
			"runtime_digest",
		],
		"portable runtime lock",
	);
	if (raw.schema !== "sure.portable.runtime.lock.v1") invalid("unsupported portable runtime lock schema");
	if (raw.runtime_version !== "portable-v1") invalid("unsupported portable runtime version");
	const corePackageVersion = stringValue(raw.core_package_version, "core_package_version");
	if (!/^[0-9]+\.[0-9]+\.[0-9]+$/.test(corePackageVersion)) invalid("core_package_version is invalid");
	if (!Array.isArray(raw.operation_ids) || raw.operation_ids.length === 0) invalid("operation_ids must not be empty");
	const operationIds = raw.operation_ids.map((value, index) => {
		const id = stringValue(value, `operation_ids[${index}]`);
		if (!OPERATION_PATTERN.test(id)) invalid(`operation_ids[${index}] is invalid`);
		return id;
	});
	if (new Set(operationIds).size !== operationIds.length) invalid("operation_ids contains duplicates");
	if (operationIds.some((id, index) => id !== [...operationIds].sort()[index])) {
		invalid("operation_ids must use canonical lexical ordering");
	}
	if (!Array.isArray(raw.files) || raw.files.length === 0) invalid("files must not be empty");
	const files = raw.files.map((value, index) => {
		const file = record(value, `files[${index}]`);
		exactKeys(file, ["path", "size_bytes", "sha256"], `files[${index}]`);
		const path = runtimePath(file.path, `files[${index}].path`);
		if (path === RUNTIME_LOCK) invalid(`${RUNTIME_LOCK} cannot contain a digest of itself`);
		if (!Number.isSafeInteger(file.size_bytes) || Number(file.size_bytes) < 0) {
			invalid(`files[${index}].size_bytes is invalid`);
		}
		return {
			path,
			size_bytes: Number(file.size_bytes),
			sha256: digestValue(file.sha256, `files[${index}].sha256`),
		};
	});
	if (new Set(files.map((file) => file.path)).size !== files.length) invalid("files contains duplicate paths");
	const sortedPaths = files.map((file) => file.path).sort();
	if (files.some((file, index) => file.path !== sortedPaths[index]))
		invalid("files must use canonical lexical ordering");
	const semanticBackendRegistryDigest = digestValue(
		raw.semantic_backend_registry_digest,
		"semantic_backend_registry_digest",
	);
	const executorRegistryDigest = digestValue(raw.executor_registry_digest, "executor_registry_digest");
	const runtimeDigest = digestValue(raw.runtime_digest, "runtime_digest");
	const unsigned = {
		schema: "sure.portable.runtime.lock.v1" as const,
		runtime_version: "portable-v1" as const,
		core_package_version: corePackageVersion,
		semantic_backend_registry_digest: semanticBackendRegistryDigest,
		executor_registry_digest: executorRegistryDigest,
		operation_ids: operationIds,
		files,
	};
	if (canonicalJsonDigest(unsigned as unknown as JsonValue) !== runtimeDigest) invalid("runtime_digest mismatch");
	return { ...unsigned, runtime_digest: runtimeDigest };
}

function digestBytes(value: Uint8Array): string {
	return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function contained(root: string, candidate: string): boolean {
	const path = relative(root, candidate);
	return path === "" || (path !== ".." && !path.startsWith(`..${sep}`) && !isAbsolute(path));
}

function expectedDirectories(paths: readonly string[]): Set<string> {
	const directories = new Set<string>([""]);
	for (const path of paths) {
		let directory = dirname(path).replaceAll("\\", "/");
		while (directory !== "." && directory !== "") {
			directories.add(directory);
			directory = dirname(directory).replaceAll("\\", "/");
		}
	}
	return directories;
}

function runtimeFiles(root: string, expectedDirs: ReadonlySet<string>, prefix = ""): string[] {
	const files: string[] = [];
	for (const entry of readdirSync(join(root, prefix), { withFileTypes: true }).sort((left, right) =>
		left.name.localeCompare(right.name),
	)) {
		const path = prefix ? `${prefix}/${entry.name}` : entry.name;
		if (entry.isSymbolicLink()) invalid(`portable runtime contains a symlink: ${path}`);
		if (entry.isDirectory()) {
			if (!expectedDirs.has(path)) invalid(`portable runtime contains an unexpected directory: ${path}`);
			files.push(...runtimeFiles(root, expectedDirs, path));
		} else if (entry.isFile()) {
			files.push(path);
		} else {
			invalid(`portable runtime contains a non-regular entry: ${path}`);
		}
	}
	return files;
}

function parseJsonBytes(value: Uint8Array, label: string): unknown {
	try {
		return JSON.parse(Buffer.from(value).toString("utf8")) as unknown;
	} catch {
		return invalid(`${label} is not valid JSON`);
	}
}

function verifyExecutorRegistry(value: Uint8Array, expectedDigest: string): void {
	const registry = record(parseJsonBytes(value, "executor registry"), "executor registry");
	exactKeys(registry, ["schema", "registry_digest", "executors"], "executor registry");
	if (registry.schema !== "sure.executor.registry.v1" || !Array.isArray(registry.executors)) {
		invalid("executor registry has an invalid contract");
	}
	const registryDigest = digestValue(registry.registry_digest, "executor registry digest");
	if (registryDigest !== expectedDigest) invalid("executor registry does not match the runtime lock");
	const unsigned = { schema: registry.schema, executors: registry.executors };
	if (canonicalJsonDigest(unsigned as unknown as JsonValue) !== registryDigest) {
		invalid("executor registry digest mismatch");
	}
}

/** Verify every executable byte and registry binding before a host runs portable SURE code. */
export function verifyPortableRuntime(
	runtimeRoot: string,
	options: PortableRuntimeVerificationOptions = {},
): VerifiedPortableRuntime {
	try {
		const root = resolve(runtimeRoot);
		const rootStat = lstatSync(root);
		if (!rootStat.isDirectory() || rootStat.isSymbolicLink())
			invalid(`portable runtime root is not a directory: ${root}`);
		const realRoot = realpathSync.native(root);
		const lockPath = join(root, RUNTIME_LOCK);
		const lockStat = lstatSync(lockPath);
		if (!lockStat.isFile() || lockStat.isSymbolicLink())
			invalid(`portable runtime lock is not a regular file: ${lockPath}`);
		const lock = parseRuntimeLock(parseJsonBytes(readFileSync(lockPath), "portable runtime lock"));
		if (options.expected_runtime_digest && lock.runtime_digest !== options.expected_runtime_digest) {
			invalid("portable runtime is not the expected distribution digest");
		}
		if (
			options.expected_core_package_version &&
			lock.core_package_version !== options.expected_core_package_version
		) {
			invalid("portable runtime core package version mismatch");
		}
		if (
			options.expected_semantic_backend_registry_digest &&
			lock.semantic_backend_registry_digest !== options.expected_semantic_backend_registry_digest
		) {
			invalid("portable runtime semantic backend registry mismatch");
		}
		if (
			options.expected_executor_registry_digest &&
			lock.executor_registry_digest !== options.expected_executor_registry_digest
		) {
			invalid("portable runtime executor registry mismatch");
		}
		const expectedPaths = [...lock.files.map((file) => file.path), RUNTIME_LOCK].sort();
		const actualPaths = runtimeFiles(root, expectedDirectories(expectedPaths)).sort();
		if (
			actualPaths.length !== expectedPaths.length ||
			actualPaths.some((path, index) => path !== expectedPaths[index])
		) {
			invalid("portable runtime file set does not match its lock");
		}
		const contents = new Map<string, Buffer>();
		for (const file of lock.files) {
			const path = join(root, file.path);
			const stat = lstatSync(path);
			if (!stat.isFile() || stat.isSymbolicLink()) invalid(`portable runtime file is not regular: ${file.path}`);
			const resolvedPath = realpathSync.native(path);
			if (!contained(realRoot, resolvedPath)) invalid(`portable runtime file escapes its root: ${file.path}`);
			const content = readFileSync(path);
			if (content.byteLength !== file.size_bytes || digestBytes(content) !== file.sha256) {
				invalid(`portable runtime file digest mismatch: ${file.path}`);
			}
			contents.set(file.path, content);
		}
		const executorRegistry = contents.get("executor-registry.json");
		if (executorRegistry === undefined) invalid("portable runtime has no executor registry");
		verifyExecutorRegistry(executorRegistry, lock.executor_registry_digest);
		const manifestPath = join(root, "semantic-backends.json");
		const manifest = loadSemanticBackendManifest(root, {
			manifestPath,
			expectedRegistryDigest: lock.semantic_backend_registry_digest,
			environment: { SURE_REPOSITORY_ROOT: root },
		});
		const manifestOperationIds = manifest.bundles
			.flatMap((bundle) => bundle.operations.map((operation) => operation.operation_id))
			.sort();
		if (
			manifestOperationIds.length !== lock.operation_ids.length ||
			manifestOperationIds.some((id, index) => id !== lock.operation_ids[index])
		) {
			invalid("portable runtime operation set does not match its lock");
		}
		for (const bundle of manifest.bundles) {
			if (!bundle.canonical_tree_digest) invalid(`portable backend ${bundle.bundle_id} has no tree digest`);
			const firstOperation = bundle.operations[0];
			if (firstOperation === undefined) invalid(`portable backend ${bundle.bundle_id} has no operations`);
			resolveSemanticBackendOperation(root, firstOperation.operation_id, {
				manifestPath,
				expectedRegistryDigest: lock.semantic_backend_registry_digest,
				expectedBundleDigest: bundle.canonical_tree_digest,
				environment: {
					SURE_REPOSITORY_ROOT: root,
					SURE_SEMANTIC_BACKEND_ROOT: join(root, "backends"),
				},
			});
			for (const operation of bundle.operations) {
				if (!operation.canonical_resource_digest) {
					invalid(`portable operation ${operation.operation_id} has no resource digest`);
				}
				const entrypoint = `backends/${bundle.bundle_id}/${operation.entrypoint}`;
				const locked = lock.files.find((file) => file.path === entrypoint);
				if (!locked || locked.sha256 !== operation.canonical_resource_digest) {
					invalid(`portable operation ${operation.operation_id} is not bound to its locked entrypoint`);
				}
			}
		}
		return { root, lock_path: lockPath, lock, verified_file_count: lock.files.length };
	} catch (error) {
		if (error instanceof PortableRuntimeVerificationError) throw error;
		throw new PortableRuntimeVerificationError(error instanceof Error ? error.message : String(error));
	}
}
