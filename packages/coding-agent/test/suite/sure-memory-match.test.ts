import { appendFileSync, chmodSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import {
	appendUsageRow,
	applyMemoryConfigDefaults,
	applyRecallBudget,
	buildMemoryBlock,
	eventsSince,
	loadMemoryConfig,
	MEMORY_CONFIG_DEFAULTS,
	type MemoryConfig,
	type MemoryIndex,
	type MemoryIndexEntry,
	matchBadCases,
	matchFacts,
	memoryLibDir,
	memoryRootFor,
	normalizeName,
	readEventCount,
	readMemoryIndex,
	redactHostPaths,
	renderEntryLine,
	triggerHits,
	usageIds,
} from "../../../../sure/runtime/memory/match.ts";

const REPO_ROOT = resolve(__dirname, "../../../..");
const LIB_DIR = join(REPO_ROOT, "sure", "runtime", "memory");
const TMP = resolve(__dirname, "tmp-memory-match");
const CONFIG: MemoryConfig = loadMemoryConfig();

// --- fixtures ---------------------------------------------------------------------------

function entry(overrides: Partial<MemoryIndexEntry> & { entry_id: string }): MemoryIndexEntry {
	const slug = overrides.entry_id.split("/")[1] ?? overrides.entry_id;
	return {
		type: "bad_case",
		status: "confirmed",
		target_skill: "sure_onboard",
		applies_to: ["sure_onboard"],
		component: "build_env",
		cause: "cuda_version_mismatch",
		trigger: [],
		scope: null,
		title: `Title of ${slug}`,
		path: `sure/skills/sure_onboard/references/memory/bad_cases/${slug}.md`,
		legacy: false,
		op: "add",
		target_entry: null,
		similar_entry: null,
		useful_activated: 0,
		useful_unattributed: 0,
		injections: 0,
		disputed: 0,
		created: "2026-08-10",
		checked_at: null,
		stale: false,
		superseded_by: null,
		...overrides,
	};
}

function fact(overrides: Partial<MemoryIndexEntry> & { entry_id: string; scope: string | null }): MemoryIndexEntry {
	const slug = overrides.entry_id.split("/")[1] ?? overrides.entry_id;
	return entry({
		type: "fact",
		status: "provisional",
		target_skill: "_shared",
		applies_to: ["sure_onboard", "sure_infer", "sure_eval", "sure_feed", "_shared"],
		component: "_",
		cause: "n.a.",
		path: `sure/memory/provisional/_shared/${slug}/entry.md`,
		checked_at: "2026-08-10",
		...overrides,
	});
}

function index(entries: MemoryIndexEntry[]): MemoryIndex {
	return {
		schema: "sure.memory.index.v1",
		built_at: "2026-08-18T00:00:00Z",
		sources_sha256: "0".repeat(64),
		entries,
		omitted_provisional: 0,
	};
}

/** Code points, the unit every injection budget in config.json is measured in. */
function codePoints(text: string): number {
	return Array.from(text).length;
}

function ids(matches: { entry: MemoryIndexEntry }[]): string[] {
	return matches.map((match) => match.entry.entry_id);
}

const KERNEL = "no kernel image is available";
const REPAIR = `RuntimeError: CUDA error: ${KERNEL} for execution on the device`;

// --- config and locations -----------------------------------------------------------------

describe("memory match: config and locations", () => {
	it("loads config.json from the library directory", () => {
		expect(memoryLibDir().replace(/\\/g, "/")).toBe(LIB_DIR.replace(/\\/g, "/"));
		expect(CONFIG.schema).toBe("sure.memory.config.v1");
		expect(CONFIG.inject_max_entries).toBe(2);
		expect(CONFIG.inject_header.startsWith("Memory (advisory")).toBe(true);
	});

	it("fills the gate digest caps a config.json written before them does not carry", () => {
		// config.json is tuned by hand and an upgrade never rewrites it (spec 8.2), so every
		// deployment's file predates these two keys. Undefined is not an error anywhere: it turns
		// the entry cap and the byte cap of the gateInputs walk into no-ops that never say a word.
		const shipped = JSON.parse(readFileSync(join(LIB_DIR, "config.json"), "utf-8")) as Record<string, unknown>;
		const older = { ...shipped };
		delete older.gate_digest_max_entries;
		delete older.gate_digest_max_bytes;
		const filled = applyMemoryConfigDefaults(older);
		expect(filled.gate_digest_max_entries).toBe(2000);
		expect(filled.gate_digest_max_bytes).toBe(8388608);
		expect(filled.defaulted_keys).toEqual(["gate_digest_max_entries", "gate_digest_max_bytes"]);
		// A key that is there but is not a number is no better than a missing one.
		const wrong = applyMemoryConfigDefaults({ ...shipped, gate_digest_max_bytes: "8MB" });
		expect(wrong.gate_digest_max_bytes).toBe(8388608);
		expect(wrong.defaulted_keys).toEqual(["gate_digest_max_bytes"]);
		// The shipped file carries both, so a current checkout defaults nothing and stays silent.
		expect(CONFIG.defaulted_keys).toEqual([]);
		expect(CONFIG.gate_digest_max_entries).toBe(MEMORY_CONFIG_DEFAULTS.gate_digest_max_entries);
		expect(CONFIG.gate_digest_max_bytes).toBe(MEMORY_CONFIG_DEFAULTS.gate_digest_max_bytes);
	});

	it("derives sure/memory from the skill package dir", () => {
		const packageDir = join(REPO_ROOT, "sure", "skills", "sure_onboard");
		expect(memoryRootFor(packageDir)).toBe(join(REPO_ROOT, "sure", "memory"));
	});
});

// --- predicate --------------------------------------------------------------------------

describe("memory match: triggerHits", () => {
	it("agrees with every shared vector in fixtures/match_vectors.json", () => {
		// The fixture belongs to Task 3 (python test_proposals.py reads the same file); key is `hit`.
		const raw = JSON.parse(readFileSync(join(LIB_DIR, "fixtures", "match_vectors.json"), "utf-8")) as {
			schema: string;
			note: string;
			vectors: { name: string; trigger: string; text: string; hit: boolean }[];
		};
		expect(raw.schema).toBe("sure.memory.match_vectors.v1");
		expect(raw.vectors.length).toBeGreaterThanOrEqual(12);
		expect(raw.vectors.some((vector) => vector.trigger.trim() === "" && vector.hit === false)).toBe(true);
		for (const vector of raw.vectors) {
			expect(triggerHits(vector.trigger, vector.text), vector.name).toBe(vector.hit);
		}
	});

	it("is a plain case-insensitive substring test with no whitespace folding", () => {
		expect(triggerHits("Partition Not Found", "vc: partition not found: x")).toBe(true);
		expect(triggerHits("  undefined symbol ", "ImportError: undefined symbol: foo")).toBe(true);
		expect(triggerHits("no kernel  image", KERNEL)).toBe(false);
		expect(triggerHits("a.c", "abc")).toBe(false);
		expect(triggerHits("", "anything")).toBe(false);
		expect(triggerHits("   ", "anything")).toBe(false);
	});

	it("normalizeName lower-cases and joins non-alphanumerics with dashes", () => {
		expect(normalizeName("Qwen/Qwen2.5-7B-Instruct")).toBe("qwen-qwen2-5-7b-instruct");
		expect(normalizeName("  --GSM8K@v1  ")).toBe("gsm8k-v1");
	});
});

// --- bad_case filter --------------------------------------------------------------------

describe("memory match: matchBadCases filter", () => {
	const kernel = entry({ entry_id: "sure_onboard/kernel", trigger: [KERNEL] });

	it("hits only the same target_skill and component", () => {
		const idx = index([kernel]);
		expect(ids(matchBadCases(idx, { skill: "sure_onboard", unit: "build_env", text: REPAIR }))).toEqual([
			"sure_onboard/kernel",
		]);
		expect(matchBadCases(idx, { skill: "sure_onboard", unit: "validate_import", text: REPAIR })).toEqual([]);
		expect(matchBadCases(idx, { skill: "sure_eval", unit: "build_env", text: REPAIR })).toEqual([]);
	});

	it("needs a trigger hit; legacy entries without triggers never match", () => {
		const legacy = entry({ entry_id: "sure_onboard/asr_metric_bypass", legacy: true, trigger: [], component: "_" });
		const idx = index([kernel, legacy]);
		expect(matchBadCases(idx, { skill: "sure_onboard", unit: "build_env", text: "something else" })).toEqual([]);
		expect(matchBadCases(idx, { skill: "sure_onboard", unit: "_", text: "ASR metric bypass" })).toEqual([]);
	});

	it("matches on hook_trigger only; a trigger that exists just for the index never injects", () => {
		// publish writes hook_trigger = the triggers seen verbatim in the digest; evidence-only
		// triggers stay in `trigger` for index.md / prompt routing but must not fire the hook.
		const split = entry({
			entry_id: "sure_onboard/split",
			trigger: ["a-hook-trigger", "evidence-only-b"],
			hook_trigger: ["a-hook-trigger"],
		});
		const idx = index([split]);
		expect(
			matchBadCases(idx, { skill: "sure_onboard", unit: "build_env", text: "log says evidence-only-b" }),
		).toEqual([]);
		const hit = matchBadCases(idx, { skill: "sure_onboard", unit: "build_env", text: "log says a-hook-trigger" });
		expect(ids(hit)).toEqual(["sure_onboard/split"]);
		expect(hit[0].hitLength).toBe("a-hook-trigger".length);
		// No hook_trigger on the entry (older index.json): fall back to trigger.
		const plain = entry({ entry_id: "sure_onboard/plain", trigger: ["evidence-only-b"] });
		expect(
			ids(matchBadCases(index([plain]), { skill: "sure_onboard", unit: "build_env", text: "evidence-only-b here" })),
		).toEqual(["sure_onboard/plain"]);
		// The same rule for the trigger part of matchFacts.
		const factSplit = fact({
			entry_id: "_shared/split-fact",
			scope: "model_family:llama",
			trigger: ["site-gpu", "evidence-only-b"],
			hook_trigger: ["site-gpu"],
		});
		expect(
			matchFacts(index([factSplit]), { skill: "sure_eval", targetId: "Qwen/Qwen2.5", args: "evidence-only-b" }),
		).toEqual([]);
	});

	it("excludes superseded and rejected entries and anything with superseded_by set", () => {
		const idx = index([
			entry({ entry_id: "sure_onboard/old", trigger: [KERNEL], status: "superseded" }),
			entry({ entry_id: "sure_onboard/gone", trigger: [KERNEL], status: "rejected" }),
			entry({ entry_id: "sure_onboard/replaced", trigger: [KERNEL], superseded_by: "sure_onboard/new" }),
			entry({ entry_id: "sure_onboard/new", trigger: [KERNEL], status: "provisional" }),
		]);
		expect(ids(matchBadCases(idx, { skill: "sure_onboard", unit: "build_env", text: REPAIR }))).toEqual([
			"sure_onboard/new",
		]);
	});

	it("honours applies_to and ignores facts", () => {
		const idx = index([
			entry({ entry_id: "sure_onboard/other-skill", trigger: [KERNEL], applies_to: ["sure_eval"] }),
			entry({ entry_id: "sure_onboard/shared", trigger: [KERNEL], applies_to: ["_shared"] }),
			fact({ entry_id: "_shared/a-fact", scope: "cluster", trigger: [KERNEL], component: "build_env" }),
		]);
		expect(ids(matchBadCases(idx, { skill: "sure_onboard", unit: "build_env", text: REPAIR }))).toEqual([
			"sure_onboard/shared",
		]);
	});
});

// --- fact filter ------------------------------------------------------------------------

describe("memory match: matchFacts scope rules", () => {
	const cluster = fact({ entry_id: "_shared/partition", scope: "cluster" });
	const qwen = fact({ entry_id: "_shared/qwen-family", scope: "model_family:Qwen2.5" });
	const gsm = fact({ entry_id: "_shared/gsm8k-layout", scope: "dataset:GSM8K" });
	const idx = index([cluster, qwen, gsm]);

	it("cluster facts always match; model_family and dataset match by normalised substring", () => {
		const onQwen = matchFacts(idx, {
			skill: "sure_eval",
			targetId: "Qwen/Qwen2.5-7B-Instruct",
			args: "model=Qwen/Qwen2.5-7B-Instruct datasets=/data/oref/gsm8k@v1",
		});
		expect(ids(onQwen).sort()).toEqual(["_shared/gsm8k-layout", "_shared/partition", "_shared/qwen-family"]);
		const onLlama = matchFacts(idx, { skill: "sure_onboard", targetId: "meta-llama/Llama-3-8B", args: "" });
		expect(ids(onLlama)).toEqual(["_shared/partition"]);
	});

	it("filters by applies_to (current skill or _shared) and skips superseded facts", () => {
		const evalOnly = fact({ entry_id: "_shared/eval-only", scope: "cluster", applies_to: ["sure_eval"] });
		const viaShared = fact({ entry_id: "_shared/via-shared", scope: "cluster", applies_to: ["_shared"] });
		const dead = fact({ entry_id: "_shared/dead", scope: "cluster", status: "superseded" });
		const both = index([evalOnly, viaShared, dead]);
		expect(ids(matchFacts(both, { skill: "sure_onboard", targetId: "x", args: "" }))).toEqual(["_shared/via-shared"]);
		expect(ids(matchFacts(both, { skill: "sure_eval", targetId: "x", args: "" })).sort()).toEqual([
			"_shared/eval-only",
			"_shared/via-shared",
		]);
	});

	it("a trigger found in target id + args is a supplement and raises hitLength", () => {
		const withTrigger = fact({
			entry_id: "_shared/llama-only",
			scope: "model_family:llama",
			trigger: ["site-gpu"],
		});
		const one = index([withTrigger]);
		const miss = matchFacts(one, { skill: "sure_eval", targetId: "Qwen/Qwen2.5", args: "partition=other" });
		expect(miss).toEqual([]);
		const hit = matchFacts(one, { skill: "sure_eval", targetId: "Qwen/Qwen2.5", args: "partition=site-gpu" });
		expect(ids(hit)).toEqual(["_shared/llama-only"]);
		expect(hit[0].hitLength).toBe("site-gpu".length);
	});

	it("scope with an unknown kind or empty name never matches", () => {
		const odd = index([
			fact({ entry_id: "_shared/odd", scope: "region:cn" }),
			fact({ entry_id: "_shared/blank", scope: "model_family:" }),
			fact({ entry_id: "_shared/none", scope: null }),
		]);
		expect(matchFacts(odd, { skill: "sure_eval", targetId: "region cn", args: "" })).toEqual([]);
	});
});

// --- ordering ---------------------------------------------------------------------------

describe("memory match: ordering", () => {
	it("confirmed before provisional before disputed, whatever the hit length", () => {
		const idx = index([
			entry({ entry_id: "sure_onboard/disputed", trigger: [KERNEL], status: "disputed" }),
			entry({ entry_id: "sure_onboard/provisional", trigger: [KERNEL], status: "provisional" }),
			entry({ entry_id: "sure_onboard/confirmed", trigger: ["kernel"], status: "confirmed" }),
		]);
		expect(ids(matchBadCases(idx, { skill: "sure_onboard", unit: "build_env", text: REPAIR }))).toEqual([
			"sure_onboard/confirmed",
			"sure_onboard/provisional",
			"sure_onboard/disputed",
		]);
	});

	it("same tier: longest single hit first, not the number of hits", () => {
		const idx = index([
			entry({ entry_id: "sure_onboard/three-short", trigger: ["kernel", "image", "device"] }),
			entry({ entry_id: "sure_onboard/one-long", trigger: [KERNEL] }),
		]);
		const got = matchBadCases(idx, { skill: "sure_onboard", unit: "build_env", text: REPAIR });
		expect(ids(got)).toEqual(["sure_onboard/one-long", "sure_onboard/three-short"]);
		expect(got[0].hitLength).toBe(KERNEL.length);
		expect(got[1].hitLength).toBe("device".length);
	});

	it("then useful_activated minus disputed, then newest first, legacy oldest", () => {
		const idx = index([
			entry({ entry_id: "sure_onboard/old", trigger: [KERNEL], created: "2026-08-01" }),
			entry({ entry_id: "sure_onboard/legacy", trigger: [KERNEL], created: "legacy", legacy: true }),
			entry({ entry_id: "sure_onboard/new", trigger: [KERNEL], created: "2026-08-15" }),
			entry({
				entry_id: "sure_onboard/useful",
				trigger: [KERNEL],
				created: "2026-07-01",
				useful_activated: 3,
				disputed: 1,
			}),
		]);
		expect(ids(matchBadCases(idx, { skill: "sure_onboard", unit: "build_env", text: REPAIR }))).toEqual([
			"sure_onboard/useful",
			"sure_onboard/new",
			"sure_onboard/old",
			"sure_onboard/legacy",
		]);
	});
});

// --- pending revision and similar -------------------------------------------------------

describe("memory match: pending revision merge and similar skip", () => {
	const target = entry({ entry_id: "sure_onboard/kernel", trigger: [KERNEL] });
	const revision = entry({
		entry_id: "sure_onboard/kernel-v2",
		trigger: [KERNEL],
		status: "provisional",
		op: "modify",
		target_entry: "sure_onboard/kernel",
		path: "sure/memory/provisional/sure_onboard/kernel-v2/entry.md",
	});

	it("folds a modify candidate into its target line and reports both ids for usage", () => {
		const got = matchBadCases(index([target, revision]), { skill: "sure_onboard", unit: "build_env", text: REPAIR });
		expect(ids(got)).toEqual(["sure_onboard/kernel"]);
		expect(got[0].pendingRevision?.entry_id).toBe("sure_onboard/kernel-v2");
		expect(usageIds(got)).toEqual([
			{ entry_id: "sure_onboard/kernel", shared: false },
			{ entry_id: "sure_onboard/kernel-v2", shared: false },
		]);
		const block = buildMemoryBlock(got, [], CONFIG);
		expect(block).toContain("pending revision: sure/memory/provisional/sure_onboard/kernel-v2/entry.md");
	});

	it("a candidate whose target did not hit stays as its own line", () => {
		const other = entry({ entry_id: "sure_onboard/kernel", trigger: ["something unrelated"] });
		const got = matchBadCases(index([other, revision]), { skill: "sure_onboard", unit: "build_env", text: REPAIR });
		expect(ids(got)).toEqual(["sure_onboard/kernel-v2"]);
		expect(got[0].pendingRevision).toBeUndefined();
	});

	it("skips an entry whose similar_entry is already in the list", () => {
		const near = entry({
			entry_id: "sure_onboard/kernel-near",
			trigger: [KERNEL],
			status: "provisional",
			similar_entry: "sure_onboard/kernel",
		});
		expect(
			ids(matchBadCases(index([target, near]), { skill: "sure_onboard", unit: "build_env", text: REPAIR })),
		).toEqual(["sure_onboard/kernel"]);
		const alone = entry({ entry_id: "sure_onboard/kernel", trigger: ["something unrelated"] });
		expect(
			ids(matchBadCases(index([alone, near]), { skill: "sure_onboard", unit: "build_env", text: REPAIR })),
		).toEqual(["sure_onboard/kernel-near"]);
	});
});

// --- budget, dedup, block ---------------------------------------------------------------

describe("memory match: applyRecallBudget and buildMemoryBlock", () => {
	const a = entry({ entry_id: "sure_onboard/a", trigger: [KERNEL], useful_activated: 3 });
	const b = entry({ entry_id: "sure_onboard/b", trigger: [KERNEL], useful_activated: 2 });
	const c = entry({ entry_id: "sure_onboard/c", trigger: [KERNEL], useful_activated: 1 });
	const matches = () => matchBadCases(index([a, b, c]), { skill: "sure_onboard", unit: "build_env", text: REPAIR });

	it("keeps at most inject_max_entries", () => {
		const { kept, repeated } = applyRecallBudget(matches(), CONFIG, []);
		expect(ids(kept)).toEqual(["sure_onboard/a", "sure_onboard/b"]);
		expect(repeated).toEqual([]);
	});

	it("caps one line at inject_max_chars_per_entry code points with a ... suffix", () => {
		const long = entry({ entry_id: "sure_onboard/long", trigger: [KERNEL], title: "T".repeat(500) });
		const got = matchBadCases(index([long]), { skill: "sure_onboard", unit: "build_env", text: REPAIR });
		const { kept } = applyRecallBudget(got, CONFIG, []);
		const line = buildMemoryBlock(kept, [], CONFIG).split("\n")[1];
		expect(Array.from(line).length).toBe(CONFIG.inject_max_chars_per_entry);
		expect(line.endsWith("...")).toBe(true);
		expect(line).toContain("(sure/skills/sure_onboard/references/memory/bad_cases/long.md)");
	});

	it("drops the second line whole when the total would exceed inject_max_chars_total", () => {
		const tight: MemoryConfig = { ...CONFIG, inject_max_chars_total: 120 };
		const { kept } = applyRecallBudget(matches(), tight, []);
		expect(ids(kept)).toEqual(["sure_onboard/a"]);
		const line = buildMemoryBlock(kept, [], tight).split("\n")[1];
		expect(line.endsWith("Title of a")).toBe(true);
	});

	it("already injected entries come back as repeated and are not injected again", () => {
		const { kept, repeated } = applyRecallBudget(matches(), CONFIG, ["sure_onboard/a"]);
		expect(ids(kept)).toEqual(["sure_onboard/b", "sure_onboard/c"]);
		expect(repeated).toEqual(["sure_onboard/a"]);
		const block = buildMemoryBlock(kept, repeated, CONFIG);
		expect(block.endsWith("Entries shown at an earlier attempt still apply: sure_onboard/a")).toBe(true);
	});

	it("only repeated entries: header plus the reminder line, no usage ids", () => {
		const { kept, repeated } = applyRecallBudget(matches(), CONFIG, [
			"sure_onboard/a",
			"sure_onboard/b",
			"sure_onboard/c",
		]);
		expect(kept).toEqual([]);
		expect(usageIds(kept)).toEqual([]);
		const block = buildMemoryBlock(kept, repeated, CONFIG);
		expect(block.split("\n")).toEqual([
			CONFIG.inject_header,
			"Entries shown at an earlier attempt still apply: sure_onboard/a, sure_onboard/b, sure_onboard/c",
		]);
	});

	it("charges the repeated-entries reminder against inject_max_chars_total", () => {
		// The reminder is one more line of the block. Only the header is exempt from the total;
		// leaving this line free let an operator raising max_retries= push a block past its
		// configured budget, since injected[unit] grows by up to inject_max_entries per block.
		const got = matches();
		const withoutReminder = buildMemoryBlock(applyRecallBudget(got, CONFIG, []).kept, [], CONFIG);
		const firstLineLength = codePoints(withoutReminder.split("\n")[1]);
		// Room for exactly one entry line once the reminder is charged, two if it is not.
		const reminder = "Entries shown at an earlier attempt still apply: sure_onboard/c";
		const tight: MemoryConfig = {
			...CONFIG,
			inject_max_chars_total: codePoints(reminder) + firstLineLength + 1 + 10,
		};
		const { kept, repeated } = applyRecallBudget(got, tight, ["sure_onboard/c"]);
		expect(repeated).toEqual(["sure_onboard/c"]);
		expect(ids(kept)).toEqual(["sure_onboard/a"]);
		const block = buildMemoryBlock(kept, repeated, tight);
		const body = block.split("\n").slice(1);
		expect(body).toHaveLength(2);
		expect(body.join("\n").endsWith(reminder)).toBe(true);
		expect(codePoints(body.join("\n"))).toBeLessThanOrEqual(tight.inject_max_chars_total);
	});

	it("the reminder alone still fits its own budget and is cut per entry", () => {
		const { kept, repeated } = applyRecallBudget(matches(), CONFIG, [
			"sure_onboard/a",
			"sure_onboard/b",
			"sure_onboard/c",
		]);
		const tiny: MemoryConfig = { ...CONFIG, inject_max_chars_per_entry: 40 };
		const line = buildMemoryBlock(kept, repeated, tiny).split("\n")[1];
		expect(codePoints(line)).toBe(40);
		expect(line.endsWith("...")).toBe(true);
	});

	it("nothing matched: empty block", () => {
		expect(buildMemoryBlock([], [], CONFIG)).toBe("");
	});

	it("block starts with the config header and one line per entry with status, id, path and title", () => {
		const disputed = entry({
			entry_id: "sure_onboard/d",
			trigger: [KERNEL],
			status: "disputed",
			title: "Two\nlines",
		});
		const got = matchBadCases(index([a, disputed]), { skill: "sure_onboard", unit: "build_env", text: REPAIR });
		const { kept } = applyRecallBudget(got, CONFIG, []);
		const block = buildMemoryBlock(kept, [], CONFIG);
		expect(block.split("\n")).toEqual([
			CONFIG.inject_header,
			"- [confirmed] sure_onboard/a (sure/skills/sure_onboard/references/memory/bad_cases/a.md): Title of a",
			"- [disputed] sure_onboard/d (sure/skills/sure_onboard/references/memory/bad_cases/d.md): Two lines",
		]);
		// §1.13: first line is exactly the header, no blank line anywhere, no trailing newline
		// (digest.py strips from the header to the next blank line; hooks join with "\n\n" before it).
		expect(block.startsWith(`${CONFIG.inject_header}\n`)).toBe(true);
		expect(block.split("\n").some((line) => line.trim() === "")).toBe(false);
		expect(block.endsWith("\n")).toBe(false);
		// renderEntryLine is the one renderer: "- [status] entry_id (path[; pending revision: path]): title".
		expect(renderEntryLine(kept[0], undefined)).toBe(
			"- [confirmed] sure_onboard/a (sure/skills/sure_onboard/references/memory/bad_cases/a.md): Title of a",
		);
		const withRevision = { ...kept[0], pendingRevision: entry({ entry_id: "sure_onboard/a-v2", path: "p/v2.md" }) };
		expect(renderEntryLine(withRevision, undefined)).toBe(
			"- [confirmed] sure_onboard/a (sure/skills/sure_onboard/references/memory/bad_cases/a.md; pending revision: p/v2.md): Title of a",
		);
	});

	it("drops an entry path that is not repo-relative, and the parenthesis with it", () => {
		// index.py's _rel() falls back to an absolute posix path whenever relative_to(repo_root)
		// raises, and resolve() follows symlinks — the normal shape when sure/memory or a skill's
		// references/memory/ is a symlink on a shared cluster filesystem. Rendered verbatim it is
		// a host path in the repair the agent reads and in run.json.lastRepair, and it is long
		// enough that truncateLine cuts inside the parenthesis, leaving a line with no closing
		// paren, no colon and no title.
		const shared = "/srv/sure/sure-harness/sure/skills/sure_onboard/references/memory";
		const absolute = { ...a, path: `${shared}/bad_cases/vc-partition-name-mismatch-on-the-shared-cluster.md` };
		const line = renderEntryLine({ entry: absolute, hitLength: 4 }, CONFIG.inject_max_chars_per_entry);
		expect(line).toBe("- [confirmed] sure_onboard/a: Title of a");
		expect(line).not.toContain(shared);
		expect(line).not.toContain("...");

		// A drive-letter path is just as absolute.
		expect(renderEntryLine({ entry: { ...a, path: "D:/sure/memory/a.md" }, hitLength: 4 }, undefined)).toBe(
			"- [confirmed] sure_onboard/a: Title of a",
		);
		// Only the offending half is dropped when a pending revision rides along.
		const mixed = {
			entry: absolute,
			hitLength: 4,
			pendingRevision: entry({ entry_id: "sure_onboard/a-v2", path: "p/v2.md" }),
		};
		expect(renderEntryLine(mixed, undefined)).toBe(
			"- [confirmed] sure_onboard/a (pending revision: p/v2.md): Title of a",
		);
	});

	it("usageIds marks _shared entries as shared", () => {
		const shared = fact({ entry_id: "_shared/partition", scope: "cluster" });
		const got = matchFacts(index([shared]), { skill: "sure_eval", targetId: "x", args: "" });
		expect(usageIds(got)).toEqual([{ entry_id: "_shared/partition", shared: true }]);
	});
});

// --- usage jsonl ------------------------------------------------------------------------

describe("memory match: appendUsageRow", () => {
	const root = join(TMP, "usage-root", "sure", "memory");

	beforeEach(() => {
		rmSync(join(TMP, "usage-root"), { recursive: true, force: true });
	});

	it("appends single-line JSON rows and creates the directories", () => {
		const first = appendUsageRow(root, "20260818-120000-abcdef12", { kind: "inject", unit: "build_env" }, CONFIG);
		expect(first).toEqual({ ok: true });
		const second = appendUsageRow(root, "20260818-120000-abcdef12", { kind: "settle", note: "two\nlines" }, CONFIG);
		expect(second.ok).toBe(true);
		const lines = readFileSync(join(root, "usage", "20260818-120000-abcdef12.jsonl"), "utf-8").split("\n");
		expect(lines).toHaveLength(3);
		expect(lines[2]).toBe("");
		expect(JSON.parse(lines[0])).toEqual({ kind: "inject", unit: "build_env" });
		expect(JSON.parse(lines[1])).toEqual({ kind: "settle", note: "two\nlines" });
	});

	it("refuses a row over usage_max_line_bytes and writes nothing", () => {
		const result = appendUsageRow(root, "run-1", { blob: "x".repeat(CONFIG.usage_max_line_bytes) }, CONFIG);
		expect(result.ok).toBe(false);
		expect(result.error).toContain("bytes");
		expect(existsSync(join(root, "usage", "run-1.jsonl"))).toBe(false);
	});

	it("refuses a run id that is not a plain file name", () => {
		const result = appendUsageRow(root, "../escape", { kind: "inject" }, CONFIG);
		expect(result.ok).toBe(false);
		expect(existsSync(join(root, "escape.jsonl"))).toBe(false);
	});

	it("reports a write failure by file name instead of throwing", () => {
		mkdirSync(root, { recursive: true });
		writeFileSync(join(root, "usage"), "not a directory", "utf-8");
		const result = appendUsageRow(root, "run-1", { kind: "inject" }, CONFIG);
		expect(result.ok).toBe(false);
		expect(result.error).toContain("usage append failed");
		// The hooks turn this into the "memory usage row not written" diagnostic, which the agent
		// reads and digest.py can carry into the next run: it may name the row's file, not where
		// sure/memory sits on this host.
		expect(result.error).toContain("usage/run-1.jsonl");
		expect(result.error).not.toContain(root);
		expect(result.error).not.toContain(root.split("\\").join("/"));
	});

	// As root every file is writable, so there is no permission failure to describe.
	it.skipIf(typeof process.getuid === "function" && process.getuid?.() === 0)(
		"describes the owner of an unwritable usage file without naming its path",
		() => {
			mkdirSync(join(root, "usage"), { recursive: true });
			const file = join(root, "usage", "run-2.jsonl");
			writeFileSync(file, "", "utf-8");
			chmodSync(file, 0o444); // EPERM on windows, EACCES on posix: both take the probe branch
			try {
				const result = appendUsageRow(root, "run-2", { kind: "inject" }, CONFIG);
				expect(result.ok).toBe(false);
				// The probe is what makes this diagnostic worth reading on a shared checkout, so it
				// keeps the ownership it found; only the path it probed goes.
				expect(result.error).toMatch(/uid \d+/);
				expect(result.error).toContain("fix-perms");
				expect(result.error).not.toContain(root);
				expect(result.error).not.toContain(root.split("\\").join("/"));
			} finally {
				chmodSync(file, 0o666);
			}
		},
	);
});

// --- index reading ----------------------------------------------------------------------

describe("memory match: readMemoryIndex", () => {
	const root = join(TMP, "index-root");

	beforeEach(() => {
		rmSync(root, { recursive: true, force: true });
		mkdirSync(root, { recursive: true });
	});

	it("missing file, bad JSON and unknown schema all come back as ok:false", () => {
		expect(readMemoryIndex(root).ok).toBe(false);
		expect(readMemoryIndex(root).error).toContain("index.json");
		writeFileSync(join(root, "index.json"), "{not json", "utf-8");
		expect(readMemoryIndex(root).error).toContain("not valid JSON");
		writeFileSync(join(root, "index.json"), JSON.stringify({ schema: "sure.memory.index.v9", entries: [] }), "utf-8");
		const unknown = readMemoryIndex(root);
		expect(unknown.ok).toBe(false);
		expect(unknown.error).toContain("unknown schema");
	});

	it("masks a host path that contains a space", () => {
		// A Windows account name with a space ("C:\\Users\\Ann Lee\\...") is ordinary. The mask used
		// to stop at the first space, so the drive and first segment went and the rest of the path --
		// including the account name -- stayed in the text the agent reads and digest.py can carry
		// into stored memory.
		const masked = redactHostPaths(
			"EACCES: permission denied, mkdir 'C:\\Users\\Ann Lee\\dev\\sure\\.sure\\runs\\r1'",
		);
		expect(masked).not.toContain("Ann");
		expect(masked).not.toContain("Lee");
		expect(masked).not.toContain("dev");
		expect(masked).toContain("EACCES: permission denied, mkdir");
	});

	it("stops the mask at the path, not at the end of the sentence", () => {
		// The space-tolerant mask must not swallow the prose after the path: a following word is
		// only part of the path when a separator still comes before the next space.
		const masked = redactHostPaths("cannot read C:\\tmp\\a.json because the disk is full");
		expect(masked).toBe("cannot read <path> because the disk is full");
		expect(redactHostPaths("open /var/tmp/x.log failed twice")).toBe("open <path> failed twice");
	});

	it("names index.json in every error and its host path in none", () => {
		// These errors are the message of the "memory index unavailable" diagnostics the hooks
		// raise at pre_start and on a gate block, so the agent reads them and digest.py can carry
		// them into the next run. The file is always sure/memory/index.json; where that sits on
		// this host is not the agent's business and must not be stored.
		const posix = root.split("\\").join("/");
		const missing = readMemoryIndex(root);
		expect(missing.ok).toBe(false);
		// The reason survives, only the path goes.
		expect(missing.error).toContain("ENOENT");
		expect(missing.error).not.toContain(root);
		expect(missing.error).not.toContain(posix);

		writeFileSync(join(root, "index.json"), "{not json", "utf-8");
		const broken = readMemoryIndex(root);
		expect(broken.error).toContain("not valid JSON");
		expect(broken.error).not.toContain(root);
		expect(broken.error).not.toContain(posix);

		writeFileSync(join(root, "index.json"), JSON.stringify({ schema: "sure.memory.index.v1" }), "utf-8");
		const noEntries = readMemoryIndex(root);
		expect(noEntries.ok).toBe(false);
		expect(noEntries.error).toContain("no entries array");
		expect(noEntries.error).not.toContain(root);
		expect(noEntries.error).not.toContain(posix);
	});

	it("normalises entries and drops the malformed ones", () => {
		writeFileSync(
			join(root, "index.json"),
			JSON.stringify({
				schema: "sure.memory.index.v1",
				built_at: "2026-08-18T00:00:00Z",
				sources_sha256: "abc",
				omitted_provisional: 3,
				entries: [
					{
						entry_id: "sure_onboard/ok",
						type: "bad_case",
						status: "confirmed",
						target_skill: "sure_onboard",
						trigger: ["x", 5, null],
						hook_trigger: ["x", 7],
					},
					{ type: "bad_case", status: "confirmed", target_skill: "sure_onboard" },
					"junk",
					{
						entry_id: "sure_onboard/meta-shape",
						type: "bad_case",
						status: "provisional",
						target_skill: "sure_onboard",
						created: { run_id: "r", date: "2026-08-17" },
					},
				],
			}),
			"utf-8",
		);
		const got = readMemoryIndex(root);
		expect(got.ok).toBe(true);
		expect(got.index?.omitted_provisional).toBe(3);
		expect(ids(got.index?.entries.map((item) => ({ entry: item })) ?? [])).toEqual([
			"sure_onboard/ok",
			"sure_onboard/meta-shape",
		]);
		const ok = got.index?.entries[0];
		expect(ok?.trigger).toEqual(["x"]);
		expect(ok?.hook_trigger).toEqual(["x"]);
		expect(ok?.applies_to).toEqual([]);
		expect(ok?.component).toBe("_");
		expect(ok?.useful_activated).toBe(0);
		expect(ok?.created).toBeNull();
		expect(got.index?.entries[1].created).toBe("2026-08-17");
		// no hook_trigger in the file: left undefined so matching falls back to trigger
		expect(got.index?.entries[1].hook_trigger).toBeUndefined();
	});

	it("reads the golden index written by the python tests and every entry matches itself", () => {
		// readMemoryIndex wants <root>/index.json, so stage the golden file under a temp root.
		writeFileSync(join(root, "index.json"), readFileSync(join(LIB_DIR, "fixtures", "golden_index.json")));
		const golden = readMemoryIndex(root);
		expect(golden.ok, golden.error).toBe(true);
		const idx = golden.index as MemoryIndex;
		expect(idx.entries.length).toBeGreaterThan(0);
		for (const item of idx.entries) {
			const single = index([item]);
			const matchable =
				["confirmed", "provisional", "disputed"].includes(item.status) && item.superseded_by === null;
			// what the hook matches on: hook_trigger when the index carries it, else trigger
			const hookTriggers = item.hook_trigger ?? item.trigger;
			if (item.type === "bad_case") {
				const own = matchBadCases(single, {
					skill: item.target_skill,
					unit: item.component,
					text: hookTriggers[0] ?? "",
				});
				expect(own.length, item.entry_id).toBe(matchable && hookTriggers.length > 0 ? 1 : 0);
				const stray = matchBadCases(single, {
					skill: item.target_skill,
					unit: item.component,
					text: `${item.title} ${item.path}`,
				});
				if (hookTriggers.length === 0) expect(stray, item.entry_id).toEqual([]);
			} else if (item.type === "fact" && item.scope === "cluster") {
				const own = matchFacts(single, { skill: item.applies_to[0] ?? "_shared", targetId: "any", args: "" });
				expect(own.length, item.entry_id).toBe(matchable ? 1 : 0);
			}
		}
	});
});

// --- events.jsonl -----------------------------------------------------------------------

describe("memory match: readEventCount and eventsSince", () => {
	const runDir = join(TMP, "run-events");

	beforeEach(() => {
		rmSync(runDir, { recursive: true, force: true });
		mkdirSync(runDir, { recursive: true });
	});

	it("counts newline-terminated lines and parses only the complete lines after the cutoff", () => {
		// §1.13: the cutoff unit is the number of "\n" in events.jsonl. A blank line and a broken
		// line still count (they end with "\n"); a torn tail without "\n" does not exist yet.
		const lines = [
			JSON.stringify({ type: "created", timestamp: "t0", data: {} }),
			JSON.stringify({
				type: "tool_call",
				timestamp: "t1",
				data: { toolName: "bash", input: { command: "cat x" } },
			}),
			"",
			JSON.stringify({ type: "tool_result", timestamp: "t2", data: { toolName: "bash", isError: false } }),
			'{"type":"bad json"',
		];
		const events = join(runDir, "events.jsonl");
		writeFileSync(events, `${lines.join("\n")}\n{"type":"torn`, "utf-8");
		expect(readEventCount(runDir)).toBe(5);
		const since = eventsSince(runDir, 1) as { type: string }[];
		expect(since.map((event) => event.type)).toEqual(["tool_call", "tool_result"]);
		expect(eventsSince(runDir, 5)).toEqual([]);
		expect(eventsSince(runDir, -1)).toHaveLength(3);
		// the torn tail becomes a (bad) line the moment its "\n" lands
		appendFileSync(events, "\n", "utf-8");
		expect(readEventCount(runDir)).toBe(6);
		expect(eventsSince(runDir, 5)).toEqual([]);
		// a file with no "\n" at all has zero complete lines
		writeFileSync(events, JSON.stringify({ type: "created", timestamp: "t0", data: {} }), "utf-8");
		expect(readEventCount(runDir)).toBe(0);
		expect(eventsSince(runDir, 0)).toEqual([]);
	});

	it("missing events.jsonl reads as zero events", () => {
		expect(readEventCount(join(TMP, "no-such-run"))).toBe(0);
		expect(eventsSince(join(TMP, "no-such-run"), 0)).toEqual([]);
	});
});
