import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
	closeSync,
	existsSync,
	lstatSync,
	mkdirSync,
	openSync,
	readdirSync,
	readFileSync,
	readSync,
	realpathSync,
	statSync,
	writeFileSync,
} from "node:fs";
import { basename, dirname, isAbsolute, join, normalize, relative, sep } from "node:path";
import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import {
	type HarnessRuntimeContract,
	harnessRuntimeEnv,
	repoRootForPackage,
	resolveHarnessPython,
} from "../harness/resolve.ts";
import {
	appendUsageRow,
	applyRecallBudget,
	buildMemoryBlock,
	displayPath,
	eventsSince,
	loadMemoryConfig,
	MEMORY_CONFIG_DEFAULTS,
	type MemoryConfig,
	type MemoryIndex,
	type MemoryMatch,
	matchBadCases,
	matchFacts,
	memoryLibDir,
	memoryRootFor,
	readEventCount,
	readMemoryIndex,
	redactHostPaths,
	triggerHits,
	usageIds,
} from "./match.ts";

// Memory orchestration shared by the sure_onboard, sure_infer and sure_eval hooks.
//
// This module never imports anything from a skill package: the skills' hooks
// import it (like ../harness/resolve.ts) and pass their own context in. Every
// number comes from config.json through match.ts loadMemoryConfig(); nothing
// memory-related may block a run, so the functions here return diagnostics
// instead of throwing wherever a failure is possible.
//
// The checkpoint's `memory` sub-object is the only place memory state lives
// between hook calls (there is no memory_state.json): checkpoints.ts of both
// skills read it back with readMemory() and carry it through advance/bumpRetry.
//
// hooks/index.ts of each skill calls these functions at the points listed in
// docs/superpowers/specs/2026-08-18-memory-system-design.md: digest at unit
// entry (4.2), gate exhaustion and non-success finish (4.5), injection on a
// gate block (7.2), settlement (8.1), publish (6.2). The only python this file
// runs are the thin wrappers under <packageDir>/scripts/: hooks may never
// spawnSync anything outside a skill's own scripts/ directory, including
// sure/runtime/memory/index.py itself (index check at pre_start goes through
// scripts/check_memory_index.py, a fourth thin wrapper next to the other three).

/** id of the extraction unit both state machines insert (spec 4.1). */
export const EXTRACT_LESSONS_UNIT_ID = "extract_lessons";

/** Memory fields carried inside CheckpointData (spec 4.2, plan skeleton 1.5). */
export interface MemoryCheckpoint {
	/** events.jsonl line count when the run digest was built. */
	digestCutoff?: number;
	/** sha256 of the artifacts/run_digest.json the hook built. */
	digestSha256?: string;
	/** unit id passed as --mark-passed when the digest was built. */
	digestPassed?: string;
	/** how many times pre_finish (non-success) has already asked for an extraction declaration. */
	finishAttempts?: number;
	/** set when the extraction gate was exhausted, pre_finish gave up asking, or the gate could not run. */
	extractionStatus?: "failed";
	/** unit id -> entry ids already injected into that unit's repairs (dedup). */
	injected?: Record<string, string[]>;
	/** unit id -> entry ids the last block still hit (settled as disputed on terminal failure). */
	pendingDisputed?: Record<string, string[]>;
}

/** What every memory hook function needs from the calling skill hook. */
export interface MemoryHookEnv {
	ctx: SureHookContext;
	skill: "sure_onboard" | "sure_infer" | "sure_eval" | "sure_trans" | "sure_feed";
	/** harness python contract when preStart already resolved it; undefined otherwise. */
	py: HarnessRuntimeContract | undefined;
}

/** Diagnostics shape the skill hooks merge into their state_patch.diagnostics. */
export type MemoryDiagnostic = { severity: "info" | "warning" | "error"; message: string; repair: string };

const DIGEST_FILE = "run_digest.json";
const DIGEST_SCHEMA = "sure.memory.run_digest.v1";
const DECLARATION_FILE = "extraction_declaration.json";
const CONTEXT_FILE = "memory_context.json";
const CONTEXT_SCHEMA = "sure.memory.context.v1";
const ARTIFACT_LOG_PREFIX = "artifact:";
const FIX_PERMS_HINT = "A maintainer can run: python3 -s sure/runtime/memory/cli.py fix-perms";
const EXTRACTION_DOC = "sure/runtime/memory/EXTRACTION.md";
// Stand-ins gateDigest hashes in place of a file's bytes. They start with NUL, which
// cannot occur in a path, so no file content can be mistaken for one of them.
const GATE_DIGEST_OVER_BUDGET = "\0size:";
const GATE_DIGEST_UNREADABLE = "\0unreadable";
const GATE_DIGEST_TRUNCATED = "\0truncated\0";

// --- small helpers -----------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
	return typeof value === "number" && Number.isFinite(value);
}

// Record<string, string[]> reader: non-array values drop the key, non-string items are dropped.
function readStringListMap(value: unknown): Record<string, string[]> | undefined {
	if (!isRecord(value)) {
		return undefined;
	}
	const out: Record<string, string[]> = {};
	for (const [key, list] of Object.entries(value)) {
		if (Array.isArray(list)) {
			out[key] = list.filter((item): item is string => typeof item === "string");
		}
	}
	return out;
}

/**
 * The one place a MemoryDiagnostic is built, and the one place its text is masked.
 *
 * Diagnostics are shown to the agent, kept in state.json and carried on by digest.py, so an
 * absolute host path in one of them can be lifted into a trigger and stored in sure/memory for
 * good. Most of them quote something the hook did not write — an fs error, a script's stderr,
 * a traceback — so the guarantee cannot rest on each call site remembering: everything that
 * goes through here is passed through match.ts redactHostPaths, which leaves repo-relative
 * paths alone. Never build a MemoryDiagnostic literal outside this function.
 */
function diag(severity: MemoryDiagnostic["severity"], message: string, repair: string): MemoryDiagnostic {
	return { severity, message: redactHostPaths(message), repair: redactHostPaths(repair) };
}

/**
 * loadMemoryConfig() throws when sure/runtime/memory/config.json is missing or its schema
 * moved on (match.ts), and that file is meant to be tuned by hand (spec 8.2). No memory
 * failure may take a hook down (spec 11), so every entry point below reads the config
 * through this and returns its own no-op result when it is not there.
 */
function tryConfig(): { config?: MemoryConfig; error?: string } {
	try {
		return { config: loadMemoryConfig() };
	} catch (error) {
		return { error: `memory config unreadable: config.json (${configErrorTag(error)})` };
	}
}

/**
 * The error code (ENOENT, EACCES) or class name (SyntaxError, MemoryConfigSchemaError) of a
 * config failure, and nothing else. The message is deliberately dropped: an fs error carries
 * the absolute host path of config.json, and this string becomes the top-level repair the
 * agent reads, diagnostics[0].repair and run.json.lastRepair, which digest.py then hands to
 * the next run as prior_runs[].last_repair — from where an agent can lift it into a trigger
 * and store it in sure/memory permanently. proposals.py's gate redacts the same way.
 */
function configErrorTag(error: unknown): string {
	if (isRecord(error) && typeof error.code === "string" && error.code) {
		return error.code;
	}
	return error instanceof Error && error.name ? error.name : typeof error;
}

/** The one diagnostic every entry point returns when config.json could not be read. */
function configFailure(error: string | undefined): MemoryDiagnostic {
	return diag(
		"warning",
		error ?? "memory config unreadable",
		"The memory system is off for this run; the skill itself is not blocked by it. Restore sure/runtime/memory/config.json from the checkout.",
	);
}

/**
 * config.json is missing keys that were added after it was written, so the run is honouring a
 * number nobody chose (match.ts MEMORY_CONFIG_DEFAULTS). Raised once per run at pre_start: it is
 * a property of the file, not of the moment, and the caps it covers are read on every gate.
 * Without it the situation is invisible — the schema matches, nothing throws, and the cap simply
 * does nothing.
 */
function configDefaultsUsed(keys: string[]): MemoryDiagnostic {
	const shown = keys.map((key) => `${key} (using ${MEMORY_CONFIG_DEFAULTS[key]})`).join(", ");
	return diag(
		"warning",
		`memory config.json does not carry ${shown}; the built-in default is in force for this run`,
		"Add the keys to sure/runtime/memory/config.json so the values are the ones this deployment chose; the run is not blocked by it.",
	);
}

/**
 * The memory config, or undefined when config.json cannot be read. For the skills' hooks,
 * which need a MemoryConfig for isExtractionGateExhausted and must not throw either.
 */
