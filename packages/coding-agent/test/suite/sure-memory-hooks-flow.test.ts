import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { basename, join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import {
	buildDigest,
	injectOnBlock,
	isExtractionGateExhausted,
	type MemoryCheckpoint,
	type MemoryDiagnostic,
	type MemoryHookEnv,
	onEnterExtractLessons,
	onErrorDigest,
	postFinishMemory,
	preFinishExtraction,
	preStartMemory,
	readLogTail,
	resolveMemoryPython,
	resolveUnitLogPath,
	runIdOf,
	runMemoryGate,
	settleOnPass,
	settleOnTerminalFailure,
	stripOutputDir,
} from "../../../../sure/runtime/memory/hooks.ts";
import { loadMemoryConfig, readEventCount } from "../../../../sure/runtime/memory/match.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// Task 12: hooks.ts orchestration. Every fixture is a fake repo under
// test/suite/tmp-mh/<name>/ so that memoryRootFor() and every python script
// write into the fixture, never into this checkout's sure/memory/. The fake
// skill's scripts/ wrappers point at this checkout's sure/runtime, so the real
// digest / gate / publish / index modules run (Tasks 2, 4, 5, 6).

const REPO_ROOT = resolve(__dirname, "../../../..");
const RUNTIME_DIR = join(REPO_ROOT, "sure", "runtime");
const TMP = resolve(__dirname, "tmp-mh");
const RUN_ID = "20260818-120000-abcd1234";
const T0 = "2026-08-18T12:00:00.000Z";
const ENTRY_ID = "sure_onboard/no-kernel-image";
const ENTRY_PATH = "sure/skills/sure_onboard/references/memory/bad_cases/no-kernel-image.md";
const ENTRY_TITLE = "CUDA arch mismatch: torch wheel lacks the kernel for this GPU";
const TRIGGER = "no kernel image is available";
const REPAIR_HIT = `BUILD_ENV gate: runtime probe failed: RuntimeError: CUDA error: ${TRIGGER} for execution on the device. Repair the environment and regenerate build_env_result.json.`;
const REPAIR_MISS =
	"BUILD_ENV gate: build_env_result.json is missing required field env_ready. Regenerate the artifact.";
const DECLARATION_OK = {
	schema: "sure.memory.extraction.v2",
	no_new_lessons: true,
	no_lessons_reason: "every unit passed on its first attempt; nothing to record",
	covered_by: [],
	candidates: [],
	infra_noise: false,
	infra_evidence: [],
};
// no_new_lessons=false with an empty candidates list contradicts itself (gate rule 10).
const DECLARATION_BAD = { ...DECLARATION_OK, no_new_lessons: false, no_lessons_reason: null };

const PYTHON_BIN = (() => {
	for (const candidate of ["python", "python3"]) {
		const r = spawnSync(candidate, ["-c", "import sys; print(sys.executable)"], {
			encoding: "utf-8",
			timeout: 10_000,
		});
		if (r.status === 0 && r.stdout.trim()) {
			return r.stdout.trim();
		}
	}
	return undefined;
})();

const previousPythonBin = process.env.HARNESS_PYTHON_BIN;

beforeAll(() => {
	if (!PYTHON_BIN) {
		throw new Error("no python / python3 on PATH; the memory hooks tests run the real memory scripts");
	}
	// hooks.ts resolves python from HARNESS_PYTHON_BIN first (skeleton 1.13), so no harness bootstrap is needed here.
	process.env.HARNESS_PYTHON_BIN = PYTHON_BIN;
});

afterAll(() => {
	if (previousPythonBin === undefined) {
		delete process.env.HARNESS_PYTHON_BIN;
	} else {
		process.env.HARNESS_PYTHON_BIN = previousPythonBin;
	}
});

function withEnv<T>(name: string, value: string | undefined, fn: () => T): T {
	const previous = process.env[name];
	if (value === undefined) {
		delete process.env[name];
	} else {
		process.env[name] = value;
	}
	try {
		return fn();
	} finally {
		if (previous === undefined) {
			delete process.env[name];
		} else {
			process.env[name] = previous;
		}
	}
}

interface Fixture {
	root: string;
	packageDir: string;
	runDir: string;
	memoryRoot: string;
	ctx: SureHookContext;
	env: MemoryHookEnv;
}

// Same shape as the real thin wrappers (skeleton 1.11) but with an absolute sys.path entry.
function wrapper(module: string): string {
	return [
		"#!/usr/bin/env python3",
		"from __future__ import annotations",
		"import sys",
		`sys.path.insert(0, ${JSON.stringify(RUNTIME_DIR.split("\\").join("/"))})`,
		`from memory import ${module}`,
		'if __name__ == "__main__":',
		`    raise SystemExit(${module}.main(sys.argv[1:]))`,
		"",
	].join("\n");
}

function fixture(name: string, skill: "sure_onboard" | "sure_eval" = "sure_onboard"): Fixture {
	const root = join(TMP, name);
	rmSync(root, { recursive: true, force: true });
	const packageDir = join(root, "sure", "skills", skill);
	const scriptsDir = join(packageDir, "scripts");
	mkdirSync(scriptsDir, { recursive: true });
	writeFileSync(join(scriptsDir, "build_run_digest.py"), wrapper("digest"), "utf-8");
	writeFileSync(join(scriptsDir, "check_memory_extraction.py"), wrapper("proposals"), "utf-8");
	writeFileSync(join(scriptsDir, "publish_memory.py"), wrapper("publish"), "utf-8");
	// Task 12 deviation-2 override: pre_start spawns this skill-local wrapper instead of
	// sure/runtime/memory/index.py directly, so hooks never spawn outside scripts/.
	writeFileSync(join(scriptsDir, "check_memory_index.py"), wrapper("index"), "utf-8");
	const runDir = join(root, ".sure", "runs", RUN_ID);
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	const ctx: SureHookContext = {
		point: "post_tool_result",
		run: { id: RUN_ID, command: `/${skill}`, status: "running" } as never,
		skill: { name: skill, command: `/${skill}` } as never,
		cwd: root,
		packageDir,
		runDir,
		args: "",
	};
	return {
		root,
		packageDir,
		runDir,
		memoryRoot: join(root, "sure", "memory"),
		ctx,
		env: { ctx, skill, py: undefined },
	};
}

function ev(type: string, data?: unknown): Record<string, unknown> {
	return { type, timestamp: T0, data };
}

function writeEvents(runDir: string, events: unknown[]): void {
	writeFileSync(join(runDir, "events.jsonl"), `${events.map((event) => JSON.stringify(event)).join("\n")}\n`, "utf-8");
}

function appendEvents(runDir: string, events: unknown[]): void {
	const existing = existsSync(join(runDir, "events.jsonl")) ? readFileSync(join(runDir, "events.jsonl"), "utf-8") : "";
	writeFileSync(
		join(runDir, "events.jsonl"),
		`${existing}${events.map((event) => JSON.stringify(event)).join("\n")}\n`,
		"utf-8",
	);
}

/** run.json plus created / started / one bash tool call and its result: four event lines, so the digest cutoff is 4. */
function seedEvents(runDir: string): number {
	const record = {
		runId: RUN_ID,
		skillName: "sure_onboard",
		command: "/sure_onboard",
		status: "running",
		cwd: "/work",
		packageDir: "/work/sure/skills/sure_onboard",
		runDir,
		args: "model_id=openai/whisper-large-v3 repo=https://example.invalid/whisper task_type=asr deployment_type=local",
		startedAt: T0,
		updatedAt: T0,
	};
	writeFileSync(join(runDir, "run.json"), `${JSON.stringify(record, null, 2)}\n`, "utf-8");
	const events = [
		ev("created", { ...record, status: "pending" }),
		ev("started", { status: "running" }),
		ev("tool_call", { toolName: "bash", toolCallId: "c1", input: { command: "uv venv .venv" } }),
		ev("tool_result", { toolName: "bash", toolCallId: "c1", isError: false }),
	];
	writeEvents(runDir, events);
	return events.length;
}

function readTool(path: string): Record<string, unknown> {
	return ev("tool_call", { toolName: "read", toolCallId: "r1", input: { path } });
}

function bashTool(command: string): Record<string, unknown> {
	return ev("tool_call", { toolName: "bash", toolCallId: "b1", input: { command } });
}

function badCase(over: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		entry_id: ENTRY_ID,
		type: "bad_case",
		status: "confirmed",
		target_skill: "sure_onboard",
		applies_to: ["sure_onboard"],
		component: "build_env",
		cause: "cuda_version_mismatch",
		trigger: [TRIGGER],
		hook_trigger: [TRIGGER],
		scope: null,
		title: ENTRY_TITLE,
		path: ENTRY_PATH,
		legacy: false,
		op: "add",
		target_entry: null,
		similar_entry: null,
		useful_activated: 0,
		useful_unattributed: 0,
		injections: 0,
		disputed: 0,
		created: "legacy",
		checked_at: null,
		stale: false,
		superseded_by: null,
		...over,
	};
}

function fact(slug: string, over: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		entry_id: `_shared/${slug}`,
		type: "fact",
		status: "confirmed",
		target_skill: "_shared",
		applies_to: ["sure_onboard", "sure_eval"],
		component: "_",
		cause: "n.a.",
		trigger: [],
		hook_trigger: [],
		scope: "cluster",
		title: `fact ${slug}`,
		path: `sure/skills/_shared/memory/facts/${slug}.md`,
		legacy: false,
		op: "add",
		target_entry: null,
		similar_entry: null,
		useful_activated: 0,
		useful_unattributed: 0,
		injections: 0,
		disputed: 0,
		created: { run_id: "20260810-000000-aaaaaaaa", date: "2026-08-10" },
		checked_at: "2026-08-10",
		stale: false,
		superseded_by: null,
		...over,
	};
}

function writeIndex(memoryRoot: string, entries: Record<string, unknown>[]): void {
	mkdirSync(memoryRoot, { recursive: true });
	const index = {
		schema: "sure.memory.index.v1",
		built_at: T0,
		sources_sha256: "0".repeat(64),
		entries,
		omitted_provisional: 0,
	};
	writeFileSync(join(memoryRoot, "index.json"), `${JSON.stringify(index, null, 2)}\n`, "utf-8");
}

function usageRows(memoryRoot: string): Record<string, unknown>[] {
	const path = join(memoryRoot, "usage", `${RUN_ID}.jsonl`);
	if (!existsSync(path)) {
		return [];
	}
	return readFileSync(path, "utf-8")
		.split("\n")
		.filter((line) => line.trim().length > 0)
		.map((line) => JSON.parse(line) as Record<string, unknown>);
}

function seedState(runDir: string, memory: MemoryCheckpoint): void {
	const data = { currentUnit: "extract_lessons", completedUnits: ["verdict"], retries: {}, memory };
	writeFileSync(join(runDir, "state.json"), JSON.stringify({ checkpoint: { data } }, null, 2), "utf-8");
}

