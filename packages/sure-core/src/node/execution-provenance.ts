import { createHash, randomUUID } from "node:crypto";
import {
	closeSync,
	constants,
	existsSync,
	fstatSync,
	fsyncSync,
	linkSync,
	lstatSync,
	mkdirSync,
	openSync,
	readFileSync,
	realpathSync,
	renameSync,
	unlinkSync,
	writeFileSync,
} from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import lockfile from "proper-lockfile";
import { evaluatePathBoundary, type ResolvedRoot } from "../contracts/path-boundary.ts";
import type { ExecutionProvenanceDocumentKey, ExecutionProvenancePublicationPort } from "../execution/provenance.ts";

const DIRECTORY_OPEN_FLAG = process.platform === "win32" ? 0 : (constants.O_DIRECTORY ?? 0);

export interface NodeExecutionProvenancePortOptions {
	root: string;
	allowed_roots: readonly string[];
	forbidden_roots?: readonly string[];
	/** Optional compatibility receipt filename inside root; all other latest names remain canonical. */
	latest_receipt_path?: string;
}

function contained(root: string, candidate: string): boolean {
	const relation = relative(root, candidate);
	return relation === "" || (relation !== ".." && !relation.startsWith(`..${sep}`) && !isAbsolute(relation));
}

function resolvedWithExistingParent(path: string): string {
	const lexical = resolve(path);
	let cursor = lexical;
	while (!existsSync(cursor)) {
		const parent = dirname(cursor);
		if (parent === cursor) return lexical;
		cursor = parent;
	}
	return resolve(realpathSync.native(cursor), relative(cursor, lexical));
}

function pathIdentity(path: string): ResolvedRoot {
	const lexical = resolve(path);
	return { path: lexical, resolved_path: resolvedWithExistingParent(lexical) };
}

function isMissing(error: unknown): boolean {
	return typeof error === "object" && error !== null && "code" in error && error.code === "ENOENT";
}

function isAlreadyExists(error: unknown): boolean {
	return typeof error === "object" && error !== null && "code" in error && error.code === "EEXIST";
}

function isUnsupportedDirectorySync(error: unknown): boolean {
	return (
		typeof error === "object" &&
		error !== null &&
		"code" in error &&
		(error.code === "EINVAL" || error.code === "ENOTSUP" || error.code === "EBADF" || error.code === "EPERM")
	);
}

function syncDirectory(path: string): void {
	let descriptor: number | undefined;
	try {
		descriptor = openSync(path, constants.O_RDONLY | DIRECTORY_OPEN_FLAG);
		fsyncSync(descriptor);
	} catch (error) {
		if (!isUnsupportedDirectorySync(error)) throw error;
	} finally {
		if (descriptor !== undefined) closeSync(descriptor);
	}
}

function writeDurableFile(path: string, content: string, exclusive: boolean): void {
	const descriptor = openSync(
		path,
		constants.O_WRONLY | constants.O_CREAT | (exclusive ? constants.O_EXCL : 0),
		0o666,
	);
	try {
		writeFileSync(descriptor, content, { encoding: "utf8" });
		fsyncSync(descriptor);
	} finally {
		closeSync(descriptor);
	}
}