export function memoryConfigOrUndefined(): MemoryConfig | undefined {
	return tryConfig().config;
}

function utcNow(): string {
	// Same second precision as python paths.utc_now() so usage rows sort together.
	return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function sha256File(path: string): string {
	return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function firstLine(text: string): string {
	return text.split(/\r?\n/, 1)[0] ?? "";
}

function truncate(text: string, max: number): string {
	return text.length > max ? `${text.slice(0, max)}...` : text;
}

function toPosix(path: string): string {
	return path.split("\\").join("/");
}

function pruneEmpty(memory: MemoryCheckpoint): MemoryCheckpoint {
	const next = { ...memory };
	if (next.injected && Object.keys(next.injected).length === 0) {
		delete next.injected;
	}
	if (next.pendingDisputed && Object.keys(next.pendingDisputed).length === 0) {
		delete next.pendingDisputed;
	}
	return next;
}

/**
 * Read the memory sub-object out of a checkpoint data object (raw JSON from
 * state.json or an already typed CheckpointData). Every key is checked by
 * type; anything unknown or mistyped is dropped, so a hand-edited state.json
 * cannot smuggle odd values into the hooks. Missing OR wrong-typed memory -> {}.
 *
 * Known wart, inherited from Task 10, kept as is: each skill's readCheckpoint
 * (checkpoints.ts) calls this as `isRecord(data.memory) ? readMemory(data) :
 * undefined`, so a present-but-wrong-typed `memory` key yields `undefined` one
 * layer up in the skill hooks, not the `{}` this function itself would give
 * it. Both fallbacks are deliberate; they just do not agree with each other.
 */
export function readMemory(checkpointData: unknown): MemoryCheckpoint {
	const data = isRecord(checkpointData) ? checkpointData : {};
	const raw = isRecord(data.memory) ? data.memory : {};
	const memory: MemoryCheckpoint = {};
	if (isFiniteNumber(raw.digestCutoff)) {
		memory.digestCutoff = raw.digestCutoff;
	}
	if (typeof raw.digestSha256 === "string") {
		memory.digestSha256 = raw.digestSha256;
	}
	if (typeof raw.digestPassed === "string") {
		memory.digestPassed = raw.digestPassed;
	}
	if (isFiniteNumber(raw.finishAttempts)) {
		memory.finishAttempts = raw.finishAttempts;
	}
	if (raw.extractionStatus === "failed") {
		memory.extractionStatus = "failed";
	}
	const injected = readStringListMap(raw.injected);
	if (injected) {
		memory.injected = injected;
	}
	const pendingDisputed = readStringListMap(raw.pendingDisputed);
	if (pendingDisputed) {
		memory.pendingDisputed = pendingDisputed;
	}
	return memory;
}

/** run_id is the run directory name; ctx.run.runId is undefined in the hook test fixtures. */
export function runIdOf(ctx: SureHookContext): string {
	return basename(ctx.runDir);
}

/**
 * Drop `output_dir` from a skill's argument string. Same token rules as
 * splitOutputDir in packages/coding-agent/src/core/sure/output-dir.ts (copied,
 * not imported: hooks must not import from packages/). Used by pre_start so the
 * text matched against fact triggers never carries the harness-owned path.
 */
export function stripOutputDir(args: string): string {
	const tokens = args.trim().split(/\s+/).filter(Boolean);
	const rest: string[] = [];
	for (let i = 0; i < tokens.length; i++) {
		const token = tokens[i];
		const eq = token.indexOf("=");
		if (eq >= 0) {
			if (token.slice(0, eq) !== "output_dir") {
				rest.push(token);
			}
			continue;
		}
		if (token.replace(/^--?/, "") !== "output_dir") {
			rest.push(token);
			continue;
		}
		const next = tokens[i + 1];
		if (next !== undefined && !next.startsWith("-")) {
			i++; // the bare form consumes its value token
		}
	}
	return rest.join(" ");
}

// --- checkpoint-level helpers (Task 10 skeleton) --------------------------------

/** One regular file found under a gateInputs path, with the size lstat reported for it. */
interface GateFile {
	file: string;
	size: number;
}

/** How many more entries (files and directories together) the gateInputs walk may visit. */
interface WalkBudget {
	entries: number;
}

/**
 * Files under `path` (a file or a directory), depth first, into `out`.
 *
 * artifacts/candidates and artifacts/memory_evidence are filled by the agent with
 * arbitrary bash, so this walk treats the tree as hostile and never throws:
 *
 *   - lstatSync, never statSync: a symlink is never followed, so a link pointing at
 *     an ancestor cannot make this recurse until the stack dies. Anything that is
 *     neither a regular file nor a directory is skipped.
 *   - every directory is entered at most once, keyed by its real path.
 *   - an unreadable directory contributes nothing instead of throwing.
 *   - the budget counts every entry visited, so both breadth and depth are bounded.
 *
 * Names are sorted so which files survive an exhausted budget is deterministic.
 */
function collectFiles(path: string, out: GateFile[], budget: WalkBudget, visited: Set<string>): void {
	if (budget.entries <= 0) {
		return;
	}
	budget.entries -= 1;
	let info: ReturnType<typeof lstatSync>;
	try {
		info = lstatSync(path);
	} catch {
		return;
	}
	if (info.isFile()) {
		out.push({ file: path, size: info.size });
		return;
	}
	if (!info.isDirectory()) {
		return;
	}
	let real: string;
	try {
		real = realpathSync(path);
	} catch {
		real = path;
	}
	if (visited.has(real)) {
		return;
	}
	visited.add(real);
	let names: string[];
	try {
		names = readdirSync(path);
	} catch {
		return;
	}
	for (const name of names.sort(compareStrings)) {
		collectFiles(join(path, name), out, budget, visited);
	}
}

function compareStrings(a: string, b: string): number {
	if (a < b) {
		return -1;
	}
	return a > b ? 1 : 0;
}

/**
 * Joint hash of a unit's produces file plus every file under its gateInputs
 * (paths relative to artifacts/). With no gateInputs the result is exactly
 * sha256(produces bytes), so units that do not declare gateInputs keep the
 * failedArtifactDigests values they had before this function existed. With
 * gateInputs, files are visited in sorted relative-path order (forward slashes
 * on every platform) and each one feeds "<relpath>\0" + bytes + "\0", so both a
 * content change and a rename change the digest. NUL is the separator because
 * it cannot occur in a path, so two different (path, bytes) sequences cannot
 * collide. Missing inputs are skipped. The produces file must exist (callers
 * check that first, as before). The pinned vitest case in
 * sure-memory-hooks.test.ts recomputes this by hand.
 *
 * The gateInputs trees are agent-writable, so the walk is bounded by
 * gate_digest_max_entries and gate_digest_max_bytes from config.json: past the byte
 * budget, or for a file that cannot be read, the file's size (or a fixed marker)
 * stands in for its bytes, so the digest stays stable and this function keeps
 * hashing instead of throwing. Both stand-ins weaken change detection for the files
 * they cover — a same-size edit no longer moves the digest — which costs a retry
 * that was not consumed, never a run that cannot proceed. Without a readable
 * config.json there are no budgets to honour, so gateInputs are skipped entirely and
 * the result is sha256(produces).
 */
export function gateDigest(ctx: SureHookContext, unit: { produces: string; gateInputs?: string[] }): string {
	const artifactsDir = join(ctx.runDir, "artifacts");
	const hash = createHash("sha256");
	hash.update(readFileSync(join(artifactsDir, unit.produces)));
	const inputs = unit.gateInputs ?? [];
	const { config } = tryConfig();
	if (inputs.length === 0 || !config) {
		return hash.digest("hex");
	}
	const budget: WalkBudget = { entries: config.gate_digest_max_entries };
	const visited = new Set<string>();
	const files: GateFile[] = [];
	for (const input of inputs) {
		collectFiles(join(artifactsDir, input), files, budget, visited);
	}
	const seen = new Set<string>();
	const entries: { rel: string; found: GateFile }[] = [];
	for (const found of files) {
		const rel = relative(artifactsDir, found.file).split(sep).join("/");
		if (!seen.has(rel)) {
			seen.add(rel);
			entries.push({ rel, found });
		}
	}
	entries.sort((a, b) => compareStrings(a.rel, b.rel));
	let bytesLeft = config.gate_digest_max_bytes;
	for (const entry of entries) {
		hash.update(`${entry.rel}\0`);
		if (entry.found.size > bytesLeft) {
			hash.update(`${GATE_DIGEST_OVER_BUDGET}${entry.found.size}`);
		} else {
			try {
				hash.update(readFileSync(entry.found.file));
				bytesLeft -= entry.found.size;
			} catch {
				hash.update(GATE_DIGEST_UNREADABLE);
			}
		}
		hash.update("\0");
	}
	if (budget.entries <= 0) {
		hash.update(GATE_DIGEST_TRUNCATED);
	}
	return hash.digest("hex");
}

/**
 * gateDigest for the two call sites in each skill's hooks (unchangedFailedArtifact and
 * failOrRetry), with every failure turned into a value instead of a throw.
 *
 * A hook that throws is turned by the harness into `{ok:false, repair:"Fix the hook
 * failure in hooks/index.ts and retry the action."}` with NO state_patch, so no retry
 * is consumed and every following tool call reproduces it: the agent cannot get out.
 * The digest only exists to notice that the artifacts changed, so any failure here
 * degrades to sha256(produces) — the value units without gateInputs use — and, when
 * even produces cannot be read (it was deleted while the gate ran), to undefined.
 *
 * undefined means "no digest for this attempt": callers must not compare it against a
 * stored digest, since two absent digests are not evidence of unchanged content.
 */
export function safeGateDigest(
	ctx: SureHookContext,
	unit: { produces: string; gateInputs?: string[] },
): string | undefined {
	try {
		return gateDigest(ctx, unit);
	} catch {
		// The walk itself is bounded and throw-free, so this is the produces read or an
		// exotic filesystem error; fall back to the produces bytes alone.
	}
	try {
		return sha256File(join(ctx.runDir, "artifacts", unit.produces));
	} catch {
		return undefined;
	}
}

/**
 * The extraction gate does not follow the generic "exhausted => FAILED" rule:
 * after `extraction_gate_max_failures` consecutive blocks the hook advances
 * anyway (spec 4.5). onboard's `max_retries=` argument may only raise that cap
 * (userMax); eval passes undefined. Any other unit id is never "exhausted"
 * here, so callers can ask without checking the unit first.
 */
export function isExtractionGateExhausted(
	unitId: string,
	attempts: number,
	userMax: number | undefined,
	config: MemoryConfig,
): boolean {
	if (unitId !== EXTRACT_LESSONS_UNIT_ID) {
		return false;
	}
	const raised = isFiniteNumber(userMax) ? userMax : 0;
	return attempts >= Math.max(config.extraction_gate_max_failures, raised);
}

// --- python + scripts ------------------------------------------------------------

/** HARNESS_PYTHON_BIN first (activateHarnessRuntime exports it after pre_start), then the harness bootstrap. */
export function resolveMemoryPython(packageDir: string): { ok: boolean; python?: string; error?: string } {
	const fromEnv = process.env.HARNESS_PYTHON_BIN?.trim();
	if (fromEnv) {
		return { ok: true, python: fromEnv };
	}
	const runtime = resolveHarnessPython(packageDir);
	if (runtime.ok && runtime.contract) {
		return { ok: true, python: runtime.contract.python_executable };
	}
	return { ok: false, error: runtime.error ?? "HARNESS_RUNTIME_NOT_READY" };
}

interface ScriptRun {
	ok: boolean;
	status: number | null;
	stdout: string;
	stderr: string;
	/** Set when the script could not run at all (missing file, no python, spawn error, timeout). */
	error?: string;
}

function runMemoryScript(env: MemoryHookEnv, script: string, args: string[], timeoutMs: number): ScriptRun {
	const failed = (error: string): ScriptRun => ({ ok: false, status: null, stdout: "", stderr: "", error });
	if (!existsSync(script)) {
		return failed(
			`Backend script not found: ${toPosix(relative(env.ctx.packageDir, script))}. Bundle the Python backend into the skill package.`,
		);
	}
	const python = env.py?.python_executable
		? { ok: true, python: env.py.python_executable }
		: resolveMemoryPython(env.ctx.packageDir);
	if (!python.ok || !python.python) {
		return failed(python.error ?? "HARNESS_RUNTIME_NOT_READY");
	}
	const r = spawnSync(python.python, [script, ...args], {
		cwd: env.ctx.packageDir,
		encoding: "utf-8",
		timeout: timeoutMs,
		env: { ...process.env, ...(env.py ? harnessRuntimeEnv(env.py) : {}) },
	});
	if (r.error) {
		return {
			ok: false,
			status: r.status ?? null,
			stdout: r.stdout ?? "",
			stderr: r.stderr ?? "",
			error: `${basename(script)}: ${r.error.message}`,
		};
	}
	return { ok: r.status === 0, status: r.status, stdout: r.stdout ?? "", stderr: r.stderr ?? "" };
}

function scriptFailureText(run: ScriptRun, fallback: string): string {
	// Same precedence as runGateScript in the skill hooks: stderr, then stdout, then a generic line.
	return run.error ?? (run.stderr.trim() || run.stdout.trim() || fallback);
}

/** First line of a python traceback; its presence means the script died instead of judging. */
const TRACEBACK_MARK = "Traceback (most recent call last):";

/**
 * How proposals.py main() opens every verdict it writes to stderr: the gate name for
 * `<gate>: N problem(s)`, `<gate> crashed: <Type>` and `check_memory_extraction cannot read
 * <file>`, or the declaration's own name for `<file> not found at <path>`.
 *
 * This is the gate's structured output, not a guess about its prose: proposals.py prints one of
 * these first and only then quotes the declaration back, so the head of stderr is the one place
 * the agent's own text can never reach. Inferring "the gate crashed" from a marker anywhere in
 * the text let a declaration carrying `Traceback (most recent call last):` describe its own
 * rejection as a crashed gate, and a crashed gate is waved through.
 */
const GATE_VERDICT_PREFIXES = ["check_memory_extraction", `${DECLARATION_FILE} not found at`];

/**
 * Did scripts/check_memory_extraction.py judge the declaration, or did it die before it could?
 *
 * True only when stderr opens with a verdict proposals.py wrote. Anything else — a traceback
 * from an import that never reached main(), an argparse usage line, silence — means no verdict
 * was reached, and callers must read that as "not gated" rather than as a rejection or a pass.
 */
function gateJudged(stderr: string): boolean {
	const lines = stderr.split(/\r?\n/).map((line) => line.trim());
	const head = lines.find((line) => line !== "") ?? "";
	return GATE_VERDICT_PREFIXES.some((prefix) => head.startsWith(prefix));
}

/** The exception class or errno a failed script named, or a fixed fallback. Nothing else. */
function scriptErrorClass(text: string): string {
	const lines = text
		.split(/\r?\n/)
		.map((line) => line.trim())
		.filter(Boolean);
	const last = lines[lines.length - 1] ?? "";
	const named = /\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Interrupt))\b/.exec(last) ?? /\b(E[A-Z]{2,})\b/.exec(last);
	return named?.[1] ?? "unknown error";
}