function writeArtifact(runDir: string, name: string, value: unknown): string {
	const path = join(runDir, "artifacts", name);
	writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, "utf-8");
	return path;
}

function writeText(path: string, text: string): string {
	mkdirSync(join(path, ".."), { recursive: true });
	writeFileSync(path, text, "utf-8");
	return path;
}

function readDigest(runDir: string): Record<string, unknown> {
	return JSON.parse(readFileSync(join(runDir, "artifacts", "run_digest.json"), "utf-8"));
}

function readContext(runDir: string): Record<string, unknown> {
	return JSON.parse(readFileSync(join(runDir, "artifacts", "memory_context.json"), "utf-8"));
}

function sha256(path: string): string {
	return createHash("sha256").update(readFileSync(path)).digest("hex");
}

/** A run that already went through extract_lessons: digest built, sha in state.json, declaration written. */
/**
 * Make scripts/publish_memory.py exit 1 with the run directory in its message. Only the publish
 * breaks: the extraction gate post_finish consults first still runs, which is what puts these
 * fixtures on the publish-failure branch rather than on the not-gated one.
 */
function failPublishScript(f: Fixture): void {
	writeFileSync(
		join(f.packageDir, "scripts", "publish_memory.py"),
		[
			"import sys",
			`sys.stderr.write("cannot write into " + ${JSON.stringify(join(f.runDir, "artifacts"))} + "\\n")`,
			"raise SystemExit(1)",
			"",
		].join("\n"),
		"utf-8",
	);
}

function seedExtractedRun(f: Fixture, declaration: unknown): { memory: MemoryCheckpoint; producesPath: string } {
	seedEvents(f.runDir);
	const entered = onEnterExtractLessons(f.env, "verdict", {});
	expect(entered.diagnostics).toEqual([]);
	seedState(f.runDir, entered.memory);
	return { memory: entered.memory, producesPath: writeArtifact(f.runDir, "extraction_declaration.json", declaration) };
}

describe("hooks.ts python resolution and ids", () => {
	it("resolveMemoryPython prefers HARNESS_PYTHON_BIN", () => {
		const f = fixture("py-env");
		expect(withEnv("HARNESS_PYTHON_BIN", "/opt/fake/python", () => resolveMemoryPython(f.packageDir))).toEqual({
			ok: true,
			python: "/opt/fake/python",
		});
	});

	it("resolveMemoryPython falls back to the harness bootstrap and reports its error", () => {
		const f = fixture("py-bootstrap");
		// The fake repo has no sure/runtime/harness/bootstrap.py, so resolveHarnessPython fails without spawning.
		const r = withEnv("HARNESS_PYTHON_BIN", undefined, () => resolveMemoryPython(f.packageDir));
		expect(r.ok).toBe(false);
		expect(r.error).toContain("HARNESS_RUNTIME_NOT_READY");
	});

	it("runIdOf takes the run directory name, not ctx.run", () => {
		const f = fixture("run-id");
		expect(runIdOf(f.ctx)).toBe(RUN_ID);
		expect(basename(f.runDir)).toBe(RUN_ID);
	});

	it("isExtractionGateExhausted only knows extract_lessons and lets max_retries raise the cap", () => {
		const config = loadMemoryConfig();
		expect(isExtractionGateExhausted("build_env", 99, undefined, config)).toBe(false);
		expect(isExtractionGateExhausted("extract_lessons", 1, undefined, config)).toBe(false);
		expect(isExtractionGateExhausted("extract_lessons", 2, undefined, config)).toBe(true);
		expect(isExtractionGateExhausted("extract_lessons", 2, 5, config)).toBe(false);
		expect(isExtractionGateExhausted("extract_lessons", 5, 5, config)).toBe(true);
		expect(isExtractionGateExhausted("extract_lessons", 2, 1, config)).toBe(true);
	});
});

describe("hooks.ts stripOutputDir", () => {
	it("drops output_dir in its key=value, --output_dir value and bare forms, keeping every other token", () => {
		expect(stripOutputDir("model_id=x output_dir=/tmp/o task_type=asr")).toBe("model_id=x task_type=asr");
		expect(stripOutputDir("model=x --output_dir /tmp/o datasets=a@v1")).toBe("model=x datasets=a@v1");
		expect(stripOutputDir("model=x output_dir /tmp/o")).toBe("model=x");
		expect(stripOutputDir("model=x -output_dir /tmp/o rest")).toBe("model=x rest");
	});

	it("does not eat a following flag, and normalises whitespace", () => {
		expect(stripOutputDir("a=1 --output_dir --verbose")).toBe("a=1 --verbose");
		expect(stripOutputDir("  a=1   b=2 ")).toBe("a=1 b=2");
		expect(stripOutputDir("")).toBe("");
		expect(stripOutputDir("output_dir=/tmp/o")).toBe("");
	});
});

describe("hooks.ts digest", () => {
	it("onEnterExtractLessons builds artifacts/run_digest.json and records cutoff, sha and passed unit", () => {
		const f = fixture("digest-ok");
		const lines = seedEvents(f.runDir);
		const r = onEnterExtractLessons(f.env, "verdict", { injected: { build_env: [ENTRY_ID] } });
		expect(r.diagnostics).toEqual([]);
		const digestPath = join(f.runDir, "artifacts", "run_digest.json");
		expect(existsSync(digestPath)).toBe(true);
		const digest = readDigest(f.runDir);
		expect(digest.schema).toBe("sure.memory.run_digest.v1");
		expect(digest.error).toBeUndefined();
		expect((digest.run as { skill: string }).skill).toBe("sure_onboard");
		expect(r.memory.digestCutoff).toBe(lines);
		expect(r.memory.digestPassed).toBe("verdict");
		expect(r.memory.digestSha256).toBe(sha256(digestPath));
		expect(r.memory.injected).toEqual({ build_env: [ENTRY_ID] });
	});

	it("buildDigest always passes --skill and forwards --finish-status (no run.json, no events)", () => {
		const f = fixture("digest-flags", "sure_eval");
		const built = buildDigest(f.env, { cutoff: 0, finishStatus: "incomplete" });
		expect(built.ok).toBe(true);
		const run = readDigest(f.runDir).run as { skill: string; status_so_far: string; cutoff: number };
		expect(run.skill).toBe("sure_eval");
		expect(run.status_so_far).toBe("incomplete");
		expect(run.cutoff).toBe(0);
	});

	it("buildDigest leaves a {schema, error} digest behind when python cannot run", () => {
		const f = fixture("digest-nopython");
		seedEvents(f.runDir);
		const built = withEnv("HARNESS_PYTHON_BIN", join(f.root, "no-such-python"), () =>
			buildDigest(f.env, { cutoff: 4 }),
		);
		expect(built.ok).toBe(false);
		expect(built.error).toContain("build_run_digest.py");
		const digestPath = join(f.runDir, "artifacts", "run_digest.json");
		const digest = readDigest(f.runDir);
		expect(Object.keys(digest).sort()).toEqual(["error", "schema"]);
		expect(digest.schema).toBe("sure.memory.run_digest.v1");
		expect(built.sha256).toBe(sha256(digestPath));
		const entered = withEnv("HARNESS_PYTHON_BIN", join(f.root, "no-such-python"), () =>
			onEnterExtractLessons(f.env, "verdict", {}),
		);
		expect(entered.memory.digestSha256).toBe(sha256(digestPath));
		expect(entered.diagnostics).toHaveLength(1);
		expect(entered.diagnostics[0].severity).toBe("warning");
		expect(entered.diagnostics[0].message).toContain("memory digest failed");
	});

	it("buildDigest keeps the {schema, error} digest build_run_digest.py wrote itself on exit 1", () => {
		const f = fixture("digest-exit1");
		// No run directory: the script raises inside build_run_digest, writes {schema, error} and exits 1
		// (skeleton 1.13: 0 = digest, 1 = error digest written, 2 = nothing written).
		rmSync(f.runDir, { recursive: true, force: true });
		const built = buildDigest(f.env, { cutoff: 0 });
		expect(built.ok).toBe(false);
		const digest = readDigest(f.runDir);
		expect(Object.keys(digest).sort()).toEqual(["error", "schema"]);
		// The hook keeps those bytes: the error it reports is the file's, not the script's stdout
		// line, which carries the absolute digest path.
		expect(built.error).toBe(digest.error);
		expect(built.error).toContain("run dir is not a directory");
		expect(built.error).not.toContain("sha256");
		expect(built.sha256).toBe(sha256(join(f.runDir, "artifacts", "run_digest.json")));
	});

	it("keeps the interpreter path out of the digest error and out of run_digest.json", () => {
		// A spawn failure's message carries the absolute interpreter path. buildDigest writes that
		// text into artifacts/run_digest.json — the file the repair tells the agent to read and the
		// one digest.py stores — and into the "memory digest failed" diagnostic.
		const f = fixture("digest-spawn-redacted");
		seedEvents(f.runDir);
		const python = join(f.root, "no-such-python");
		const entered = withEnv("HARNESS_PYTHON_BIN", python, () => onEnterExtractLessons(f.env, "verdict", {}));
		expect(entered.diagnostics).toHaveLength(1);
		expect(entered.diagnostics[0].message).toContain("memory digest failed");
		expect(entered.diagnostics[0].message).not.toContain(python);
		const digest = readDigest(f.runDir);
		// Which script failed and how is still there; only the paths are masked.
		expect(String(digest.error)).toContain("build_run_digest.py");
		expect(String(digest.error)).toContain("<path>");
		expect(String(digest.error)).not.toContain(python);
		expect(String(digest.error)).not.toContain(f.root);
		const raw = readFileSync(join(f.runDir, "artifacts", "run_digest.json"), "utf-8");
		expect(raw).not.toContain(f.root.split("\\").join("/"));
	});

	it("onErrorDigest builds a digest at the current event count without a passed unit", () => {
		const f = fixture("digest-onerror");
		seedEvents(f.runDir);
		const r = onErrorDigest(f.env);
		expect(r.diagnostics).toEqual([]);
		expect(existsSync(join(f.runDir, "artifacts", "run_digest.json"))).toBe(true);
	});
});

describe("hooks.ts readLogTail", () => {
	it("splits on \\r and \\n, keeps the last N lines from the tail window and truncates long lines", () => {
		const f = fixture("log-tail");
		const path = join(f.runDir, "artifacts", "build_env.log");
		const filler = `${"x".repeat(100)}\n`.repeat(50);
		writeFileSync(
			path,
			`${filler}progress 10%\rprogress 50%\rprogress 100%\n${"y".repeat(500)}\nlast line\n`,
			"utf-8",
		);
		const tail = readLogTail(path, { lines: 4, lineChars: 20, seekBytes: 600 });
		expect(tail).toEqual(["progress 10%", "progress 50%", "progress 100%", "y".repeat(20), "last line"].slice(-4));
		expect(readLogTail(join(f.runDir, "missing.log"), { lines: 4, lineChars: 20, seekBytes: 600 })).toEqual([]);
	});
});