function regularFileBytes(path: string): Buffer | undefined {
	let descriptor: number | undefined;
	try {
		descriptor = openSync(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
		if (!fstatSync(descriptor).isFile()) throw new Error(`execution provenance path is not a regular file: ${path}`);
		return readFileSync(descriptor);
	} catch (error) {
		if (isMissing(error)) return undefined;
		throw error;
	} finally {
		if (descriptor !== undefined) closeSync(descriptor);
	}
}

/** Shared Node storage adapter used by both surectl and the Pi compatibility host. */
export class NodeExecutionProvenancePublicationPort implements ExecutionProvenancePublicationPort {
	readonly root: string;
	private readonly resolvedRoot: string;
	private readonly latestReceiptPath: string | undefined;
	readonly lock = {
		withLock: <T>(key: string, operation: () => T): T => {
			const lockTarget = join(this.root, `.sure-provenance-${createHash("sha256").update(key).digest("hex")}`);
			const release = lockfile.lockSync(lockTarget, {
				realpath: false,
				lockfilePath: `${lockTarget}.lock`,
			});
			try {
				return operation();
			} finally {
				release();
			}
		},
	};

	constructor(options: NodeExecutionProvenancePortOptions) {
		if (!isAbsolute(options.root)) throw new Error("execution provenance root must be absolute");
		if (options.allowed_roots.length === 0)
			throw new Error("execution provenance requires an explicit writable root");
		const lexicalRoot = resolve(options.root);
		const allowedRoots = options.allowed_roots.map((root) => resolve(root));
		const forbiddenRoots = (options.forbidden_roots ?? []).map((root) => resolve(root));
		if (!allowedRoots.some((root) => contained(root, lexicalRoot))) {
			throw new Error("execution provenance root is outside the admitted writable roots");
		}
		if (forbiddenRoots.some((root) => contained(root, lexicalRoot))) {
			throw new Error("execution provenance root is beneath a read-only reference root");
		}
		const identities = {
			allowed: allowedRoots.map(pathIdentity),
			forbidden: forbiddenRoots.map(pathIdentity),
		};
		const preflight = evaluatePathBoundary({
			candidate_path: lexicalRoot,
			candidate_resolved_path: resolvedWithExistingParent(lexicalRoot),
			allowed_roots: identities.allowed,
			forbidden_roots: identities.forbidden,
		});
		if (!preflight.admitted) {
			throw new Error(
				`execution provenance root failed path admission before creation: ${preflight.reason_code ?? "INVALID_CONTRACT"}`,
			);
		}
		mkdirSync(lexicalRoot, { recursive: true });
		const stat = lstatSync(lexicalRoot);
		if (!stat.isDirectory() || stat.isSymbolicLink()) {
			throw new Error("execution provenance root must be a regular directory");
		}
		const boundary = evaluatePathBoundary({
			candidate_path: lexicalRoot,
			candidate_resolved_path: resolve(realpathSync.native(lexicalRoot)),
			allowed_roots: identities.allowed,
			forbidden_roots: identities.forbidden,
		});
		if (!boundary.admitted) {
			throw new Error(
				`execution provenance root failed path admission: ${boundary.reason_code ?? "INVALID_CONTRACT"}`,
			);
		}
		this.root = lexicalRoot;
		this.resolvedRoot = resolve(realpathSync.native(lexicalRoot));
		this.latestReceiptPath = (() => {
			if (options.latest_receipt_path === undefined) return undefined;
			if (!isAbsolute(options.latest_receipt_path)) {
				throw new Error("execution provenance latest receipt location must be absolute");
			}
			const location = resolve(options.latest_receipt_path);
			if (dirname(location) !== this.root) {
				throw new Error("execution provenance latest receipt location must be inside its publication root");
			}
			this.assertPublicationParent(location);
			return location;
		})();
		mkdirSync(join(this.root, "execution_contracts"), { recursive: true });
		this.assertPublicationParent(join(this.root, "execution_contracts", "placeholder"));
	}

	location(key: ExecutionProvenanceDocumentKey): string {
		if (key.view === "latest" && key.document === "receipt" && this.latestReceiptPath !== undefined)
			return this.latestReceiptPath;
		if (key.view === "latest") return join(this.root, `execution_${key.document}.json`);
		return join(this.root, "execution_contracts", `${key.request_id}.${key.document}.json`);
	}

	read(key: ExecutionProvenanceDocumentKey): string | undefined {
		return regularFileBytes(this.location(key))?.toString("utf8");
	}

	fileType(key: ExecutionProvenanceDocumentKey): "missing" | "file" | "directory" | "symlink" | "other" {
		try {
			const stat = lstatSync(this.location(key));
			if (stat.isSymbolicLink()) return "symlink";
			if (stat.isFile()) return "file";
			if (stat.isDirectory()) return "directory";
			return "other";
		} catch (error) {
			if (isMissing(error)) return "missing";
			throw error;
		}
	}

	digest(key: ExecutionProvenanceDocumentKey): string | undefined {
		const bytes = regularFileBytes(this.location(key));
		return bytes === undefined ? undefined : `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
	}

	writeLatest(key: ExecutionProvenanceDocumentKey, content: string): void {
		if (key.view !== "latest") throw new Error("writeLatest requires a latest provenance key");
		const path = this.location(key);
		this.assertPublicationParent(path);
		const existingType = this.fileType(key);
		if (existingType !== "missing" && existingType !== "file") {
			throw new Error(`refusing to replace non-regular latest execution provenance: ${path}`);
		}
		const temporary = `${path}.sure-tmp-${process.pid}-${randomUUID()}`;
		try {
			writeDurableFile(temporary, content, true);
			renameSync(temporary, path);
			syncDirectory(dirname(path));
		} catch (error) {
			try {
				if (existsSync(temporary)) unlinkSync(temporary);
			} catch {
				// Preserve the publication error.
			}
			throw error;
		}
	}

	writeImmutable(key: ExecutionProvenanceDocumentKey, content: string): void {
		if (key.view !== "immutable") throw new Error("writeImmutable requires an immutable provenance key");
		const path = this.location(key);
		this.assertPublicationParent(path);
		const existingType = this.fileType(key);
		if (existingType !== "missing") {
			if (existingType !== "file" || this.read(key) !== content) {
				throw new Error(`refusing to replace immutable execution provenance: ${path}`);
			}
			return;
		}
		const temporary = `${path}.sure-immutable-${process.pid}-${randomUUID()}`;
		try {
			writeDurableFile(temporary, content, true);
			linkSync(temporary, path);
			syncDirectory(dirname(path));
		} catch (error) {
			if (!isAlreadyExists(error) || this.fileType(key) !== "file" || this.read(key) !== content) throw error;
		} finally {
			try {
				if (existsSync(temporary)) unlinkSync(temporary);
			} catch {
				// Preserve the publication or collision result.
			}
		}
	}

	private assertPublicationParent(path: string): void {
		const parent = dirname(path);
		const stat = lstatSync(parent);
		if (!stat.isDirectory() || stat.isSymbolicLink()) {
			throw new Error(`execution provenance parent must be a regular directory: ${parent}`);
		}
		const resolvedParent = resolve(realpathSync.native(parent));
		if (!contained(this.resolvedRoot, resolvedParent)) {
			throw new Error(`execution provenance parent escaped its admitted root: ${parent}`);
		}
	}
}