/**
 * Text from a memory script that could not run or died, reduced to something that may be shown
 * to the agent and stored: a traceback becomes the exception class alone, anything else keeps
 * its first line with absolute paths masked.
 *
 * A gate that never judged the declaration hands back a traceback whose frames carry absolute
 * host paths, and spawnSync failures put the absolute interpreter path in their message. Both
 * would otherwise become the repair, diagnostics[0].repair and run.json.lastRepair, which
 * digest.py carries into the next run's prior_runs for an agent to lift into a stored trigger.
 * proposals.py's own crash handler already prints type(exc).__name__ and never the message.
 * A gate rejection the agent CAN act on never comes through here.
 *
 * `script` names the wrapper the text came from; it only shows in the traceback case.
 */
export function redactScriptFailure(text: string, script = "check_memory_extraction.py"): string {
	if (text.includes(TRACEBACK_MARK)) {
		return `${script} crashed: ${scriptErrorClass(text)}`;
	}
	return redactHostPaths(firstLine(text));
}

/**
 * Runs scripts/check_memory_extraction.py with the same --run-dir/--produces contract as
 * every other gate script. `ranFailed` separates "the declaration was rejected" from "the
 * gate could not run at all" (missing wrapper, no interpreter, spawn error, timeout);
 * pre_finish must not turn the second case into a repair for the agent.
 */