describe("hooks.ts resolveUnitLogPath", () => {
	it("reads the table's artifact:<produces> entry without a producesPath argument (bare relative log_path -> run artifacts)", () => {
		const f = fixture("logpath-artifact-entry");
		const log = writeText(join(f.runDir, "artifacts", "custom", "build.log"), "x\n");
		writeArtifact(f.runDir, "build_env_result.json", { env_ready: false, log_path: "custom/build.log" });
		writeText(join(f.runDir, "artifacts", "build_env.log"), "table log\n");
		expect(resolveUnitLogPath(f.env, { unitId: "build_env" })).toBe(log);
	});

	it("resolves artifacts/-prefixed log_path under the product dir, or ctx.cwd when no product dir is known", () => {
		const f = fixture("logpath-artifacts-prefix");
		const productDir = join(f.root, "sure", "models", "demo");
		const productLog = writeText(join(productDir, "artifacts", "build_env.log"), "product\n");
		const cwdLog = writeText(join(f.root, "artifacts", "build_env.log"), "cwd\n");
		writeArtifact(f.runDir, "build_env_result.json", { env_ready: false, log_path: "artifacts/build_env.log" });
		expect(resolveUnitLogPath(f.env, { unitId: "build_env", productDir })).toBe(productLog);
		expect(resolveUnitLogPath(f.env, { unitId: "build_env" })).toBe(cwdLog);
	});

	it("takes an absolute log_path as is and falls through the table when the declared file is missing", () => {
		const f = fixture("logpath-absolute");
		const elsewhere = writeText(join(f.root, "elsewhere", "build.log"), "abs\n");
		writeArtifact(f.runDir, "build_env_result.json", { env_ready: false, log_path: elsewhere });
		expect(resolveUnitLogPath(f.env, { unitId: "build_env" })).toBe(elsewhere);
		writeArtifact(f.runDir, "build_env_result.json", { env_ready: false, log_path: join(f.root, "gone.log") });
		const tableLog = writeText(join(f.runDir, "artifacts", "build_env.log"), "table\n");
		expect(resolveUnitLogPath(f.env, { unitId: "build_env" })).toBe(tableLog);
	});

	it("skips {product_dir} templates without a product dir and uses them with one; unknown units resolve nothing", () => {
		const f = fixture("logpath-product-template");
		const productDir = join(f.root, "sure", "models", "demo");
		const productLog = writeText(join(productDir, "artifacts", "build_env.log"), "product\n");
		expect(resolveUnitLogPath(f.env, { unitId: "build_env" })).toBeUndefined();
		expect(resolveUnitLogPath(f.env, { unitId: "build_env", productDir })).toBe(productLog);
		expect(resolveUnitLogPath(f.env, { unitId: "validate_env_compat", productDir })).toBeUndefined();
	});

	it("uses the caller's producesPath for a unit the table does not list", () => {
		const f = fixture("logpath-produces-path");
		const productDir = join(f.root, "sure", "models", "demo");
		const compatLog = writeText(join(productDir, "artifacts", "compat.log"), "compat\n");
		const producesPath = writeArtifact(f.runDir, "env_compat_result.json", { log_path: "artifacts/compat.log" });
		expect(resolveUnitLogPath(f.env, { unitId: "validate_env_compat", productDir, producesPath })).toBe(compatLog);
	});
});

describe("hooks.ts injectOnBlock", () => {
	it("appends the Memory block, records an inject row and remembers the injected id", () => {
		const f = fixture("inject-hit");
		const lines = seedEvents(f.runDir);
		writeIndex(f.memoryRoot, [badCase()]);
		const r = injectOnBlock(f.env, { unitId: "build_env", attempt: 1, rawRepair: REPAIR_HIT, memory: {} });
		expect(r.diagnostics).toEqual([]);
		// Block shape (skeleton 1.13): raw repair, one blank line, then the block whose first line is
		// config.inject_header and whose lines are match.ts renderEntryLine output; no blank line inside.
		expect(r.repair.startsWith(`${REPAIR_HIT}\n\n${loadMemoryConfig().inject_header}\n`)).toBe(true);
		const block = r.repair.slice(REPAIR_HIT.length + 2).split("\n");
		expect(block[0]).toBe(loadMemoryConfig().inject_header);
		expect(block[1]).toBe(`- [confirmed] ${ENTRY_ID} (${ENTRY_PATH}): ${ENTRY_TITLE}`);
		expect(block.every((line) => line.trim().length > 0)).toBe(true);
		expect(r.memory.injected).toEqual({ build_env: [ENTRY_ID] });
		expect(r.memory.pendingDisputed).toBeUndefined();
		const rows = usageRows(f.memoryRoot);
		expect(rows).toHaveLength(1);
		expect(rows[0]).toMatchObject({
			kind: "inject",
			run_id: RUN_ID,
			skill: "sure_onboard",
			unit: "build_env",
			attempt: 1,
			events_cutoff: lines,
			entries: [{ entry_id: ENTRY_ID, shared: false }],
		});
		expect(typeof rows[0].at).toBe("string");
	});

	it("leaves the repair alone when nothing matches", () => {
		const f = fixture("inject-miss");
		seedEvents(f.runDir);
		writeIndex(f.memoryRoot, [badCase()]);
		const r = injectOnBlock(f.env, { unitId: "build_env", attempt: 1, rawRepair: REPAIR_MISS, memory: {} });
		expect(r.repair).toBe(REPAIR_MISS);
		expect(r.memory).toEqual({});
		expect(usageRows(f.memoryRoot)).toEqual([]);
	});

	it("does not inject the same entry twice into one unit and marks it pending disputed when it still hits", () => {
		const f = fixture("inject-dedup");
		seedEvents(f.runDir);
		writeIndex(f.memoryRoot, [badCase()]);
		const first = injectOnBlock(f.env, { unitId: "build_env", attempt: 1, rawRepair: REPAIR_HIT, memory: {} });
		const second = injectOnBlock(f.env, {
			unitId: "build_env",
			attempt: 2,
			rawRepair: REPAIR_HIT,
			memory: first.memory,
		});
		expect(usageRows(f.memoryRoot)).toHaveLength(1);
		expect(second.memory.injected).toEqual({ build_env: [ENTRY_ID] });
		expect(second.memory.pendingDisputed).toEqual({ build_env: [ENTRY_ID] });
		const third = injectOnBlock(f.env, {
			unitId: "build_env",
			attempt: 3,
			rawRepair: REPAIR_MISS,
			memory: second.memory,
		});
		expect(third.memory.pendingDisputed).toBeUndefined();
		expect(third.memory.injected).toEqual({ build_env: [ENTRY_ID] });
	});

	it("matches against the unit's log tail from log_paths.json when the repair alone does not hit", () => {
		const f = fixture("inject-logtail");
		seedEvents(f.runDir);
		writeIndex(f.memoryRoot, [badCase()]);
		writeFileSync(
			join(f.runDir, "artifacts", "build_env.log"),
			`Collecting torch\rInstalling torch\nRuntimeError: CUDA error: ${TRIGGER} for execution on the device\n`,
			"utf-8",
		);
		const r = injectOnBlock(f.env, { unitId: "build_env", attempt: 1, rawRepair: REPAIR_MISS, memory: {} });
		expect(r.memory.injected).toEqual({ build_env: [ENTRY_ID] });
		expect(r.repair).toContain(loadMemoryConfig().inject_header);
	});

	it("reads the produces artifact's own log_path through the table's artifact: entry, no producesPath needed", () => {
		const f = fixture("inject-artifact-log");
		seedEvents(f.runDir);
		writeIndex(f.memoryRoot, [badCase()]);
		writeText(join(f.runDir, "artifacts", "custom", "build.log"), `${TRIGGER}\n`);
		writeFileSync(join(f.runDir, "artifacts", "build_env.log"), "nothing relevant here\n", "utf-8");
		writeArtifact(f.runDir, "build_env_result.json", { env_ready: false, log_path: "custom/build.log" });
		const r = injectOnBlock(f.env, { unitId: "build_env", attempt: 1, rawRepair: REPAIR_MISS, memory: {} });
		expect(r.memory.injected).toEqual({ build_env: [ENTRY_ID] });
	});

	it("uses producesPath for a unit the table does not list (artifacts/ log_path under productDir)", () => {
		const f = fixture("inject-produces-path");
		seedEvents(f.runDir);
		writeIndex(f.memoryRoot, [badCase({ component: "validate_env_compat" })]);
		const productDir = join(f.root, "sure", "models", "demo");
		writeText(join(productDir, "artifacts", "compat.log"), `${TRIGGER}\n`);
		const producesPath = writeArtifact(f.runDir, "env_compat_result.json", { log_path: "artifacts/compat.log" });
		const r = injectOnBlock(f.env, {
			unitId: "validate_env_compat",
			attempt: 1,
			rawRepair: REPAIR_MISS,
			productDir,
			producesPath,
			memory: {},
		});
		expect(r.memory.injected).toEqual({ validate_env_compat: [ENTRY_ID] });
	});

	it("skips injection with a warning when index.json is missing", () => {
		const f = fixture("inject-noindex");
		seedEvents(f.runDir);
		const r = injectOnBlock(f.env, { unitId: "build_env", attempt: 1, rawRepair: REPAIR_HIT, memory: {} });
		expect(r.repair).toBe(REPAIR_HIT);
		expect(r.memory).toEqual({});
		expect(r.diagnostics).toHaveLength(1);
		expect(r.diagnostics[0].severity).toBe("warning");
		expect(r.diagnostics[0].message).toContain("index");
	});

	it("clears the unit's pending disputes when the index is gone, so the entries settle as abandoned, not disputed", () => {
		// pendingDisputed[unit] means "the LAST block's failure text still named these". A block
		// that could not read the index named nothing; carrying the previous block's list forward
		// would let settleOnTerminalFailure write disputed rows for entries this failure never
		// mentioned, and two of those meet demote_disputed_streak.
		const f = fixture("inject-noindex-pending");
		seedEvents(f.runDir);
		const before: MemoryCheckpoint = {
			injected: { build_env: [ENTRY_ID], validate_import: ["_shared/other"] },
			pendingDisputed: { build_env: [ENTRY_ID], validate_import: ["_shared/other"] },
		};
		const r = injectOnBlock(f.env, { unitId: "build_env", attempt: 2, rawRepair: REPAIR_HIT, memory: before });
		expect(r.repair).toBe(REPAIR_HIT);
		expect(r.memory.pendingDisputed).toEqual({ validate_import: ["_shared/other"] });
		// Everything else is untouched: the entries were injected, they are still injected.
		expect(r.memory.injected).toEqual(before.injected);

		const settled = settleOnTerminalFailure(f.env, { unitId: "build_env", memory: r.memory });
		expect(settled.memory.pendingDisputed).toEqual({ validate_import: ["_shared/other"] });
		const rows = usageRows(f.memoryRoot).filter((row) => row.kind === "settle");
		expect(rows).toHaveLength(1);
		expect(rows[0]).toMatchObject({ unit: "build_env", entry_id: ENTRY_ID, outcome: "abandoned" });
	});
});

