import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
	appendFileSync,
	cpSync,
	existsSync,
	mkdirSync,
	readdirSync,
	readFileSync,
	rmSync,
	statSync,
	symlinkSync,
	writeFileSync,
} from "node:fs";
import { basename, join, resolve } from "node:path";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { resolveHarnessPython } from "../../../../sure/runtime/harness/resolve.ts";
// Imported as a namespace (not a named import) so vi.spyOn can override just
// memoryConfigOrUndefined for one test, the same way sure-extension.test.ts spies on
// SettingsManager.create / process.stdout.write / process.exit: spy, mockRestore in a finally.
import * as memoryHooksModule from "../../../../sure/runtime/memory/hooks.ts";
import { loadMemoryConfig } from "../../../../sure/runtime/memory/match.ts";
import type { CheckpointData } from "../../../../sure/skills/sure_onboard/hooks/checkpoints.ts";
import {
	onError,
	postFinish,
	postToolResult,
	preFinish,
	preStart,
} from "../../../../sure/skills/sure_onboard/hooks/index.ts";
// The real harness normalizer: extension.ts applyStatePatch runs every state_patch through it and
// drops the whole patch when it comes back not ok, so a test that writes checkpoint.data straight
// out of a state_patch is testing a stricter harness than the one that ships.
import { normalizeSureDisplayStatePatch } from "../../src/core/sure/state.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// Wiring tests for the memory system inside the sure_onboard hooks (plan Task 13).
// Two fixture styles:
//   - fakeRepo(): a throwaway repo skeleton whose sure/skills/sure_onboard is the packageDir the
//     hooks see, so memoryRootFor(packageDir) lands inside the temp tree (the real sure/memory/ is
//     never touched) and its scripts/ wrappers call the real shared library. Runs anywhere with a
//     python 3 on PATH: the memory scripts are stdlib only.
//   - realRun(): the shipped package; needed where a non-memory gate (check_verdict.py) must pass,
//     which goes through runBackend -> resolveHarnessPython (Linux with a materialized harness).

const REPO_ROOT = resolve(__dirname, "../../../..");
const REAL_PACKAGE_DIR = join(REPO_ROOT, "sure", "skills", "sure_onboard");
const TMP = resolve(__dirname, "tmp-ob-memory");
const CONFIG = loadMemoryConfig();
const HARNESS = resolveHarnessPython(REAL_PACKAGE_DIR);

const UNITS_BEFORE_VERDICT = [
	"load_model_input",
	"context_selection",
	"discover",
	"classify",
	"plan",
	"build_plan",
	"validate_spec",
	"prepare_fixture",
	"build_env",
	"fetch_weights",
	"validate_env_compat",
	"generate_wrapper",
	"validate_import",
	"validate_load",
	"validate_infer",
	"validate_contract",
	"package_container",
	"save_artifacts",
	"package_gate",
	"write_runtime_inventory",
];