export function runMemoryGate(
	env: MemoryHookEnv,
	producesPath: string,
): { ok: boolean; repair?: string; reason?: string; ranFailed?: boolean } {
	const { config, error: configError } = tryConfig();
	if (!config) {
		return { ok: false, repair: configError, reason: "memory config unreadable", ranFailed: true };
	}
	const script = join(env.ctx.packageDir, "scripts", "check_memory_extraction.py");
	const args = [
		"--run-dir",
		env.ctx.runDir,
		"--produces",
		producesPath,
		"--repo-root",
		repoRootForPackage(env.ctx.packageDir),
	];
	const run = runMemoryScript(env, script, args, config.publish_timeout_ms);
	if (run.ok) {
		return { ok: true };
	}
	// ScriptRun.error is only set when the script never ran. Past that, the gate is taken to have
	// judged only when stderr opens with a verdict it wrote itself (gateJudged): proposals.py
	// catches everything around its own checks and prints a repairable line, so a stderr that
	// starts with anything else — an import traceback, an argparse usage line, nothing at all —
	// comes from a broken memory installation, not from a declaration the agent can fix. Reading
	// this off the text the gate quotes back would let the declaration decide it.
	const ranFailed = run.error !== undefined || !gateJudged(run.stderr);
	const text = scriptFailureText(run, `Gate script scripts/check_memory_extraction.py exited ${run.status}.`);
	return {
		ok: false,
		// A rejection the agent can act on is kept whole, only masked: it becomes the top-level
		// repair, run.json.lastRepair and, through digest.py, the next run's prior_runs.
		repair: ranFailed ? redactScriptFailure(text) : redactHostPaths(text),
		reason: "gate script check_memory_extraction.py failed",
		ranFailed,
	};
}

/**
 * The diagnostic for an extraction gate that could not run at all, on the unit path.
 * preFinishExtraction carries the same warning for the finish path and says why: a gate that
 * never ran produces a traceback, not something an agent can repair, and blocking on it would
 * stall the unit — failOrRetry's unchanged-artifact guard consumes no retry when the agent has
 * nothing to change, so the auto-advance cap is never reached.
 */
export function gateUnavailable(repair: string | undefined): MemoryDiagnostic {
	return diag(
		"warning",
		`memory extraction gate could not run: ${firstLine(repair ?? "unknown error")}`,
		`The unit is not blocked by the memory system and nothing is published for this run. Check scripts/check_memory_extraction.py and the python interpreter, then see ${EXTRACTION_DOC}.`,
	);
}

// --- digest ------------------------------------------------------------------

function writeErrorDigest(path: string, error: string): string | undefined {
	// The agent must still be able to declare no_new_lessons and cite the error (spec 4.2).
	try {
		mkdirSync(dirname(path), { recursive: true });
		writeFileSync(
			path,
			`${JSON.stringify({ schema: DIGEST_SCHEMA, error: truncate(error, 2000) }, null, 2)}\n`,
			"utf-8",
		);
		return sha256File(path);
	} catch {
		return undefined; // artifacts/ unwritable: the checkpoint simply carries no sha
	}
}

/** The `error` string of a {schema, error} digest, or undefined when the file is not one. */
function errorDigestText(path: string): string | undefined {
	try {
		const data = JSON.parse(readFileSync(path, "utf-8"));
		if (isRecord(data) && typeof data.error === "string" && data.error.trim()) {
			return data.error.trim();
		}
	} catch {
		// missing, torn or not JSON: treat it as no digest at all
	}
	return undefined;
}

/**
 * Spawns scripts/build_run_digest.py; on any failure leaves a {schema, error}
 * digest behind so the unit can still be declared. `--skill` is always passed
 * (the fixture runs have no run.json, so python cannot learn the skill
 * otherwise); `--finish-status` only from pre_finish, where the events do not
 * yet say the run is ending non-success (skeleton 1.13).
 */
export function buildDigest(
	env: MemoryHookEnv,
	opts: { cutoff: number; markPassed?: string; finishStatus?: string },
): { ok: boolean; sha256?: string; error?: string } {
	const ctx = env.ctx;
	const digestPath = join(ctx.runDir, "artifacts", DIGEST_FILE);
	const { config, error: configError } = tryConfig();
	if (!config) {
		const error = configError ?? "memory config unreadable";
		return { ok: false, sha256: writeErrorDigest(digestPath, error), error };
	}
	const script = join(ctx.packageDir, "scripts", "build_run_digest.py");
	const args = [
		"--run-dir",
		ctx.runDir,
		"--repo-root",
		repoRootForPackage(ctx.packageDir),
		"--cutoff",
		String(opts.cutoff),
		"--skill",
		env.skill,
	];
	if (opts.markPassed) {
		args.push("--mark-passed", opts.markPassed);
	}
	if (opts.finishStatus) {
		args.push("--finish-status", opts.finishStatus);
	}
	// config.json has no per-script budget for the digest; the publish budget (60 s) is the largest memory-script budget.
	const run = runMemoryScript(env, script, args, config.publish_timeout_ms);
	if (run.ok && existsSync(digestPath)) {
		return { ok: true, sha256: sha256File(digestPath) };
	}
	if (run.status === 1) {
		// exit 1 means the script already wrote the {schema, error} digest itself (skeleton
		// 1.13). Keep its bytes: rewriting them would put our own reading of stdout, which is
		// the script's `{"path": "<absolute digest path>", …}` line, into the digest.
		const written = errorDigestText(digestPath);
		if (written !== undefined) {
			return { ok: false, sha256: sha256File(digestPath), error: written };
		}
	}
	// Redacted like every other script failure: a spawn error carries the absolute interpreter
	// path and a traceback carries its frames, and this text is written into run_digest.json,
	// the file the repair sends the agent to read and the one digest.py stores.
	const error = run.ok
		? `build_run_digest.py exited 0 but artifacts/${DIGEST_FILE} is missing`
		: redactScriptFailure(scriptFailureText(run, `build_run_digest.py exited ${run.status}`), "build_run_digest.py");
	return { ok: false, sha256: writeErrorDigest(digestPath, error), error };
}

function digestFailure(error: string | undefined): MemoryDiagnostic {
	return diag(
		"warning",
		`memory digest failed: ${error ?? "unknown error"}`,
		`artifacts/${DIGEST_FILE} only carries the error; the extract_lessons unit can still be completed by declaring no_new_lessons=true and citing that error (see ${EXTRACTION_DOC}).`,
	);
}

/** Called right after advance() lands on extract_lessons: build the digest and remember cutoff / sha / passed unit. */
export function onEnterExtractLessons(
	env: MemoryHookEnv,
	passedUnitId: string,
	memory: MemoryCheckpoint,
): { memory: MemoryCheckpoint; diagnostics: MemoryDiagnostic[] } {
	const cutoff = readEventCount(env.ctx.runDir);
	const built = buildDigest(env, { cutoff, markPassed: passedUnitId });
	const next: MemoryCheckpoint = { ...memory, digestCutoff: cutoff, digestPassed: passedUnitId };
	if (built.sha256) {
		next.digestSha256 = built.sha256;
	} else {
		delete next.digestSha256;
	}
	return { memory: next, diagnostics: built.ok ? [] : [digestFailure(built.error)] };
}

/**
 * on_error: keep a digest for the next run's prior_runs. Nothing is published from here.
 *
 * The rebuild is skipped when the file on disk is still the one the checkpoint's
 * digestSha256 names. That digest was validated by the extraction gate (rule 9 checks each
 * candidate's source.digest_sha256 against it), post_finish never ran, and the documented
 * recovery is a hand run of scripts/publish_memory.py --run-dir, which derives every entry's
 * hook_trigger from whatever run_digest.json then holds: rebuilding it here with a later
 * cutoff and no --mark-passed drops the triggers the gate accepted, and an entry whose
 * hook_trigger comes out empty can never inject again. onError's patch carries no checkpoint,
 * so digestSha256 would keep pointing at bytes that no longer exist.
 */
export function onErrorDigest(env: MemoryHookEnv, memory?: MemoryCheckpoint): { diagnostics: MemoryDiagnostic[] } {
	const digestPath = join(env.ctx.runDir, "artifacts", DIGEST_FILE);
	const gated = memory?.digestSha256;
	if (gated && existsSync(digestPath)) {
		try {
			if (sha256File(digestPath) === gated) {
				return { diagnostics: [] };
			}
		} catch {
			// unreadable: fall through and rebuild, there is nothing to preserve
		}
	}
	const built = buildDigest(env, { cutoff: readEventCount(env.ctx.runDir) });
	return { diagnostics: built.ok ? [] : [digestFailure(built.error)] };
}

// --- log tail (same window as digest.py read_log_tail) --------------------------