describe("hooks.ts settlement", () => {
	function injected(f: Fixture): MemoryCheckpoint {
		seedEvents(f.runDir);
		writeIndex(f.memoryRoot, [badCase()]);
		const r = injectOnBlock(f.env, { unitId: "build_env", attempt: 1, rawRepair: REPAIR_HIT, memory: {} });
		expect(r.memory.injected).toEqual({ build_env: [ENTRY_ID] });
		return r.memory;
	}

	it("settles useful_activated when a read after the injection names the entry file, then forgets the unit", () => {
		const f = fixture("settle-read");
		const memory = injected(f);
		appendEvents(f.runDir, [
			readTool(join(f.root, ENTRY_PATH)),
			ev("tool_result", { toolName: "read", toolCallId: "r1", isError: false }),
		]);
		const r = settleOnPass(f.env, {
			unitId: "build_env",
			memory: {
				...memory,
				pendingDisputed: { build_env: [ENTRY_ID] },
				injected: { ...memory.injected, plan: ["x/y"] },
			},
		});
		expect(r.diagnostics).toEqual([]);
		// The unit's injected / pendingDisputed lists are cleared; other units' lists stay.
		expect(r.memory).toEqual({ injected: { plan: ["x/y"] } });
		const rows = usageRows(f.memoryRoot);
		expect(rows).toHaveLength(2);
		expect(rows[1]).toMatchObject({
			kind: "settle",
			run_id: RUN_ID,
			skill: "sure_onboard",
			unit: "build_env",
			entry_id: ENTRY_ID,
			outcome: "useful_activated",
		});
	});

	it("settles useful_unattributed when the unit passed without reading the entry", () => {
		const f = fixture("settle-unread");
		const memory = injected(f);
		appendEvents(f.runDir, [
			bashTool("uv pip install torch==2.4.0 --index-url https://download.pytorch.org/whl/cu121"),
		]);
		const r = settleOnPass(f.env, { unitId: "build_env", memory });
		expect(r.memory).toEqual({});
		expect(usageRows(f.memoryRoot)[1]).toMatchObject({ kind: "settle", outcome: "useful_unattributed" });
	});

	it("counts a bash command that mentions the entry path as a read", () => {
		const f = fixture("settle-bash");
		const memory = injected(f);
		appendEvents(f.runDir, [bashTool(`sed -n 1,40p ${ENTRY_PATH}`)]);
		settleOnPass(f.env, { unitId: "build_env", memory });
		expect(usageRows(f.memoryRoot)[1]).toMatchObject({ kind: "settle", outcome: "useful_activated" });
	});

	it("ignores reads that happened before the injection", () => {
		const f = fixture("settle-early-read");
		seedEvents(f.runDir);
		appendEvents(f.runDir, [readTool(join(f.root, ENTRY_PATH))]);
		writeIndex(f.memoryRoot, [badCase()]);
		const r = injectOnBlock(f.env, { unitId: "build_env", attempt: 1, rawRepair: REPAIR_HIT, memory: {} });
		settleOnPass(f.env, { unitId: "build_env", memory: r.memory });
		expect(usageRows(f.memoryRoot)[1]).toMatchObject({ kind: "settle", outcome: "useful_unattributed" });
	});

	it("writes one settle row per entry even when settleOnPass runs twice with the same un-persisted memory", () => {
		const f = fixture("settle-twice");
		const memory = injected(f);
		const first = settleOnPass(f.env, { unitId: "build_env", memory });
		const second = settleOnPass(f.env, { unitId: "build_env", memory });
		expect(usageRows(f.memoryRoot).filter((row) => row.kind === "settle")).toHaveLength(1);
		expect(second.memory).toEqual(first.memory);
		// With the returned (cleared) memory there is nothing left to settle either.
		expect(settleOnPass(f.env, { unitId: "build_env", memory: first.memory })).toEqual({
			memory: {},
			diagnostics: [],
		});
	});

	it("does nothing for a unit that had no injection", () => {
		const f = fixture("settle-none");
		seedEvents(f.runDir);
		const r = settleOnPass(f.env, { unitId: "validate_spec", memory: { injected: { build_env: [ENTRY_ID] } } });
		expect(r.memory).toEqual({ injected: { build_env: [ENTRY_ID] } });
		expect(usageRows(f.memoryRoot)).toEqual([]);
	});

	it("settleOnTerminalFailure turns pending disputes into disputed rows and clears the unit's lists", () => {
		const f = fixture("settle-disputed");
		const memory = injected(f);
		const blocked = injectOnBlock(f.env, { unitId: "build_env", attempt: 2, rawRepair: REPAIR_HIT, memory });
		expect(blocked.memory.pendingDisputed).toEqual({ build_env: [ENTRY_ID] });
		const r = settleOnTerminalFailure(f.env, { unitId: "build_env", memory: blocked.memory });
		expect(r.diagnostics).toEqual([]);
		expect(r.memory).toEqual({});
		const rows = usageRows(f.memoryRoot);
		expect(rows).toHaveLength(2);
		expect(rows[1]).toMatchObject({ kind: "settle", unit: "build_env", entry_id: ENTRY_ID, outcome: "disputed" });
		// Re-entry with the un-persisted memory: the existing settle row wins, nothing is appended.
		const again = settleOnTerminalFailure(f.env, { unitId: "build_env", memory: blocked.memory });
		expect(again.memory).toEqual({});
		expect(usageRows(f.memoryRoot)).toHaveLength(2);
	});

	it("settles entries the failure never named again as abandoned", () => {
		// One block, so pendingDisputed is empty: without an abandoned row the entry stays
		// "injected, never settled" forever and the cold ratio counts it as unused.
		const f = fixture("settle-abandoned");
		const memory = injected(f);
		const r = settleOnTerminalFailure(f.env, { unitId: "build_env", memory });
		expect(r.diagnostics).toEqual([]);
		expect(r.memory).toEqual({});
		const rows = usageRows(f.memoryRoot);
		expect(rows).toHaveLength(2);
		expect(rows[1]).toMatchObject({
			kind: "settle",
			unit: "build_env",
			entry_id: ENTRY_ID,
			outcome: "abandoned",
		});
		// Re-entry with the un-persisted memory: settledIds does not look at the outcome.
		settleOnTerminalFailure(f.env, { unitId: "build_env", memory });
		expect(usageRows(f.memoryRoot)).toHaveLength(2);
	});

	it("splits a terminal failure into disputed for what it still names and abandoned for the rest", () => {
		const f = fixture("settle-split");
		seedEvents(f.runDir);
		const memory: MemoryCheckpoint = {
			injected: { build_env: [ENTRY_ID, "_shared/other"] },
			pendingDisputed: { build_env: [ENTRY_ID] },
		};
		const r = settleOnTerminalFailure(f.env, { unitId: "build_env", memory });
		expect(r.memory).toEqual({});
		const rows = usageRows(f.memoryRoot).filter((row) => row.kind === "settle");
		expect(rows.map((row) => [row.entry_id, row.outcome])).toEqual([
			[ENTRY_ID, "disputed"],
			["_shared/other", "abandoned"],
		]);
	});

	it("still settles useful_activated after an abandoned row, so a resumed run keeps its credit", () => {
		// on_error abandons the stuck unit, then /sure_resume reuses the same run id and the
		// same usage file: the abandoned row must not swallow the credit the retry earned.
		const f = fixture("settle-abandoned-then-pass");
		const memory = injected(f);
		settleOnTerminalFailure(f.env, { unitId: "build_env", memory });
		appendEvents(f.runDir, [
			readTool(join(f.root, ENTRY_PATH)),
			ev("tool_result", { toolName: "read", toolCallId: "r1", isError: false }),
		]);
		settleOnPass(f.env, { unitId: "build_env", memory });
		const rows = usageRows(f.memoryRoot).filter((row) => row.kind === "settle");
		expect(rows.map((row) => [row.entry_id, row.outcome])).toEqual([
			[ENTRY_ID, "abandoned"],
			[ENTRY_ID, "useful_activated"],
		]);
	});
});

