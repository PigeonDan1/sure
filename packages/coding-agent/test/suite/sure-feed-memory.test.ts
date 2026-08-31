import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { appendFileSync, cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { loadMemoryConfig } from "../../../../sure/runtime/memory/match.ts";
import { writeSkillRuntimeBinding } from "../../../../sure/runtime/usage.ts";
import type { CheckpointData } from "../../../../sure/skills/sure_feed/hooks/checkpoints.ts";
import {
	onError,
	postFinish,
	postToolResult,
	preFinish,
	preStart,
} from "../../../../sure/skills/sure_feed/hooks/index.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// Wiring tests for the memory system inside the sure_feed hooks (plan §5). The minimal set
// decided in D4: one case per hook point, the deadlock exemption, the file-level gate digest
// and the two shapes the template did not fit (a declaration that is not JSON, masked args).
// The wider behaviour of the shared library is covered by sure/runtime/memory/test_*.py and by
// sure-onboard-memory.test.ts; nothing here re-tests it.
//
// Every fixture is a throwaway repo whose sure/skills/sure_feed is the packageDir the hooks
// see, so memoryRootFor(packageDir) lands inside the temp tree and the real sure/memory/ is
// never touched. Its harness bootstrap is a stub that reports the local python, which is what
// lets preStart and runBackend work here without a materialized harness runtime.

const REPO_ROOT = resolve(__dirname, "../../../..");
const REAL_PACKAGE_DIR = join(REPO_ROOT, "sure", "skills", "sure_feed");
const TMP = resolve(__dirname, "tmp-feed-memory");
const CONFIG = loadMemoryConfig();

const UNITS_BEFORE_RANK = [
	"scan_modelscope",
	"match_task",
	"collect_metadata",
	"convert_to_oref",
	"synthesize_model_input",
];

const ENTRY_ID = "sure_feed/metadata-models-field";
const ENTRY_PATH = "sure/skills/sure_feed/references/memory/bad_cases/metadata-models-field.md";
const ENTRY_TITLE = "collect_metadata writes models[], not model[]";
// One confirmed bad_case whose trigger is a substring of the validate.ts required-field repair.
const INDEX_ENTRY = {
	entry_id: ENTRY_ID,
	type: "bad_case",
	status: "confirmed",
	target_skill: "sure_feed",
	applies_to: ["sure_feed"],
	component: "collect_metadata",
	cause: "result_layout",
	trigger: ['missing required field "models"'],
	hook_trigger: ['missing required field "models"'],
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
	created: "2026-08-31",
	checked_at: null,
	stale: false,
	superseded_by: null,
};

const FACT_ENTRY = {
	...INDEX_ENTRY,
	entry_id: "_shared/hf-mirror-endpoint",
	type: "fact",
	target_skill: "_shared",
	applies_to: ["sure_feed"],
	component: "_",
	cause: "n.a.",
	trigger: ["hf-mirror.com"],
	hook_trigger: ["hf-mirror.com"],
	title: "hf-mirror is the fallback endpoint",
	path: "sure/skills/_shared/memory/facts/hf-mirror-endpoint.md",
};

type StatePatchForTest = {
	message?: string;
	diagnostics?: Array<{ severity?: string; message: string; repair?: string }>;
	phase?: { id?: string; status?: string };
	checkpoint?: { data: CheckpointData };
};

function statePatch(result: { state_patch?: unknown }): StatePatchForTest {
	return (result.state_patch ?? {}) as StatePatchForTest;
}

function detectPython(): string {
	for (const candidate of ["python3", "python"]) {
		const probe = spawnSync(candidate, ["-c", "import sys; print(sys.version_info[0])"], { encoding: "utf-8" });
		if (probe.status === 0 && probe.stdout.trim() === "3") {
			return candidate;
		}
	}
	return "python3";
}

function pythonBin(): string {
	return process.env.HARNESS_PYTHON_BIN ?? "python3";
}

beforeAll(() => {
	// resolveMemoryPython() honours HARNESS_PYTHON_BIN first; the harness bootstrap stub below
	// exports the same interpreter for runBackend, so both halves agree.
	if (!process.env.HARNESS_PYTHON_BIN) {
		process.env.HARNESS_PYTHON_BIN = detectPython();
	}
});

interface Fixture {
	ctx: SureHookContext;
	runDir: string;
	repoRoot: string;
	packageDir: string;
	memoryRoot: string;
}

function writeWrapper(packageDir: string, script: string, module: string): void {
	// Same shape as the shipped wrapper except the sys.path entry is absolute, because this
	// fake package does not live inside the real repo.
	const runtimeDir = join(REPO_ROOT, "sure", "runtime").replace(/\\/g, "/");
	const body = [
		"#!/usr/bin/env python3",
		"from __future__ import annotations",
		"import sys",
		`sys.path.insert(0, ${JSON.stringify(runtimeDir)})`,
		`from memory import ${module}`,
		`raise SystemExit(${module}.main(sys.argv[1:]))`,
		"",
	].join("\n");
	writeFileSync(join(packageDir, "scripts", script), body, "utf-8");
}

function writePublishStub(packageDir: string): void {
	// Records its argv; publish semantics are covered by sure/runtime/memory/test_publish.py.
	const body = [
		"import json, sys",
		"from pathlib import Path",
		"args = sys.argv[1:]",
		"run_dir = Path(args[args.index('--run-dir') + 1])",
		"(run_dir / 'publish_called.json').write_text(json.dumps(args), encoding='utf-8')",
		"",
	].join("\n");
	writeFileSync(join(packageDir, "scripts", "publish_memory.py"), body, "utf-8");
}

// resolveHarnessPython spawns <repoRoot>/sure/runtime/harness/bootstrap.py --json and takes the
// contract from stdout. The real one builds a locked runtime; here the local interpreter is the
// answer, which is what makes preStart and the non-memory gate scripts runnable in this fixture.
function writeBootstrapStub(repoRoot: string): void {
	const dir = join(repoRoot, "sure", "runtime", "harness");
	mkdirSync(dir, { recursive: true });
	const body = [
		"import json, sys",
		"print(json.dumps({",
		'    "runtime_id": "sure-harness-fixture",',
		'    "python_executable": sys.executable,',
		'    "python_abi": "fixture",',
		'    "python_version": ".".join(str(p) for p in sys.version_info[:2]),',
		`    "lock_sha256": "${"a".repeat(64)}",`,
		'    "harness_version": "fixture",',
		'    "manifest_path": __file__,',
		'    "runtime_root": str(sys.prefix),',
		"}))",
		"",
	].join("\n");
	writeFileSync(join(dir, "bootstrap.py"), body, "utf-8");
}

function fakeRepo(name: string): Fixture {
	const base = join(TMP, name);
	rmSync(base, { recursive: true, force: true });
	const repoRoot = join(base, "repo");
	const packageDir = join(repoRoot, "sure", "skills", "sure_feed");
	const runDir = join(repoRoot, ".sure", "runs", name);
	mkdirSync(join(packageDir, "scripts"), { recursive: true });
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	cpSync(join(REAL_PACKAGE_DIR, "schemas"), join(packageDir, "schemas"), { recursive: true });
	cpSync(
		join(REAL_PACKAGE_DIR, "scripts", "check_rank_select.py"),
		join(packageDir, "scripts", "check_rank_select.py"),
	);
	writeWrapper(packageDir, "build_run_digest.py", "digest");
	writeWrapper(packageDir, "check_memory_extraction.py", "proposals");
	writePublishStub(packageDir);
	writeBootstrapStub(repoRoot);
	return {
		ctx: {
			point: "post_tool_result",
			run: { id: name, command: "/sure_feed", status: "running" } as never,
			skill: { name: "sure_feed", command: "/sure_feed" } as never,
			cwd: repoRoot,
			packageDir,
			runDir,
			args: "",
		},
		runDir,
		repoRoot,
		packageDir,
		memoryRoot: join(repoRoot, "sure", "memory"),
	};
}

function seedCheckpoint(runDir: string, data: CheckpointData): void {
	writeFileSync(join(runDir, "state.json"), JSON.stringify({ checkpoint: { data } }, null, 2), "utf-8");
}

function persist(runDir: string, result: unknown): CheckpointData | undefined {
	const data = statePatch(result as { state_patch?: unknown }).checkpoint?.data;
	if (data) {
		seedCheckpoint(runDir, data);
	}
	return data;
}

function writeArtifact(runDir: string, produces: string, value: unknown): void {
	writeFileSync(join(runDir, "artifacts", produces), JSON.stringify(value, null, 2), "utf-8");
}

function seedEvents(runDir: string, count: number, args = ""): void {
	const lines: string[] = [];
	for (let i = 0; i < count; i++) {
		const timestamp = `2026-08-31T00:00:${String(i).padStart(2, "0")}Z`;
		if (i === 0) {
			lines.push(JSON.stringify({ type: "created", timestamp, data: { runId: basename(runDir), args } }));
		} else if (i % 2 === 1) {
			lines.push(
				JSON.stringify({
					type: "tool_call",
					timestamp,
					data: { toolName: "bash", toolCallId: `c${i}`, input: { command: "ls artifacts" } },
				}),
			);
		} else {
			lines.push(
				JSON.stringify({
					type: "tool_result",
					timestamp,
					data: { toolName: "bash", toolCallId: `c${i - 1}`, isError: false },
				}),
			);
		}
	}
	writeFileSync(join(runDir, "events.jsonl"), `${lines.join("\n")}\n`, "utf-8");
}

function appendEvent(runDir: string, event: Record<string, unknown>): void {
	appendFileSync(join(runDir, "events.jsonl"), `${JSON.stringify(event)}\n`, "utf-8");
}

function seedIndex(memoryRoot: string, entries: unknown[]): void {
	mkdirSync(memoryRoot, { recursive: true });
	writeFileSync(
		join(memoryRoot, "index.json"),
		JSON.stringify(
			{
				schema: "sure.memory.index.v1",
				built_at: "2026-08-31T00:00:00Z",
				sources_sha256: "0".repeat(64),
				entries,
				omitted_provisional: 0,
			},
			null,
			2,
		),
		"utf-8",
	);
}

function seedUsage(memoryRoot: string, runId: string, row: Record<string, unknown>): void {
	mkdirSync(join(memoryRoot, "usage"), { recursive: true });
	appendFileSync(join(memoryRoot, "usage", `${runId}.jsonl`), `${JSON.stringify(row)}\n`, "utf-8");
}

function readUsage(memoryRoot: string, runId: string): Array<Record<string, unknown>> {
	const path = join(memoryRoot, "usage", `${runId}.jsonl`);
	if (!existsSync(path)) {
		return [];
	}
	return readFileSync(path, "utf-8")
		.split("\n")
		.filter((line) => line.trim())
		.map((line) => JSON.parse(line) as Record<string, unknown>);
}

function sha256File(path: string): string {
	return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function rankSelectResult(): Record<string, unknown> {
	return {
		selected: [
			{
				model_id: "demo/asr-tiny",
				repo: "https://huggingface.co/demo/asr-tiny",
				weights_source: "huggingface",
				task_type: "asr",
				score: 1.25,
			},
		],
	};
}

function feedReport(): Record<string, unknown> {
	return { selected: { model_id: "demo/asr-tiny" } };
}

function validDeclaration(): Record<string, unknown> {
	return {
		schema: "sure.memory.extraction.v2",
		no_new_lessons: true,
		no_lessons_reason: "Fixture run: nothing new to record.",
		covered_by: [],
		candidates: [],
		infra_noise: false,
		infra_evidence: [],
	};
}

// no_new_lessons together with a declared candidate violates gate rule 10 (declaration consistency).
function inconsistentDeclaration(): Record<string, unknown> {
	return { ...validDeclaration(), candidates: ["01-demo"] };
}

function writeCandidate(runDir: string, id: string, body: string): void {
	const dir = join(runDir, "artifacts", "candidates", id);
	mkdirSync(dir, { recursive: true });
	writeFileSync(join(dir, "proposal.md"), body, "utf-8");
}

// Build artifacts/run_digest.json with the real builder and return its sha256, so a seeded
// checkpoint carries the digest the extraction gate checks each candidate against.
function seedDigest(fixture: Fixture): string {
	const r = spawnSync(
		pythonBin(),
		[
			join(fixture.packageDir, "scripts", "build_run_digest.py"),
			"--run-dir",
			fixture.runDir,
			"--repo-root",
			fixture.repoRoot,
			"--cutoff",
			"0",
			"--mark-passed",
			"rank_and_select",
		],
		{ encoding: "utf-8" },
	);
	expect(r.status, r.stderr || r.stdout).toBe(0);
	return sha256File(join(fixture.runDir, "artifacts", "run_digest.json"));
}

function seedExtractLessons(fixture: Fixture): string {
	const sha = seedDigest(fixture);
	seedCheckpoint(fixture.runDir, {
		currentUnit: "extract_lessons",
		completedUnits: [...UNITS_BEFORE_RANK, "rank_and_select"],
		retries: {},
		failedArtifactDigests: {},
		memory: { digestCutoff: 0, digestSha256: sha, digestPassed: "rank_and_select" },
	});
	return sha;
}

// A linear unit blocked in-process (missing required field): no python needed, so injection and
// settlement can be exercised anywhere.
function seedCollectMetadataBlock(fixture: Fixture): void {
	seedIndex(fixture.memoryRoot, [INDEX_ENTRY]);
	seedEvents(fixture.runDir, 2);
	seedCheckpoint(fixture.runDir, {
		currentUnit: "collect_metadata",
		completedUnits: ["scan_modelscope", "match_task"],
		retries: {},
		failedArtifactDigests: {},
	});
	writeArtifact(fixture.runDir, "metadata_result.json", { model: [] });
}

describe("sure_feed memory wiring: the extraction unit", () => {
	it("runs the memory gate on extract_lessons and advances to emit_handoff_manifest", () => {
		const fixture = fakeRepo("gate-pass");
		const sha = seedExtractLessons(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", validDeclaration());
		const result = postToolResult(fixture.ctx);
		expect(result.ok, result.repair).toBe(true);
		const data = statePatch(result).checkpoint?.data;
		expect(data?.currentUnit).toBe("emit_handoff_manifest");
		expect(data?.completedUnits).toContain("extract_lessons");
		expect(data?.memory?.digestSha256).toBe(sha);
	});

	it("re-runs the gate and consumes a retry when only a candidate file changes (D1 file-level digest)", () => {
		// The old digest hashed the parsed declaration only, so an agent that repaired a candidate
		// without touching the declaration hit the unchanged-artifact guard for ever and the unit
		// could never reach its cap.
		const fixture = fakeRepo("gate-inputs-rerun");
		seedExtractLessons(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", inconsistentDeclaration());
		writeCandidate(fixture.runDir, "01-demo", "# Demo\n");
		const first = postToolResult(fixture.ctx);
		expect(first.ok).toBe(false);
		expect(persist(fixture.runDir, first)?.retries.extract_lessons).toBe(1);

		// Nothing touched: the guard holds and the ledger does not move.
		const unchanged = postToolResult(fixture.ctx);
		expect(unchanged.ok).toBe(true);
		expect(statePatch(unchanged).message).toContain("unchanged artifact content");
		expect(persist(fixture.runDir, unchanged)?.retries.extract_lessons).toBe(1);

		// Only the candidate body changed; the declaration is byte-identical. The digest has to
		// move anyway, or the unit sits behind the guard for ever and never reaches its cap.
		writeCandidate(fixture.runDir, "01-demo", "# Demo\n\nedited body\n");
		const second = postToolResult(fixture.ctx);
		expect(statePatch(second).message).toContain('Extraction gate "extract_lessons" exhausted 2 blocked attempts');
	});

	it("repairs a malformed extraction_declaration.json instead of stalling on it", () => {
		// readArtifact returns undefined for "absent" and for "present but not JSON" alike, so
		// validateProduces reported missing and postToolResult answered ok with no repair, no
		// diagnostic and no retry: the gate never ran and the unit hung until the run ended.
		const fixture = fakeRepo("declaration-not-json");
		fixture.ctx.args = "max_retries=3";
		seedExtractLessons(fixture);
		writeFileSync(
			join(fixture.runDir, "artifacts", "extraction_declaration.json"),
			'{\n  "schema": "sure.memory.extraction.v2",\n  "no_new_lessons": true,\n',
			"utf-8",
		);
		const first = postToolResult(fixture.ctx);
		expect(first.ok).toBe(false);
		expect(first.repair).toContain("extraction_declaration.json is present but is not valid JSON");
		expect(first.repair).not.toContain(fixture.runDir.split("\\").join("/"));
		expect(persist(fixture.runDir, first)?.retries.extract_lessons).toBe(1);

		writeArtifact(fixture.runDir, "extraction_declaration.json", validDeclaration());
		const fixed = postToolResult(fixture.ctx);
		expect(fixed.ok, fixed.repair).toBe(true);
		expect(statePatch(fixed).checkpoint?.data.currentUnit).toBe("emit_handoff_manifest");
	});

	it("auto-advances with extractionStatus=failed at the extraction cap, ignoring max_retries=", () => {
		// The deadlock exemption: a by-product unit must never end a feed run, and the user's
		// max_retries= governs the feed's own gates, not the extraction cap in config.json.
		const fixture = fakeRepo("gate-exhausted");
		fixture.ctx.args = "max_retries=9";
		seedExtractLessons(fixture);
		let landed: CheckpointData | undefined;
		for (let attempt = 1; attempt <= CONFIG.extraction_gate_max_failures; attempt++) {
			writeArtifact(fixture.runDir, "extraction_declaration.json", {
				...inconsistentDeclaration(),
				no_lessons_reason: `attempt ${attempt}`,
			});
			landed = persist(fixture.runDir, postToolResult(fixture.ctx));
		}
		expect(landed?.currentUnit).toBe("emit_handoff_manifest");
		expect(landed?.completedUnits).toContain("extract_lessons");
		expect(landed?.memory?.extractionStatus).toBe("failed");
	});

	it("advances with extractionStatus=failed when the gate script never judged the declaration", () => {
		// A gate that dies before it can write a verdict hands back a traceback, not a repair; the
		// unit must not block on it, and nothing may be published for a run nothing gated.
		const fixture = fakeRepo("gate-cannot-run");
		seedExtractLessons(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", validDeclaration());
		writeFileSync(
			join(fixture.packageDir, "scripts", "check_memory_extraction.py"),
			'raise RuntimeError("the memory package is not importable here")\n',
			"utf-8",
		);
		const result = postToolResult(fixture.ctx);
		expect(result.ok, result.repair).toBe(true);
		const patch = statePatch(result);
		expect(patch.checkpoint?.data.currentUnit).toBe("emit_handoff_manifest");
		expect(patch.checkpoint?.data.completedUnits).toContain("extract_lessons");
		expect(patch.checkpoint?.data.memory?.extractionStatus).toBe("failed");
		expect(patch.diagnostics?.map((diagnostic) => diagnostic.message)).toContainEqual(
			expect.stringContaining("memory extraction gate could not run"),
		);
	});
});

describe("sure_feed memory wiring: injection, settlement and the digest", () => {
	it("pre_start writes memory_context.json with the matching fact and no output_dir", () => {
		const fixture = fakeRepo("pre-start-memory");
		seedIndex(fixture.memoryRoot, [FACT_ENTRY]);
		fixture.ctx.point = "pre_start";
		fixture.ctx.args = "url=https://hf-mirror.com/demo/asr-tiny output_dir=/tmp/should-not-leak";
		const result = preStart(fixture.ctx);
		expect(result.ok, result.repair).toBe(true);
		const context = JSON.parse(readFileSync(join(fixture.runDir, "artifacts", "memory_context.json"), "utf-8")) as {
			skill: string;
			target_id: string;
			facts: Array<{ entry_id: string }>;
		};
		expect(context.skill).toBe("sure_feed");
		expect(context.target_id).toBe("https://hf-mirror.com/demo/asr-tiny");
		expect(context.facts.map((fact) => fact.entry_id)).toEqual([FACT_ENTRY.entry_id]);
		expect(readFileSync(join(fixture.runDir, "artifacts", "memory_context.json"), "utf-8")).not.toContain(
			"should-not-leak",
		);
		expect(readUsage(fixture.memoryRoot, "pre-start-memory")[0]).toMatchObject({
			kind: "pre_start",
			skill: "sure_feed",
		});
	});

	it("names the target from a bare positional URL, the invocation SKILL.md prefers", () => {
		// parseArgs turns a lone URL token into a flag ("<url>": "true"), so args.url was undefined
		// and the documented /sure_feed <url> form named no target at all.
		const fixture = fakeRepo("pre-start-positional-url");
		fixture.ctx.point = "pre_start";
		fixture.ctx.args = "https://modelscope.cn/models/demo/asr-tiny";
		const result = preStart(fixture.ctx);
		expect(result.ok, result.repair).toBe(true);
		const context = JSON.parse(readFileSync(join(fixture.runDir, "artifacts", "memory_context.json"), "utf-8")) as {
			target_id: string;
		};
		expect(context.target_id).toBe("https://modelscope.cn/models/demo/asr-tiny");
	});

	it("injects a matching entry into the blocked unit's repair and records the inject row", () => {
		const fixture = fakeRepo("inject-on-block");
		seedCollectMetadataBlock(fixture);
		const blocked = postToolResult(fixture.ctx);
		expect(blocked.ok).toBe(false);
		expect(blocked.repair).toContain(CONFIG.inject_header);
		expect(blocked.repair).toContain(ENTRY_TITLE);
		const patch = statePatch(blocked);
		// diagnostics keep the raw gate repair; only the top-level repair carries the Memory block.
		expect(patch.diagnostics?.[0]?.repair).not.toContain(CONFIG.inject_header);
		expect(patch.checkpoint?.data.memory?.injected?.collect_metadata).toEqual([ENTRY_ID]);
		expect(readUsage(fixture.memoryRoot, "inject-on-block")[0]).toMatchObject({
			kind: "inject",
			skill: "sure_feed",
			unit: "collect_metadata",
			attempt: 1,
			entries: [{ entry_id: ENTRY_ID, shared: false }],
		});
	});

	it("settles the injected entry as useful when the unit passes", () => {
		const fixture = fakeRepo("settle-on-pass");
		seedCollectMetadataBlock(fixture);
		persist(fixture.runDir, postToolResult(fixture.ctx));
		appendEvent(fixture.runDir, {
			type: "tool_call",
			timestamp: "2026-08-31T00:00:10Z",
			data: { toolName: "read", toolCallId: "c9", input: { path: `/checkout/${ENTRY_PATH}` } },
		});
		writeArtifact(fixture.runDir, "metadata_result.json", { models: [{ model_id: "demo/asr-tiny" }] });
		const passed = postToolResult(fixture.ctx);
		expect(passed.ok, passed.repair).toBe(true);
		expect(statePatch(passed).checkpoint?.data.currentUnit).toBe("convert_to_oref");
		const settle = readUsage(fixture.memoryRoot, "settle-on-pass").filter((row) => row.kind === "settle");
		expect(settle).toHaveLength(1);
		expect(settle[0]).toMatchObject({
			unit: "collect_metadata",
			entry_id: ENTRY_ID,
			outcome: "useful_activated",
		});
	});

	it("builds the run digest when rank_and_select passes and lands on extract_lessons", () => {
		const fixture = fakeRepo("enter-extract-lessons");
		seedEvents(fixture.runDir, 3);
		seedCheckpoint(fixture.runDir, {
			currentUnit: "rank_and_select",
			completedUnits: UNITS_BEFORE_RANK,
			retries: {},
			failedArtifactDigests: {},
		});
		writeArtifact(fixture.runDir, "rank_select_result.json", rankSelectResult());
		const entered = postToolResult(fixture.ctx);
		expect(entered.ok, entered.repair).toBe(true);
		const data = statePatch(entered).checkpoint?.data;
		expect(data?.currentUnit).toBe("extract_lessons");
		const digestPath = join(fixture.runDir, "artifacts", "run_digest.json");
		expect(existsSync(digestPath)).toBe(true);
		expect(data?.memory).toMatchObject({
			digestCutoff: 3,
			digestSha256: sha256File(digestPath),
			digestPassed: "rank_and_select",
		});
		const digest = JSON.parse(readFileSync(digestPath, "utf-8")) as {
			run: { skill: string; target: { id: string } };
			units: Array<{ id: string; outcome: string }>;
		};
		expect(digest.run.skill).toBe("sure_feed");
		expect(digest.units.find((unit) => unit.id === "rank_and_select")?.outcome).toBe("passed");
	});

	it("masks the host path in handoff_root= and the endpoint URL in hf_endpoint= (D2)", () => {
		const fixture = fakeRepo("digest-masks-args");
		seedEvents(fixture.runDir, 3, "handoff_root=/srv/handoffs hf_endpoint=https://mirror.internal/hf query=asr");
		writeArtifact(fixture.runDir, "feed_report.json", feedReport());
		seedCheckpoint(fixture.runDir, {
			currentUnit: "rank_and_select",
			completedUnits: UNITS_BEFORE_RANK,
			retries: {},
			failedArtifactDigests: {},
		});
		writeArtifact(fixture.runDir, "rank_select_result.json", rankSelectResult());
		const entered = postToolResult(fixture.ctx);
		expect(entered.ok, entered.repair).toBe(true);
		const digest = JSON.parse(readFileSync(join(fixture.runDir, "artifacts", "run_digest.json"), "utf-8")) as {
			run: { args: string; target: { id: string } };
		};
		expect(digest.run.args).toContain("handoff_root=<path>");
		expect(digest.run.args).toContain("hf_endpoint=<url>");
		expect(digest.run.args).toContain("query=asr");
		expect(digest.run.target.id).toBe("demo/asr-tiny");
	});

	it("consumes a retry for every different corrupt produces file, on a unit without gateInputs", () => {
		// readArtifact returns undefined for "present but not JSON", and the artifact-object digest
		// hashed that undefined: every corrupt rewrite produced the same sha256("null"), so the
		// unchanged-artifact guard held from the second attempt on and the unit never reached its cap.
		const fixture = fakeRepo("corrupt-produces-digest");
		seedCheckpoint(fixture.runDir, {
			currentUnit: "collect_metadata",
			completedUnits: ["scan_modelscope", "match_task"],
			retries: {},
			failedArtifactDigests: {},
		});
		const produces = join(fixture.runDir, "artifacts", "metadata_result.json");
		writeFileSync(produces, '{"models": [\n', "utf-8");
		const first = postToolResult(fixture.ctx);
		expect(first.ok).toBe(false);
		expect(persist(fixture.runDir, first)?.retries.collect_metadata).toBe(1);

		writeFileSync(produces, '{"models": [{"model_id": "demo/asr-tiny"\n', "utf-8");
		const second = postToolResult(fixture.ctx);
		expect(second.ok).toBe(false);
		expect(persist(fixture.runDir, second)?.retries.collect_metadata).toBe(2);

		// Byte-identical rewrite: here the guard is right to hold.
		writeFileSync(produces, '{"models": [{"model_id": "demo/asr-tiny"\n', "utf-8");
		const unchanged = postToolResult(fixture.ctx);
		expect(unchanged.ok).toBe(true);
		expect(statePatch(unchanged).message).toContain("unchanged artifact content");
		expect(persist(fixture.runDir, unchanged)?.retries.collect_metadata).toBe(2);
	});
});

describe("sure_feed memory wiring: finish and error hooks", () => {
	function seedTerminal(fixture: Fixture): void {
		const runtimeRoot = join(fixture.runDir, "test-harness-runtime");
		const python = join(runtimeRoot, "bin", "python");
		const manifestPath = join(runtimeRoot, "runtime-manifest.json");
		mkdirSync(join(runtimeRoot, "bin"), { recursive: true });
		writeFileSync(python, "#!/bin/sh\n", "utf-8");
		writeFileSync(
			manifestPath,
			JSON.stringify({
				schema: "sure.harness.runtime.manifest.v1",
				runtime_id: "sure-harness-fixture",
				runtime_type: "harness_python",
				lock_sha256: "a".repeat(64),
			}),
			"utf-8",
		);
		writeSkillRuntimeBinding({
			runDir: fixture.runDir,
			skill: "sure_feed",
			harnessRuntime: {
				runtime_id: "sure-harness-fixture",
				python_executable: python,
				python_abi: "fixture",
				python_version: "3.11",
				lock_sha256: "a".repeat(64),
				harness_version: "fixture",
				manifest_path: manifestPath,
				runtime_root: runtimeRoot,
			},
			harnessRole: "test",
			modelRuntimeReason: "feed performs no inference",
			evaluationRuntime: { reason: "feed performs no evaluation" },
		});
		writeArtifact(fixture.runDir, "handoff_manifest.json", {
			manifest_path: "sure/handoffs/demo__asr-tiny/artifacts/handoff_manifest.json",
			models: [{ model_id: "demo/asr-tiny", repo: "https://huggingface.co/demo/asr-tiny" }],
		});
	}

	it("pre_finish asks a failed run for the extraction declaration and accepts it once written", () => {
		const fixture = fakeRepo("prefinish-extraction");
		seedEvents(fixture.runDir, 3);
		seedTerminal(fixture);
		seedCheckpoint(fixture.runDir, {
			currentUnit: "rank_and_select",
			completedUnits: UNITS_BEFORE_RANK,
			retries: {},
			failedArtifactDigests: {},
		});
		fixture.ctx.point = "pre_finish";
		fixture.ctx.event = { finish: { status: "failed" } } as never;

		const blocked = preFinish(fixture.ctx);
		expect(blocked.ok).toBe(false);
		expect(blocked.repair).toContain("EXTRACTION.md");
		expect(blocked.repair).toContain("extraction_declaration.json");
		expect(persist(fixture.runDir, blocked)?.memory?.finishAttempts).toBe(1);

		writeArtifact(fixture.runDir, "extraction_declaration.json", validDeclaration());
		const accepted = preFinish(fixture.ctx);
		expect(accepted.ok, accepted.repair).toBe(true);
		expect(statePatch(accepted).checkpoint?.data.memory?.extractionStatus).toBeUndefined();
	});

	it("post_finish spawns scripts/publish_memory.py with --run-dir and --repo-root", () => {
		const fixture = fakeRepo("postfinish-publish");
		seedEvents(fixture.runDir, 3);
		const sha = seedDigest(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", validDeclaration());
		seedCheckpoint(fixture.runDir, {
			currentUnit: "emit_handoff_manifest",
			completedUnits: [...UNITS_BEFORE_RANK, "rank_and_select", "extract_lessons", "emit_handoff_manifest"],
			retries: {},
			failedArtifactDigests: {},
			memory: { digestCutoff: 0, digestSha256: sha, digestPassed: "rank_and_select" },
		});
		fixture.ctx.point = "post_finish";
		fixture.ctx.run = { id: "postfinish-publish", command: "/sure_feed", status: "success" } as never;
		expect(postFinish(fixture.ctx).ok).toBe(true);
		const called = JSON.parse(readFileSync(join(fixture.runDir, "publish_called.json"), "utf-8")) as string[];
		expect(resolve(called[called.indexOf("--run-dir") + 1])).toBe(resolve(fixture.runDir));
		expect(resolve(called[called.indexOf("--repo-root") + 1])).toBe(resolve(fixture.repoRoot));
	});

	it("on_error writes artifacts/run_digest.json and never publishes", () => {
		const fixture = fakeRepo("onerror-digest");
		seedEvents(fixture.runDir, 4);
		seedCheckpoint(fixture.runDir, {
			currentUnit: "collect_metadata",
			completedUnits: ["scan_modelscope", "match_task"],
			retries: { collect_metadata: 1 },
			failedArtifactDigests: {},
		});
		fixture.ctx.point = "on_error";
		fixture.ctx.event = { reason: "session_shutdown" } as never;
		expect(onError(fixture.ctx).ok).toBe(true);
		const digest = JSON.parse(readFileSync(join(fixture.runDir, "artifacts", "run_digest.json"), "utf-8")) as {
			schema: string;
			run: { cutoff: number };
		};
		expect(digest.schema).toBe("sure.memory.run_digest.v1");
		expect(digest.run.cutoff).toBe(4);
		expect(existsSync(join(fixture.runDir, "publish_called.json"))).toBe(false);
	});

	it("on_error settles the entries still pending on the unit the run died on as disputed", () => {
		const fixture = fakeRepo("onerror-disputed");
		seedEvents(fixture.runDir, 4);
		seedIndex(fixture.memoryRoot, [INDEX_ENTRY]);
		seedUsage(fixture.memoryRoot, "onerror-disputed", {
			kind: "inject",
			run_id: "onerror-disputed",
			skill: "sure_feed",
			unit: "collect_metadata",
			attempt: 1,
			events_cutoff: 2,
			entries: [{ entry_id: ENTRY_ID, shared: false }],
			at: "2026-08-31T00:00:05Z",
		});
		seedCheckpoint(fixture.runDir, {
			currentUnit: "collect_metadata",
			completedUnits: ["scan_modelscope", "match_task"],
			retries: { collect_metadata: 3 },
			failedArtifactDigests: {},
			memory: {
				injected: { collect_metadata: [ENTRY_ID] },
				pendingDisputed: { collect_metadata: [ENTRY_ID] },
			},
		});
		fixture.ctx.point = "on_error";
		fixture.ctx.event = { reason: "session_shutdown" } as never;
		expect(onError(fixture.ctx).ok).toBe(true);
		const settle = readUsage(fixture.memoryRoot, "onerror-disputed").filter((row) => row.kind === "settle");
		expect(settle).toHaveLength(1);
		expect(settle[0]).toMatchObject({ unit: "collect_metadata", entry_id: ENTRY_ID, outcome: "disputed" });
	});
});