/** Last `lines` lines of a log, reading at most `seekBytes` from the end; splits on \n and \r; empty lines dropped. */
export function readLogTail(path: string, limits: { lines: number; lineChars: number; seekBytes: number }): string[] {
	let fd: number | undefined;
	try {
		const size = statSync(path).size;
		const start = Math.max(0, size - limits.seekBytes);
		const length = size - start;
		const buffer = Buffer.alloc(length);
		fd = openSync(path, "r");
		readSync(fd, buffer, 0, length, start);
		const lines = buffer
			.toString("utf-8")
			.split(/\r\n|\r|\n/)
			.filter((line) => line.length > 0);
		return lines
			.slice(-limits.lines)
			.map((line) => (line.length > limits.lineChars ? line.slice(0, limits.lineChars) : line));
	} catch {
		return []; // unreadable log: the repair text alone is matched
	} finally {
		if (fd !== undefined) {
			closeSync(fd);
		}
	}
}

function tailLimits(config: MemoryConfig): { lines: number; lineChars: number; seekBytes: number } {
	return {
		lines: config.digest_limits.log_tail_lines,
		lineChars: config.digest_limits.log_line_chars,
		seekBytes: config.digest_limits.log_seek_bytes,
	};
}

function loadLogPaths(): Record<string, unknown> {
	try {
		const parsed = JSON.parse(readFileSync(join(memoryLibDir(), "log_paths.json"), "utf-8"));
		return isRecord(parsed) ? parsed : {};
	} catch {
		return {}; // no table: nothing but the caller's producesPath can be used
	}
}

/**
 * A produces JSON's `log_path`, resolved the way run_validate.log_path_for does
 * (skeleton 1.3): absolute as is; `artifacts/...` relative to the validation
 * cwd, which is the product dir (digest.py does the same; ctx.cwd only when no
 * product dir is known); anything else under <run_dir>/artifacts/. Only an
 * existing file counts.
 */
function resolveDeclaredLogPath(raw: string, ctx: SureHookContext, productDir: string | undefined): string | undefined {
	let candidate: string;
	if (isAbsolute(raw)) {
		candidate = raw;
	} else if (raw.startsWith("artifacts/")) {
		candidate = join(productDir ?? ctx.cwd, raw);
	} else {
		candidate = join(ctx.runDir, "artifacts", raw);
	}
	return existsSync(candidate) ? candidate : undefined;
}

/** The string field `log_path` of an artifact JSON, or undefined when the file is missing, unreadable or has none. */
function declaredLogPath(artifactPath: string): string | undefined {
	try {
		const data = JSON.parse(readFileSync(artifactPath, "utf-8"));
		return isRecord(data) && typeof data.log_path === "string" && data.log_path.trim()
			? data.log_path.trim()
			: undefined;
	} catch {
		return undefined;
	}
}

/**
 * The unit's log file for the injection match text: the caller's producesPath
 * (its own `log_path`) first when given, then log_paths.json in order, where an
 * `artifact:<name>` entry means "<run_dir>/artifacts/<name>'s log_path" and a
 * template entry has {run_dir} / {product_dir} filled in. First existing file
 * wins. The table lists the produces artifact of every unit that has one, so
 * producesPath is only a shortcut for callers that already have it.
 */
export function resolveUnitLogPath(
	env: MemoryHookEnv,
	args: { unitId: string; productDir?: string; producesPath?: string },
): string | undefined {
	const ctx = env.ctx;
	if (args.producesPath) {
		const raw = declaredLogPath(args.producesPath);
		const found = raw ? resolveDeclaredLogPath(raw, ctx, args.productDir) : undefined;
		if (found) {
			return found;
		}
	}
	const table = loadLogPaths()[env.skill];
	const declared = isRecord(table) ? table[args.unitId] : undefined;
	const entries: unknown[] = Array.isArray(declared) ? declared : [];
	for (const entry of entries) {
		if (typeof entry !== "string" || !entry) {
			continue;
		}
		if (entry.startsWith(ARTIFACT_LOG_PREFIX)) {
			const raw = declaredLogPath(join(ctx.runDir, "artifacts", entry.slice(ARTIFACT_LOG_PREFIX.length)));
			const found = raw ? resolveDeclaredLogPath(raw, ctx, args.productDir) : undefined;
			if (found) {
				return found;
			}
			continue;
		}
		if (entry.includes("{product_dir}") && !args.productDir) {
			continue;
		}
		// normalize() so the platform separator is used throughout (templates are written with "/").
		const candidate = normalize(
			entry
				.split("{run_dir}")
				.join(ctx.runDir)
				.split("{product_dir}")
				.join(args.productDir ?? ""),
		);
		if (existsSync(candidate)) {
			return candidate;
		}
	}
	return undefined;
}

// --- usage rows ------------------------------------------------------------------

/** Rows of sure/memory/usage/<run_id>.jsonl; unparsable lines are skipped (python readers count them). */
export function readUsageRows(memoryRoot: string, runId: string): Record<string, unknown>[] {
	const path = join(memoryRoot, "usage", `${runId}.jsonl`);
	if (!existsSync(path)) {
		return [];
	}
	const rows: Record<string, unknown>[] = [];
	for (const line of readFileSync(path, "utf-8").split("\n")) {
		const text = line.trim();
		if (!text) {
			continue;
		}
		try {
			const value = JSON.parse(text);
			if (isRecord(value)) {
				rows.push(value);
			}
		} catch {
			// torn or corrupt line
		}
	}
	return rows;
}

function usageWriteFailure(memoryRoot: string, error: string | undefined): MemoryDiagnostic {
	let owner = "unknown";
	try {
		owner = String(statSync(memoryRoot).uid);
	} catch {
		// memory root not created yet
	}
	return diag(
		"warning",
		`memory usage row not written: ${error ?? "unknown error"}`,
		`sure/memory/ must stay group-writable for everyone using this checkout (owner uid ${owner}). ${FIX_PERMS_HINT}`,
	);
}

function settledIds(rows: Record<string, unknown>[], unitId: string): Set<string> {
	const ids = new Set<string>();
	for (const row of rows) {
		if (row.kind === "settle" && row.unit === unitId && typeof row.entry_id === "string") {
			ids.add(row.entry_id);
		}
	}
	return ids;
}

/** entry id -> events_cutoff of the earliest inject row of this unit that listed it. */
function injectCutoffs(rows: Record<string, unknown>[], unitId: string): Map<string, number> {
	const cutoffs = new Map<string, number>();
	for (const row of rows) {
		if (
			row.kind !== "inject" ||
			row.unit !== unitId ||
			typeof row.events_cutoff !== "number" ||
			!Array.isArray(row.entries)
		) {
			continue;
		}
		for (const entry of row.entries) {
			if (!isRecord(entry) || typeof entry.entry_id !== "string") {
				continue;
			}
			const known = cutoffs.get(entry.entry_id);
			if (known === undefined || row.events_cutoff < known) {
				cutoffs.set(entry.entry_id, row.events_cutoff);
			}
		}
	}
	return cutoffs;
}

function appendSettle(
	env: MemoryHookEnv,
	memoryRoot: string,
	config: MemoryConfig,
	unitId: string,
	entryId: string,
	// "abandoned" is deliberately not in usage.py OUTCOMES: _fold skips the row whole, so it moves
	// no counter and cannot promote or demote anything. Known ceiling: the archive seed in
	// cli.py rebuilds `settles` from the three counted outcomes, so an entry whose only settles are
	// abandoned reads as cold again once prune_usage folds its runs away (500 runs by default).
	// The upgrade is an `abandoned` field on usage.Counts, which drags meta.schema.json, the golden
	// index fixture and a row of test expectations with it; not worth it for that one statistic.
	outcome: "useful_activated" | "useful_unattributed" | "disputed" | "abandoned",
): MemoryDiagnostic | undefined {
	const runId = runIdOf(env.ctx);
	const row = {
		kind: "settle",
		run_id: runId,
		skill: env.skill,
		unit: unitId,
		entry_id: entryId,
		outcome,
		at: utcNow(),
	};
	const written = appendUsageRow(memoryRoot, runId, row, config);
	return written.ok ? undefined : usageWriteFailure(memoryRoot, written.error);
}

// --- injection on a gate block (7.2) ------------------------------------------------

/** Same predicate as matchBadCases: hook_trigger when the index carries it, else trigger (skeleton 1.7). */
function entryHits(index: MemoryIndex, entryId: string, text: string): boolean {
	const entry = index.entries.find((candidate) => candidate.entry_id === entryId);
	if (!entry) {
		return false;
	}
	return (entry.hook_trigger ?? entry.trigger).some((trigger) => triggerHits(trigger, text));
}

