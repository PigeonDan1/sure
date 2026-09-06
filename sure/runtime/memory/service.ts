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
	/** Explicit roots supplied by a host adapter; package layout is compatibility-only. */
	repoRoot?: string;
	memoryRoot?: string;
	canonicalRoot?: string;
	legacySkillsRoot?: string;
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

function validateKindForSkill(skill: string, kind: MemoryEntryKind): void {
	safeSkill(skill);
	if (kind === "fact" && skill !== "_shared") {
		throw new Error("memory facts must use the _shared skill");
	}
	if (kind === "bad_case" && skill === "_shared") {
		throw new Error("memory bad cases must use a skill-specific namespace");
	}
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

	/** Build a service from host-supplied roots without inspecting package layout. */
	static fromRunContext(context: MemoryRunContext): MemoryService {
		if (!context.repoRoot) {
			throw new Error("memory run context must provide an explicit repoRoot");
		}
		return new MemoryService({
			repoRoot: context.repoRoot,
			...(context.memoryRoot === undefined ? {} : { memoryRoot: context.memoryRoot }),
			...(context.canonicalRoot === undefined ? {} : { canonicalRoot: context.canonicalRoot }),
			...(context.legacySkillsRoot === undefined ? {} : { legacySkillsRoot: context.legacySkillsRoot }),
		});
	}

	get repoRoot(): string {
		return this.roots.repoRoot;
	}

	get memoryRoot(): string {
		return this.roots.memoryRoot;
	}

	/** Return the stable URI used in contracts and cross-host evidence. */
	logicalUri(skill: string, kind: MemoryEntryKind, slug: string): string {
		validateKindForSkill(skill, kind);
		return `${URI_PREFIX}${safeSkill(skill)}/${kindSegment(kind)}/${safeSlug(slug)}`;
	}

	parseUri(uri: string): ParsedMemoryUri {
		if (!uri.startsWith(URI_PREFIX)) throw new Error(`memory URI must start with ${URI_PREFIX}`);
		const parts = uri.slice(URI_PREFIX.length).split("/");
		if (parts.length !== 3) throw new Error(`memory URI must have skill/kind/slug: ${uri}`);
		const skill = safeSkill(parts[0] ?? "");
		const kind = parts[1] as MemoryEntryKind;
		if (kind !== "bad_case" && kind !== "fact") throw new Error(`unknown memory URI kind: ${parts[1]}`);
		validateKindForSkill(skill, kind);
		const slug = safeSlug(parts[2] ?? "");
		return { skill, kind, slug };
	}

	uriForEntry(entryId: string, kind: MemoryEntryKind): string {
		const parts = entryId.split("/");
		if (parts.length !== 2) throw new Error(`memory entry id must be <skill>/<slug>: ${entryId}`);
		return this.logicalUri(parts[0] ?? "", kind, parts[1] ?? "");
	}

	canonicalPath(skill: string, kind: MemoryEntryKind, slug: string): string {
		validateKindForSkill(skill, kind);
		safeSlug(slug);
		const base =
			kind === "fact"
				? join(this.roots.canonicalRoot, "shared", "legacy-resources", "memory", "facts")
				: join(this.roots.canonicalRoot, "skills", skill.replaceAll("_", "-"), "references", "memory", "bad_cases");
		return assertPath(this.roots.canonicalRoot, join(base, `${slug}.md`), "canonical memory reference");
	}

	legacyPath(skill: string, kind: MemoryEntryKind, slug: string): string {
		validateKindForSkill(skill, kind);
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
			try {
				assertPath(root, candidate, "memory reference path");
			} catch {
				continue;
			}
			const rel = relative(root, candidate).split(sep).join("/").split("/");
			const leaf = rel.at(-1) ?? "";
			if (!leaf.endsWith(".md")) continue;
			const slug = leaf.slice(0, -3);
			if (!slug || !SEGMENT.test(slug)) continue;
			if (root === this.roots.legacySkillsRoot) {
				if (rel.length === 4 && rel[0] === "_shared" && rel[1] === "memory" && rel[2] === "facts") {
					return this.logicalUri("_shared", "fact", slug);
				}
				if (rel.length === 5 && rel[1] === "references" && rel[2] === "memory" && rel[3] === "bad_cases") {
					return this.logicalUri(rel[0] ?? "", "bad_case", slug);
				}
			} else if (
				rel.length === 5 &&
				rel[0] === "shared" &&
				rel[1] === "legacy-resources" &&
				rel[2] === "memory" &&
				rel[3] === "facts"
			) {
				return this.logicalUri("_shared", "fact", slug);
			} else if (
				rel.length === 6 &&
				rel[0] === "skills" &&
				rel[2] === "references" &&
				rel[3] === "memory" &&
				rel[4] === "bad_cases"
			) {
				return this.logicalUri((rel[1] ?? "").replaceAll("-", "_"), "bad_case", slug);
			}
		}
		return undefined;
	}
}

export const MEMORY_URI_PREFIX = URI_PREFIX;
