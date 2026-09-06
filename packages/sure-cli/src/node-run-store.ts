import { createHash, randomUUID } from "node:crypto";
import {
	closeSync,
	constants,
	existsSync,
	fsyncSync,
	lstatSync,
	mkdirSync,
	openSync,
	readFileSync,
	realpathSync,
	renameSync,
	unlinkSync,
	writeFileSync,
} from "node:fs";
import { dirname } from "node:path";
import {
	CoreRunStore,
	type RunStoreFileSystem,
	type RunStoreLock,
	type RunStoreOptions,
} from "@earendil-works/sure-core";
import lockfile from "proper-lockfile";

const DIRECTORY_OPEN_FLAG = process.platform === "win32" ? 0 : (constants.O_DIRECTORY ?? 0);

function syncDirectory(path: string): void {
	let descriptor: number | undefined;
	try {
		descriptor = openSync(path, constants.O_RDONLY | DIRECTORY_OPEN_FLAG);
		fsyncSync(descriptor);
	} catch (error) {
		// Windows and a few virtual filesystems do not permit directory fsync.
		// File contents are still fsynced; preserve the adapter's historical
		// behavior on those hosts while requiring it where the OS supports it.
		if (!isUnsupportedDirectorySync(error)) throw error;
	} finally {
		if (descriptor !== undefined) closeSync(descriptor);
	}
}

function isUnsupportedDirectorySync(error: unknown): boolean {
	return (
		typeof error === "object" &&
		error !== null &&
		"code" in error &&
		(error.code === "EINVAL" || error.code === "ENOTSUP" || error.code === "EBADF" || error.code === "EPERM")
	);
}

function writeDurableFile(path: string, content: string, exclusive: boolean): void {
	const flags = constants.O_WRONLY | constants.O_CREAT | (exclusive ? constants.O_EXCL : 0);
	const descriptor = openSync(path, flags, 0o666);
	try {
		writeFileSync(descriptor, content, { encoding: "utf8" });
		fsyncSync(descriptor);
	} finally {
		closeSync(descriptor);
	}
}

class NodeFileSystem implements RunStoreFileSystem {
	mkdir(path: string): void {
		mkdirSync(path, { recursive: true });
	}

	readFile(path: string): string | undefined {
		try {
			return readFileSync(path, "utf8");
		} catch (error) {
			if (isMissing(error)) return undefined;
			throw error;
		}
	}

	writeFileAtomic(path: string, content: string): void {
		mkdirSync(dirname(path), { recursive: true });
		const temporary = `${path}.sure-tmp-${process.pid}-${randomUUID()}`;
		try {
			writeDurableFile(temporary, content, true);
			renameSync(temporary, path);
			syncDirectory(dirname(path));
		} catch (error) {
			try {
				if (existsSync(temporary)) unlinkSync(temporary);
			} catch {
				// Preserve the original failure.
			}
			throw error;
		}
	}

	appendLine(path: string, line: string): void {
		mkdirSync(dirname(path), { recursive: true });
		const existed = existsSync(path);
		const descriptor = openSync(path, constants.O_WRONLY | constants.O_CREAT | constants.O_APPEND, 0o666);
		try {
			writeFileSync(descriptor, line, { encoding: "utf8" });
			fsyncSync(descriptor);
		} finally {
			closeSync(descriptor);
		}
		if (!existed) syncDirectory(dirname(path));
	}

	exists(path: string): boolean {
		return existsSync(path);
	}

	realpath(path: string): string | undefined {
		try {
			return realpathSync.native(path);
		} catch (error) {
			if (isMissing(error)) return undefined;
			throw error;
		}
	}

	fileType(path: string): "missing" | "file" | "directory" | "symlink" | "other" {
		try {
			const stat = lstatSync(path);
			if (stat.isSymbolicLink()) return "symlink";
			if (stat.isFile()) return "file";
			if (stat.isDirectory()) return "directory";
			return "other";
		} catch (error) {
			if (isMissing(error)) return "missing";
			throw error;
		}
	}

	digestFile(path: string): string | undefined {
		if (this.fileType(path) !== "file") return undefined;
		return `sha256:${createHash("sha256").update(readFileSync(path)).digest("hex")}`;
	}
}

class NodeLock implements RunStoreLock {
	withLock<T>(key: string, operation: () => T): T {
		const parent = dirname(key);
		mkdirSync(parent, { recursive: true });
		mkdirSync(dirname(`${key}.lock`), { recursive: true });
		const release = lockfile.lockSync(parent, { realpath: false, lockfilePath: `${key}.lock` });
		try {
			return operation();
		} finally {
			release();
		}
	}
}

function isMissing(error: unknown): boolean {
	return typeof error === "object" && error !== null && "code" in error && error.code === "ENOENT";
}

export type NodeRunStoreOptions = Omit<RunStoreOptions, "filesystem" | "lock">;

/** Node adapter kept in the CLI package so the Core package stays port-based. */
export class NodeRunStore extends CoreRunStore {
	constructor(options: NodeRunStoreOptions) {
		super({ ...options, filesystem: new NodeFileSystem(), lock: new NodeLock() });
	}
}