/**
 * Called by failOrRetry only when a retry is really consumed. Returns the repair
 * with the Memory block appended (or unchanged), the memory sub-object to put
 * into the bumped checkpoint, and diagnostics. attempt = retries after the bump.
 * The block comes from match.ts buildMemoryBlock (one renderer for the whole
 * system): first line config.inject_header, one line per entry, no blank line,
 * glued to the raw repair with "\n\n" so digest.py strip_memory_block can cut it.
 */
export function injectOnBlock(
	env: MemoryHookEnv,
	args: {
		unitId: string;
		attempt: number;
		rawRepair: string;
		productDir?: string;
		producesPath?: string;
		memory: MemoryCheckpoint;
	},
): { repair: string; memory: MemoryCheckpoint; diagnostics: MemoryDiagnostic[] } {
	const { config, error: configError } = tryConfig();
	if (!config) {
		return { repair: args.rawRepair, memory: args.memory, diagnostics: [configFailure(configError)] };
	}
	const memoryRoot = memoryRootFor(env.ctx.packageDir);
	const diagnostics: MemoryDiagnostic[] = [];
	const loaded = readMemoryIndex(memoryRoot);
	if (!loaded.ok || !loaded.index) {
		diagnostics.push(
			diag(
				"warning",
				`memory index unavailable, injection skipped: ${loaded.error ?? "unknown error"}`,
				"The next pre_start rebuilds sure/memory/index.json; by hand: python3 -s sure/runtime/memory/index.py --repo-root <repo> --rebuild",
			),
		);
		// pendingDisputed[unit] means "the LAST block's failure text still named these entries".
		// Without an index this block named nothing, so the list has to go: if this is the block
		// that exhausts the unit, settleOnTerminalFailure would otherwise write disputed rows for
		// entries this failure never mentioned, and two of those meet demote_disputed_streak.
		const pendingDisputed = { ...(args.memory.pendingDisputed ?? {}) };
		delete pendingDisputed[args.unitId];
		return { repair: args.rawRepair, memory: pruneEmpty({ ...args.memory, pendingDisputed }), diagnostics };
	}
	const index = loaded.index;
	const logPath = resolveUnitLogPath(env, args);
	const tail = logPath ? readLogTail(logPath, tailLimits(config)) : [];
	const text = tail.length > 0 ? `${args.rawRepair}\n${tail.join("\n")}` : args.rawRepair;

	const injected = { ...(args.memory.injected ?? {}) };
	const pendingDisputed = { ...(args.memory.pendingDisputed ?? {}) };
	const already = injected[args.unitId] ?? [];
	// Entries shown at an earlier block of this unit that the new failure text still names (8.1: pending until the unit's end).
	const stillHit = already.filter((id) => entryHits(index, id, text));
	if (stillHit.length > 0) {
		pendingDisputed[args.unitId] = stillHit;
	} else {
		delete pendingDisputed[args.unitId];
	}

	const matches = matchBadCases(index, { skill: env.skill, unit: args.unitId, text });
	const { kept, repeated } = applyRecallBudget(matches, config, already);
	const block = buildMemoryBlock(kept, repeated, config);
	const repair = block ? `${args.rawRepair}\n\n${block}` : args.rawRepair;
	if (kept.length > 0) {
		const entries = usageIds(kept);
		const runId = runIdOf(env.ctx);
		const row = {
			kind: "inject",
			run_id: runId,
			skill: env.skill,
			unit: args.unitId,
			attempt: args.attempt,
			events_cutoff: readEventCount(env.ctx.runDir),
			entries,
			at: utcNow(),
		};
		const written = appendUsageRow(memoryRoot, runId, row, config);
		if (!written.ok) {
			diagnostics.push(usageWriteFailure(memoryRoot, written.error));
		}
		const fresh = entries.map((entry) => entry.entry_id).filter((id) => !already.includes(id));
		injected[args.unitId] = [...already, ...fresh];
	}
	return { repair, memory: pruneEmpty({ ...args.memory, injected, pendingDisputed }), diagnostics };
}

// --- settlement (8.1) --------------------------------------------------------------

function readNeedles(index: MemoryIndex | undefined, entryId: string): string[] {
	// The entry's file path as the index records it, plus "<skill>/<slug>/" which
	// appears in every provisional / outbox path even when the index is stale.
	const needles = [`${entryId}/`];
	const entry = index?.entries.find((candidate) => candidate.entry_id === entryId);
	if (entry?.path) {
		needles.unshift(toPosix(entry.path));
	}
	return needles;
}

/** True when a tool_call after `cutoff` read the entry: read/ls path or bash command containing the entry path. */
function wasReadAfter(runDir: string, cutoff: number, needles: string[]): boolean {
	for (const event of eventsSince(runDir, cutoff)) {
		if (!isRecord(event) || event.type !== "tool_call" || !isRecord(event.data) || !isRecord(event.data.input)) {
			continue;
		}
		const input = event.data.input;
		for (const value of [input.path, input.command]) {
			if (typeof value !== "string") {
				continue;
			}
			const text = toPosix(value);
			if (needles.some((needle) => text.includes(needle))) {
				return true;
			}
		}
	}
	return false;
}

/** The unit's memory bookkeeping is over: drop its injected and pendingDisputed lists (empty maps are pruned). */
function forgetUnit(memory: MemoryCheckpoint, unitId: string): MemoryCheckpoint {
	const injected = { ...(memory.injected ?? {}) };
	const pendingDisputed = { ...(memory.pendingDisputed ?? {}) };
	delete injected[unitId];
	delete pendingDisputed[unitId];
	return pruneEmpty({ ...memory, injected, pendingDisputed });
}

/**
 * The unit passed: every entry injected into it settles as useful (activated
 * when it was read after its inject row), pending disputes are void. Afterwards
 * the unit's injected / pendingDisputed lists are cleared, so a second call
 * (post_tool_result re-entering advance on the last unit) finds nothing; the
 * settle rows already on disk are the second guard when the memory object was
 * not persisted between the two calls.
 */
export function settleOnPass(
	env: MemoryHookEnv,
	args: { unitId: string; memory: MemoryCheckpoint },
): { memory: MemoryCheckpoint; diagnostics: MemoryDiagnostic[] } {
	const memory = forgetUnit(args.memory, args.unitId);
	const ids = args.memory.injected?.[args.unitId] ?? [];
	if (ids.length === 0) {
		return { memory, diagnostics: [] };
	}
	const { config, error: configError } = tryConfig();
	if (!config) {
		return { memory, diagnostics: [configFailure(configError)] };
	}
	const memoryRoot = memoryRootFor(env.ctx.packageDir);
	const rows = readUsageRows(memoryRoot, runIdOf(env.ctx));
	// An abandoned row is not a settlement the unit earned, so it must not suppress this one:
	// on_error abandons the stuck unit and /sure_resume then reuses the same run id and usage file.
	const settled = settledIds(
		rows.filter((row) => row.outcome !== "abandoned"),
		args.unitId,
	);
	const cutoffs = injectCutoffs(rows, args.unitId);
	const loaded = readMemoryIndex(memoryRoot);
	const diagnostics: MemoryDiagnostic[] = [];
	for (const id of ids) {
		if (settled.has(id)) {
			continue; // idempotent: a settle row for (unit, entry) already exists in this run
		}
		const cutoff = cutoffs.get(id);
		if (cutoff === undefined) {
			diagnostics.push(
				diag(
					"info",
					`memory settle skipped for ${id}: no inject row in usage/${runIdOf(env.ctx)}.jsonl`,
					"Nothing to do.",
				),
			);
			continue;
		}
		const read = wasReadAfter(env.ctx.runDir, cutoff, readNeedles(loaded.index, id));
		const failure = appendSettle(
			env,
			memoryRoot,
			config,
			args.unitId,
			id,
			read ? "useful_activated" : "useful_unattributed",
		);
		if (failure) {
			diagnostics.push(failure);
		}
	}
	return { memory, diagnostics };
}

/**
 * The unit ended in failure (retries exhausted, or the run finishes while it is
 * still blocked): pending disputes settle as disputed, and everything else that
 * was injected into the unit settles as abandoned - shown and neither followed
 * nor contradicted, which is not the entry's fault but must still leave a row,
 * or a run that gives up after the first block leaves the entry injected and
 * never settled. The unit's lists are cleared afterwards, same idempotency
 * rules as settleOnPass.
 */