describe("hooks.ts preStartMemory", () => {
	const CONTEXT_KEYS = ["facts", "omitted_provisional", "schema", "skill", "target_id"];
	const FACT_KEYS = ["checked_at", "entry_id", "path", "scope", "stale", "status", "title"];

	it("runs index.py --check, then writes memory_context.json (empty on a fresh clone)", () => {
		const f = fixture("prestart-fresh");
		const r = preStartMemory(f.env, {
			targetId: "openai/whisper-large-v3",
			strippedArgs: "model_id=openai/whisper-large-v3 task_type=asr deployment_type=local",
		});
		expect(r.diagnostics.filter((d) => d.severity === "error")).toEqual([]);
		expect(existsSync(join(f.memoryRoot, "index.json"))).toBe(true);
		const context = readContext(f.runDir);
		expect(Object.keys(context).sort()).toEqual(CONTEXT_KEYS);
		expect(context.schema).toBe("sure.memory.context.v1");
		expect(context.skill).toBe("sure_onboard");
		expect(context.target_id).toBe("openai/whisper-large-v3");
		expect(context.facts).toEqual([]);
		expect(context.omitted_provisional).toBe(0);
		expect(usageRows(f.memoryRoot)).toEqual([]);
	});

	it("keeps every confirmed fact, caps provisional facts, and logs a pre_start row (existing index, python unavailable)", () => {
		const f = fixture("prestart-facts", "sure_eval");
		const config = loadMemoryConfig();
		const provisional = Array.from({ length: config.memory_context_max_provisional + 2 }, (_, i) =>
			fact(`prov-${i}`, {
				status: "provisional",
				path: `sure/memory/provisional/_shared/prov-${i}/entry.md`,
				checked_at: `2026-07-${String(i + 1).padStart(2, "0")}`,
			}),
		);
		writeIndex(f.memoryRoot, [...provisional, fact("vc-partition-names")]);
		const r = withEnv("HARNESS_PYTHON_BIN", join(f.root, "no-such-python"), () =>
			preStartMemory(f.env, {
				targetId: "demo",
				strippedArgs: "model=demo datasets=aishell1__v1.0.2 output_dir=/tmp/should-not-leak",
			}),
		);
		// index --check could not run: warning only, the existing index is still used.
		expect(r.diagnostics.map((d) => d.severity)).toEqual(["warning"]);
		expect(r.diagnostics[0].message).toContain("index check");
		const context = readContext(f.runDir);
		const facts = context.facts as Record<string, unknown>[];
		expect(facts).toHaveLength(1 + config.memory_context_max_provisional);
		expect(Object.keys(facts[0]).sort()).toEqual(FACT_KEYS);
		expect(facts[0]).toMatchObject({
			entry_id: "_shared/vc-partition-names",
			title: "fact vc-partition-names",
			path: "sure/skills/_shared/memory/facts/vc-partition-names.md",
			scope: "cluster",
			checked_at: "2026-08-10",
			stale: false,
			status: "confirmed",
		});
		expect(facts[1].entry_id).toBe(`_shared/prov-${config.memory_context_max_provisional + 1}`);
		expect(context.omitted_provisional).toBe(2);
		expect(JSON.stringify(context)).not.toContain("output_dir");
		expect(JSON.stringify(context)).not.toContain("should-not-leak");
		const rows = usageRows(f.memoryRoot);
		expect(rows).toHaveLength(1);
		expect(rows[0]).toMatchObject({ kind: "pre_start", run_id: RUN_ID, skill: "sure_eval" });
		expect(rows[0].entries).toHaveLength(1 + config.memory_context_max_provisional);
		expect((rows[0].entries as { shared: boolean }[])[0].shared).toBe(true);
	});

	it("charges a pending revision against memory_context_max_provisional", () => {
		// contextFacts emits a folded-in revision as an item of its own, and a modify candidate is
		// itself provisional, so counting only the top-level matches let one provisional entry into
		// the file for free: neither against the budget nor into omitted_provisional.
		const f = fixture("prestart-revision-budget", "sure_eval");
		const max = loadMemoryConfig().memory_context_max_provisional;
		const provisional = Array.from({ length: max }, (_, i) =>
			fact(`prov-${i}`, {
				status: "provisional",
				path: `sure/memory/provisional/_shared/prov-${i}/entry.md`,
				checked_at: `2026-07-${String(i + 1).padStart(2, "0")}`,
			}),
		);
		writeIndex(f.memoryRoot, [
			...provisional,
			fact("vc-partition-names"),
			fact("vc-partition-names-v2", {
				status: "provisional",
				op: "modify",
				target_entry: "_shared/vc-partition-names",
				path: "sure/memory/provisional/_shared/vc-partition-names-v2/entry.md",
			}),
		]);
		const r = withEnv("HARNESS_PYTHON_BIN", join(f.root, "no-such-python"), () =>
			preStartMemory(f.env, { targetId: "demo", strippedArgs: "model=demo" }),
		);
		expect(r.diagnostics.filter((d) => d.severity === "error")).toEqual([]);
		const context = readContext(f.runDir);
		const facts = context.facts as Record<string, unknown>[];
		// The confirmed target is free; its revision takes one of the max provisional slots, so
		// one of the other provisional facts drops out instead of riding along for nothing.
		expect(facts).toHaveLength(1 + max);
		expect(facts[0].entry_id).toBe("_shared/vc-partition-names");
		expect(facts[1].entry_id).toBe("_shared/vc-partition-names-v2");
		expect(context.omitted_provisional).toBe(1);
		// The usage row lists exactly what the file shows.
		expect((usageRows(f.memoryRoot)[0].entries as unknown[]).length).toBe(1 + max);
	});

	it("blanks a fact path that is not repo-relative instead of writing a host path", () => {
		// index.py's _rel() falls back to an absolute posix path when relative_to(repo_root)
		// raises, and resolve() follows symlinks: on a shared cluster filesystem sure/memory is
		// normally a symlink out of the checkout, so every entry under it gets one. The agent
		// reads memory_context.json, so it must not carry the host path.
		const f = fixture("prestart-absolute-path", "sure_eval");
		const outside = "/srv/sure/sure-harness/sure/memory/facts/vc-partition-names.md";
		writeIndex(f.memoryRoot, [fact("vc-partition-names", { path: outside })]);
		const r = withEnv("HARNESS_PYTHON_BIN", join(f.root, "no-such-python"), () =>
			preStartMemory(f.env, { targetId: "demo", strippedArgs: "model=demo" }),
		);
		expect(r.diagnostics.filter((d) => d.severity === "error")).toEqual([]);
		const context = readContext(f.runDir);
		const facts = context.facts as Record<string, unknown>[];
		expect(facts).toHaveLength(1);
		expect(Object.keys(facts[0]).sort()).toEqual(FACT_KEYS);
		expect(facts[0].entry_id).toBe("_shared/vc-partition-names");
		expect(facts[0].path).toBe("");
		expect(JSON.stringify(context)).not.toContain("/srv/sure");
	});

	it("keeps the interpreter path out of the index-check warning", () => {
		// The check is spawned like every other memory script, so a spawn failure names the
		// absolute interpreter path; the warning it becomes is read by the agent and stored.
		const f = fixture("prestart-check-redacted");
		const python = join(f.root, "no-such-python");
		const r = withEnv("HARNESS_PYTHON_BIN", python, () =>
			preStartMemory(f.env, { targetId: "demo", strippedArgs: "model_id=demo" }),
		);
		const check = r.diagnostics.find((d) => d.message.includes("index check failed"));
		expect(check?.message).toContain("check_memory_index.py");
		expect(check?.message).not.toContain(python);
		expect(check?.message).not.toContain(f.root);
		expect(check?.message).not.toContain(f.root.split("\\").join("/"));
	});

	it("points a hash mismatch at the fix instead of at a rebuild", () => {
		// index.py --check exits EXIT_HASH_MISMATCH when it dropped an entry whose entry.md no
		// longer matches the entry_sha256 in its meta. A rebuild drops it again, so the old advice
		// sent the operator round in a circle; the entry has to be restored or rejected by hand.
		const f = fixture("prestart-hash-mismatch");
		writeFileSync(
			join(f.packageDir, "scripts", "check_memory_index.py"),
			[
				"import sys",
				'sys.stdout.write("index: up to date\\n")',
				'sys.stderr.write("index: 1 provisional entries dropped: hash mismatch (sure_onboard/x)\\n")',
				"raise SystemExit(2)",
				"",
			].join("\n"),
			"utf-8",
		);
		const r = preStartMemory(f.env, { targetId: "demo", strippedArgs: "model_id=demo" });
		const check = r.diagnostics.find((d) => d.message.includes("index check failed"));
		expect(check?.message).toContain("hash mismatch (sure_onboard/x)");
		expect(check?.repair).not.toContain("--rebuild");
		expect(check?.repair).toContain("cli.py reject");
		expect(check?.repair).toContain("entry_sha256");
		// The exit status is the whole contract this branch reads, and index.py owns it.
		expect(readFileSync(join(REPO_ROOT, "sure", "runtime", "memory", "index.py"), "utf-8")).toContain(
			"EXIT_HASH_MISMATCH = 2",
		);
		// A check that could not run at all is a different failure and keeps the rebuild advice.
		const g = fixture("prestart-check-unrunnable");
		rmSync(join(g.packageDir, "scripts", "check_memory_index.py"));
		const other = preStartMemory(g.env, { targetId: "demo", strippedArgs: "model_id=demo" });
		const unrunnable = other.diagnostics.find((d) => d.message.includes("index check failed"));
		expect(unrunnable?.repair).toContain("--rebuild");
	});

	it("keeps the run directory out of the memory_context.json write failure", () => {
		const f = fixture("prestart-context-unwritable");
		// artifacts/ replaced by a file: mkdirSync fails and its message names the absolute path.
		rmSync(join(f.runDir, "artifacts"), { recursive: true, force: true });
		writeFileSync(join(f.runDir, "artifacts"), "not a directory", "utf-8");
		const r = withEnv("HARNESS_PYTHON_BIN", join(f.root, "no-such-python"), () =>
			preStartMemory(f.env, { targetId: "demo", strippedArgs: "model_id=demo" }),
		);
		const failure = r.diagnostics.find((d) => d.message.includes("memory_context.json not written"));
		// The errno still says why; where the run directory lives on this host does not.
		expect(failure?.message).toMatch(/E[A-Z]{3,}/);
		expect(failure?.message).not.toContain(f.runDir);
		expect(failure?.message).not.toContain(f.runDir.split("\\").join("/"));
	});

	it("still writes an empty memory_context.json when neither python nor an index is available", () => {
		const f = fixture("prestart-noindex");
		const r = withEnv("HARNESS_PYTHON_BIN", join(f.root, "no-such-python"), () =>
			preStartMemory(f.env, { targetId: "demo", strippedArgs: "model_id=demo" }),
		);
		expect(r.diagnostics.map((d) => d.severity)).toEqual(["warning", "warning"]);
		expect(r.diagnostics[1].message).toContain("index unavailable");
		const context = readContext(f.runDir);
		expect(Object.keys(context).sort()).toEqual(CONTEXT_KEYS);
		expect(context.facts).toEqual([]);
		expect(usageRows(f.memoryRoot)).toEqual([]);
	});
});

