import { existsSync, lstatSync, realpathSync } from "node:fs";
import { isAbsolute, join, relative, resolve, sep } from "node:path";

/**
 * Host-neutral location and identity service for SURE memory references.
 *
 * This module intentionally knows nothing about Pi hooks, event payloads or
 * Python backends.  A host supplies explicit roots once, then all memory
 * readers/writers use the same logical reference identity.  The legacy
 * `sure/skills/<skill>/references/memory` tree remains the default write alias
 * during migration; canonical roots can be selected explicitly by a future
 * publisher without changing the URI or entry-id contract.
 */

export type MemoryEntryKind = "bad_case" | "fact";
export type MemoryReferenceWriteRoot = "legacy" | "canonical";

export interface MemoryServiceOptions {
	repoRoot: string;
	memoryRoot?: string;
	/** `<repo>/sure/canonical`; may be outside repoRoot only when explicitly admitted. */
	canonicalRoot?: string;
	/** `<repo>/sure/skills`; the compatibility alias. */
	legacySkillsRoot?: string;
	writeRoot?: MemoryReferenceWriteRoot;
	preferCanonicalReads?: boolean;
}

export interface MemoryRoots {
	repoRoot: string;
	memoryRoot: string;
	canonicalRoot: string;
	legacySkillsRoot: string;
	writeRoot: MemoryReferenceWriteRoot;
	preferCanonicalReads: boolean;
}

export interface ParsedMemoryUri {
	skill: string;
	kind: MemoryEntryKind;
	slug: string;
}

/** Minimal run context consumed by memory orchestration; deliberately host-neutral. */
export interface MemoryRunContext {
	packageDir: string;
	runDir: string;
	cwd: string;
}

const SEGMENT = /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/;
const URI_PREFIX = "memory://";

function absoluteRoot(value: string, label: string): string {
	if (!isAbsolute(value)) throw new Error(`${label} must be absolute: ${value}`);
	return resolve(value);
}

function safeSegment(value: string, label: string): string {
	if (!SEGMENT.test(value) || value === "." || value === "..") {
		throw new Error(`${label} must be a single safe path segment: ${value}`);
	}
	return value;
}

function safeSkill(value: string): string {
	return safeSegment(value, "memory skill");
}

function safeSlug(value: string): string {
	return safeSegment(value, "memory slug");
}

function inside(root: string, candidate: string): boolean {
	const relation = relative(root, candidate);
	return relation === "" || (relation !== ".." && !relation.startsWith(`..${sep}`) && !isAbsolute(relation));
}

function assertSymlinkContainment(root: string, candidate: string, label: string): void {
	const realRoot = existsSync(root) ? realpathSync(root) : resolve(root);
	const relativePath = relative(root, candidate);
	let prefix = root;
	for (const segment of relativePath.split(sep).filter(Boolean)) {
		prefix = join(prefix, segment);
		let stat: ReturnType<typeof lstatSync>;
		try {
			stat = lstatSync(prefix);
		} catch {
			// A missing prefix is safe only when no earlier component was a link. The
			// first later-created component will be checked on the next operation.
			break;
		}
		if (!stat.isSymbolicLink()) continue;
		let realPrefix: string;
		try {
			realPrefix = realpathSync(prefix);
		} catch {
			throw new Error(`${label} contains an unresolved symlink: ${prefix}`);
		}
		if (!inside(realRoot, realPrefix)) {
			throw new Error(`${label} resolves outside its admitted root: ${prefix}`);
		}
	}
}

function assertPath(root: string, candidate: string, label: string): string {
	const resolved = resolve(candidate);
	if (!inside(root, resolved)) throw new Error(`${label} escapes its admitted root: ${resolved}`);
	assertSymlinkContainment(root, resolved, label);
	// Existing symlinks are resolved as well.  A reference alias must not turn a
	// read or write into an escape through a mutable link.
	if (existsSync(resolved)) {
		const realRoot = realpathSync(root);
		const realCandidate = realpathSync(resolved);
		if (!inside(realRoot, realCandidate)) throw new Error(`${label} resolves outside its admitted root: ${resolved}`);
	} else {
		const parent = resolve(resolved, "..");
		if (existsSync(parent)) {
			const realRoot = existsSync(root) ? realpathSync(root) : resolve(root);
			const realParent = realpathSync(parent);
			if (!inside(realRoot, realParent))
				throw new Error(`${label} parent resolves outside its admitted root: ${resolved}`);
		}
	}
	return resolved;
}

function kindSegment(kind: MemoryEntryKind): string {
	return kind;
}

export class MemoryService {
	readonly roots: MemoryRoots;

	constructor(options: MemoryServiceOptions) {
		const repoRoot = absoluteRoot(options.repoRoot, "memory repoRoot");
		const memoryRoot = absoluteRoot(options.memoryRoot ?? join(repoRoot, "sure", "memory"), "memoryRoot");
		const canonicalRoot = absoluteRoot(options.canonicalRoot ?? join(repoRoot, "sure", "canonical"), "canonicalRoot");
		const legacySkillsRoot = absoluteRoot(
			options.legacySkillsRoot ?? join(repoRoot, "sure", "skills"),
			"legacySkillsRoot",
		);
		this.roots = Object.freeze({
			repoRoot,
			memoryRoot,
			canonicalRoot,
			legacySkillsRoot,
			writeRoot: options.writeRoot ?? "legacy",
			preferCanonicalReads: options.preferCanonicalReads ?? false,
		});
	}