export function settleOnTerminalFailure(
	env: MemoryHookEnv,
	args: { unitId: string; memory: MemoryCheckpoint },
): { memory: MemoryCheckpoint; diagnostics: MemoryDiagnostic[] } {
	const memory = forgetUnit(args.memory, args.unitId);
	const disputed = args.memory.pendingDisputed?.[args.unitId] ?? [];
	const abandoned = (args.memory.injected?.[args.unitId] ?? []).filter((id) => !disputed.includes(id));
	if (disputed.length === 0 && abandoned.length === 0) {
		return { memory, diagnostics: [] };
	}
	const { config, error: configError } = tryConfig();
	if (!config) {
		return { memory, diagnostics: [configFailure(configError)] };
	}
	const memoryRoot = memoryRootFor(env.ctx.packageDir);
	const settled = settledIds(readUsageRows(memoryRoot, runIdOf(env.ctx)), args.unitId);
	const diagnostics: MemoryDiagnostic[] = [];
	for (const [outcome, ids] of [
		["disputed", disputed],
		["abandoned", abandoned],
	] as const) {
		for (const id of ids) {
			if (settled.has(id)) {
				continue;
			}
			const failure = appendSettle(env, memoryRoot, config, args.unitId, id, outcome);
			if (failure) {
				diagnostics.push(failure);
			}
		}
	}
	return { memory, diagnostics };
}

// --- pre_start: index check + facts context (6.4, 7.2) --------------------------------

function byCheckedAtDesc(a: MemoryMatch, b: MemoryMatch): number {
	const left = a.entry.checked_at ?? "";
	const right = b.entry.checked_at ?? "";
	return left === right ? 0 : left > right ? -1 : 1;
}

/**
 * memory_context.json budget: every confirmed fact, then at most `maxProvisional` unconfirmed
 * ones, each group newest checked_at first.
 *
 * A pending revision is charged too. contextFacts emits it as an item of its own right after its
 * target, and a modify / supersede candidate is itself unconfirmed, so counting only the
 * top-level matches let a provisional entry ride into the file for free — neither against the
 * budget nor into omitted_provisional. When only the revision does not fit, the target still
 * goes in without it.
 */
function contextBudget(matches: MemoryMatch[], maxProvisional: number): { kept: MemoryMatch[]; omitted: number } {
	const confirmed = matches.filter((match) => match.entry.status === "confirmed").sort(byCheckedAtDesc);
	const others = matches.filter((match) => match.entry.status !== "confirmed").sort(byCheckedAtDesc);
	const kept: MemoryMatch[] = [];
	let left = Math.max(0, maxProvisional);
	let omitted = 0;
	const cost = (entry: MemoryMatch["entry"] | undefined): number => (entry && entry.status !== "confirmed" ? 1 : 0);
	for (const match of [...confirmed, ...others]) {
		if (cost(match.entry) > left) {
			omitted += cost(match.entry) + cost(match.pendingRevision);
			continue;
		}
		left -= cost(match.entry);
		if (cost(match.pendingRevision) > left) {
			omitted += cost(match.pendingRevision);
			kept.push({ ...match, pendingRevision: undefined });
			continue;
		}
		left -= cost(match.pendingRevision);
		kept.push(match);
	}
	return { kept, omitted };
}

/**
 * One memory_context.json fact item (skeleton 1.13): exactly these seven keys. `path` is empty
 * when index.json carries an absolute one (index.py's _rel() falls back to an absolute posix
 * path, and resolve() follows symlinks): that file is read by the agent, so it must not become
 * a channel for host paths. The entry id still locates the entry.
 */
function contextFact(entry: MemoryMatch["entry"]): Record<string, unknown> {
	return {
		entry_id: entry.entry_id,
		title: entry.title,
		path: displayPath(entry.path),
		scope: entry.scope,
		checked_at: entry.checked_at,
		stale: entry.stale,
		status: entry.status,
	};
}

/** The kept matches as context items; a pending revision rides along right after its target (usageIds lists both). */
function contextFacts(kept: MemoryMatch[]): Record<string, unknown>[] {
	const facts: Record<string, unknown>[] = [];
	for (const match of kept) {
		facts.push(contextFact(match.entry));
		if (match.pendingRevision) {
			facts.push(contextFact(match.pendingRevision));
		}
	}
	return facts;
}

/**
 * index.py's EXIT_HASH_MISMATCH: --check left at least one entry out of the index because its
 * entry.md no longer matches the entry_sha256 its meta records. 1 means the check itself could
 * not run. The status is the whole report — runMemoryScript judges a script by `r.status === 0`
 * and never by stderr — so this file has to know the one code that means something else.
 */
const INDEX_CHECK_HASH_MISMATCH = 2;

/**
 * What to do about a failed index check. A rebuild is the answer when the check could not run,
 * and no answer at all for a hash mismatch: every build drops such an entry again, so rebuilding
 * looks like it did nothing. cli.py's rebuild-index prints the same two ways out of it.
 */
function indexCheckRepair(status: number | null): string {
	if (status === INDEX_CHECK_HASH_MISMATCH) {
		return "The named entries stay out of sure/memory/index.json and can never be injected; a rebuild drops them again instead of clearing it. Either restore the entry.md text so it matches the entry_sha256 its meta file under sure/memory/meta records, or drop the entry with python3 -s sure/runtime/memory/cli.py reject <entry_id> --reason <why> and publish it again.";
	}
	return "Injection keeps using the existing sure/memory/index.json if there is one; by hand: python3 -s sure/runtime/memory/index.py --repo-root <repo> --rebuild";
}

/**
 * After resolveHarnessPython succeeded: refresh the index (scripts/check_memory_index.py
 * --check, a thin wrapper around index.py so this stays inside the "hooks only spawn a
 * skill's own scripts/" rule), match facts against "<targetId> <args>", write
 * artifacts/memory_context.json (always, facts: [] when nothing matched or the index is
 * unavailable) and log one pre_start usage row when something matched.
 */
export function preStartMemory(
	env: MemoryHookEnv,
	args: { targetId: string; strippedArgs: string },
): { diagnostics: MemoryDiagnostic[] } {
	const ctx = env.ctx;
	const memoryRoot = memoryRootFor(ctx.packageDir);
	const diagnostics: MemoryDiagnostic[] = [];
	const { config, error: configError } = tryConfig();
	let kept: MemoryMatch[] = [];
	let omitted = 0;
	if (!config) {
		// No budgets, no timeout, no usage limit: skip the index check and the matching, but
		// still write the empty context file the SKILL.md tells the agent to read.
		diagnostics.push(configFailure(configError));
	} else {
		if (config.defaulted_keys.length > 0) {
			diagnostics.push(configDefaultsUsed(config.defaulted_keys));
		}
		const check = runMemoryScript(
			env,
			join(ctx.packageDir, "scripts", "check_memory_index.py"),
			["--repo-root", repoRootForPackage(ctx.packageDir), "--check"],
			config.index_check_timeout_ms,
		);
		if (!check.ok) {
			diagnostics.push(
				diag(
					"warning",
					// Redacted like every other script failure: the spawn error names the interpreter.
					`memory index check failed: ${redactScriptFailure(scriptFailureText(check, `check_memory_index.py exited ${check.status}`), "check_memory_index.py")}`,
					indexCheckRepair(check.status),
				),
			);
		}
		const loaded = readMemoryIndex(memoryRoot);
		if (loaded.ok && loaded.index) {
			// Strip output_dir again here: the match text and the usage rows must never carry the harness-owned path.
			const matches = matchFacts(loaded.index, {
				skill: env.skill,
				targetId: args.targetId,
				args: stripOutputDir(args.strippedArgs),
			});
			({ kept, omitted } = contextBudget(matches, config.memory_context_max_provisional));
		} else {
			diagnostics.push(
				diag(
					"warning",
					`memory index unavailable, memory_context.json written empty: ${loaded.error ?? "unknown error"}`,
					"Injection is skipped until sure/memory/index.json exists; by hand: python3 -s sure/runtime/memory/index.py --repo-root <repo> --rebuild",
				),
			);
		}
	}
	const context = {
		schema: CONTEXT_SCHEMA,
		skill: env.skill,
		target_id: args.targetId,
		facts: contextFacts(kept),
		omitted_provisional: omitted,
	};
	try {
		const artifactsDir = join(ctx.runDir, "artifacts");
		mkdirSync(artifactsDir, { recursive: true });
		writeFileSync(join(artifactsDir, CONTEXT_FILE), `${JSON.stringify(context, null, 2)}\n`, "utf-8");
	} catch (error) {
		// The fs error names the absolute artifacts path it failed on; the agent only needs to
		// know the file was not written and why.
		const message = redactHostPaths(error instanceof Error ? error.message : String(error));
		diagnostics.push(
			diag("warning", `memory_context.json not written: ${message}`, "The run continues without the fact context."),
		);
	}
	if (config && kept.length > 0) {
		const runId = runIdOf(ctx);
		const row = { kind: "pre_start", run_id: runId, skill: env.skill, entries: usageIds(kept), at: utcNow() };
		const written = appendUsageRow(memoryRoot, runId, row, config);
		if (!written.ok) {
			diagnostics.push(usageWriteFailure(memoryRoot, written.error));
		}
	}
	return { diagnostics };
}