describe("hooks.ts runMemoryGate", () => {
	it("passes a consistent no_new_lessons declaration through the real gate script", () => {
		const f = fixture("gate-pass");
		const { producesPath } = seedExtractedRun(f, DECLARATION_OK);
		expect(runMemoryGate(f.env, producesPath)).toEqual({ ok: true });
	});

	it("returns the gate script's stderr as the repair when the declaration is inconsistent", () => {
		const f = fixture("gate-fail");
		const { producesPath } = seedExtractedRun(f, DECLARATION_BAD);
		const r = runMemoryGate(f.env, producesPath);
		expect(r.ok).toBe(false);
		expect(r.reason).toContain("check_memory_extraction.py");
		expect(r.repair).toBeTruthy();
		expect(r.repair).not.toContain("Gate script scripts/check_memory_extraction.py exited");
	});

	it("masks a host path the gate printed before handing it on as the repair", () => {
		// A rejection the agent can act on is passed through as written, so it becomes the
		// top-level repair, run.json.lastRepair and, through digest.py, the next run's
		// prior_runs[].last_repair. proposals.py masks its own text; this is the backstop for
		// everything that reaches the hook from outside it.
		const f = fixture("gate-stderr-path");
		const { producesPath } = seedExtractedRun(f, DECLARATION_OK);
		const evidence = join(f.runDir, "artifacts", "evidence.log");
		// Shaped like a real rejection, header line included: that header is what tells the hook a
		// verdict was reached at all, so a stub without one is a gate that never judged.
		writeFileSync(
			join(f.packageDir, "scripts", "check_memory_extraction.py"),
			[
				"import sys",
				'sys.stderr.write("check_memory_extraction gate: 1 problem(s) in artifacts/extraction_declaration.json and its candidates.\\n")',
				`sys.stderr.write("- [rule 2] candidate 1: evidence " + ${JSON.stringify(evidence)} + " is outside the run dir\\n")`,
				"raise SystemExit(1)",
				"",
			].join("\n"),
			"utf-8",
		);
		const r = runMemoryGate(f.env, producesPath);
		expect(r.ok).toBe(false);
		// The gate ran and judged, so the text is the agent's repair, not a crash report.
		expect(r.ranFailed).toBe(false);
		expect(r.repair).toContain("candidate 1: evidence");
		expect(r.repair).not.toContain(f.runDir);
		expect(r.repair).not.toContain(f.runDir.split("\\").join("/"));
	});

	it("keeps a rejection a rejection when the declaration quotes a python traceback header", () => {
		// The gate quotes the declaration back at the agent ("... got 'Traceback (most recent call
		// last):'"), so a declaration carrying that literal used to make the hook read its own
		// rejection as a crashed gate — and a crashed gate is waved through. The declaration is the
		// one input the agent writes, so nothing it contains may decide whether the gate judged.
		const f = fixture("gate-traceback-in-declaration");
		const { producesPath } = seedExtractedRun(f, {
			...DECLARATION_OK,
			no_new_lessons: false,
			no_lessons_reason: null,
			covered_by: "Traceback (most recent call last):",
		});
		const r = runMemoryGate(f.env, producesPath);
		expect(r.ok).toBe(false);
		// Not vacuous: the gate really did echo the literal back into its repair text.
		expect(r.repair).toContain("Traceback (most recent call last):");
		expect(r.repair).toContain("covered_by must be a list of strings");
		expect(r.ranFailed).toBe(false);
	});

	it("still calls a real interpreter traceback a gate that never judged", () => {
		// The other half of the same decision: python dying at import writes its traceback at the
		// very start of stderr, where the gate's own verdict header would otherwise be.
		const f = fixture("gate-import-traceback");
		const { producesPath } = seedExtractedRun(f, DECLARATION_OK);
		writeFileSync(
			join(f.packageDir, "scripts", "check_memory_extraction.py"),
			"import no_such_module_for_memory_tests\n",
			"utf-8",
		);
		const r = runMemoryGate(f.env, producesPath);
		expect(r.ok).toBe(false);
		expect(r.ranFailed).toBe(true);
		expect(r.repair).toContain("crashed: ModuleNotFoundError");
	});

	it("treats a non-zero exit with no verdict header as a gate that never judged", () => {
		// The third case, and the reason the decision is an allowlist: argparse rejecting the
		// hook's own arguments exits 2 with a usage line, no traceback and no verdict. Calling
		// that a rejection would hand the agent a repair it cannot act on; calling it a pass
		// would publish candidates nothing gated. It is neither, so it is "not gated".
		const f = fixture("gate-usage-error");
		const { producesPath } = seedExtractedRun(f, DECLARATION_OK);
		writeFileSync(
			join(f.packageDir, "scripts", "check_memory_extraction.py"),
			[
				"import sys",
				'sys.stderr.write("usage: check_memory_extraction.py [-h] --run-dir RUN_DIR\\n")',
				"raise SystemExit(2)",
				"",
			].join("\n"),
			"utf-8",
		);
		const r = runMemoryGate(f.env, producesPath);
		expect(r.ok).toBe(false);
		expect(r.ranFailed).toBe(true);
	});

	it("reports a missing wrapper the same way runBackend does", () => {
		const f = fixture("gate-missing");
		const { producesPath } = seedExtractedRun(f, DECLARATION_OK);
		rmSync(join(f.packageDir, "scripts", "check_memory_extraction.py"));
		const r = runMemoryGate(f.env, producesPath);
		expect(r.ok).toBe(false);
		expect(r.repair).toContain("Backend script not found: scripts/check_memory_extraction.py");
	});
});

describe("hooks.ts preFinishExtraction", () => {
	it("does nothing for a success finish", () => {
		const f = fixture("finish-success");
		const memory: MemoryCheckpoint = { injected: { build_env: [ENTRY_ID] } };
		const r = withEnv("HARNESS_PYTHON_BIN", join(f.root, "no-such-python"), () =>
			preFinishExtraction(f.env, { finishStatus: "success", memory }),
		);
		expect(r).toEqual({ ok: true, memory, diagnostics: [] });
		expect(existsSync(join(f.runDir, "artifacts", "run_digest.json"))).toBe(false);
	});

	it("blocks a failed finish twice without a declaration, building the digest once, then lets the third through", () => {
		const f = fixture("finish-three");
		seedEvents(f.runDir);
		const first = preFinishExtraction(f.env, { finishStatus: "failed", memory: {} });
		expect(first.ok).toBe(false);
		expect(first.repair).toContain("artifacts/extraction_declaration.json is missing");
		expect(first.repair).toContain("sure/runtime/memory/EXTRACTION.md");
		expect(first.repair).toContain("do not end the turn");
		expect(first.memory.finishAttempts).toBe(1);
		const digestPath = join(f.runDir, "artifacts", "run_digest.json");
		expect(existsSync(digestPath)).toBe(true);
		expect(first.memory.digestSha256).toBe(sha256(digestPath));
		expect(first.memory.digestCutoff).toBe(4);
		expect(first.memory.digestPassed).toBeUndefined();
		// --finish-status reached digest.py: the run is recorded as ending failed.
		expect((readDigest(f.runDir).run as { status_so_far: string }).status_so_far).toBe("failed");

		const before = readFileSync(digestPath, "utf-8");
		appendEvents(f.runDir, [bashTool("ls")]);
		const second = preFinishExtraction(f.env, { finishStatus: "failed", memory: first.memory });
		expect(second.ok).toBe(false);
		expect(second.memory.finishAttempts).toBe(2);
		expect(second.memory.digestSha256).toBe(first.memory.digestSha256);
		expect(readFileSync(digestPath, "utf-8")).toBe(before);

		const third = preFinishExtraction(f.env, { finishStatus: "incomplete", memory: second.memory });
		expect(third.ok).toBe(true);
		expect(third.repair).toBeUndefined();
		expect(third.memory.extractionStatus).toBe("failed");
		expect(third.memory.finishAttempts).toBe(3);
		expect(third.diagnostics).toHaveLength(1);
		expect(third.diagnostics[0].message).toContain("extraction: failed");

		const fourth = preFinishExtraction(f.env, { finishStatus: "failed", memory: third.memory });
		expect(fourth).toEqual({ ok: true, memory: third.memory, diagnostics: [] });
	});

	it("passes a failed finish when the declaration exists and the gate accepts it", () => {
		const f = fixture("finish-declared");
		const { memory } = seedExtractedRun(f, DECLARATION_OK);
		const r = preFinishExtraction(f.env, { finishStatus: "failed", memory });
		expect(r).toEqual({ ok: true, memory, diagnostics: [] });
	});

	it("does not block the finish when the extraction gate itself could not run", () => {
		const f = fixture("finish-gate-broken");
		// The declaration would be rejected, but the gate cannot even start: that is memory
		// infrastructure, not something the agent can repair, so the finish goes through.
		const { memory } = seedExtractedRun(f, DECLARATION_BAD);
		rmSync(join(f.packageDir, "scripts", "check_memory_extraction.py"));
		const r = preFinishExtraction(f.env, { finishStatus: "failed", memory });
		expect(r.ok).toBe(true);
		expect(r.repair).toBeUndefined();
		expect(r.memory.finishAttempts).toBeUndefined();
		// Nothing passed a gate this run, so post_finish must not publish either.
		expect(r.memory.extractionStatus).toBe("failed");
		expect(r.diagnostics.map((d) => d.severity)).toEqual(["warning"]);
		expect(r.diagnostics[0].message).toContain("memory extraction gate could not run");
	});

	it("blocks with the gate repair when the declaration is inconsistent", () => {
		const f = fixture("finish-gate-fail");
		const { memory } = seedExtractedRun(f, DECLARATION_BAD);
		const r = preFinishExtraction(f.env, { finishStatus: "incomplete", memory });
		expect(r.ok).toBe(false);
		expect(r.memory.finishAttempts).toBe(1);
		expect(r.memory.digestSha256).toBe(memory.digestSha256);
		expect(r.repair).toContain("extraction attempt 1 of");
		expect(r.repair).toContain("artifacts/extraction_declaration.json");
		expect(r.repair).toContain("sure/runtime/memory/EXTRACTION.md");
	});
});

