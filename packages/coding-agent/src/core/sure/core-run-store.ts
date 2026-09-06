import { createHash, randomUUID } from "node:crypto";
import {
	appendFileSync,
	existsSync,
	lstatSync,
	mkdirSync,
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

class NodeRunStoreFileSystem implements RunStoreFileSystem {
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
			writeFileSync(temporary, content, { encoding: "utf8", flag: "wx" });
			renameSync(temporary, path);
		} catch (error) {
			try {
				if (existsSync(temporary)) unlinkSync(temporary);
			} catch {
				// Preserve the original write/rename error.
			}
			throw error;
		}
	}

	appendLine(path: string, line: string): void {
		mkdirSync(dirname(path), { recursive: true });
		appendFileSync(path, line, { encoding: "utf8" });
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

class NodeRunStoreLock implements RunStoreLock {
	withLock<T>(key: string, operation: () => T): T {
		const parent = dirname(key);
		mkdirSync(parent, { recursive: true });
		// Lock the stable parent while a run directory or diagnostic file is being
		// created. The lock path is adjacent to the logical key and is never used
		// as an application data file.
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

export type NodeCoreRunStoreOptions = Omit<RunStoreOptions, "filesystem" | "lock">;

/** Node/Pi filesystem adapter for the host-neutral CoreRunStore. */
export class NodeCoreRunStore extends CoreRunStore {
	constructor(options: NodeCoreRunStoreOptions) {
		super({
			...options,
			filesystem: new NodeRunStoreFileSystem(),
			lock: new NodeRunStoreLock(),
		});
	}
}

export function createNodeCoreRunStore(options: NodeCoreRunStoreOptions): NodeCoreRunStore {
	return new NodeCoreRunStore(options);
}
