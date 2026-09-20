import type { Dirent } from "node:fs";
import { readdir, readFile, stat } from "node:fs/promises";
import ignore from "ignore";
import { minimatch } from "minimatch";
import path from "path";

/**
 * Filesystem-only stand-ins for ripgrep and fd.
 *
 * A fresh Windows or macOS box has neither binary, and the grep and find tools
 * are dead without them. These walk the tree with readdir instead, so the tools
 * get slower rather than disappearing. They never shell out: `grep` and `find`
 * are different programs on the three platforms, and Windows ships neither.
 *
 * One deliberate difference from ripgrep: a `--glob` that contains a slash is
 * anchored at the search root here, while ripgrep anchors it at its own working
 * directory. The search root is what the caller asked about, so this is the more
 * predictable of the two.
 */

// ponytail: 5 MiB grep ceiling and a first-8-KiB binary sniff; raise both if a
// real repository needs it.
const MAX_GREP_FILE_BYTES = 5 * 1024 * 1024;
const BINARY_SNIFF_BYTES = 8192;

interface IgnoreScope {
	prefix: string;
	matcher: ReturnType<typeof ignore>;
}

function ignoredBy(scopes: IgnoreScope[], relativePosixPath: string, isDirectory: boolean): boolean {
	for (const scope of scopes) {
		const candidate = scope.prefix ? relativePosixPath.slice(scope.prefix.length + 1) : relativePosixPath;
		if (!candidate) continue;
		if (scope.matcher.ignores(isDirectory ? `${candidate}/` : candidate)) return true;
	}
	return false;
}

export interface WalkedEntry {
	absolutePath: string;
	relativePosixPath: string;
	isDirectory: boolean;
}

/**
 * Yield every file below `root`, and every directory too when
 * `includeDirectories` is set. Skips `.git` and anything a `.gitignore` at or
 * below `root` excludes. Symlinks and Windows junctions are reported by readdir
 * as neither file nor directory, so they are never followed and a link cycle
 * cannot hang the walk.
 */
// ponytail: only reads .gitignore files at or below the search root; ripgrep and
// fd also read the ones above it.
export async function* walkTree(
	root: string,
	options: { includeDirectories?: boolean; signal?: AbortSignal } = {},
): AsyncGenerator<WalkedEntry> {
	const stack: Array<{ dir: string; relativePosixPath: string; scopes: IgnoreScope[] }> = [
		{ dir: root, relativePosixPath: "", scopes: [] },
	];
	while (stack.length > 0) {
		const current = stack.pop();
		if (!current) break;
		if (options.signal?.aborted) return;
		let entries: Dirent[];
		try {
			entries = await readdir(current.dir, { withFileTypes: true });
		} catch {
			continue; // unreadable directory: skip it, the same as rg and fd do
		}
		let scopes = current.scopes;
		if (entries.some((entry) => entry.isFile() && entry.name === ".gitignore")) {
			try {
				const patterns = await readFile(path.join(current.dir, ".gitignore"), "utf-8");
				scopes = [...scopes, { prefix: current.relativePosixPath, matcher: ignore().add(patterns) }];
			} catch {
				// An unreadable .gitignore just means nothing extra is ignored here.
			}
		}
		for (const entry of entries) {
			if (entry.name === ".git") continue;
			const isDirectory = entry.isDirectory();
			if (!isDirectory && !entry.isFile()) continue;
			const absolutePath = path.join(current.dir, entry.name);
			const relativePosixPath = current.relativePosixPath
				? `${current.relativePosixPath}/${entry.name}`
				: entry.name;
			if (ignoredBy(scopes, relativePosixPath, isDirectory)) continue;
			if (isDirectory) {
				if (options.includeDirectories) yield { absolutePath, relativePosixPath, isDirectory: true };
				stack.push({ dir: absolutePath, relativePosixPath, scopes });
			} else {
				yield { absolutePath, relativePosixPath, isDirectory: false };
			}
		}
	}
}

export interface FallbackGrepMatch {
	filePath: string;
	lineNumber: number;
	lineText: string;
}

/** Stand-in for `rg --json --line-number --hidden [--ignore-case] [--fixed-strings] [--glob G]`. */
export async function grepFallback(params: {
	searchPath: string;
	isDirectory: boolean;
	pattern: string;
	glob?: string;
	ignoreCase?: boolean;
	literal?: boolean;
	limit: number;
	signal?: AbortSignal;
}): Promise<FallbackGrepMatch[]> {
	const source = params.literal ? params.pattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") : params.pattern;
	let regex: RegExp;
	try {
		regex = new RegExp(source, params.ignoreCase ? "i" : "");
	} catch (error) {
		throw new Error(`Invalid pattern: ${error instanceof Error ? error.message : String(error)}`);
	}
	const matches: FallbackGrepMatch[] = [];
	const scan = async (absolutePath: string, relativePosixPath: string): Promise<void> => {
		if (params.glob && !minimatch(relativePosixPath, params.glob, { dot: true, matchBase: true })) return;
		let size: number;
		try {
			size = (await stat(absolutePath)).size;
		} catch {
			return;
		}
		if (size > MAX_GREP_FILE_BYTES) return;
		let buffer: Buffer;
		try {
			buffer = await readFile(absolutePath);
		} catch {
			return;
		}
		if (buffer.subarray(0, BINARY_SNIFF_BYTES).includes(0)) return;
		const lines = buffer.toString("utf-8").split("\n");
		// The final newline terminates the last line, it does not start another
		// one. Without this, every pattern that matches the empty string reports a
		// phantom hit past the end of the file and spends a slot of the limit.
		if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
		for (let index = 0; index < lines.length && matches.length < params.limit; index++) {
			const lineText = (lines[index] ?? "").replace(/\r$/, "");
			if (regex.test(lineText)) matches.push({ filePath: absolutePath, lineNumber: index + 1, lineText });
		}
	};
	if (!params.isDirectory) {
		await scan(params.searchPath, path.basename(params.searchPath));
		return matches;
	}
	for await (const entry of walkTree(params.searchPath, { signal: params.signal })) {
		if (matches.length >= params.limit) break;
		await scan(entry.absolutePath, entry.relativePosixPath);
	}
	return matches;
}

/** Stand-in for `fd --glob --hidden --max-results N [--full-path]`. Returns absolute paths. */
export async function findFallback(params: {
	searchPath: string;
	pattern: string;
	limit: number;
	signal?: AbortSignal;
}): Promise<string[]> {
	// Same rule find.ts applies to fd: a path-shaped pattern matches at any depth.
	let pattern = params.pattern;
	if (pattern.includes("/") && !pattern.startsWith("/") && !pattern.startsWith("**/") && pattern !== "**") {
		pattern = `**/${pattern}`;
	}
	const results: string[] = [];
	for await (const entry of walkTree(params.searchPath, { includeDirectories: true, signal: params.signal })) {
		if (results.length >= params.limit) break;
		if (minimatch(entry.relativePosixPath, pattern, { dot: true, matchBase: true })) {
			results.push(entry.absolutePath);
		}
	}
	return results;
}