describe("hooks.ts postFinishMemory", () => {
	it("skips publish when extraction failed, and says nothing", () => {
		const f = fixture("publish-skip");
		const r = withEnv("HARNESS_PYTHON_BIN", join(f.root, "no-such-python"), () =>
			postFinishMemory(f.env, { extractionStatus: "failed" }),
		);
		// An info line here would replace state.diagnostics and drop the last gate's messages.
		expect(r.diagnostics).toEqual([]);
		expect(existsSync(join(f.memoryRoot, "digests"))).toBe(false);
	});

	it("runs scripts/publish_memory.py and stays silent when it succeeds", () => {
		const f = fixture("publish-run");
		seedExtractedRun(f, DECLARATION_OK);
		const r = postFinishMemory(f.env, {});
		expect(r.diagnostics).toEqual([]);
		expect(existsSync(join(f.memoryRoot, "provisional"))).toBe(true);
	});

	it("skips the publish when the declaration no longer passes the gate", () => {
		// Fail closed: publish_memory.py re-runs none of the ten rules, so post_finish asks the gate
		// itself rather than trusting the checkpoint to have reached it. Here the checkpoint says
		// nothing at all - exactly what a state_patch dropped by the harness normalizer leaves
		// behind - and the declaration on disk contradicts itself.
		const f = fixture("publish-ungated");
		seedExtractedRun(f, DECLARATION_BAD);
		const r = postFinishMemory(f.env, {});
		expect(r.diagnostics.map((d) => d.severity)).toEqual(["warning"]);
		expect(r.diagnostics[0].message).toContain("memory publish skipped");
		// Not vacuous: the same fixture with a declaration the gate accepts does publish.
		expect(existsSync(join(f.memoryRoot, "provisional"))).toBe(false);
		writeArtifact(f.runDir, "extraction_declaration.json", DECLARATION_OK);
		expect(postFinishMemory(f.env, {}).diagnostics).toEqual([]);
		expect(existsSync(join(f.memoryRoot, "provisional"))).toBe(true);
	});

	it("skips the publish when the gate cannot run at all", () => {
		// An unknown gate state is not a passing gate state: no interpreter means no verdict, and
		// no verdict means nothing is stored.
		const f = fixture("publish-gate-unrunnable");
		seedExtractedRun(f, DECLARATION_OK);
		const r = withEnv("HARNESS_PYTHON_BIN", join(f.root, "no-such-python"), () => postFinishMemory(f.env, {}));
		expect(r.diagnostics.map((d) => d.severity)).toEqual(["warning"]);
		expect(r.diagnostics[0].message).toContain("memory publish skipped");
		expect(existsSync(join(f.memoryRoot, "provisional"))).toBe(false);
	});

	it("reports a publish failure as an error diagnostic without throwing", () => {
		const f = fixture("publish-fail");
		seedExtractedRun(f, DECLARATION_OK);
		failPublishScript(f);
		const r = postFinishMemory(f.env, {});
		expect(r.diagnostics.map((d) => d.severity)).toEqual(["error"]);
		expect(r.diagnostics[0].message).toContain("memory publish failed");
		expect(r.diagnostics[0].repair).toContain("fix-perms");
	});

	it("masks the host paths of a publish traceback in the diagnostic it raises", () => {
		// publish_memory.py dies at import: the traceback frames carry the absolute path of the
		// wrapper and of the interpreter's own files. Nothing builds a diagnostic by hand, so the
		// masking belongs to diag() and covers whatever text a call site puts in.
		const f = fixture("publish-traceback");
		seedExtractedRun(f, DECLARATION_OK);
		writeFileSync(
			join(f.packageDir, "scripts", "publish_memory.py"),
			"import no_such_module_for_memory_tests\n",
			"utf-8",
		);
		const r = postFinishMemory(f.env, {});
		expect(r.diagnostics).toHaveLength(1);
		expect(r.diagnostics[0].message).toContain("memory publish failed");
		expect(r.diagnostics[0].message).toContain("ModuleNotFoundError");
		expect(r.diagnostics[0].message).not.toContain(f.packageDir);
		expect(r.diagnostics[0].message).not.toContain(f.packageDir.split("\\").join("/"));
	});

	it("keeps the run directory out of the publish repair digest.py carries into the next run", () => {
		// This repair is the only memory text that lands in diagnostics[].repair, and digest.py
		// _repair_of harvests that field into the NEXT run's prior_runs[].last_repair: a host path
		// in it is copied into the memory system's own input for every following run of the same
		// target, from where an agent can lift it into a trigger and store it for good.
		const f = fixture("publish-fail-run-dir");
		seedExtractedRun(f, DECLARATION_OK);
		failPublishScript(f);
		const r = postFinishMemory(f.env, {});
		expect(r.diagnostics).toHaveLength(1);
		// The publish itself failed; this is not the gate declining to let it run.
		expect(r.diagnostics[0].message).toContain("memory publish failed");
		const repair = r.diagnostics[0].repair;
		expect(repair).not.toContain(f.runDir);
		expect(repair).not.toContain(f.runDir.split("\\").join("/"));
		// The operator still learns which run to republish and with which script.
		expect(repair).toContain(RUN_ID);
		expect(repair).toContain("publish_memory.py");

		// The propagation itself: park that diagnostic where digest.py looks for the last repair of
		// a finished run (a finish_repair event of a prior run with the same skill and target), then
		// build the next run's digest with the real script and read prior_runs back.
		const priorDir = resolve(f.runDir, "..", "20260818-110000-aaaaaaaa");
		mkdirSync(join(priorDir, "artifacts"), { recursive: true });
		const priorRecord = JSON.parse(readFileSync(join(f.runDir, "run.json"), "utf-8")) as Record<string, unknown>;
		writeFileSync(
			join(priorDir, "run.json"),
			`${JSON.stringify({ ...priorRecord, runId: basename(priorDir), runDir: priorDir, status: "failed" }, null, 2)}\n`,
			"utf-8",
		);
		writeEvents(priorDir, [ev("finish_repair", { state_patch: { diagnostics: r.diagnostics } })]);
		const built = buildDigest(f.env, { cutoff: readEventCount(f.runDir) });
		expect(built.ok).toBe(true);
		const priorRuns = readDigest(f.runDir).prior_runs as { last_repair: string | null }[];
		// Not vacuous: the harvest really happened, and what it carried names no host path.
		expect(priorRuns[0].last_repair).toContain("publish_memory.py");
		expect(priorRuns[0].last_repair).not.toContain(f.runDir);
		expect(priorRuns[0].last_repair).not.toContain(f.runDir.split("\\").join("/"));
	});
});

describe("hooks.ts with an unreadable config.json", () => {
	const MATCH_MODULE = "../../../../sure/runtime/memory/match.ts";

	it("every entry point degrades to a warning when config.json cannot be read", async () => {
		const f = fixture("config-unreadable");
		seedEvents(f.runDir);
		// loadMemoryConfig() throws when sure/runtime/memory/config.json is missing or its schema
		// moved on (Task 9), and config.json is a file humans tune (spec 8.2). Mocking the module
		// beats renaming the real file: every other test file reads the same config.json, and
		// vitest runs test files in parallel.
		vi.resetModules();
		vi.doMock(MATCH_MODULE, async (importOriginal) => {
			const actual = await importOriginal<typeof import("../../../../sure/runtime/memory/match.ts")>();
			return {
				...actual,
				loadMemoryConfig: () => {
					throw new Error("memory config has an unknown schema: config.json");
				},
			};
		});
		try {
			const hooks = await import("../../../../sure/runtime/memory/hooks.ts");
			const warned = (diagnostics: { severity: string; message: string }[]): void => {
				expect(diagnostics.map((item) => item.severity)).toEqual(["warning"]);
				expect(diagnostics[0].message).toContain("memory config unreadable");
			};
			const injected = hooks.injectOnBlock(f.env, {
				unitId: "build_env",
				attempt: 1,
				rawRepair: REPAIR_HIT,
				memory: {},
			});
			expect(injected.repair).toBe(REPAIR_HIT);
			expect(injected.memory).toEqual({});
			warned(injected.diagnostics);

			const settled = hooks.settleOnPass(f.env, {
				unitId: "build_env",
				memory: { injected: { build_env: [ENTRY_ID] } },
			});
			expect(settled.memory).toEqual({});
			warned(settled.diagnostics);

			warned(hooks.postFinishMemory(f.env, {}).diagnostics);
			warned(hooks.preStartMemory(f.env, { targetId: "demo", strippedArgs: "model_id=demo" }).diagnostics);
			expect(readContext(f.runDir).facts).toEqual([]);

			const finish = hooks.preFinishExtraction(f.env, { finishStatus: "failed", memory: {} });
			expect(finish.ok).toBe(true);
			warned(finish.diagnostics);

			const built = hooks.buildDigest(f.env, { cutoff: 0 });
			expect(built.ok).toBe(false);
			expect(Object.keys(readDigest(f.runDir)).sort()).toEqual(["error", "schema"]);
			expect(usageRows(f.memoryRoot)).toEqual([]);
		} finally {
			vi.doUnmock(MATCH_MODULE);
			vi.resetModules();
		}
	});

	it("keeps the absolute path of config.json out of the gate repair the agent reads", async () => {
		// runMemoryGate is the one entry point whose config failure becomes the TOP-LEVEL repair:
		// both skills hand its `repair` straight to failOrRetry, which puts it in the repair the
		// agent reads, in diagnostics[0].repair and in run.json.lastRepair, from where digest.py
		// carries it into the next run's prior_runs[].last_repair and an agent can lift it into a
		// stored trigger. So the error text must name the file and the error code, nothing else.
		// The mock throws the REAL fs error, absolute path and all, exactly as a hand-tuned
		// config.json that went missing would (spec 8.2).
		const f = fixture("config-unreadable-gate");
		seedEvents(f.runDir);
		const realConfig = join(REPO_ROOT, "sure", "runtime", "memory", "config.json");
		let fsError: Error | undefined;
		try {
			readFileSync(`${realConfig}.absent`, "utf-8");
		} catch (error) {
			fsError = error as Error;
		}
		expect(fsError?.message).toContain("config.json.absent");
		expect(fsError?.message).toContain(REPO_ROOT.split("\\").join("/").split("/").slice(-1)[0]);
		vi.resetModules();
		vi.doMock(MATCH_MODULE, async (importOriginal) => {
			const actual = await importOriginal<typeof import("../../../../sure/runtime/memory/match.ts")>();
			return {
				...actual,
				loadMemoryConfig: () => {
					throw fsError;
				},
			};
		});
		try {
			const hooks = await import("../../../../sure/runtime/memory/hooks.ts");
			const gate = hooks.runMemoryGate(f.env, join(f.runDir, "artifacts", "extraction_declaration.json"));
			expect(gate.ok).toBe(false);
			// The gate never ran: a broken installation, not a declaration the agent can repair.
			expect(gate.ranFailed).toBe(true);
			expect(gate.repair).toBe("memory config unreadable: config.json (ENOENT)");
			expect(gate.repair).not.toContain(REPO_ROOT);
			// No path of any shape: no drive letter, no leading slash, no separator run.
			expect(gate.repair).not.toMatch(/[A-Za-z]:[\\/]|[\\/][\w.-]+[\\/]/);
		} finally {
			vi.doUnmock(MATCH_MODULE);
			vi.resetModules();
		}
	});
});

describe("hooks.ts with a config.json from before the gate digest caps", () => {
	const MATCH_MODULE = "../../../../sure/runtime/memory/match.ts";

	it("says at pre_start which keys are running on a built-in default", async () => {
		// Nothing fails when config.json predates a key: the schema still matches and the number
		// reads back undefined, so the cap it stands for quietly does nothing. Every deployment's
		// config.json is in that state the first time it is upgraded, so the run has to say so.
		const f = fixture("config-defaulted");
		const older = JSON.parse(
			readFileSync(join(REPO_ROOT, "sure", "runtime", "memory", "config.json"), "utf-8"),
		) as Record<string, unknown>;
		delete older.gate_digest_max_entries;
		delete older.gate_digest_max_bytes;
		vi.resetModules();
		vi.doMock(MATCH_MODULE, async (importOriginal) => {
			const actual = await importOriginal<typeof import("../../../../sure/runtime/memory/match.ts")>();
			return { ...actual, loadMemoryConfig: () => actual.applyMemoryConfigDefaults(older) };
		});
		try {
			const hooks = await import("../../../../sure/runtime/memory/hooks.ts");
			const r = withEnv("HARNESS_PYTHON_BIN", join(f.root, "no-such-python"), () =>
				hooks.preStartMemory(f.env, { targetId: "demo", strippedArgs: "model_id=demo" }),
			);
			const said = r.diagnostics.find((d) => d.message.includes("gate_digest_max_entries"));
			expect(said?.severity).toBe("warning");
			// Both keys, and the value each one is running on.
			expect(said?.message).toContain("2000");
			expect(said?.message).toContain("gate_digest_max_bytes");
			expect(said?.message).toContain("8388608");
			expect(said?.repair).toContain("sure/runtime/memory/config.json");
		} finally {
			vi.doUnmock(MATCH_MODULE);
			vi.resetModules();
		}
	});

	it("says nothing when config.json carries every key", () => {
		const f = fixture("config-complete");
		const r = withEnv("HARNESS_PYTHON_BIN", join(f.root, "no-such-python"), () =>
			preStartMemory(f.env, { targetId: "demo", strippedArgs: "model_id=demo" }),
		);
		expect(r.diagnostics.filter((d) => d.message.includes("built-in default"))).toEqual([]);
	});
});