const ENTRY_ID = "sure_onboard/task-type-enum";
const ENTRY_PATH = "sure/skills/sure_onboard/references/memory/bad_cases/task-type-enum.md";
const ENTRY_TITLE = "Task type must match the onboard enum";
// One confirmed bad_case whose trigger is a substring of the validate.ts enum repair for context_selection.
// hook_trigger is the subset match.ts uses for hook injection (plan 1.7); here it equals trigger.
const INDEX_ENTRY = {
	entry_id: ENTRY_ID,
	type: "bad_case",
	status: "confirmed",
	target_skill: "sure_onboard",
	applies_to: ["sure_onboard"],
	component: "context_selection",
	cause: "config_not_set",
	trigger: ['field "task_type" in context_selection.json'],
	hook_trigger: ['field "task_type" in context_selection.json'],
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
	created: "2026-08-18",
	checked_at: null,
	stale: false,
	superseded_by: null,
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
	// resolveMemoryPython() honours HARNESS_PYTHON_BIN first (plan 1.13). On the cluster the harness
	// resolution above already exported it; on a dev box we point it at the local python.
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

function baseCtx(name: string, cwd: string, packageDir: string, runDir: string): SureHookContext {
	return {
		point: "post_tool_result",
		run: { id: name, command: "/sure_onboard", status: "running" } as never,
		skill: { name: "sure_onboard", command: "/sure_onboard" } as never,
		cwd,
		packageDir,
		runDir,
		args: "",
	};
}

function writeWrapper(packageDir: string, script: string, module: string): void {
	// Same shape as the shipped wrapper (plan 1.11) except the sys.path entry is absolute,
	// because this fake package does not live inside the real repo.
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

function fakeRepo(name: string): Fixture {
	const base = join(TMP, name);
	rmSync(base, { recursive: true, force: true });
	const repoRoot = join(base, "repo");
	const packageDir = join(repoRoot, "sure", "skills", "sure_onboard");
	const runDir = join(repoRoot, ".sure", "runs", name);
	mkdirSync(join(packageDir, "scripts"), { recursive: true });
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	cpSync(join(REAL_PACKAGE_DIR, "schemas"), join(packageDir, "schemas"), { recursive: true });
	writeWrapper(packageDir, "build_run_digest.py", "digest");
	writeWrapper(packageDir, "check_memory_extraction.py", "proposals");
	writePublishStub(packageDir);
	return {
		ctx: baseCtx(name, repoRoot, packageDir, runDir),
		runDir,
		repoRoot,
		packageDir,
		memoryRoot: join(repoRoot, "sure", "memory"),
	};
}

// preStart reads the site policy from ctx.cwd when package=none (sure_onboard hooks, 7e035c8);
// the fixture cwd has none, so write the same minimal local-Python policy the state-machine suite uses.
function writeLocalPythonSitePolicy(cwd: string): void {
	const path = join(cwd, "config", "site.local.yaml");
	mkdirSync(resolve(path, ".."), { recursive: true });
	writeFileSync(
		path,
		`${[
			"schema: sure.site.policy.v1",
			"site_id: test",
			"policy_version: 1",
			"storage:",
			`  approved_models_roots: [${join(cwd, "sure", "models")}]`,
			`  approved_results_roots: [${join(cwd, "sure", "results")}]`,
			`  forbidden_output_roots: [${join(cwd, "forbidden")}]`,
			`  runtime_root: ${join(cwd, ".runtime")}`,
			"datasets:",
			`  allowed_source_roots: [${join(cwd, "datasets")}]`,
			"execution:",
			"  surfaces: [local]",
			"  local_runtimes: [python]",
		].join("\n")}\n`,
		"utf-8",
	);
}

function realRun(name: string): Fixture {
	const base = join(TMP, "real", name);
	rmSync(base, { recursive: true, force: true });
	const cwd = join(base, "cwd");
	const runDir = join(cwd, ".sure", "runs", name);
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	return {
		ctx: baseCtx(name, cwd, REAL_PACKAGE_DIR, runDir),
		runDir,
		repoRoot: REPO_ROOT,
		packageDir: REAL_PACKAGE_DIR,
		memoryRoot: join(REPO_ROOT, "sure", "memory"),
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

// What the harness really does with a state_patch (extension.ts applyStatePatch): normalize it,
// and on a rejection write nothing at all and keep going. Returns the normalizer's complaint so a
// test can assert the patch was accepted rather than assume it.
function persistThroughHarness(runDir: string, result: unknown): string | undefined {
	const patch = (result as { state_patch?: unknown }).state_patch;
	if (patch === undefined) {
		return undefined;
	}
	const normalized = normalizeSureDisplayStatePatch(patch);
	if (!normalized.ok || !normalized.state) {
		return normalized.message ?? "Invalid Sure state patch.";
	}
	const data = normalized.state.checkpoint?.data as CheckpointData | undefined;
	if (data) {
		seedCheckpoint(runDir, data);
	}
	return undefined;
}

function writeArtifact(runDir: string, produces: string, value: unknown): void {
	writeFileSync(join(runDir, "artifacts", produces), JSON.stringify(value, null, 2), "utf-8");
}

function seedEvents(runDir: string, count: number): void {
	// Realistic events.jsonl lines (tests-tooling report section 7): created, then tool_call/tool_result pairs.
	const lines: string[] = [];
	for (let i = 0; i < count; i++) {
		const timestamp = `2026-08-18T00:00:${String(i).padStart(2, "0")}Z`;
		if (i === 0) {
			lines.push(JSON.stringify({ type: "created", timestamp, data: { runId: basename(runDir), args: "" } }));
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
	const index = {
		schema: "sure.memory.index.v1",
		built_at: "2026-08-18T00:00:00Z",
		sources_sha256: "0".repeat(64),
		entries,
		omitted_provisional: 0,
	};
	writeFileSync(join(memoryRoot, "index.json"), JSON.stringify(index, null, 2), "utf-8");
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

// Build artifacts/run_digest.json with the real builder (as the hook does when entering
// extract_lessons) and return its sha256 so a seeded checkpoint carries the matching memory.
function seedDigest(fixture: Fixture): string {
	const script = join(fixture.packageDir, "scripts", "build_run_digest.py");
	const r = spawnSync(
		pythonBin(),
		[
			script,
			"--run-dir",
			fixture.runDir,
			"--repo-root",
			fixture.repoRoot,
			"--cutoff",
			"0",
			"--mark-passed",
			"verdict",
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
		completedUnits: [...UNITS_BEFORE_VERDICT, "verdict"],
		retries: {},
		memory: { digestCutoff: 0, digestSha256: sha, digestPassed: "verdict" },
	});
	return sha;
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

// A candidate publish.py writes without complaint but the extraction gate refuses: its trigger
// never appears in this run's repairs or log tails (rule 4) and source.digest_sha256 names a
// digest that is not the one on disk (rule 9).
const UNGATED_CANDIDATE = "01-no-kernel-image";

function writeUngatedCandidate(fixture: Fixture): void {
	const dir = join(fixture.runDir, "artifacts", "candidates", UNGATED_CANDIDATE);
	mkdirSync(dir, { recursive: true });
	writeFileSync(
		join(dir, "proposal.json"),
		JSON.stringify(
			{
				schema: "sure.memory.proposal.v2",
				type: "bad_case",
				op: "add",
				target_skill: "sure_onboard",
				target_entry: null,
				applies_to: ["sure_onboard"],
				cell: { component: "build_env", cause: "cuda_version_mismatch" },
				trigger: ["no kernel image is available"],
				causal: true,
				evidence: ["artifacts/build_env.log:2"],
				claims: [{ kind: "unit_result", unit: "build_env", attempt: 1, status: "passed" }],
				source: {
					run_id: basename(fixture.runDir),
					skill: "sure_onboard",
					target: "demo__model",
					digest_sha256: "0".repeat(64),
				},
				similar: null,
				scope: null,
				checked_at: null,
			},
			null,
			2,
		),
		"utf-8",
	);
	writeFileSync(
		join(dir, "proposal.md"),
		[
			"# CUDA arch mismatch: no kernel image",
			"",
			"## Trigger",
			"`no kernel image is available` right after `pip install torch` in build_env.",
			"",
			"## Affected Step",
			"sure_onboard / build_env",
			"",
			"## Minimum Evidence",
			"artifacts/build_env.log:2",
			"",
			"## Known Mitigation",
			"Install the cu121 torch wheel.",
			"",
			"## Verification",
			'python -c "import torch; print(torch.cuda.is_available())"',
			"",
		].join("\n"),
		"utf-8",
	);
	writeFileSync(
		join(fixture.runDir, "artifacts", "build_env.log"),
		"line one\nno kernel image is available\n",
		"utf-8",
	);
	writeArtifact(fixture.runDir, "extraction_declaration.json", {
		schema: "sure.memory.extraction.v2",
		no_new_lessons: false,
		no_lessons_reason: null,
		covered_by: [],
		candidates: [UNGATED_CANDIDATE],
		infra_noise: false,
		infra_evidence: [],
	});
}

/** "<target_skill>/<slug>" for every entry sitting in sure/memory/provisional/. */
function provisionalEntries(memoryRoot: string): string[] {
	const dir = join(memoryRoot, "provisional");
	if (!existsSync(dir)) {
		return [];
	}
	const out: string[] = [];
	for (const skill of readdirSync(dir)) {
		const skillDir = join(dir, skill);
		if (!statSync(skillDir).isDirectory()) {
			continue;
		}
		for (const slug of readdirSync(skillDir)) {
			out.push(`${skill}/${slug}`);
		}
	}
	return out.sort();
}

function writeCandidate(runDir: string, id: string, body: string): void {
	const dir = join(runDir, "artifacts", "candidates", id);
	mkdirSync(dir, { recursive: true });
	writeFileSync(join(dir, "proposal.md"), body, "utf-8");
}

function contextSelection(taskType: string): Record<string, unknown> {
	return {
		task_type: taskType,
		selected_references: { default: [], task_playbooks: [], environment_playbooks: [], contracts: [] },
	};
}

// A linear unit blocked in-process (enum violation): no python needed, so injection and settlement
// can be exercised on any machine.
function seedContextSelectionBlock(fixture: Fixture): void {
	seedIndex(fixture.memoryRoot, [INDEX_ENTRY]);
	seedEvents(fixture.runDir, 2);
	seedCheckpoint(fixture.runDir, {
		currentUnit: "context_selection",
		completedUnits: ["load_model_input"],
		retries: {},
	});
	writeArtifact(fixture.runDir, "context_selection.json", contextSelection("invalid"));
}

function seedNonSuccessTerminal(runDir: string): void {
	writeArtifact(runDir, "package_gate.json", {
		status: "blocked",
		bundle_ready: false,
		readiness: { bundle_ready: false, registry_ready: false },
	});
	writeArtifact(runDir, "deployment_ready.json", {
		schema: "sure.onboard.deployment_ready.v1",
		generated_at: "2026-08-18T00:00:00Z",
		status: "blocked",
		model_name: "demo__model",
		package_profile: "none",
		execution_policy: {
			container_only: false,
			nfs_models_read_only: true,
			host_python_fallback: false,
			approved_image_override: false,
		},
		required_artifact_sha256: {},
		bundle_identity_sha256: "0".repeat(64),
	});
}

describe("sure_onboard memory wiring: extraction gate", () => {
	it("passes a valid no_new_lessons declaration through the memory gate and keeps memory across advance", () => {
		const fixture = fakeRepo("gate-pass");
		const sha = seedExtractLessons(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", validDeclaration());
		const result = postToolResult(fixture.ctx);
		expect(result.ok, result.repair).toBe(true);
		const data = statePatch(result).checkpoint?.data;
		expect(data?.currentUnit).toBe("finalize_model_bundle");
		expect(data?.completedUnits).toContain("extract_lessons");
		expect(data?.memory?.digestSha256).toBe(sha);
	});

	it("re-runs the gate and consumes a retry when only a candidate file changes (gateInputs joint hash)", () => {
		const fixture = fakeRepo("gate-inputs-rerun");
		fixture.ctx.args = "max_retries=3"; // raise the extraction cap so the second block does not auto-advance
		const sha = seedExtractLessons(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", inconsistentDeclaration());
		writeCandidate(fixture.runDir, "01-demo", "# Demo\n");
		const first = postToolResult(fixture.ctx);
		expect(first.ok).toBe(false);
		const afterFirst = persist(fixture.runDir, first);
		expect(afterFirst?.currentUnit).toBe("extract_lessons");
		expect(afterFirst?.retries.extract_lessons).toBe(1);

		const unchanged = postToolResult({ ...fixture.ctx, event: { toolName: "read", isError: false } });
		expect(unchanged.ok).toBe(true);
		expect(statePatch(unchanged).message).toContain("unchanged artifact content");

		writeCandidate(fixture.runDir, "01-demo", "# Demo\n\nedited body\n");
		const second = postToolResult(fixture.ctx);
		expect(second.ok).toBe(false);
		const afterSecond = statePatch(second).checkpoint?.data;
		expect(afterSecond?.currentUnit).toBe("extract_lessons");
		expect(afterSecond?.retries.extract_lessons).toBe(2);
		expect(afterSecond?.memory?.digestSha256).toBe(sha);
	});

	it("repairs a malformed extraction_declaration.json instead of stalling on it", () => {
		// readArtifact returns undefined for "absent" and for "present but not JSON" alike, so
		// validateProduces reported missing and postToolResult answered ok with no repair, no
		// diagnostic and no retry: the gate never ran, its cap was never reached, and a success
		// finish only told the agent the state machine had not reached the terminal unit.
		// extract_lessons is the one gated unit whose produces the agent writes by hand.
		const fixture = fakeRepo("declaration-not-json");
		fixture.ctx.args = "max_retries=3";
		seedExtractLessons(fixture);
		const declaration = join(fixture.runDir, "artifacts", "extraction_declaration.json");
		writeFileSync(declaration, '{\n  "schema": "sure.memory.extraction.v2",\n  "no_new_lessons": true,\n', "utf-8");

		const first = postToolResult(fixture.ctx);
		expect(first.ok).toBe(false);
		expect(first.repair).toContain("extraction_declaration.json is present but is not valid JSON");
		expect(first.repair).not.toContain(fixture.runDir.split("\\").join("/"));
		const afterFirst = persist(fixture.runDir, first);
		expect(afterFirst?.currentUnit).toBe("extract_lessons");
		expect(afterFirst?.retries.extract_lessons).toBe(1);

		// Same broken bytes: the unchanged-artifact guard, no second retry consumed.
		const again = postToolResult({ ...fixture.ctx, event: { toolName: "read", isError: false } });
		expect(again.ok).toBe(true);
		expect(statePatch(again).message).toContain("unchanged artifact content");

		writeArtifact(fixture.runDir, "extraction_declaration.json", validDeclaration());
		const fixed = postToolResult(fixture.ctx);
		expect(fixed.ok, fixed.repair).toBe(true);
		expect(statePatch(fixed).checkpoint?.data.currentUnit).toBe("finalize_model_bundle");
	});

	it("survives a symlink the agent left under artifacts/memory_evidence", () => {
		// EXTRACTION.md tells the agent to put evidence under artifacts/memory_evidence/, and it
		// fills that tree with bash. A link pointing back at an ancestor used to be followed by
		// gateDigest's walk; the retry ledger must not move because of one.
		const fixture = fakeRepo("gate-inputs-symlink");
		fixture.ctx.args = "max_retries=3";
		seedExtractLessons(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", inconsistentDeclaration());
		writeCandidate(fixture.runDir, "01-demo", "# Demo\n");
		const first = postToolResult(fixture.ctx);
		expect(first.ok).toBe(false);
		expect(persist(fixture.runDir, first)?.retries.extract_lessons).toBe(1);

		const evidence = join(fixture.runDir, "artifacts", "memory_evidence");
		mkdirSync(evidence, { recursive: true });
		// "junction" is the link type Windows creates without elevation; readdirSync walks
		// through it exactly like a POSIX symlink to a directory.
		symlinkSync(join(fixture.runDir, "artifacts"), join(evidence, "loop"), "junction");
		const second = postToolResult({ ...fixture.ctx, event: { toolName: "read", isError: false } });
		expect(second.ok, second.repair).toBe(true);
		expect(statePatch(second).message).toContain("unchanged artifact content");
		expect(statePatch(second).checkpoint?.data.retries.extract_lessons).toBe(1);
	});

	it("advances with extractionStatus=failed when the gate script is not there to run", () => {
		// A gate that never ran hands back a traceback or a spawn error, not something an agent
		// can repair, and blocking on it stalls the unit: failOrRetry's unchanged-artifact guard
		// consumes no retry when the agent has nothing it can change, so the extraction cap is
		// never reached and the unit never auto-advances.
		const fixture = fakeRepo("gate-cannot-run");
		seedExtractLessons(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", validDeclaration());
		rmSync(join(fixture.packageDir, "scripts", "check_memory_extraction.py"));

		const result = postToolResult(fixture.ctx);
		expect(result.ok, result.repair).toBe(true);
		expect(result.repair).toBeUndefined();
		const patch = statePatch(result);
		expect(patch.checkpoint?.data.currentUnit).toBe("finalize_model_bundle");
		expect(patch.checkpoint?.data.completedUnits).toContain("extract_lessons");
		// Nothing was gated, so post_finish must publish nothing.
		expect(patch.checkpoint?.data.memory?.extractionStatus).toBe("failed");
		const warning = patch.diagnostics?.find((item) =>
			item.message.startsWith("memory extraction gate could not run"),
		);
		expect(warning?.severity).toBe("warning");
		expect(warning?.repair).toContain("check_memory_extraction.py");
	});

	it("advances with extractionStatus=failed when the gate crashes, without leaking the traceback", () => {
		// An incomplete bundle makes the wrapper die at import: python prints a traceback whose
		// frames carry absolute host paths, and that used to become the agent's repair verbatim
		// (top-level repair, diagnostics[0].repair and run.json.lastRepair, from where digest.py
		// hands it to the next run's prior_runs).
		const fixture = fakeRepo("gate-crashes");
		seedExtractLessons(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", validDeclaration());
		const hostPath = join(fixture.packageDir, "scripts", "check_memory_extraction.py").split("\\").join("/");
		writeFileSync(
			join(fixture.packageDir, "scripts", "check_memory_extraction.py"),
			[
				"import sys",
				"sys.stderr.write('Traceback (most recent call last):\\n')",
				`sys.stderr.write('  File "${hostPath}", line 5, in <module>\\n    from memory import proposals\\n')`,
				"sys.stderr.write(\"ModuleNotFoundError: No module named 'memory'\\n\")",
				"raise SystemExit(1)",
				"",
			].join("\n"),
			"utf-8",
		);

		const result = postToolResult(fixture.ctx);
		expect(result.ok, result.repair).toBe(true);
		const patch = statePatch(result);
		expect(patch.checkpoint?.data.currentUnit).toBe("finalize_model_bundle");
		expect(patch.checkpoint?.data.memory?.extractionStatus).toBe("failed");
		const warning = patch.diagnostics?.find((item) =>
			item.message.startsWith("memory extraction gate could not run"),
		);
		expect(warning?.message).toContain("crashed: ModuleNotFoundError");
		for (const item of patch.diagnostics ?? []) {
			expect(item.message).not.toContain(hostPath);
			expect(item.repair ?? "").not.toContain(hostPath);
		}
	});

	it("auto-advances with extractionStatus=failed at the cap and still lets a non-success finish through", () => {
		const fixture = fakeRepo("gate-exhausted");
		seedExtractLessons(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", inconsistentDeclaration());
		writeCandidate(fixture.runDir, "01-demo", "# Demo\n");
		const first = postToolResult(fixture.ctx);
		expect(first.ok).toBe(false);
		persist(fixture.runDir, first);
		writeCandidate(fixture.runDir, "01-demo", "# Demo\n\nsecond try\n");
		const second = postToolResult(fixture.ctx);
		expect(second.ok).toBe(true);
		expect(second.repair).toBeUndefined();
		const patch = statePatch(second);
		expect(patch.checkpoint?.data.currentUnit).toBe("finalize_model_bundle");
		expect(patch.checkpoint?.data.completedUnits).toContain("extract_lessons");
		expect(patch.checkpoint?.data.retries.extract_lessons).toBeUndefined();
		expect(patch.checkpoint?.data.memory?.extractionStatus).toBe("failed");
		expect(patch.diagnostics?.some((item) => item.message.startsWith("extraction: failed ("))).toBe(true);
		expect(patch.message).not.toContain("still cannot produce a valid artifact");
		// Not a terminal failure of the unit: the phase is the next unit's running phase and the
		// message deliberately does not carry the `Gate "<id>" exhausted` prefix digest.py looks for.
		expect(patch.phase).toMatchObject({ id: "finalize_model_bundle", status: "running" });
		expect(patch.message).toBe(
			'Extraction gate "extract_lessons" exhausted 2 blocked attempts; extraction marked failed, advanced to unit "finalize_model_bundle".',
		);
		persist(fixture.runDir, second);

		seedNonSuccessTerminal(fixture.runDir);
		fixture.ctx.point = "pre_finish";
		fixture.ctx.event = { finish: { status: "incomplete" } } as never;
		const finish = preFinish(fixture.ctx);
		expect(finish.ok, finish.repair).toBe(true);
		expect(statePatch(finish).phase?.status).toBe("incomplete");
		expect(statePatch(finish).checkpoint?.data.memory?.extractionStatus).toBe("failed");
		expect(statePatch(finish).checkpoint?.data.memory?.finishAttempts).toBeUndefined();
	});

	it("max_retries= can only raise the extraction cap, never lower it", () => {
		const fixture = fakeRepo("gate-user-max");
		fixture.ctx.args = "max_retries=1";
		seedExtractLessons(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", inconsistentDeclaration());
		writeCandidate(fixture.runDir, "01-demo", "# Demo\n");
		const first = postToolResult(fixture.ctx);
		expect(first.ok).toBe(false); // cap is max(config 2, user 1) = 2, so attempt 1 is a normal block
		expect(statePatch(first).checkpoint?.data.currentUnit).toBe("extract_lessons");
		persist(fixture.runDir, first);
		writeCandidate(fixture.runDir, "01-demo", "# Demo\n\nsecond try\n");
		const second = postToolResult(fixture.ctx);
		expect(second.ok).toBe(true);
		expect(statePatch(second).checkpoint?.data.currentUnit).toBe("finalize_model_bundle");
		expect(statePatch(second).checkpoint?.data.memory?.extractionStatus).toBe("failed");
	});

	it("still exhausts and auto-advances extract_lessons when memory config.json cannot be read", () => {
		// failOrRetry reads memoryConfigOrUndefined() (not the throwing loadMemoryConfig()) to stay
		// safe when config.json is missing or malformed; when it comes back undefined, the unit
		// must fall back to the state machine's own retryExhausted cap (DEFAULT_MAX_RETRIES = 3 in
		// checkpoints.ts) instead of isExtractionGateExhausted's config-driven cap (2). Without that
		// fallback extractionExhausted can never become true and extract_lessons retries forever —
		// the worst failure mode in this system. Force the undefined branch by spying on the same
		// memoryConfigOrUndefined export failOrRetry calls; every other memory call in this test
		// (injectOnBlock, settleOnTerminalFailure, the gate script itself) still reads the real
		// config.json from disk, so this isolates just the one fallback line.
		const fixture = fakeRepo("gate-config-unreadable");
		seedExtractLessons(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", inconsistentDeclaration());
		writeCandidate(fixture.runDir, "01-demo", "# Demo\n");
		const spy = vi.spyOn(memoryHooksModule, "memoryConfigOrUndefined").mockReturnValue(undefined);
		try {
			const first = postToolResult(fixture.ctx);
			expect(first.ok).toBe(false); // attempt 1 of the fallback cap (3) — an ordinary block
			persist(fixture.runDir, first);

			writeCandidate(fixture.runDir, "01-demo", "# Demo\n\nsecond try\n");
			const second = postToolResult(fixture.ctx);
			expect(second.ok).toBe(false); // attempt 2 of 3 — still not exhausted
			persist(fixture.runDir, second);

			writeCandidate(fixture.runDir, "01-demo", "# Demo\n\nthird try\n");
			const third = postToolResult(fixture.ctx);
			// Attempt 3 hits the fallback cap: the unit auto-advances (never a bare retry-forever
			// loop) with the same observable outcome as the config-driven exhaustion path.
			expect(third.ok).toBe(true);
			const patch = statePatch(third);
			expect(patch.checkpoint?.data.currentUnit).toBe("finalize_model_bundle");
			expect(patch.checkpoint?.data.completedUnits).toContain("extract_lessons");
			expect(patch.checkpoint?.data.memory?.extractionStatus).toBe("failed");
			expect(patch.diagnostics?.some((item) => item.message.startsWith("extraction: failed ("))).toBe(true);
		} finally {
			spy.mockRestore();
		}
	});
});

describe("sure_onboard memory wiring: injection and settlement", () => {
	it("injects matching entries into the repair, records an inject row, and does not inject twice", () => {
		const fixture = fakeRepo("inject-on-block");
		seedContextSelectionBlock(fixture);
		const first = postToolResult(fixture.ctx);
		expect(first.ok).toBe(false);
		expect(first.repair).toContain(CONFIG.inject_header);
		expect(first.repair).toContain(ENTRY_TITLE);
		const patch = statePatch(first);
		// diagnostics keep the raw gate repair; only the top-level repair carries the Memory block.
		expect(patch.diagnostics?.[0]?.repair).toContain('Field "task_type"');
		expect(patch.diagnostics?.[0]?.repair).not.toContain(CONFIG.inject_header);
		expect(patch.checkpoint?.data.memory?.injected?.context_selection).toEqual([ENTRY_ID]);
		const rows = readUsage(fixture.memoryRoot, "inject-on-block");
		expect(rows).toHaveLength(1);
		expect(rows[0]).toMatchObject({
			kind: "inject",
			skill: "sure_onboard",
			unit: "context_selection",
			attempt: 1,
			events_cutoff: 2,
			entries: [{ entry_id: ENTRY_ID, shared: false }],
		});
		persist(fixture.runDir, first);

		writeArtifact(fixture.runDir, "context_selection.json", contextSelection("still-invalid"));
		const second = postToolResult(fixture.ctx);
		expect(second.ok).toBe(false);
		// The entry still matches, so applyRecallBudget puts it in `repeated`: the block is the header
		// plus one reminder line, no entry line and no second usage row (plan 1.7 dedup).
		expect(second.repair).toContain("Entries shown at an earlier attempt still apply");
		expect(second.repair).not.toContain(ENTRY_TITLE);
		expect(readUsage(fixture.memoryRoot, "inject-on-block").filter((row) => row.kind === "inject")).toHaveLength(1);
		const afterSecond = statePatch(second).checkpoint?.data;
		expect(afterSecond?.retries.context_selection).toBe(2);
		expect(afterSecond?.memory?.pendingDisputed?.context_selection).toEqual([ENTRY_ID]);
	});

	it("settles useful_activated when the unit passes after the agent read the injected entry", () => {
		const fixture = fakeRepo("settle-useful-activated");
		seedContextSelectionBlock(fixture);
		persist(fixture.runDir, postToolResult(fixture.ctx));
		appendEvent(fixture.runDir, {
			type: "tool_call",
			timestamp: "2026-08-18T00:00:10Z",
			data: { toolName: "read", toolCallId: "c9", input: { path: `/checkout/${ENTRY_PATH}` } },
		});
		appendEvent(fixture.runDir, {
			type: "tool_result",
			timestamp: "2026-08-18T00:00:11Z",
			data: { toolName: "read", toolCallId: "c9", isError: false },
		});
		writeArtifact(fixture.runDir, "context_selection.json", contextSelection("asr"));
		const passed = postToolResult(fixture.ctx);
		expect(passed.ok, passed.repair).toBe(true);
		expect(statePatch(passed).checkpoint?.data.currentUnit).toBe("discover");
		const settle = readUsage(fixture.memoryRoot, "settle-useful-activated").filter((row) => row.kind === "settle");
		expect(settle).toHaveLength(1);
		expect(settle[0]).toMatchObject({
			kind: "settle",
			unit: "context_selection",
			entry_id: ENTRY_ID,
			outcome: "useful_activated",
		});
	});

	it("settles useful_unattributed when the unit passes without the entry being read", () => {
		const fixture = fakeRepo("settle-useful-unattributed");
		seedContextSelectionBlock(fixture);
		persist(fixture.runDir, postToolResult(fixture.ctx));
		writeArtifact(fixture.runDir, "context_selection.json", contextSelection("asr"));
		const passed = postToolResult(fixture.ctx);
		expect(passed.ok, passed.repair).toBe(true);
		const settle = readUsage(fixture.memoryRoot, "settle-useful-unattributed").filter((row) => row.kind === "settle");
		expect(settle).toHaveLength(1);
		expect(settle[0]).toMatchObject({
			unit: "context_selection",
			entry_id: ENTRY_ID,
			outcome: "useful_unattributed",
		});
	});

	it("settles disputed when the unit exhausts its retries while the entry keeps matching", () => {
		const fixture = fakeRepo("settle-disputed");
		seedContextSelectionBlock(fixture);
		persist(fixture.runDir, postToolResult(fixture.ctx));
		writeArtifact(fixture.runDir, "context_selection.json", contextSelection("still-invalid"));
		persist(fixture.runDir, postToolResult(fixture.ctx));
		writeArtifact(fixture.runDir, "context_selection.json", contextSelection("again-invalid"));
		const third = postToolResult(fixture.ctx);
		expect(third.ok).toBe(false);
		expect(third.repair).toContain("consecutive blocked attempts");
		// The Memory block stays LAST in the repair even on the exhausted branch (plan 1.13):
		// strip_memory_block cuts from the header line to the next blank line or the end of the text,
		// so a sentence appended after the block would vanish from run.json.lastRepair.
		const thirdRepair = third.repair ?? "";
		expect(thirdRepair.indexOf(CONFIG.inject_header)).toBeGreaterThan(
			thirdRepair.indexOf("consecutive blocked attempts"),
		);
		const settle = readUsage(fixture.memoryRoot, "settle-disputed").filter((row) => row.kind === "settle");
		expect(settle).toHaveLength(1);
		expect(settle[0]).toMatchObject({ unit: "context_selection", entry_id: ENTRY_ID, outcome: "disputed" });
		const patch = statePatch(third);
		expect(patch.checkpoint?.data.memory?.pendingDisputed?.context_selection ?? []).toEqual([]);
		expect(patch.diagnostics?.[0]?.repair).not.toContain(CONFIG.inject_header);
		// The hand-built exhausted patch keeps failure()'s shape: phase id "gate" and the message
		// prefix `Gate "<id>" exhausted` (digest.py marks the unit's terminal failure from these two).
		expect(patch.phase).toMatchObject({ id: "gate", status: "blocked" });
		expect(patch.message).toMatch(/^Gate "context_selection" exhausted 3 blocked attempts: /);
		expect(patch.diagnostics?.[0]?.message).toBe(patch.message);
	});
});

describe("sure_onboard memory wiring: finish and error hooks", () => {
	it("pre_finish on a failed run asks for the extraction declaration twice, then lets the third finish through", () => {
		const fixture = fakeRepo("prefinish-three-attempts");
		seedEvents(fixture.runDir, 3);
		seedCheckpoint(fixture.runDir, { currentUnit: "verdict", completedUnits: UNITS_BEFORE_VERDICT, retries: {} });
		seedNonSuccessTerminal(fixture.runDir);
		fixture.ctx.point = "pre_finish";
		fixture.ctx.event = { finish: { status: "failed" } } as never;

		const first = preFinish(fixture.ctx);
		expect(first.ok).toBe(false);
		expect(first.repair).toContain("EXTRACTION.md");
		expect(first.repair).toContain("extraction_declaration.json");
		const afterFirst = persist(fixture.runDir, first);
		expect(afterFirst?.memory?.finishAttempts).toBe(1);
		expect(afterFirst?.memory?.digestSha256).toBe(sha256File(join(fixture.runDir, "artifacts", "run_digest.json")));

		const second = preFinish(fixture.ctx);
		expect(second.ok).toBe(false);
		expect(persist(fixture.runDir, second)?.memory?.finishAttempts).toBe(2);

		const third = preFinish(fixture.ctx);
		expect(third.ok, third.repair).toBe(true);
		const patch = statePatch(third);
		expect(patch.phase?.status).toBe("failed");
		expect(patch.checkpoint?.data.memory?.extractionStatus).toBe("failed");
		expect(patch.diagnostics?.some((item) => item.message.toLowerCase().includes("extraction"))).toBe(true);
	});

	it("pre_finish keeps the digest diagnostic on the blocking path", () => {
		// The repair tells the agent to read artifacts/run_digest.json first. When the digest
		// build failed, that file holds only {schema, error}, and the diagnostic explaining that
		// — and that no_new_lessons=true citing the error is acceptable — is the only thing that
		// says so. failure() hard-codes a one-element diagnostics array and used to drop it.
		const fixture = fakeRepo("prefinish-digest-failure");
		seedEvents(fixture.runDir, 3);
		seedCheckpoint(fixture.runDir, { currentUnit: "verdict", completedUnits: UNITS_BEFORE_VERDICT, retries: {} });
		seedNonSuccessTerminal(fixture.runDir);
		rmSync(join(fixture.packageDir, "scripts", "build_run_digest.py"));
		fixture.ctx.point = "pre_finish";
		fixture.ctx.event = { finish: { status: "failed" } } as never;

		const blocked = preFinish(fixture.ctx);
		expect(blocked.ok).toBe(false);
		expect(blocked.repair).toContain("run_digest.json");
		const patch = statePatch(blocked);
		expect(patch.phase).toMatchObject({ id: "gate", status: "blocked" });
		expect(patch.diagnostics?.[0]).toMatchObject({
			severity: "error",
			message: "Non-success Onboard finish requires an extraction declaration.",
		});
		const digestDiagnostic = patch.diagnostics?.find((item) => item.message.startsWith("memory digest failed"));
		expect(digestDiagnostic?.severity).toBe("warning");
		expect(digestDiagnostic?.repair).toContain("no_new_lessons=true");
		// The file the repair points at really does hold nothing but the error.
		const digest = JSON.parse(readFileSync(join(fixture.runDir, "artifacts", "run_digest.json"), "utf-8"));
		expect(Object.keys(digest).sort()).toEqual(["error", "schema"]);
		expect(patch.checkpoint?.data.memory?.finishAttempts).toBe(1);
	});

	it("pre_finish accepts a non-success finish at once when a valid declaration is already on disk", () => {
		const fixture = fakeRepo("prefinish-declared");
		const sha = seedDigest(fixture);
		seedCheckpoint(fixture.runDir, {
			currentUnit: "verdict",
			completedUnits: UNITS_BEFORE_VERDICT,
			retries: {},
			memory: { digestCutoff: 0, digestSha256: sha, digestPassed: "verdict" },
		});
		writeArtifact(fixture.runDir, "extraction_declaration.json", validDeclaration());
		seedNonSuccessTerminal(fixture.runDir);
		fixture.ctx.point = "pre_finish";
		fixture.ctx.event = { finish: { status: "failed" } } as never;
		const result = preFinish(fixture.ctx);
		expect(result.ok, result.repair).toBe(true);
		expect(statePatch(result).checkpoint?.data.memory?.finishAttempts).toBeUndefined();
		expect(statePatch(result).checkpoint?.data.memory?.extractionStatus).toBeUndefined();
	});

	it("post_finish spawns scripts/publish_memory.py with --run-dir and --repo-root", () => {
		const fixture = fakeRepo("postfinish-publish");
		seedEvents(fixture.runDir, 3);
		// post_finish publishes only what the gate accepts there and then, so the declaration the
		// extract_lessons unit left behind has to still be on disk and still pass.
		const sha = seedDigest(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", validDeclaration());
		seedCheckpoint(fixture.runDir, {
			currentUnit: "finalize_model_bundle",
			completedUnits: [...UNITS_BEFORE_VERDICT, "verdict", "extract_lessons", "finalize_model_bundle"],
			retries: {},
			memory: { digestCutoff: 0, digestSha256: sha, digestPassed: "verdict" },
		});
		fixture.ctx.point = "post_finish";
		fixture.ctx.run = { id: "postfinish-publish", command: "/sure_onboard", status: "success" } as never;
		const result = postFinish(fixture.ctx);
		expect(result.ok).toBe(true);
		const called = JSON.parse(readFileSync(join(fixture.runDir, "publish_called.json"), "utf-8")) as string[];
		expect(resolve(called[called.indexOf("--run-dir") + 1])).toBe(resolve(fixture.runDir));
		expect(resolve(called[called.indexOf("--repo-root") + 1])).toBe(resolve(fixture.repoRoot));
	});

	it("post_finish skips publish when extraction was marked failed", () => {
		const fixture = fakeRepo("postfinish-skip");
		seedCheckpoint(fixture.runDir, {
			currentUnit: "finalize_model_bundle",
			completedUnits: [...UNITS_BEFORE_VERDICT, "verdict", "extract_lessons", "finalize_model_bundle"],
			retries: {},
			memory: { extractionStatus: "failed" },
		});
		fixture.ctx.point = "post_finish";
		fixture.ctx.run = { id: "postfinish-skip", command: "/sure_onboard", status: "incomplete" } as never;
		const result = postFinish(fixture.ctx);
		expect(result.ok).toBe(true);
		expect(existsSync(join(fixture.runDir, "publish_called.json"))).toBe(false);
	});

	it("publish_memory.py stores a declared candidate whatever the gate thought of it", () => {
		// The control for the test below: publish.py re-runs none of the ten rules, so the only
		// thing between this candidate and sure/memory/provisional is post_finish deciding not to
		// call it. If this ever stops publishing, the test below proves nothing.
		const fixture = fakeRepo("publish-control");
		writeWrapper(fixture.packageDir, "publish_memory.py", "publish");
		seedEvents(fixture.runDir, 3);
		seedDigest(fixture);
		writeUngatedCandidate(fixture);
		const r = spawnSync(
			pythonBin(),
			[
				join(fixture.packageDir, "scripts", "publish_memory.py"),
				"--run-dir",
				fixture.runDir,
				"--repo-root",
				fixture.repoRoot,
			],
			{ encoding: "utf-8" },
		);
		expect(r.status, r.stderr || r.stdout).toBe(0);
		expect(provisionalEntries(fixture.memoryRoot)).toHaveLength(1);
	});

	it("publishes nothing when the finish that marked the extraction failed was refused by the harness", () => {
		// End to end: the non-success pre_finish patch carried the memory checkpoint AND an
		// artifacts[] entry with status "blocked", which is not one of the four statuses the
		// harness normalizer accepts. The normalizer rejects a patch whole, so extractionStatus
		// never reached state.json and post_finish published candidates no gate ever accepted.
		const fixture = fakeRepo("prefinish-ungated-not-published");
		writeWrapper(fixture.packageDir, "publish_memory.py", "publish");
		writeWrapper(fixture.packageDir, "check_memory_extraction.py", "proposals");
		seedEvents(fixture.runDir, 3);
		seedCheckpoint(fixture.runDir, { currentUnit: "verdict", completedUnits: UNITS_BEFORE_VERDICT, retries: {} });
		seedNonSuccessTerminal(fixture.runDir);
		writeUngatedCandidate(fixture);
		fixture.ctx.point = "pre_finish";
		fixture.ctx.event = { finish: { status: "failed" } } as never;

		const first = preFinish(fixture.ctx);
		expect(first.ok).toBe(false);
		// The gate really judged this declaration and rejected it, rule by rule.
		expect(first.repair).toContain("check_memory_extraction gate:");
		expect(persistThroughHarness(fixture.runDir, first)).toBeUndefined();

		const second = preFinish(fixture.ctx);
		expect(second.ok).toBe(false);
		expect(persistThroughHarness(fixture.runDir, second)).toBeUndefined();

		const third = preFinish(fixture.ctx);
		expect(third.ok, third.repair).toBe(true);
		expect(statePatch(third).checkpoint?.data.memory?.extractionStatus).toBe("failed");
		// The harness has to be able to store that verdict, or post_finish never learns it.
		expect(persistThroughHarness(fixture.runDir, third)).toBeUndefined();

		fixture.ctx.point = "post_finish";
		fixture.ctx.run = { id: "prefinish-ungated-not-published", command: "/sure_onboard", status: "failed" } as never;
		expect(postFinish(fixture.ctx).ok).toBe(true);
		expect(provisionalEntries(fixture.memoryRoot)).toEqual([]);
	});

	it("on_error writes artifacts/run_digest.json and never publishes", () => {
		const fixture = fakeRepo("onerror-digest");
		seedEvents(fixture.runDir, 4);
		seedCheckpoint(fixture.runDir, {
			currentUnit: "build_env",
			completedUnits: UNITS_BEFORE_VERDICT.slice(0, 8),
			retries: { build_env: 1 },
		});
		fixture.ctx.point = "on_error";
		fixture.ctx.event = { reason: "session_shutdown" } as never;
		const result = onError(fixture.ctx);
		expect(result.ok).toBe(true);
		const digestPath = join(fixture.runDir, "artifacts", "run_digest.json");
		expect(existsSync(digestPath)).toBe(true);
		const digest = JSON.parse(readFileSync(digestPath, "utf-8")) as { schema: string; run: { cutoff: number } };
		expect(digest.schema).toBe("sure.memory.run_digest.v1");
		expect(digest.run.cutoff).toBe(4);
		expect(existsSync(join(fixture.runDir, "publish_called.json"))).toBe(false);
	});

	it("on_error leaves the digest the checkpoint's digestSha256 is bound to alone", () => {
		// The turn ended without sure_finish after extract_lessons passed. The gate accepted
		// candidates whose source.digest_sha256 equals this file's sha; onError writes no
		// checkpoint, so digestSha256 stays pinned to it. Rebuilding with a later cutoff and no
		// --mark-passed would leave publish_memory.py --run-dir (the documented hand recovery)
		// deriving hook_trigger from a digest the gate never saw.
		const fixture = fakeRepo("onerror-keeps-gated-digest");
		seedEvents(fixture.runDir, 4);
		const sha = seedDigest(fixture);
		const digestPath = join(fixture.runDir, "artifacts", "run_digest.json");
		const before = readFileSync(digestPath, "utf-8");
		seedCheckpoint(fixture.runDir, {
			currentUnit: "finalize_model_bundle",
			completedUnits: [...UNITS_BEFORE_VERDICT, "verdict", "extract_lessons"],
			retries: {},
			memory: { digestCutoff: 0, digestSha256: sha, digestPassed: "verdict" },
		});
		// More events after the digest was pinned: a rebuild would pick a later cutoff.
		appendEvent(fixture.runDir, { type: "tool_call", timestamp: "2026-08-18T00:01:00Z", data: {} });
		fixture.ctx.point = "on_error";
		fixture.ctx.event = { reason: "session_shutdown" } as never;

		const result = onError(fixture.ctx);
		expect(result.ok).toBe(true);
		expect(readFileSync(digestPath, "utf-8")).toBe(before);
		expect(sha256File(digestPath)).toBe(sha);
		expect(statePatch(result).diagnostics ?? []).toEqual([]);
	});

	it("on_error still rebuilds when the pinned digest no longer matches the file", () => {
		const fixture = fakeRepo("onerror-rebuilds-stale");
		seedEvents(fixture.runDir, 4);
		seedCheckpoint(fixture.runDir, {
			currentUnit: "build_env",
			completedUnits: UNITS_BEFORE_VERDICT.slice(0, 8),
			retries: {},
			memory: { digestSha256: "b".repeat(64) },
		});
		fixture.ctx.point = "on_error";
		fixture.ctx.event = { reason: "session_shutdown" } as never;
		expect(onError(fixture.ctx).ok).toBe(true);
		const digest = JSON.parse(readFileSync(join(fixture.runDir, "artifacts", "run_digest.json"), "utf-8")) as {
			run: { cutoff: number };
		};
		expect(digest.run.cutoff).toBe(4);
	});
});

describe.skipIf(!HARNESS.ok)("sure_onboard memory wiring: paths that need the harness python", () => {
	it("advancing from verdict into extract_lessons builds the digest and records it in checkpoint memory", () => {
		const fixture = realRun("enter-extract-lessons");
		seedEvents(fixture.runDir, 3);
		seedCheckpoint(fixture.runDir, { currentUnit: "verdict", completedUnits: UNITS_BEFORE_VERDICT, retries: {} });
		writeArtifact(fixture.runDir, "verdict.json", { status: "partial" }); // check_verdict.py accepts a bare partial verdict
		const entered = postToolResult(fixture.ctx);
		expect(entered.ok, entered.repair).toBe(true);
		const data = persist(fixture.runDir, entered);
		expect(data?.currentUnit).toBe("extract_lessons");
		const digestPath = join(fixture.runDir, "artifacts", "run_digest.json");
		expect(existsSync(digestPath)).toBe(true);
		const sha = sha256File(digestPath);
		expect(data?.memory).toMatchObject({ digestCutoff: 3, digestSha256: sha, digestPassed: "verdict" });
		const digest = JSON.parse(readFileSync(digestPath, "utf-8")) as { units: Array<{ id: string; outcome: string }> };
		expect(digest.units.find((unit) => unit.id === "verdict")?.outcome).toBe("passed");

		writeArtifact(fixture.runDir, "extraction_declaration.json", validDeclaration());
		const passed = postToolResult(fixture.ctx);
		expect(passed.ok, passed.repair).toBe(true);
		const after = statePatch(passed).checkpoint?.data;
		expect(after?.currentUnit).toBe("finalize_model_bundle");
		expect(after?.memory?.digestSha256).toBe(sha);
	});

	it("pre_start writes artifacts/memory_context.json without leaking output_dir", () => {
		const fixture = realRun("pre-start-memory");
		const usageFile = join(fixture.memoryRoot, "usage", "pre-start-memory.jsonl");
		rmSync(usageFile, { force: true }); // this test runs against the real sure/memory/; leave no trace
		writeLocalPythonSitePolicy(fixture.ctx.cwd);
		fixture.ctx.point = "pre_start";
		fixture.ctx.args =
			"model_id=demo/model model_name=demo__model repo=https://example.com/demo task_type=asr deployment_type=local package=none output_dir=/tmp/should-not-leak";
		try {
			const result = preStart(fixture.ctx);
			expect(result.ok, result.repair).toBe(true);
			const contextPath = join(fixture.runDir, "artifacts", "memory_context.json");
			expect(existsSync(contextPath)).toBe(true);
			expect(readFileSync(contextPath, "utf-8")).not.toContain("should-not-leak");
		} finally {
			rmSync(usageFile, { force: true });
		}
	});
});