// --- pre_finish / post_finish (4.5, 6.2) -----------------------------------------------

/**
 * Non-success finish: the declaration must exist and pass the gate. Two blocks,
 * then the third attempt is let through with extractionStatus = "failed".
 */
export function preFinishExtraction(
	env: MemoryHookEnv,
	args: { finishStatus: string; memory: MemoryCheckpoint },
): { ok: boolean; repair?: string; memory: MemoryCheckpoint; diagnostics: MemoryDiagnostic[] } {
	if (args.finishStatus !== "failed" && args.finishStatus !== "incomplete") {
		return { ok: true, memory: args.memory, diagnostics: [] };
	}
	if (args.memory.extractionStatus === "failed") {
		return { ok: true, memory: args.memory, diagnostics: [] };
	}
	const { config, error: configError } = tryConfig();
	if (!config) {
		// No attempt budget and no digest to point the agent at: never block the finish.
		return { ok: true, memory: args.memory, diagnostics: [configFailure(configError)] };
	}
	const ctx = env.ctx;
	const declaration = join(ctx.runDir, "artifacts", DECLARATION_FILE);
	let problem: string | undefined;
	if (!existsSync(declaration)) {
		problem = `artifacts/${DECLARATION_FILE} is missing.`;
	} else {
		const gate = runMemoryGate(env, declaration);
		if (!gate.ok && gate.ranFailed) {
			// The gate never ran (missing wrapper, no interpreter, timeout). Its text is a
			// traceback, not something the agent can repair, and two rounds of it would burn
			// the harness's finish nudges. Let the finish through and record that nothing was
			// gated, so post_finish publishes nothing either.
			return {
				ok: true,
				memory: { ...args.memory, extractionStatus: "failed" },
				diagnostics: [
					diag(
						"warning",
						`memory extraction gate could not run: ${firstLine(gate.repair ?? "unknown error")}`,
						`The finish is not blocked by the memory system and nothing is published for this run. Check scripts/check_memory_extraction.py and the python interpreter, then see ${EXTRACTION_DOC}.`,
					),
				],
			};
		}
		if (!gate.ok) {
			problem = gate.repair ?? `artifacts/${DECLARATION_FILE} did not pass check_memory_extraction.py.`;
		}
	}
	if (!problem) {
		return { ok: true, memory: args.memory, diagnostics: [] };
	}
	const attempts = (args.memory.finishAttempts ?? 0) + 1;
	const memory: MemoryCheckpoint = { ...args.memory, finishAttempts: attempts };
	const diagnostics: MemoryDiagnostic[] = [];
	if (attempts > config.finish_extraction_max_attempts) {
		memory.extractionStatus = "failed";
		diagnostics.push(
			diag(
				"warning",
				`extraction: failed (pre_finish gave up after ${attempts - 1} attempts: ${firstLine(problem)})`,
				"The run finishes without a memory extraction; nothing is published for it.",
			),
		);
		return { ok: true, memory, diagnostics };
	}
	// Give the agent something to extract from. Build once: rebuilding would
	// invalidate the sha the agent's candidates already cite (gate rule 9).
	// --finish-status tells digest.py the run is ending non-success, so the
	// unit still blocked is recorded as failed with its log tail.
	if (!memory.digestSha256 || !existsSync(join(ctx.runDir, "artifacts", DIGEST_FILE))) {
		const cutoff = readEventCount(ctx.runDir);
		const built = buildDigest(env, { cutoff, finishStatus: args.finishStatus });
		memory.digestCutoff = cutoff;
		delete memory.digestPassed;
		if (built.sha256) {
			memory.digestSha256 = built.sha256;
		}
		if (!built.ok) {
			diagnostics.push(digestFailure(built.error));
		}
	}
	const repair =
		`${problem}\n\n` +
		`Before finishing with status ${args.finishStatus}, produce artifacts/${DECLARATION_FILE} as described in ${EXTRACTION_DOC} (section 10) ` +
		`(read artifacts/${DIGEST_FILE} first; no_new_lessons=true with a reason is acceptable). ` +
		`Only produce the declaration, do not end the turn; then call sure_finish again. ` +
		`(extraction attempt ${attempts} of ${config.finish_extraction_max_attempts})`;
	return { ok: false, repair, memory, diagnostics };
}

/**
 * The publish was declined because the gate did not accept the declaration here and now. Nothing
 * is actionable in-run — the run is over — so this is a warning, and it names no host path: it
 * lands in diagnostics[].repair, which digest.py _repair_of harvests into the next run's
 * prior_runs[].last_repair.
 */
function notGatedAtPublish(env: MemoryHookEnv, why: string): MemoryDiagnostic {
	return diag(
		"warning",
		`memory publish skipped, the extraction gate did not pass at finish: ${firstLine(why)}`,
		`Nothing from run ${runIdOf(env.ctx)} was stored. publish_memory.py re-checks none of the extraction rules, so candidates are only ever published behind a gate that accepted them; see ${EXTRACTION_DOC}.`,
	);
}

/** post_finish: publish -> promote -> rebuild index through scripts/publish_memory.py (skipped when extraction failed). */
export function postFinishMemory(env: MemoryHookEnv, memory: MemoryCheckpoint): { diagnostics: MemoryDiagnostic[] } {
	// Nothing to publish, and nothing to say: at post_finish a diagnostics key REPLACES
	// state.diagnostics wholesale, so an advisory info line here would wipe whatever the
	// last gate left on screen. Only failures earn a diagnostic from this hook point.
	if (memory.extractionStatus === "failed") {
		return { diagnostics: [] };
	}
	const { config, error: configError } = tryConfig();
	if (!config) {
		return { diagnostics: [configFailure(configError)] };
	}
	const ctx = env.ctx;
	// Fail closed. publish_memory.py re-runs none of the ten rules, so this hook point is the last
	// thing between a candidate and sure/memory/provisional, and until now it published on the
	// ABSENCE of extractionStatus="failed" — a checkpoint field that only arrives if every patch
	// carrying it was accepted by the harness normalizer, which drops a state_patch whole over any
	// one bad field. An unknown gate state is not a passing gate state: ask the gate again instead
	// of inferring a verdict from what the checkpoint happens to hold, and publish only on a pass.
	// A declaration untouched since the unit gate accepted it passes again in milliseconds; one
	// edited afterwards, or never gated at all, does not.
	const declaration = join(ctx.runDir, "artifacts", DECLARATION_FILE);
	if (!existsSync(declaration)) {
		return { diagnostics: [notGatedAtPublish(env, `artifacts/${DECLARATION_FILE} is missing.`)] };
	}
	const gate = runMemoryGate(env, declaration);
	if (!gate.ok) {
		return { diagnostics: [notGatedAtPublish(env, gate.repair ?? "the gate gave no verdict.")] };
	}
	const script = join(ctx.packageDir, "scripts", "publish_memory.py");
	const run = runMemoryScript(
		env,
		script,
		["--run-dir", ctx.runDir, "--repo-root", repoRootForPackage(ctx.packageDir)],
		config.publish_timeout_ms,
	);
	if (!run.ok) {
		return {
			diagnostics: [
				diag(
					"error",
					`memory publish failed: ${truncate(scriptFailureText(run, `publish_memory.py exited ${run.status}`), 600)}`,
					// The run directory is named by its id, never by its host path: this is the one memory
					// text that reaches diagnostics[].repair, which digest.py _repair_of harvests into the
					// next run's prior_runs[].last_repair — the memory system's own input from then on.
					`Candidates of run ${runIdOf(ctx)} were not published. Check sure/memory/ permissions (${FIX_PERMS_HINT}) and rerun by hand: python3 -s sure/skills/${env.skill}/scripts/publish_memory.py --run-dir <run dir> --repo-root <repo>`,
				),
			],
		};
	}
	// Success is silent for the same reason (the publish summary is on publish_memory.py's
	// stdout and in `cli list`); only the error branch above reaches the TUI.
	return { diagnostics: [] };
}