// --- the host path guard --------------------------------------------------------------------

describe("hooks.ts host path guard", () => {
	// Point fixes did not stop new host-path leaks appearing, so this drives the emitting paths
	// and checks everything they produce at once: both fields of every diagnostic, the repair
	// injectOnBlock hands back, artifacts/run_digest.json and artifacts/memory_context.json. A
	// leak added to any of them later fails here without anyone having to think of it.
	//
	// It can do that because the fixture is hermetic: every path the run itself carries (the
	// args, the events, the index entries) is repo-relative, so an absolute path in the output
	// can only have been put there by the memory system. That is also the boundary. The digest
	// legitimately quotes bash commands and log tails, so a run whose own content named a host
	// path would trip this; and it only covers the failures the scenarios below actually reach.
	const DIGEST_NAME = "run_digest.json";
	const CONTEXT_NAME = "memory_context.json";
	const HOST_ROOTS = [REPO_ROOT, PYTHON_BIN ?? "", tmpdir(), homedir()].filter((root) => root.length > 0);
	// An absolute path of any spelling: a drive letter (never the "s:" of "https://"), an
	// extended-length prefix, or a rooted posix path of at least two segments. A repo-relative
	// "sure/memory/index.json" or "usage/<run_id>.jsonl" matches none of them.
	const ABSOLUTE_ANYWHERE = /(?:^|[^\w])[A-Za-z]:[\\/]|\\\\\?\\|\/\/\?\/|(?:^|[\s"'(=,])\/[A-Za-z][\w.-]*\/[\w.-]/g;

	function hostPaths(text: string): string[] {
		const hits: string[] = [];
		// JSON escapes every backslash, so a windows path is looked for in both spellings.
		for (const form of new Set([text, text.split("\\\\").join("\\")])) {
			for (const root of HOST_ROOTS) {
				for (const spelling of [root, root.split("\\").join("/"), `\\\\?\\${root}`]) {
					if (form.includes(spelling)) {
						hits.push(spelling);
					}
				}
			}
			hits.push(...(form.match(ABSOLUTE_ANYWHERE) ?? []).map((hit) => hit.trim()));
		}
		return [...new Set(hits)];
	}

	/** Every leak found in `texts`, as "<where>: [<what>] in <the text it sits in>". */
	function leaks(label: string, texts: (string | undefined)[]): string[] {
		return texts.flatMap((text) => hostPaths(text ?? "").map((hit) => `${label}: [${hit}] in ${text ?? ""}`));
	}

	function diagnosticText(diagnostics: MemoryDiagnostic[]): string[] {
		return diagnostics.flatMap((diagnostic) => [diagnostic.message, diagnostic.repair]);
	}

	function artifactText(f: Fixture): string[] {
		return [DIGEST_NAME, CONTEXT_NAME]
			.map((name) => join(f.runDir, "artifacts", name))
			.filter((path) => existsSync(path))
			.map((path) => readFileSync(path, "utf-8"));
	}

	it("finds the host paths this guard exists to catch", () => {
		// The detector itself, so that a scenario coming back clean means something.
		expect(hostPaths(`saw ${join(REPO_ROOT, "sure", "memory")}`)).not.toEqual([]);
		expect(hostPaths(`saw ${REPO_ROOT.split("\\").join("/")}/sure`)).not.toEqual([]);
		expect(hostPaths("spawnSync C:\\Python312\\python.exe ENOENT")).not.toEqual([]);
		expect(hostPaths("open '/home/someone/sure/memory/index.json'")).not.toEqual([]);
		expect(hostPaths('File "\\\\?\\D:\\sure\\x.py", line 1')).not.toEqual([]);
		// A JSON file escapes its backslashes; the leak is still a leak.
		expect(hostPaths(JSON.stringify({ error: "mkdir 'D:\\sure\\runs\\r1'" }))).not.toEqual([]);
		// What must still get through: repo-relative paths, the mask, a url, an injected line.
		expect(hostPaths("sure/runtime/memory/EXTRACTION.md and usage/20260818-120000-ab.jsonl")).toEqual([]);
		expect(hostPaths("python3 -s sure/runtime/memory/cli.py fix-perms (<path>)")).toEqual([]);
		expect(hostPaths("repo=https://example.invalid/whisper model_id=openai/whisper-large-v3")).toEqual([]);
		expect(hostPaths("- [confirmed] sure_onboard/x (sure/skills/sure_onboard/references/memory/x.md): t")).toEqual(
			[],
		);
	});

	it("emits none through a run where every script works", () => {
		const f = fixture("guard-working");
		const found: string[] = [];
		const start = preStartMemory(f.env, {
			targetId: "openai/whisper-large-v3",
			strippedArgs: "model_id=openai/whisper-large-v3 task_type=asr deployment_type=local",
		});
		found.push(...leaks("pre_start", diagnosticText(start.diagnostics)));
		seedEvents(f.runDir);
		const entered = onEnterExtractLessons(f.env, "verdict", {});
		found.push(...leaks("extract_lessons", diagnosticText(entered.diagnostics)));
		const produces = writeArtifact(f.runDir, "extraction_declaration.json", DECLARATION_OK);
		const gate = runMemoryGate(f.env, produces);
		found.push(...leaks("gate", [gate.repair, gate.reason]));
		const finish = preFinishExtraction(f.env, { finishStatus: "failed", memory: entered.memory });
		found.push(...leaks("pre_finish", [finish.repair, ...diagnosticText(finish.diagnostics)]));
		found.push(...leaks("post_finish", diagnosticText(postFinishMemory(f.env, finish.memory).diagnostics)));
		found.push(...leaks("artifacts", artifactText(f)));
		expect(found).toEqual([]);
		// Not vacuous: the digest and the context file were really written and really scanned.
		expect(artifactText(f)).toHaveLength(2);
	});

	it("emits none through a run with matching entries and no interpreter", () => {
		const f = fixture("guard-no-python");
		const found: string[] = [];
		writeIndex(f.memoryRoot, [badCase(), fact("vc-partition-names")]);
		seedEvents(f.runDir);
		withEnv("HARNESS_PYTHON_BIN", join(f.root, "no-such-python"), () => {
			const start = preStartMemory(f.env, {
				targetId: "openai/whisper-large-v3",
				strippedArgs: "model_id=openai/whisper-large-v3 output_dir=/tmp/should-not-leak",
			});
			found.push(...leaks("pre_start", diagnosticText(start.diagnostics)));
			const entered = onEnterExtractLessons(f.env, "verdict", {});
			found.push(...leaks("extract_lessons", diagnosticText(entered.diagnostics)));
			const blocked = injectOnBlock(f.env, {
				unitId: "build_env",
				attempt: 1,
				rawRepair: REPAIR_HIT,
				memory: entered.memory,
			});
			found.push(...leaks("inject", [blocked.repair, ...diagnosticText(blocked.diagnostics)]));
			const settled = settleOnPass(f.env, { unitId: "build_env", memory: blocked.memory });
			found.push(...leaks("settle", diagnosticText(settled.diagnostics)));
			const finish = preFinishExtraction(f.env, { finishStatus: "failed", memory: blocked.memory });
			found.push(...leaks("pre_finish", [finish.repair, ...diagnosticText(finish.diagnostics)]));
			found.push(...leaks("post_finish", diagnosticText(postFinishMemory(f.env, finish.memory).diagnostics)));
		});
		found.push(...leaks("artifacts", artifactText(f)));
		expect(found).toEqual([]);
		// The entry really was injected, so the block, the usage row and the context file are real.
		expect(readContext(f.runDir).facts).toHaveLength(1);
		expect(usageRows(f.memoryRoot).map((row) => row.kind)).toContain("inject");
	});

	it("emits none when every backend script dies at import", () => {
		const f = fixture("guard-crashing-scripts");
		const found: string[] = [];
		const scripts = [
			"build_run_digest.py",
			"check_memory_extraction.py",
			"publish_memory.py",
			"check_memory_index.py",
		];
		for (const script of scripts) {
			writeFileSync(join(f.packageDir, "scripts", script), "import no_such_module_for_memory_tests\n", "utf-8");
		}
		seedEvents(f.runDir);
		const start = preStartMemory(f.env, { targetId: "demo", strippedArgs: "model_id=demo" });
		found.push(...leaks("pre_start", diagnosticText(start.diagnostics)));
		const entered = onEnterExtractLessons(f.env, "verdict", {});
		found.push(...leaks("extract_lessons", diagnosticText(entered.diagnostics)));
		const produces = writeArtifact(f.runDir, "extraction_declaration.json", DECLARATION_OK);
		const gate = runMemoryGate(f.env, produces);
		found.push(...leaks("gate", [gate.repair, gate.reason]));
		const finish = preFinishExtraction(f.env, { finishStatus: "failed", memory: entered.memory });
		found.push(...leaks("pre_finish", [finish.repair, ...diagnosticText(finish.diagnostics)]));
		found.push(...leaks("post_finish", diagnosticText(postFinishMemory(f.env, {}).diagnostics)));
		found.push(...leaks("artifacts", artifactText(f)));
		expect(found).toEqual([]);
		// Every one of those scripts really did fail, so there was something to leak.
		expect(entered.diagnostics[0].message).toContain("memory digest failed");
		expect(gate.ranFailed).toBe(true);
	});

	it("emits none when the usage tree and the artifacts directory cannot be written", () => {
		const found: string[] = [];
		const f = fixture("guard-unwritable-usage");
		writeIndex(f.memoryRoot, [badCase()]);
		writeFileSync(join(f.memoryRoot, "usage"), "not a directory", "utf-8");
		seedEvents(f.runDir);
		const injected = withEnv("HARNESS_PYTHON_BIN", join(f.root, "no-such-python"), () =>
			injectOnBlock(f.env, { unitId: "build_env", attempt: 1, rawRepair: REPAIR_HIT, memory: {} }),
		);
		found.push(...leaks("inject", [injected.repair, ...diagnosticText(injected.diagnostics)]));
		const settled = settleOnPass(f.env, { unitId: "build_env", memory: injected.memory });
		found.push(...leaks("settle", diagnosticText(settled.diagnostics)));

		const g = fixture("guard-unwritable-artifacts");
		rmSync(join(g.runDir, "artifacts"), { recursive: true, force: true });
		writeFileSync(join(g.runDir, "artifacts"), "not a directory", "utf-8");
		const start = withEnv("HARNESS_PYTHON_BIN", join(g.root, "no-such-python"), () =>
			preStartMemory(g.env, { targetId: "demo", strippedArgs: "model_id=demo" }),
		);
		found.push(...leaks("pre_start", diagnosticText(start.diagnostics)));
		expect(found).toEqual([]);
		// Both failures really happened.
		expect(diagnosticText(injected.diagnostics).join(" ")).toContain("usage row not written");
		expect(diagnosticText(start.diagnostics).join(" ")).toContain("memory_context.json not written");
	});
});
