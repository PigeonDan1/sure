// Memory matching, recall budget and usage rows for the SURE hooks (spec §7.2, §8.1).
//
// This module is shared by the sure_onboard and sure_eval hooks. It only READS
// sure/memory/index.json (python builds it); it never parses entry files and never
// touches meta. The only file it writes is the per-run usage jsonl (inject / pre_start /
// settle rows), which python later replays into counts.
//
// budget logic adapted from TencentDB-Agent-Memory src/core/hooks/auto-recall.ts
// applyRecallBudget/truncateRecallLine/normalizeBudgetLimit (MIT, Copyright (C) 2026 Tencent).
// The upstream "truncate the last line to the remaining space" branch is removed on purpose:
// spec §7.2 says a line that does not fit is dropped whole, never cut mid-sentence.
import { appendFileSync, chmodSync, existsSync, mkdirSync, readFileSync, statSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { repoRootForPackage } from "../harness/resolve.ts";

export interface MemoryConfig {
	schema: string;
	promote_useful_activated: number;
	promote_min_distinct_runs: number;
	demote_disputed_streak: number;
	max_candidates_per_run: number;
	max_triggers_per_candidate: number;
	bad_case_max_words: number;
	fact_max_words: number;
	trigger_min_chars: number;
	trigger_stopwords: string[];
	trigger_template_phrases: string[];
	target_skills: string[];
	cause_enum: string[];
	fact_scopes: string[];
	extraction_gate_max_failures: number;
	finish_extraction_max_attempts: number;
	inject_max_entries: number;
	inject_max_chars_per_entry: number;
	inject_max_chars_total: number;
	inject_header: string;
	memory_context_max_provisional: number;
	digest_max_bytes: number;
	digest_limits: Record<string, number>;
	digest_trim_order: string[];
	index_md_max_lines: number;
	index_md_max_bytes: number;
	stale_after_days: Record<string, number>;
	usage_max_line_bytes: number;
	/** Entries (files + directories) the gateInputs walk may visit before it stops. */
	gate_digest_max_entries: number;
	/** Bytes the gateInputs walk may read before it hashes sizes instead of contents. */
	gate_digest_max_bytes: number;
	publish_timeout_ms: number;
	index_check_timeout_ms: number;
	dedup_jaccard_min: number;
	dedup_ratio_min: number;
	/**
	 * Keys config.json did not carry, filled from MEMORY_CONFIG_DEFAULTS. Not a config.json key:
	 * it is derived at load time so a hook can say the deployment is running on a default nobody
	 * chose. Empty for a config.json that carries every key.
	 */
	defaulted_keys: string[];
}

/** One row of index.json `entries` (same field names as the file, see plan §1.7). */
export interface MemoryIndexEntry {
	entry_id: string;
	type: string;
	status: string;
	target_skill: string;
	applies_to: string[];
	component: string;
	cause: string;
	/** Every trigger of the entry; feeds index.md / prompt routing, never the hook match. */
	trigger: string[];
	/**
	 * The subset the hook matches on (plan §1.7): for a bad_case, the triggers publish found
	 * verbatim in the run digest; for a fact and for legacy entries, equal to `trigger`.
	 * Absent in an older index.json: matching then falls back to `trigger`.
	 */
	hook_trigger?: string[];
	scope: string | null;
	title: string;
	path: string;
	legacy: boolean;
	op: string;
	target_entry: string | null;
	similar_entry: string | null;
	useful_activated: number;
	useful_unattributed: number;
	injections: number;
	disputed: number;
	/** "legacy" for the old entries, otherwise the Added date (YYYY-MM-DD). */
	created: string | null;
	checked_at: string | null;
	stale: boolean;
	superseded_by: string | null;
}

export interface MemoryIndex {
	schema: string;
	built_at: string;
	sources_sha256: string;
	entries: MemoryIndexEntry[];
	omitted_provisional: number;
}

export interface MemoryMatch {
	entry: MemoryIndexEntry;
	/** Length of the longest trigger that hit (0 when only the fact scope hit). Drives ordering. */
	hitLength: number;
	/** A modify / supersede candidate whose target_entry is this entry; rendered on the same line. */
	pendingRevision?: MemoryIndexEntry;
}

export const MEMORY_INDEX_SCHEMA = "sure.memory.index.v1";
export const MEMORY_CONFIG_SCHEMA = "sure.memory.config.v1";
/** `name` of the error loadMemoryConfig throws when config.json parses but is not a config. */
export const MEMORY_CONFIG_SCHEMA_ERROR = "MemoryConfigSchemaError";

// Lower tier sorts first. Any other status (superseded, rejected, unknown) never matches.
const STATUS_TIER: Record<string, number> = { confirmed: 0, provisional: 1, disputed: 2 };
const TRUNCATION_SUFFIX = "...";
const RUN_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
// An absolute path: a drive letter, or a separator that does not continue a word, so a
// repo-relative "sure/memory/index.json" or "usage/<run_id>.jsonl" is left alone. UNC and
// "\\?\" paths start with a separator too, so they are covered by the second branch.
//
// A space is part of the path only when another separator still comes before the next space:
// "C:\Users\Ann Lee\dev\x" is one path, while in "C:\tmp\a.json because the disk is full" the
// word after the space has no separator, so the match stops at the path. Without that, the mask
// ended at the first space and left the account name and the rest of the path in the text.
const ABSOLUTE_PATH_RE = /(?:[A-Za-z]:[\\/]|(?<![\w.$])[\\/])(?:[^\s'"]|[ ](?=[^\s'"]*[\\/]))*/g;

/**
 * Every absolute host path in `text` masked as "<path>".
 *
 * Everything this module returns as an `error` becomes diagnostics[].message in hooks.ts: the
 * agent reads it, and digest.py carries a repair of the same shape into the next run's
 * prior_runs, from where it can be lifted into a trigger and stored in sure/memory for good. An
 * fs error names the absolute path of the file it failed on, so no error text may be passed on
 * unmasked. Repo-relative paths survive: those are the ones the agent is meant to open.
 */
export function redactHostPaths(text: string): string {
	return text.replace(ABSOLUTE_PATH_RE, "<path>");
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
	return typeof value === "string" ? value : undefined;
}

function asStringList(value: unknown): string[] {
	return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function asCount(value: unknown): number {
	return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function codePointLength(text: string): number {
	return Array.from(text).length;
}

// --- locations and files -----------------------------------------------------------

/** Directory of this file (sure/runtime/memory), where config.json and fixtures live. */
export function memoryLibDir(): string {
	return dirname(fileURLToPath(import.meta.url));
}

/**
 * config.json, or a throw. The throw carries NO path: hooks.ts turns it into repair text the
 * agent reads, run.json.lastRepair, and, through digest.py's prior_runs, into the next run's
 * digest, where an agent can lift it into a trigger and store it in sure/memory for good. The
 * error's `name` is the only thing tryConfig() reports, so the schema case gets its own.
 */
export function loadMemoryConfig(): MemoryConfig {
	const path = join(memoryLibDir(), "config.json");
	const parsed: unknown = JSON.parse(readFileSync(path, "utf-8"));
	if (!isRecord(parsed) || parsed.schema !== MEMORY_CONFIG_SCHEMA) {
		const error = new Error("memory config has an unknown schema");
		error.name = MEMORY_CONFIG_SCHEMA_ERROR;
		throw error;
	}
	return applyMemoryConfigDefaults(parsed);
}

/**
 * The value a config.json that does not carry the key gets.
 *
 * config.json is tuned by hand and no upgrade rewrites it (spec 8.2), so a key added on the .ts
 * side is missing from every config.json already deployed. A missing key is not an error: the
 * schema still matches, nothing throws, and the number simply reads back undefined — which for
 * these two turns the entry cap and the byte cap of the gateInputs walk into permanent silent
 * no-ops. Keys listed here are filled in instead, and loadMemoryConfig names the ones it filled
 * in `defaulted_keys` so pre_start can say a value nobody chose is in force.
 */
export const MEMORY_CONFIG_DEFAULTS: Readonly<Record<string, number>> = {
	gate_digest_max_entries: 2000,
	gate_digest_max_bytes: 8388608,
};

/**
 * `parsed` with every MEMORY_CONFIG_DEFAULTS key that is missing (or is there but is not a finite
 * number, which reads the same at every use site) filled in, and `defaulted_keys` naming them.
 * A key the file does carry is never touched, however odd its value: config.json is the operator's.
 */
export function applyMemoryConfigDefaults(parsed: Record<string, unknown>): MemoryConfig {
	const config = { ...parsed } as Record<string, unknown>;
	const defaulted: string[] = [];
	for (const [key, fallback] of Object.entries(MEMORY_CONFIG_DEFAULTS)) {
		if (typeof config[key] !== "number" || !Number.isFinite(config[key])) {
			config[key] = fallback;
			defaulted.push(key);
		}
	}
	config.defaulted_keys = defaulted;
	return config as unknown as MemoryConfig;
}

/** <repo root>/sure/memory for the skill package at packageDir (same root rule as resolve.ts). */
export function memoryRootFor(packageDir: string): string {
	return join(repoRootForPackage(packageDir), "sure", "memory");
}

function normalizeEntry(raw: unknown): MemoryIndexEntry | undefined {
	if (!isRecord(raw)) return undefined;
	const entryId = asString(raw.entry_id);
	const type = asString(raw.type);
	const status = asString(raw.status);
	const targetSkill = asString(raw.target_skill);
	if (!entryId || !type || !status || !targetSkill) return undefined;
	// Tolerate the meta shape {run_id, date} for created; the index is expected to write a string.
	const created = isRecord(raw.created) ? asString(raw.created.date) : asString(raw.created);
	// hook_trigger is only set when the file carries a list; a missing key stays undefined so
	// hookTriggers() falls back to `trigger` for an index written before the field existed.
	const hookTrigger = Array.isArray(raw.hook_trigger) ? { hook_trigger: asStringList(raw.hook_trigger) } : {};
	return {
		entry_id: entryId,
		type,
		status,
		target_skill: targetSkill,
		applies_to: asStringList(raw.applies_to),
		component: asString(raw.component) ?? "_",
		cause: asString(raw.cause) ?? "n.a.",
		trigger: asStringList(raw.trigger),
		...hookTrigger,
		scope: asString(raw.scope) ?? null,
		title: asString(raw.title) ?? entryId,
		path: asString(raw.path) ?? "",
		legacy: raw.legacy === true,
		op: asString(raw.op) ?? "add",
		target_entry: asString(raw.target_entry) ?? null,
		similar_entry: asString(raw.similar_entry) ?? null,
		useful_activated: asCount(raw.useful_activated),
		useful_unattributed: asCount(raw.useful_unattributed),
		injections: asCount(raw.injections),
		disputed: asCount(raw.disputed),
		created: created ?? null,
		checked_at: asString(raw.checked_at) ?? null,
		stale: raw.stale === true,
		superseded_by: asString(raw.superseded_by) ?? null,
	};
}

/**
 * Read sure/memory/index.json. Never throws: a missing file, bad JSON or an unknown schema
 * comes back as ok:false so the hook can record a diagnostic and skip injection (spec §6.4).
 * Malformed entries are dropped one by one; the rest of the index stays usable.
 */
export function readMemoryIndex(memoryRoot: string): { ok: boolean; index?: MemoryIndex; error?: string } {
	const path = join(memoryRoot, "index.json");
	// The file is always sure/memory/index.json, so naming it is enough; its host path would only
	// travel into the agent's text and into stored memory (see redactHostPaths).
	let raw: string;
	try {
		raw = readFileSync(path, "utf-8");
	} catch (error) {
		return {
			ok: false,
			error: `memory index.json is missing or unreadable (${redactHostPaths(describeError(error))})`,
		};
	}
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch (error) {
		return { ok: false, error: `memory index.json is not valid JSON (${redactHostPaths(describeError(error))})` };
	}
	if (!isRecord(parsed) || parsed.schema !== MEMORY_INDEX_SCHEMA) {
		const seen = isRecord(parsed) ? String(parsed.schema) : typeof parsed;
		return { ok: false, error: `memory index.json has an unknown schema (${seen}); expected ${MEMORY_INDEX_SCHEMA}` };
	}
	if (!Array.isArray(parsed.entries)) {
		return { ok: false, error: "memory index.json has no entries array" };
	}
	const entries: MemoryIndexEntry[] = [];
	for (const item of parsed.entries) {
		const entry = normalizeEntry(item);
		if (entry) entries.push(entry);
	}
	return {
		ok: true,
		index: {
			schema: MEMORY_INDEX_SCHEMA,
			built_at: asString(parsed.built_at) ?? "",
			sources_sha256: asString(parsed.sources_sha256) ?? "",
			entries,
			omitted_provisional: asCount(parsed.omitted_provisional),
		},
	};
}

// --- predicate and matching -----------------------------------------------------------

/**
 * The one trigger predicate of the whole system (also python proposals.trigger_hits):
 * trim the trigger, lower-case both sides, plain substring. No whitespace folding, no regex,
 * no other normalisation. An empty trigger never hits. fixtures/match_vectors.json pins it.
 */
export function triggerHits(trigger: string, text: string): boolean {
	const needle = trigger.trim().toLowerCase();
	if (needle === "") return false;
	return text.toLowerCase().includes(needle);
}

function longestHit(triggers: string[], text: string): number {
	let best = 0;
	for (const trigger of triggers) {
		if (triggerHits(trigger, text)) best = Math.max(best, codePointLength(trigger.trim()));
	}
	return best;
}

// The hook only matches on hook_trigger (plan §1.7). Triggers that exist just for index.md /
// prompt routing (evidence-only ones) live in `trigger` and must never inject. An index.json
// without the field (older build) falls back to `trigger`.
function hookTriggers(entry: MemoryIndexEntry): string[] {
	return entry.hook_trigger ?? entry.trigger;
}

function isMatchable(entry: MemoryIndexEntry): boolean {
	return STATUS_TIER[entry.status] !== undefined && entry.superseded_by === null;
}

// applies_to must name the current skill or _shared; an empty list falls back to target_skill.
function appliesTo(entry: MemoryIndexEntry, skill: string): boolean {
	if (entry.applies_to.length === 0) return entry.target_skill === skill || entry.target_skill === "_shared";
	return entry.applies_to.includes(skill) || entry.applies_to.includes("_shared");
}

/** Lower-case, every run of non [a-z0-9] becomes "-": "Qwen/Qwen2.5-7B" -> "qwen-qwen2-5-7b". */
export function normalizeName(text: string): string {
	return text
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "");
}

// Fact scope is matched mechanically: cluster always; model_family:<n> / dataset:<n> when the
// normalised name is a substring of the normalised "targetId args" haystack.
function scopeHits(scope: string | null, haystack: string): boolean {
	if (!scope) return false;
	const trimmed = scope.trim();
	if (trimmed === "cluster") return true;
	const colon = trimmed.indexOf(":");
	if (colon <= 0) return false;
	const kind = trimmed.slice(0, colon).trim();
	const name = normalizeName(trimmed.slice(colon + 1));
	if ((kind !== "model_family" && kind !== "dataset") || name === "") return false;
	return haystack.includes(name);
}

function createdKey(entry: MemoryIndexEntry): string {
	// "legacy" and unknown sort as oldest.
	return entry.created && entry.created !== "legacy" ? entry.created : "";
}

// Spec §7.2 ordering: status tier, longest hit, useful_activated - disputed, newest first.
function compareMatches(a: MemoryMatch, b: MemoryMatch): number {
	const tier = STATUS_TIER[a.entry.status] - STATUS_TIER[b.entry.status];
	if (tier !== 0) return tier;
	if (a.hitLength !== b.hitLength) return b.hitLength - a.hitLength;
	const scoreA = a.entry.useful_activated - a.entry.disputed;
	const scoreB = b.entry.useful_activated - b.entry.disputed;
	if (scoreA !== scoreB) return scoreB - scoreA;
	const dateA = createdKey(a.entry);
	const dateB = createdKey(b.entry);
	if (dateA !== dateB) return dateA < dateB ? 1 : -1;
	return a.entry.entry_id.localeCompare(b.entry.entry_id);
}

// A modify / supersede candidate that hit together with its target folds into the target's
// line (one slot, both ids in usage). The first candidate per target wins; others stay lines.
function mergePendingRevisions(sorted: MemoryMatch[]): MemoryMatch[] {
	const byId = new Map(sorted.map((match) => [match.entry.entry_id, match]));
	const absorbed = new Set<string>();
	for (const match of sorted) {
		const entry = match.entry;
		if ((entry.op !== "modify" && entry.op !== "supersede") || !entry.target_entry) continue;
		const target = byId.get(entry.target_entry);
		if (!target || target === match || target.pendingRevision) continue;
		target.pendingRevision = entry;
		absorbed.add(entry.entry_id);
	}
	return sorted.filter((match) => !absorbed.has(match.entry.entry_id));
}

// An entry whose similar_entry is also in the list is skipped (the pointed-to entry stands for it).
function skipSimilar(list: MemoryMatch[]): MemoryMatch[] {
	const present = new Set<string>();
	for (const match of list) {
		present.add(match.entry.entry_id);
		if (match.pendingRevision) present.add(match.pendingRevision.entry_id);
	}
	const skipped = new Set<string>();
	const kept: MemoryMatch[] = [];
	for (const match of list) {
		const similar = match.entry.similar_entry;
		if (similar && similar !== match.entry.entry_id && present.has(similar) && !skipped.has(similar)) {
			skipped.add(match.entry.entry_id);
			continue;
		}
		kept.push(match);
	}
	return kept;
}

function finalizeMatches(hits: MemoryMatch[]): MemoryMatch[] {
	return skipSimilar(mergePendingRevisions(hits.sort(compareMatches)));
}

/**
 * bad_case matching for a gate rejection: same target_skill and component (unit), any hook
 * trigger hits the text (raw repair + log tail, concatenated by the caller). Returns the full
 * ordered list; applyRecallBudget picks what is injected.
 */
export function matchBadCases(index: MemoryIndex, args: { skill: string; unit: string; text: string }): MemoryMatch[] {
	const hits: MemoryMatch[] = [];
	for (const entry of index.entries) {
		if (entry.type !== "bad_case" || !isMatchable(entry)) continue;
		if (entry.target_skill !== args.skill || entry.component !== args.unit) continue;
		if (!appliesTo(entry, args.skill)) continue;
		const hitLength = longestHit(hookTriggers(entry), args.text);
		if (hitLength === 0) continue;
		hits.push({ entry, hitLength });
	}
	return finalizeMatches(hits);
}

/**
 * fact matching for pre_start: scope decides (cluster / model_family / dataset), hook triggers
 * are a supplement (one found in "targetId args" also matches and raises hitLength).
 */
export function matchFacts(index: MemoryIndex, args: { skill: string; targetId: string; args: string }): MemoryMatch[] {
	const text = `${args.targetId} ${args.args}`;
	const haystack = normalizeName(text);
	const hits: MemoryMatch[] = [];
	for (const entry of index.entries) {
		if (entry.type !== "fact" || !isMatchable(entry)) continue;
		if (!appliesTo(entry, args.skill)) continue;
		const hitLength = longestHit(hookTriggers(entry), text);
		if (!scopeHits(entry.scope, haystack) && hitLength === 0) continue;
		hits.push({ entry, hitLength });
	}
	return finalizeMatches(hits);
}

// --- budget and rendering ---------------------------------------------------------------

function normalizeBudgetLimit(value: number | undefined): number | undefined {
	if (value == null || !Number.isFinite(value) || value <= 0) return undefined;
	return Math.floor(value);
}

// Cut by code point so a surrogate pair is never split (kept from the upstream helper).
function truncateLine(line: string, maxChars: number): string {
	const cps = Array.from(line);
	if (cps.length <= maxChars) return line;
	if (maxChars <= TRUNCATION_SUFFIX.length) return cps.slice(0, maxChars).join("");
	return `${cps
		.slice(0, maxChars - TRUNCATION_SUFFIX.length)
		.join("")
		.trimEnd()}${TRUNCATION_SUFFIX}`;
}

function oneLine(text: string, fallback: string): string {
	const flat = text.replace(/\s*[\r\n]+\s*/g, " ").trim();
	return flat === "" ? fallback : flat;
}

// A path index.json should never carry: a drive letter, or a leading separator (posix or UNC).
const ABSOLUTE_ENTRY_PATH_RE = /^(?:[A-Za-z]:[\\/]|[\\/])/;

/**
 * An entry path fit to show, or "" when it is not repo-relative.
 *
 * index.py's _rel() falls back to an absolute posix path whenever relative_to(repo_root) raises,
 * and resolve() follows symlinks — the normal shape when sure/memory or a skill's
 * references/memory/ is a symlink on a shared cluster filesystem. Such a path is a host path in
 * text the agent reads and that digest.py stores; it is also long enough to push the line past
 * inject_max_chars_per_entry, so the cut lands inside the parenthesis and the agent gets a line
 * with no closing paren, no colon and no title. The entry id alone still locates the entry, and
 * §8.1 activation detection matches on "<entry_id>/" as well as on the path.
 */
export function displayPath(path: string): string {
	return path && !ABSOLUTE_ENTRY_PATH_RE.test(path) ? path : "";
}

/**
 * One injected line: "- [status] entry_id (path[; pending revision: path]): H1 title".
 * The path comes before the title so a per-entry cut only ever shortens the title; the agent
 * needs the path to read the entry (that read is what §8.1 calls activation). A path that is
 * not repo-relative is dropped, and with it the parenthesis when nothing is left to show.
 */
export function renderEntryLine(match: MemoryMatch, maxChars: number | undefined): string {
	const entry = match.entry;
	const parts: string[] = [];
	const target = displayPath(entry.path);
	if (target) {
		parts.push(target);
	}
	const revision = match.pendingRevision ? displayPath(match.pendingRevision.path) : "";
	if (revision) {
		parts.push(`pending revision: ${revision}`);
	}
	const where = parts.length > 0 ? ` (${parts.join("; ")})` : "";
	const line = `- [${entry.status}] ${entry.entry_id}${where}: ${oneLine(entry.title, entry.entry_id)}`;
	return maxChars === undefined ? line : truncateLine(line, maxChars);
}

/** The one reminder line a block ends with when entries from an earlier attempt still apply. */
function renderRepeatedLine(repeated: string[], maxChars: number | undefined): string {
	const line = `Entries shown at an earlier attempt still apply: ${repeated.join(", ")}`;
	return maxChars === undefined ? line : truncateLine(line, maxChars);
}

/**
 * Drop entries already injected for this unit in this run (they come back as `repeated`), then
 * keep at most inject_max_entries lines within inject_max_chars_total (entry lines plus their
 * "\n" separators; only the header is exempt). A line that does not fit is dropped whole,
 * together with everything after it.
 *
 * The repeated-entries reminder is one more line of the block, so it is charged too: it is
 * reserved before the fresh lines are filled in, and the first fresh line also pays the
 * separator that will join the two. `injected[unit]` grows by up to inject_max_entries per
 * block and /sure_onboard … max_retries=N raises the number of blocks, so leaving that line
 * uncharged let the operator push the block past its configured budget.
 */
export function applyRecallBudget(
	matches: MemoryMatch[],
	config: MemoryConfig,
	alreadyInjected: string[],
): { kept: MemoryMatch[]; repeated: string[] } {
	const seen = new Set(alreadyInjected);
	const repeated: string[] = [];
	const fresh: MemoryMatch[] = [];
	for (const match of matches) {
		if (seen.has(match.entry.entry_id)) {
			repeated.push(match.entry.entry_id);
		} else {
			fresh.push(match);
		}
	}
	const maxEntries = Number.isFinite(config.inject_max_entries)
		? Math.max(0, Math.floor(config.inject_max_entries))
		: 0;
	const perEntry = normalizeBudgetLimit(config.inject_max_chars_per_entry);
	const total = normalizeBudgetLimit(config.inject_max_chars_total);
	const kept: MemoryMatch[] = [];
	// The reminder always comes last, so on its own it carries no separator.
	const reminder = repeated.length > 0 ? codePointLength(renderRepeatedLine(repeated, perEntry)) : 0;
	let used = reminder;
	for (const match of fresh) {
		if (kept.length >= maxEntries) break;
		const line = renderEntryLine(match, perEntry);
		if (total !== undefined) {
			// The first kept line pays the separator the reminder will need behind it.
			const separator = kept.length > 0 || reminder > 0 ? 1 : 0;
			const cost = separator + codePointLength(line);
			if (used + cost > total) break;
			used += cost;
		}
		kept.push(match);
	}
	return { kept, repeated };
}

/**
 * The text appended to a repair. First line is always config.inject_header (digest.py strips
 * from that prefix to the end of the text, so the caller must append this block LAST).
 * "" means nothing to add. When only repeated entries remain, one reminder line is emitted
 * (no usage row, no settlement: those follow usageIds(kept)).
 */
export function buildMemoryBlock(kept: MemoryMatch[], repeated: string[], config: MemoryConfig): string {
	if (kept.length === 0 && repeated.length === 0) return "";
	const perEntry = normalizeBudgetLimit(config.inject_max_chars_per_entry);
	const lines = [config.inject_header];
	for (const match of kept) lines.push(renderEntryLine(match, perEntry));
	if (repeated.length > 0) lines.push(renderRepeatedLine(repeated, perEntry));
	return lines.join("\n");
}

/** Ids for the usage inject row: the entry and, when folded in, its pending revision. */
export function usageIds(kept: MemoryMatch[]): { entry_id: string; shared: boolean }[] {
	const rows: { entry_id: string; shared: boolean }[] = [];
	for (const match of kept) {
		rows.push({ entry_id: match.entry.entry_id, shared: match.entry.target_skill === "_shared" });
		if (match.pendingRevision) {
			rows.push({
				entry_id: match.pendingRevision.entry_id,
				shared: match.pendingRevision.target_skill === "_shared",
			});
		}
	}
	return rows;
}

// --- usage jsonl -----------------------------------------------------------------------

function chmodQuiet(path: string, mode: number): void {
	try {
		chmodSync(path, mode);
	} catch {
		// Best effort: Windows and foreign-owned files. python cli.py fix-perms is the repair.
	}
}

// Directories we create get setgid + group rwx (spec §6.1); existing ones are left alone.
function ensureGroupDir(path: string): void {
	if (existsSync(path)) return;
	mkdirSync(path, { recursive: true });
	chmodQuiet(path, 0o2775);
}

function describeError(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

function nearestExisting(path: string): string {
	let current = path;
	while (!existsSync(current)) {
		const parent = dirname(current);
		if (parent === current) break;
		current = parent;
	}
	return current;
}

function describeWriteError(error: unknown, file: string): string {
	const code = isRecord(error) && typeof error.code === "string" ? error.code : "";
	// The row's file is named as usage/<run_id>.jsonl and the fs error is masked: this text
	// becomes a diagnostic message (see redactHostPaths), and the repair beside it already says
	// the tree is sure/memory/, so the host path adds nothing the agent may act on.
	let text = `usage append failed${code ? ` (${code})` : ""}: usage/${basename(file)}: ${redactHostPaths(describeError(error))}`;
	if (code === "EACCES" || code === "EPERM") {
		const probe = nearestExisting(file);
		try {
			const info = statSync(probe);
			text += `; the nearest existing path there is owned by uid ${info.uid} gid ${info.gid} mode ${(info.mode & 0o7777).toString(8)}`;
		} catch {
			text += "; the nearest existing path there could not be inspected";
		}
		text += "; sure/memory is shared by everyone using this checkout: ask its maintainer to run";
		text += " python3 -s sure/runtime/memory/cli.py fix-perms";
	}
	return text;
}

/**
 * Append one JSON object as a single line to <memoryRoot>/usage/<runId>.jsonl (spec §1.6 rows).
 * Refuses lines over usage_max_line_bytes (nothing written). Never throws: failures come back
 * as ok:false with a message that names the path, the owner and the fix-perms command.
 */
export function appendUsageRow(
	memoryRoot: string,
	runId: string,
	row: Record<string, unknown>,
	config: MemoryConfig,
): { ok: boolean; error?: string } {
	if (!RUN_ID_RE.test(runId)) {
		return { ok: false, error: `usage: refusing run id "${runId}" as a file name` };
	}
	const line = `${JSON.stringify(row)}\n`;
	const bytes = Buffer.byteLength(line, "utf-8");
	if (bytes > config.usage_max_line_bytes) {
		return { ok: false, error: `usage row is ${bytes} bytes, limit ${config.usage_max_line_bytes}; nothing written` };
	}
	const dir = join(memoryRoot, "usage");
	const file = join(dir, `${runId}.jsonl`);
	try {
		ensureGroupDir(memoryRoot);
		ensureGroupDir(dir);
		const fresh = !existsSync(file);
		appendFileSync(file, line, { encoding: "utf-8", mode: 0o664 });
		if (fresh) chmodQuiet(file, 0o664);
		return { ok: true };
	} catch (error) {
		return { ok: false, error: describeWriteError(error, file) };
	}
}

// --- events.jsonl --------------------------------------------------------------------

// The complete lines of events.jsonl: everything up to the last "\n". The tail after it (a
// write still in flight) is not a line yet. Blank and broken lines are still lines: they end
// with "\n" and python's digest.py counts them the same way (plan §1.13), so the cutoff stored
// in the checkpoint and the usage row means the same thing on both sides.
function readEventLines(runDir: string): string[] {
	let raw: string;
	try {
		raw = readFileSync(join(runDir, "events.jsonl"), "utf-8");
	} catch {
		return [];
	}
	const parts = raw.split("\n");
	parts.pop();
	return parts;
}

/** Number of "\n" in events.jsonl, i.e. complete lines (0 when missing). This is the cutoff unit. */
export function readEventCount(runDir: string): number {
	return readEventLines(runDir).length;
}

/** Parsed events after the first `cutoff` complete lines; blank and unparsable lines are skipped. */
export function eventsSince(runDir: string, cutoff: number): unknown[] {
	const lines = readEventLines(runDir);
	const out: unknown[] = [];
	for (let i = Math.max(0, Math.floor(cutoff)); i < lines.length; i++) {
		if (lines[i].trim() === "") continue;
		try {
			out.push(JSON.parse(lines[i]));
		} catch {
			// broken line (torn write that later got its "\n"), skip
		}
	}
	return out;
}