	static fromRepoRoot(repoRoot: string, options: Omit<MemoryServiceOptions, "repoRoot"> = {}): MemoryService {
		return new MemoryService({ ...options, repoRoot });
	}

	get repoRoot(): string {
		return this.roots.repoRoot;
	}

	get memoryRoot(): string {
		return this.roots.memoryRoot;
	}

	/** Return the stable URI used in contracts and cross-host evidence. */
	logicalUri(skill: string, kind: MemoryEntryKind, slug: string): string {
		return `${URI_PREFIX}${safeSkill(skill)}/${kindSegment(kind)}/${safeSlug(slug)}`;
	}

	parseUri(uri: string): ParsedMemoryUri {
		if (!uri.startsWith(URI_PREFIX)) throw new Error(`memory URI must start with ${URI_PREFIX}`);
		const parts = uri.slice(URI_PREFIX.length).split("/");
		if (parts.length !== 3) throw new Error(`memory URI must have skill/kind/slug: ${uri}`);
		const skill = safeSkill(parts[0] ?? "");
		const kind = parts[1] as MemoryEntryKind;
		if (kind !== "bad_case" && kind !== "fact") throw new Error(`unknown memory URI kind: ${parts[1]}`);
		const slug = safeSlug(parts[2] ?? "");
		return { skill, kind, slug };
	}

	uriForEntry(entryId: string, kind: MemoryEntryKind): string {
		const parts = entryId.split("/");
		if (parts.length !== 2) throw new Error(`memory entry id must be <skill>/<slug>: ${entryId}`);
		return this.logicalUri(parts[0] ?? "", kind, parts[1] ?? "");
	}

	canonicalPath(skill: string, kind: MemoryEntryKind, slug: string): string {
		safeSkill(skill);
		safeSlug(slug);
		const base =
			kind === "fact"
				? join(this.roots.canonicalRoot, "references", "memory", "facts")
				: join(this.roots.canonicalRoot, "skills", skill.replaceAll("_", "-"), "references", "memory", "bad_cases");
		return assertPath(this.roots.canonicalRoot, join(base, `${slug}.md`), "canonical memory reference");
	}

	legacyPath(skill: string, kind: MemoryEntryKind, slug: string): string {
		safeSkill(skill);
		safeSlug(slug);
		const base =
			kind === "fact"
				? join(this.roots.legacySkillsRoot, "_shared", "memory", "facts")
				: join(this.roots.legacySkillsRoot, skill, "references", "memory", "bad_cases");
		return assertPath(this.roots.legacySkillsRoot, join(base, `${slug}.md`), "legacy memory reference");
	}

	readCandidates(skill: string, kind: MemoryEntryKind, slug: string): string[] {
		const ordered = this.roots.preferCanonicalReads
			? [this.canonicalPath(skill, kind, slug), this.legacyPath(skill, kind, slug)]
			: [this.legacyPath(skill, kind, slug), this.canonicalPath(skill, kind, slug)];
		return [...new Set(ordered)];
	}

	/** Existing reference path, or undefined when neither alias is materialized. */
	resolveReference(skill: string, kind: MemoryEntryKind, slug: string): string | undefined {
		const aliases: MemoryReferenceWriteRoot[] = this.roots.preferCanonicalReads
			? ["canonical", "legacy"]
			: ["legacy", "canonical"];
		for (const alias of aliases) {
			try {
				const candidate =
					alias === "canonical" ? this.canonicalPath(skill, kind, slug) : this.legacyPath(skill, kind, slug);
				if (lstatSync(candidate).isFile()) return candidate;
			} catch {
				// Missing or unreadable aliases are simply unavailable to the
				// advisory reader; a writer can use readCandidates() to surface a
				// path-policy violation explicitly.
			}
		}
		return undefined;
	}

	/** The only path a writer may create.  Defaults to the legacy alias for compatibility. */
	writeReference(skill: string, kind: MemoryEntryKind, slug: string): string {
		return this.roots.writeRoot === "canonical"
			? this.canonicalPath(skill, kind, slug)
			: this.legacyPath(skill, kind, slug);
	}

	/** Convert a materialized path back to a logical URI when it is an admitted alias. */
	uriForPath(path: string): string | undefined {
		const candidate = resolve(path);
		const roots = [this.roots.legacySkillsRoot, this.roots.canonicalRoot];
		for (const root of roots) {
			if (!inside(root, candidate)) continue;
			const rel = relative(root, candidate).split(sep).join("/").split("/");
			const factIndex = rel.indexOf("facts");
			if (factIndex >= 1 && rel[factIndex - 1] === "memory" && rel.length === factIndex + 2) {
				return this.logicalUri("_shared", "fact", (rel[factIndex + 1] ?? "").replace(/\.md$/, ""));
			}
			const badIndex = rel.indexOf("bad_cases");
			if (badIndex >= 3 && rel[badIndex - 1] === "memory" && rel.length === badIndex + 2) {
				const skill =
					root === this.roots.canonicalRoot ? (rel[badIndex - 3] ?? "").replaceAll("-", "_") : (rel[0] ?? "");
				return this.logicalUri(skill, "bad_case", (rel[badIndex + 1] ?? "").replace(/\.md$/, ""));
			}
		}
		return undefined;
	}
}

export const MEMORY_URI_PREFIX = URI_PREFIX;
