import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
	appendFileSync,
	copyFileSync,
	existsSync,
	lstatSync,
	mkdirSync,
	readFileSync,
	rmdirSync,
	rmSync,
	symlinkSync,
	unlinkSync,
	writeFileSync,
} from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
// Imported as a namespace (not a named import) so vi.spyOn can override just
// memoryConfigOrUndefined for one test, same pattern as sure-onboard-memory.test.ts.
import * as memoryHooksModule from "../../../../sure/runtime/memory/hooks.ts";
import type { CheckpointData } from "../../../../sure/skills/sure_eval/hooks/checkpoints.ts";
import {
	evalProductDir,
	onError,
	postFinish,
	postToolResult,
	preFinish,
	preToolCall,
} from "../../../../sure/skills/sure_eval/hooks/index.ts";
// The real harness normalizer: extension.ts applyStatePatch runs every state_patch through it and
// drops the whole patch when it comes back not ok, so a test that reads checkpoint.data straight
// out of a state_patch is testing a stricter harness than the one that ships.
import { normalizeSureDisplayStatePatch } from "../../src/core/sure/state.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// The sure_eval hooks are exercised against a throwaway repo layout so nothing
// touches the real sure/memory/ of this checkout:
//   <tmp>/repo/sure/runtime/memory                link to the real shared library
//   <tmp>/repo/sure/runtime/harness/bootstrap.py  fake: reports the local python as the harness python
//   <tmp>/repo/sure/skills/sure_eval/scripts/     copies of the stdlib-only scripts used here
//   <tmp>/repo/sure/skills/sure_eval/schemas      link to the real schemas
//   <tmp>/repo/.sure/runs/<run_id>/               the run dir
// HARNESS_PYTHON_BIN and SURE_HARNESS_BOOTSTRAP_PYTHON point at the local python,
// so the memory scripts (stdlib only) and the two eval gate scripts used here
// (check_assessment.py, check_run_report.py, also stdlib only) run on Windows and
// Linux without a materialized harness runtime.

const REPO_ROOT = resolve(__dirname, "../../../..");
const REAL_PACKAGE_DIR = join(REPO_ROOT, "sure", "skills", "sure_eval");
const MEMORY_LIB_DIR = join(REPO_ROOT, "sure", "runtime", "memory");
const SUITE_TMP = resolve(__dirname, "tmp-eval-mem");
const MEMORY_CONFIG = JSON.parse(readFileSync(join(MEMORY_LIB_DIR, "config.json"), "utf-8")) as {
	inject_header: string;
	extraction_gate_max_failures: number;
};

const PYTHON_BIN = (() => {
	for (const candidate of ["python3", "python"]) {
		const r = spawnSync(candidate, ["-c", "import sys; print(sys.executable)"], {
			encoding: "utf-8",
			timeout: 10_000,
		});
		if (r.status === 0 && r.stdout.trim()) {
			return r.stdout.trim();
		}
	}
	return "";
})();

const ENTRY_ID = "sure_eval/handoff-loop";
const ENTRY_TITLE = "Model not onboarded: run /sure_onboard before /sure_eval";
const ENTRY_PATH = "sure/skills/sure_eval/references/memory/bad_cases/handoff-loop.md";
const RUN_ARGS = "model=demo_asr datasets=aishell1__v1";

const UNITS_BEFORE_ASSESSMENT = [
	"task_classification",
	"tool_readiness_routing",
	"plan",
	"dataset_scope",
	"script_routing",
	"execution_surface",
	"execution_readiness",
	"smoke_test",
	"submit_vc_run",
	"execute_wait",
];
const UNITS_THROUGH_EXTRACT = [...UNITS_BEFORE_ASSESSMENT, "assessment", "extract_lessons"];
const UNITS_BEFORE_SMOKE = UNITS_BEFORE_ASSESSMENT.slice(0, 7);

// Stands in for sure/runtime/harness/bootstrap.py (Linux-only fcntl/grp): reports
// the interpreter running it as the harness python. Only stdlib scripts run here.
const FAKE_BOOTSTRAP = [
	"import json, sys",
	"contract = {",
	'    "runtime_id": "vitest-local",',
	'    "python_executable": sys.executable,',
	'    "python_abi": "cp3",',
	'    "python_version": ".".join(str(part) for part in sys.version_info[:3]),',
	'    "lock_sha256": "0" * 64,',
	'    "harness_version": "vitest",',
	'    "manifest_path": __file__,',
	'    "runtime_root": ".",',
	"}",
	"print(json.dumps(contract))",
	"",
].join("\n");

// Records how post_finish called publish; used only by the wiring tests.
const PUBLISH_STUB = [
	"import json, sys",
	"from pathlib import Path",
	"argv = sys.argv[1:]",
	'run_dir = Path(argv[argv.index("--run-dir") + 1])',
	'run_dir.joinpath("publish_called.json").write_text(json.dumps(argv), encoding="utf-8")',
	'print("stub publish ok")',
	"",
].join("\n");

const COPIED_SCRIPTS = [
	"build_run_digest.py",
	"check_memory_extraction.py",
	"publish_memory.py",
	"check_assessment.py",
	"check_run_report.py",
];

const SAVED_ENV = {
	HARNESS_PYTHON_BIN: process.env.HARNESS_PYTHON_BIN,
	SURE_HARNESS_BOOTSTRAP_PYTHON: process.env.SURE_HARNESS_BOOTSTRAP_PYTHON,
};

beforeAll(() => {
	process.env.HARNESS_PYTHON_BIN = PYTHON_BIN;
	process.env.SURE_HARNESS_BOOTSTRAP_PYTHON = PYTHON_BIN;
});

afterAll(() => {
	for (const [key, value] of Object.entries(SAVED_ENV)) {
		if (value === undefined) {
			delete process.env[key];
		} else {
			process.env[key] = value;
		}
	}
});

interface Fixture {
	ctx: SureHookContext;
	runDir: string;
	runId: string;
	repoRoot: string;
	packageDir: string;
	memoryRoot: string;
}

type StatePatchForTest = {
	message?: string;
	diagnostics?: Array<{ severity?: string; message: string; repair?: string }>;
	phase?: { status?: string };
	checkpoint?: { data: CheckpointData };
};

function statePatch(result: { state_patch?: unknown }): StatePatchForTest {
	return (result.state_patch ?? {}) as StatePatchForTest;
}

// Remove a directory link without ever recursing into the real library it points at.
function unlinkLink(path: string): void {
	try {
		if (!lstatSync(path).isSymbolicLink()) {
			return;
		}
	} catch {
		return;
	}
	try {
		unlinkSync(path);
	} catch {
		rmdirSync(path);
	}
}

function fixture(name: string, options: { publishStub?: boolean } = {}): Fixture {
	const root = join(SUITE_TMP, name);
	const repoRoot = join(root, "repo");
	const packageDir = join(repoRoot, "sure", "skills", "sure_eval");
	unlinkLink(join(repoRoot, "sure", "runtime", "memory"));
	unlinkLink(join(packageDir, "schemas"));
	rmSync(root, { recursive: true, force: true });
	const scriptsDir = join(packageDir, "scripts");
	mkdirSync(scriptsDir, { recursive: true });
	mkdirSync(join(repoRoot, "sure", "runtime", "harness"), { recursive: true });
	symlinkSync(MEMORY_LIB_DIR, join(repoRoot, "sure", "runtime", "memory"), "junction");
	symlinkSync(join(REAL_PACKAGE_DIR, "schemas"), join(packageDir, "schemas"), "junction");
	writeFileSync(join(repoRoot, "sure", "runtime", "harness", "bootstrap.py"), FAKE_BOOTSTRAP, "utf-8");
	for (const script of COPIED_SCRIPTS) {
		copyFileSync(join(REAL_PACKAGE_DIR, "scripts", script), join(scriptsDir, script));
	}
	if (options.publishStub) {
		writeFileSync(join(scriptsDir, "publish_memory.py"), PUBLISH_STUB, "utf-8");
	}
	const runId = `20260818-120000-${name
		.replace(/[^a-z0-9]/gi, "")
		.slice(0, 8)
		.padEnd(8, "0")}`;
	const runDir = join(repoRoot, ".sure", "runs", runId);
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	const ctx: SureHookContext = {
		point: "post_tool_result",
		run: { runId, command: "/sure_eval", status: "running" } as never,
		skill: { name: "sure_eval", command: "/sure_eval" } as never,
		cwd: repoRoot,
		packageDir,
		runDir,
		args: RUN_ARGS,
	};
	return { ctx, runDir, runId, repoRoot, packageDir, memoryRoot: join(repoRoot, "sure", "memory") };
}

function seedCheckpoint(fx: Fixture, data: CheckpointData): void {
	writeFileSync(join(fx.runDir, "state.json"), JSON.stringify({ checkpoint: { data } }, null, 2), "utf-8");
}

function persist(fx: Fixture, result: { state_patch?: unknown }): CheckpointData {
	const data = statePatch(result).checkpoint?.data;
	if (!data) {
		throw new Error("hook result carried no checkpoint");
	}
	seedCheckpoint(fx, data);
	return data;
}

// What the harness really does with a state_patch (extension.ts applyStatePatch): normalize it,
// and on a rejection write nothing at all and keep going. Returns the normalizer's complaint so a
// test can assert the patch was accepted rather than assume it.
function persistThroughHarness(fx: Fixture, result: { state_patch?: unknown }): string | undefined {
	const normalized = normalizeSureDisplayStatePatch(result.state_patch);
	if (!normalized.ok || !normalized.state) {
		return normalized.message ?? "Invalid Sure state patch.";
	}
	const data = normalized.state.checkpoint?.data as CheckpointData | undefined;
	if (data) {
		seedCheckpoint(fx, data);
	}
	return undefined;
}

function writeArtifact(fx: Fixture, produces: string, value: unknown): void {
	writeFileSync(join(fx.runDir, "artifacts", produces), JSON.stringify(value, null, 2), "utf-8");
}

function appendEvents(fx: Fixture, events: Array<Record<string, unknown>>): void {
	const lines = events.map((event) => `${JSON.stringify({ timestamp: "2026-08-18T12:00:00Z", ...event })}\n`).join("");
	appendFileSync(join(fx.runDir, "events.jsonl"), lines, "utf-8");
}

const BASE_EVENTS = [
	{ type: "created", data: { command: "/sure_eval", args: RUN_ARGS, status: "pending" } },
	{ type: "tool_call", data: { toolName: "bash", toolCallId: "c1", input: { command: "ls artifacts" } } },
	{ type: "tool_result", data: { toolName: "bash", toolCallId: "c1", isError: false } },
];

function readUsage(fx: Fixture): Array<Record<string, unknown>> {
	const path = join(fx.memoryRoot, "usage", `${fx.runId}.jsonl`);
	if (!existsSync(path)) {
		return [];
	}
	return readFileSync(path, "utf-8")
		.split("\n")
		.filter((line) => line.trim().length > 0)
		.map((line) => JSON.parse(line) as Record<string, unknown>);
}

function indexEntry(overrides: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		entry_id: ENTRY_ID,
		type: "bad_case",
		status: "provisional",
		target_skill: "sure_eval",
		applies_to: ["sure_eval"],
		component: "tool_readiness_routing",
		cause: "config_not_set",
		trigger: ["handoff_to_tool_agent=true"],
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
		...overrides,
	};
}

function writeIndex(fx: Fixture, entries: unknown[]): void {
	mkdirSync(fx.memoryRoot, { recursive: true });
	writeFileSync(
		join(fx.memoryRoot, "index.json"),
		JSON.stringify(
			{
				schema: "sure.memory.index.v1",
				built_at: "2026-08-18T00:00:00Z",
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

// The inject row the hook would have written when the entry was injected at `unit`.
function writeInjectRow(fx: Fixture, unit: string): void {
	mkdirSync(join(fx.memoryRoot, "usage"), { recursive: true });
	const row = {
		kind: "inject",
		run_id: fx.runId,
		skill: "sure_eval",
		unit,
		attempt: 1,
		events_cutoff: 0,
		entries: [{ entry_id: ENTRY_ID, shared: false }],
		at: "2026-08-18T12:00:00Z",
	};
	appendFileSync(join(fx.memoryRoot, "usage", `${fx.runId}.jsonl`), `${JSON.stringify(row)}\n`, "utf-8");
}

function handoffArtifact(extra: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		readiness: "needs_onboarding",
		model_dir: "/srv/sure/models/demo_asr",
		handoff_to_tool_agent: true,
		...extra,
	};
}

// Drive assessment -> extract_lessons through the real gate; returns the persisted checkpoint.
function passAssessment(fx: Fixture): CheckpointData {
	appendEvents(fx, BASE_EVENTS);
	seedCheckpoint(fx, { currentUnit: "assessment", completedUnits: UNITS_BEFORE_ASSESSMENT, retries: {} });
	writeArtifact(fx, "assessment_report.json", { anomaly_detected: false, user_confirmed: true, status: "ok" });
	const result = postToolResult(fx.ctx);
	expect(result.ok, result.repair).toBe(true);
	const data = persist(fx, result);
	expect(data.currentUnit).toBe("extract_lessons");
	return data;
}

function writeDeclaration(fx: Fixture, overrides: Record<string, unknown> = {}): void {
	writeArtifact(fx, "extraction_declaration.json", {
		schema: "sure.memory.extraction.v2",
		no_new_lessons: true,
		no_lessons_reason: "clean run, nothing beyond the existing entries",
		covered_by: [],
		candidates: [],
		infra_noise: false,
		infra_evidence: [],
		...overrides,
	});
}

// A run that failed at smoke_test and still wrote a compliant failed run report
// (check_run_report.py: failed pre-submit needs a failed smoke result, an
// assessment report and next_action; no execution_result.json required).
function seedFailedFinish(fx: Fixture, checkpoint: Partial<CheckpointData> = {}): void {
	seedCheckpoint(fx, {
		currentUnit: "smoke_test",
		completedUnits: UNITS_BEFORE_SMOKE,
		retries: { smoke_test: 2 },
		...checkpoint,
	});
	writeArtifact(fx, "smoke_test_result.json", {
		smoke_passed: false,
		sample_count: 0,
		exit_code: 125,
		stdout_excerpt: "",
		stderr_excerpt: "docker: entrypoint not found",
		failures: ["container entrypoint missing"],
	});
	writeArtifact(fx, "assessment_report.json", { anomaly_detected: false, user_confirmed: true, status: "failed" });
	writeArtifact(fx, "main_agent_run_report.json", {
		run_id: fx.runId,
		timestamp: "2026-08-18T12:00:00Z",
		task_type: "evaluate_existing_model",
		goal: "bounded smoke evaluation",
		selected_datasets: ["aishell1__v1"],
		executed_steps: ["smoke_test"],
		status: "failed",
		report_persisted: true,
		execution_path_actual: "local_docker",
		execution: { path_actual: "blocked_before_submit", failure_class: "smoke_test_failed" },
		next_action: "fix the container entrypoint and rerun smoke_test",
	});
}

function finishCtx(fx: Fixture, status: "failed" | "incomplete" | "success"): SureHookContext {
	return { ...fx.ctx, point: "pre_finish", event: { finish: { status } } };
}

function postFinishCtx(fx: Fixture): SureHookContext {
	return { ...fx.ctx, point: "post_finish", run: { ...(fx.ctx.run as object), status: "success" } as never };
}

// The whitelist itself (gate scripts of current + completed units, helper scripts
// of the current unit only) is Task 11's; these three cases pin how it interacts
// with the memory wiring: publish is never callable by the agent, and the
// exhausted-unit block never applies to extract_lessons (its gate auto-advances).
describe("sure_eval preToolCall with extract_lessons", () => {
	const PREVIEW_CMD = "python3 scripts/build_run_digest.py --run-dir . --out artifacts/run_digest.preview.json";
	const GATE_CMD =
		"python3 scripts/check_memory_extraction.py --run-dir . --produces artifacts/extraction_declaration.json";
	const PUBLISH_CMD = "python3 scripts/publish_memory.py --run-dir . --repo-root .";
	const AT_EXTRACT: CheckpointData = {
		currentUnit: "extract_lessons",
		completedUnits: [...UNITS_BEFORE_ASSESSMENT, "assessment"],
		retries: {},
	};

	function bashCtx(fx: Fixture, command: string): SureHookContext {
		return { ...fx.ctx, point: "pre_tool_call", event: { toolName: "bash", input: { command } } };
	}

	it("allows the digest preview helper and the extraction gate from extract_lessons, rejects publish", () => {
		const fx = fixture("ptc-helper");
		seedCheckpoint(fx, AT_EXTRACT);
		expect(preToolCall(bashCtx(fx, PREVIEW_CMD)).ok).toBe(true);
		expect(preToolCall(bashCtx(fx, GATE_CMD)).ok).toBe(true);
		const publish = preToolCall(bashCtx(fx, PUBLISH_CMD));
		expect(publish.ok).toBe(false);
		expect(publish.repair).toContain("not permitted");
	});

	it("never treats extract_lessons as exhausted (its retries auto-advance instead)", () => {
		const fx = fixture("ptc-exhausted");
		seedCheckpoint(fx, { ...AT_EXTRACT, retries: { extract_lessons: MEMORY_CONFIG.extraction_gate_max_failures } });
		const result = preToolCall(bashCtx(fx, PREVIEW_CMD));
		expect(result.ok).toBe(true);
		expect(result.repair ?? "").not.toContain("already exhausted");
	});

	it("keeps helper scripts unit-local: run_report may rerun the extraction gate but not the digest preview", () => {
		const fx = fixture("ptc-run-report");
		seedCheckpoint(fx, { currentUnit: "run_report", completedUnits: UNITS_THROUGH_EXTRACT, retries: {} });
		expect(preToolCall(bashCtx(fx, GATE_CMD)).ok).toBe(true);
		const preview = preToolCall(bashCtx(fx, PREVIEW_CMD));
		expect(preview.ok).toBe(false);
		expect(preview.repair).toContain("not permitted");
		expect(preview.repair ?? "").not.toContain("already exhausted");
	});
});

describe.skipIf(!PYTHON_BIN)("sure_eval postToolResult memory wiring", () => {
	it("passing assessment enters extract_lessons and builds the run digest into the checkpoint", () => {
		const fx = fixture("enter-extract");
		const data = passAssessment(fx);
		expect(data.completedUnits).toContain("assessment");
		expect(data.memory?.digestPassed).toBe("assessment");
		expect(data.memory?.digestCutoff).toBe(BASE_EVENTS.length);
		expect(data.memory?.digestSha256).toMatch(/^[0-9a-f]{64}$/);
		const digestPath = join(fx.runDir, "artifacts", "run_digest.json");
		expect(existsSync(digestPath)).toBe(true);
		const onDisk = createHash("sha256").update(readFileSync(digestPath)).digest("hex");
		expect(onDisk).toBe(data.memory?.digestSha256);
		const digest = JSON.parse(readFileSync(digestPath, "utf-8")) as {
			schema: string;
			run: { cutoff: number };
			units: Array<{ id: string; outcome: string }>;
		};
		expect(digest.schema).toBe("sure.memory.run_digest.v1");
		expect(digest.run.cutoff).toBe(BASE_EVENTS.length);
		expect(digest.units.find((unit) => unit.id === "assessment")?.outcome).toBe("passed");
	});

	it("repairs a malformed extraction_declaration.json instead of stalling on it", () => {
		// readArtifact returns undefined for "absent" and "present but not JSON" alike, so the
		// unit answered ok with no repair, no diagnostic and no retry consumed: the gate never
		// ran and the extraction cap was never reached.
		const fx = fixture("declaration-not-json");
		passAssessment(fx);
		writeFileSync(
			join(fx.runDir, "artifacts", "extraction_declaration.json"),
			'{\n  "schema": "sure.memory.extraction.v2",\n  "no_new_lessons": true,\n',
			"utf-8",
		);

		const first = postToolResult(fx.ctx);
		expect(first.ok).toBe(false);
		expect(first.repair).toContain("extraction_declaration.json is present but is not valid JSON");
		expect(first.repair).not.toContain(fx.runDir.split("\\").join("/"));
		const afterFirst = persist(fx, first);
		expect(afterFirst.currentUnit).toBe("extract_lessons");
		expect(afterFirst.retries.extract_lessons).toBe(1);

		writeDeclaration(fx);
		const fixed = postToolResult(fx.ctx);
		expect(fixed.ok, fixed.repair).toBe(true);
		expect(statePatch(fixed).checkpoint?.data.currentUnit).toBe("run_report");
	});

	it("advances with extraction failed when the gate crashes instead of judging", () => {
		// An incomplete bundle makes the wrapper die at import. The traceback carries absolute
		// host paths and is not something an agent can repair; blocking on it also stalls the
		// unit, because failOrRetry's unchanged-artifact guard consumes no retry when the agent
		// has nothing it can change, so the extraction cap is never reached.
		const fx = fixture("extract-gate-crash");
		passAssessment(fx);
		writeDeclaration(fx);
		const hostPath = join(fx.packageDir, "scripts", "check_memory_extraction.py").split("\\").join("/");
		writeFileSync(
			join(fx.packageDir, "scripts", "check_memory_extraction.py"),
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

		const result = postToolResult(fx.ctx);
		expect(result.ok, result.repair).toBe(true);
		expect(result.repair).toBeUndefined();
		const patch = statePatch(result);
		expect(patch.checkpoint?.data.currentUnit).toBe("run_report");
		expect(patch.checkpoint?.data.completedUnits).toContain("extract_lessons");
		expect(patch.checkpoint?.data.memory?.extractionStatus).toBe("failed");
		const warning = patch.diagnostics?.find((d) => d.message.startsWith("memory extraction gate could not run"));
		expect(warning?.severity).toBe("warning");
		expect(warning?.message).toContain("crashed: ModuleNotFoundError");
		for (const item of patch.diagnostics ?? []) {
			expect(item.message).not.toContain(hostPath);
			expect(item.repair ?? "").not.toContain(hostPath);
		}
	});

	it("extraction gate: changed candidate re-runs the gate, exhaustion auto-advances with extraction failed", () => {
		const fx = fixture("extract-exhaust");
		passAssessment(fx);
		writeDeclaration(fx, { no_new_lessons: false, no_lessons_reason: null, candidates: ["01-bad"] });
		const candidateDir = join(fx.runDir, "artifacts", "candidates", "01-bad");
		mkdirSync(candidateDir, { recursive: true });
		writeFileSync(join(candidateDir, "proposal.json"), "{}\n", "utf-8");
		writeFileSync(join(candidateDir, "proposal.md"), "# Bad candidate\n", "utf-8");

		const first = postToolResult(fx.ctx);
		expect(first.ok).toBe(false);
		expect((first.repair ?? "").length).toBeGreaterThan(0);
		const afterFirst = persist(fx, first);
		expect(afterFirst.currentUnit).toBe("extract_lessons");
		expect(afterFirst.retries.extract_lessons).toBe(1);
		expect(afterFirst.memory?.digestSha256).toMatch(/^[0-9a-f]{64}$/);

		// Nothing changed (declaration and candidate identical): no retry consumed.
		const unchanged = postToolResult({ ...fx.ctx, event: { toolName: "read", isError: false } });
		expect(unchanged.ok).toBe(true);
		expect(statePatch(unchanged).message).toContain("unchanged artifact content");
		expect(statePatch(unchanged).checkpoint?.data.retries.extract_lessons).toBe(1);

		// Only the candidate changes (declaration byte-identical): the joint gate
		// digest differs, the gate reruns, the second failure exhausts the gate.
		writeFileSync(join(candidateDir, "proposal.json"), '{"schema": "sure.memory.proposal.v2"}\n', "utf-8");
		const second = postToolResult(fx.ctx);
		expect(second.ok).toBe(true);
		expect(second.repair).toBeUndefined();
		const patch = statePatch(second);
		expect(patch.checkpoint?.data.currentUnit).toBe("run_report");
		expect(patch.checkpoint?.data.completedUnits).toContain("extract_lessons");
		expect(patch.checkpoint?.data.retries.extract_lessons).toBeUndefined();
		expect(patch.checkpoint?.data.memory?.extractionStatus).toBe("failed");
		expect(patch.diagnostics?.some((d) => d.message.startsWith("extraction: failed ("))).toBe(true);
		// Same fixed wording as sure_onboard (Task 13); the handbook quotes it once.
		expect(patch.message).toBe(
			`Extraction gate "extract_lessons" exhausted ${MEMORY_CONFIG.extraction_gate_max_failures} blocked attempts; extraction marked failed, advanced to unit "run_report".`,
		);
		expect(patch.message ?? "").not.toContain("finish with status failed");
		persist(fx, second);

		// The exhausted block of preToolCall cannot fire: the state machine already moved on.
		const gateCall = preToolCall({
			...fx.ctx,
			point: "pre_tool_call",
			event: { toolName: "bash", input: { command: "python3 scripts/check_memory_extraction.py --run-dir ." } },
		});
		expect(gateCall.ok).toBe(true);
	});

	it("extract_lessons still exhausts and auto-advances when memory config.json cannot be read", () => {
		// failOrRetry reads memoryConfigOrUndefined() (not the throwing loadMemoryConfig()) precisely
		// so an unreadable config.json can never take the run down. When it comes back undefined the
		// unit must fall back to the state machine's own retryExhausted cap (DEFAULT_MAX_RETRIES = 2
		// in sure_eval/hooks/checkpoints.ts) instead of isExtractionGateExhausted's config-driven cap
		// (extraction_gate_max_failures, also 2 here) -- the two happen to coincide for eval, so the
		// attempt count alone cannot tell the fallback path from the normal one. What this test pins
		// is that the fallback branch itself is exercised (memoryConfigOrUndefined mocked to
		// undefined for the whole run) and still reaches the same terminal outcome: the unit
		// auto-advances with extractionStatus=failed rather than blocking forever. Every other memory
		// call in this run (injectOnBlock, settleOnTerminalFailure, the gate script itself) still
		// reads the real config.json from disk; only the one export failOrRetry calls is mocked.
		const fx = fixture("extract-exhaust-no-config");
		passAssessment(fx);
		writeDeclaration(fx, { no_new_lessons: false, no_lessons_reason: null, candidates: ["01-bad"] });
		const candidateDir = join(fx.runDir, "artifacts", "candidates", "01-bad");
		mkdirSync(candidateDir, { recursive: true });
		writeFileSync(join(candidateDir, "proposal.json"), "{}\n", "utf-8");
		writeFileSync(join(candidateDir, "proposal.md"), "# Bad candidate\n", "utf-8");

		const spy = vi.spyOn(memoryHooksModule, "memoryConfigOrUndefined").mockReturnValue(undefined);
		try {
			const first = postToolResult(fx.ctx);
			expect(first.ok).toBe(false); // attempt 1 of the fallback cap (2) -- an ordinary block
			const afterFirst = persist(fx, first);
			expect(afterFirst.retries.extract_lessons).toBe(1);

			// Only the candidate changes: the joint gate digest differs, the gate reruns, and this
			// second failure hits the fallback cap.
			writeFileSync(join(candidateDir, "proposal.json"), '{"schema": "sure.memory.proposal.v2"}\n', "utf-8");
			const second = postToolResult(fx.ctx);
			// Attempt 2 hits the fallback cap: the unit auto-advances (never a bare retry-forever
			// loop), the same observable outcome as the config-driven exhaustion path.
			expect(second.ok).toBe(true);
			const patch = statePatch(second);
			expect(patch.checkpoint?.data.currentUnit).toBe("run_report");
			expect(patch.checkpoint?.data.completedUnits).toContain("extract_lessons");
			expect(patch.checkpoint?.data.retries.extract_lessons).toBeUndefined();
			expect(patch.checkpoint?.data.memory?.extractionStatus).toBe("failed");
			expect(patch.diagnostics?.some((d) => d.message.startsWith("extraction: failed ("))).toBe(true);
		} finally {
			spy.mockRestore();
		}
	});

	it("a no_new_lessons declaration passes the gate and leaves extractionStatus unset", () => {
		const fx = fixture("extract-pass");
		const entered = passAssessment(fx);
		writeDeclaration(fx);
		const result = postToolResult(fx.ctx);
		expect(result.ok, result.repair).toBe(true);
		const data = persist(fx, result);
		expect(data.currentUnit).toBe("run_report");
		expect(data.completedUnits).toContain("extract_lessons");
		expect(data.memory?.extractionStatus).toBeUndefined();
		expect(data.memory?.digestSha256).toBe(entered.memory?.digestSha256);
	});

	// eval's generic retry ceiling is 2, so the SECOND real block of a unit is
	// always the exhausted one. The three tests below therefore split §8.1 as:
	// block -> read -> pass (activated), block -> pass (unattributed), block ->
	// block (dedup + disputed).
	const READY_ARTIFACT = { readiness: "ready", model_dir: "/srv/sure/models/demo_asr" };
	const AT_ROUTING: CheckpointData = {
		currentUnit: "tool_readiness_routing",
		completedUnits: ["task_classification"],
		retries: {},
	};

	// First real block at tool_readiness_routing with the entry in the index; returns the hook result.
	function blockOnce(fx: Fixture) {
		writeIndex(fx, [indexEntry()]);
		appendEvents(fx, BASE_EVENTS.slice(0, 2));
		seedCheckpoint(fx, AT_ROUTING);
		writeArtifact(fx, "tool_readiness_routing.json", handoffArtifact());
		const first = postToolResult(fx.ctx);
		expect(first.ok).toBe(false);
		return first;
	}

	it("injects a matching entry into the repair, records usage, and settles useful_activated on pass", () => {
		const fx = fixture("inject-activated");
		const first = blockOnce(fx);
		expect(first.repair).toContain("handoff_to_tool_agent");
		expect(first.repair).toContain(MEMORY_CONFIG.inject_header);
		expect(first.repair).toContain(ENTRY_TITLE);
		const firstPatch = statePatch(first);
		expect(firstPatch.diagnostics?.[0]?.repair).toContain("handoff_to_tool_agent");
		expect(firstPatch.diagnostics?.[0]?.repair ?? "").not.toContain(MEMORY_CONFIG.inject_header);
		const afterFirst = persist(fx, first);
		expect(afterFirst.retries.tool_readiness_routing).toBe(1);
		expect(afterFirst.memory?.injected?.tool_readiness_routing).toEqual([ENTRY_ID]);
		let usage = readUsage(fx);
		expect(usage.filter((row) => row.kind === "inject")).toHaveLength(1);
		const inject = usage[0];
		expect(inject.run_id).toBe(fx.runId);
		expect(inject.skill).toBe("sure_eval");
		expect(inject.unit).toBe("tool_readiness_routing");
		expect(inject.attempt).toBe(1);
		expect(inject.events_cutoff).toBe(2);
		expect(inject.entries).toEqual([{ entry_id: ENTRY_ID, shared: false }]);

		// An unchanged artifact neither injects nor settles (ok:true, retry not consumed).
		const unchanged = postToolResult({ ...fx.ctx, event: { toolName: "read", isError: false } });
		expect(unchanged.ok).toBe(true);
		expect(statePatch(unchanged).message).toContain("unchanged artifact content");
		expect(readUsage(fx)).toHaveLength(1);

		// The agent reads the entry file, then the unit passes: useful_activated.
		appendEvents(fx, [
			{ type: "tool_call", data: { toolName: "read", toolCallId: "c9", input: { path: ENTRY_PATH } } },
			{ type: "tool_result", data: { toolName: "read", toolCallId: "c9", isError: false } },
		]);
		writeArtifact(fx, "tool_readiness_routing.json", READY_ARTIFACT);
		const passed = postToolResult(fx.ctx);
		expect(passed.ok, passed.repair).toBe(true);
		expect(statePatch(passed).checkpoint?.data.currentUnit).toBe("plan");
		usage = readUsage(fx);
		const settle = usage.filter((row) => row.kind === "settle");
		expect(settle).toHaveLength(1);
		expect(settle[0].entry_id).toBe(ENTRY_ID);
		expect(settle[0].unit).toBe("tool_readiness_routing");
		expect(settle[0].outcome).toBe("useful_activated");
	});

	it("settles useful_unattributed when the entry was never read", () => {
		const fx = fixture("inject-unattributed");
		persist(fx, blockOnce(fx));
		writeArtifact(fx, "tool_readiness_routing.json", READY_ARTIFACT);
		const passed = postToolResult(fx.ctx);
		expect(passed.ok, passed.repair).toBe(true);
		const settle = readUsage(fx).filter((row) => row.kind === "settle");
		expect(settle).toHaveLength(1);
		expect(settle[0].outcome).toBe("useful_unattributed");
	});

	it("does not inject the same entry twice and settles disputed when the unit exhausts on a trigger hit", () => {
		const fx = fixture("inject-disputed");
		persist(fx, blockOnce(fx));
		writeArtifact(fx, "tool_readiness_routing.json", handoffArtifact({ routing_reason: "second try" }));
		const exhausted = postToolResult(fx.ctx);
		expect(exhausted.ok).toBe(false);
		expect(exhausted.repair).toContain("FAILED after 2 retries");
		// The Memory block stays last (the still-apply line for the entry already shown),
		// so strip_memory_block cannot swallow the FAILED sentence with it.
		expect(exhausted.repair).toContain(MEMORY_CONFIG.inject_header);
		expect((exhausted.repair ?? "").indexOf("FAILED after 2 retries")).toBeLessThan(
			(exhausted.repair ?? "").indexOf(MEMORY_CONFIG.inject_header),
		);
		// Dedup: the title (the injected line) does not appear again; usage keeps one inject row.
		expect((exhausted.repair ?? "").split(ENTRY_TITLE).length - 1).toBe(0);
		const patch = statePatch(exhausted);
		// Hand-built exhausted patch (no failure()): diagnostics keep the raw gate
		// repair, and the message keeps the prefix digest.py uses to spot a unit's
		// terminal failure.
		expect(patch.message).toMatch(/^Gate "tool_readiness_routing" exhausted/);
		expect(patch.diagnostics?.[0]?.message).toBe(patch.message);
		expect(patch.diagnostics?.[0]?.repair ?? "").not.toContain(MEMORY_CONFIG.inject_header);
		expect(patch.checkpoint?.data.retries.tool_readiness_routing).toBe(2);
		const usage = readUsage(fx);
		expect(usage.filter((row) => row.kind === "inject")).toHaveLength(1);
		const settle = usage.filter((row) => row.kind === "settle");
		expect(settle).toHaveLength(1);
		expect(settle[0].entry_id).toBe(ENTRY_ID);
		expect(settle[0].unit).toBe("tool_readiness_routing");
		expect(settle[0].outcome).toBe("disputed");
	});
});

describe.skipIf(!PYTHON_BIN)("sure_eval preFinish memory wiring", () => {
	it("failed finish without a declaration: two repairs then let go with extractionStatus failed", () => {
		const fx = fixture("finish-three");
		seedFailedFinish(fx);
		const ctx = finishCtx(fx, "failed");

		const first = preFinish(ctx);
		expect(first.ok).toBe(false);
		expect(first.repair).toContain("extraction_declaration.json");
		const afterFirst = persist(fx, first);
		expect(afterFirst.memory?.finishAttempts).toBe(1);
		expect(afterFirst.memory?.digestSha256).toMatch(/^[0-9a-f]{64}$/);
		expect(existsSync(join(fx.runDir, "artifacts", "run_digest.json"))).toBe(true);

		const second = preFinish(ctx);
		expect(second.ok).toBe(false);
		const afterSecond = persist(fx, second);
		expect(afterSecond.memory?.finishAttempts).toBe(2);

		const third = preFinish(ctx);
		expect(third.ok, third.repair).toBe(true);
		const patch = statePatch(third);
		expect(patch.checkpoint?.data.memory?.extractionStatus).toBe("failed");
		expect(patch.diagnostics?.some((d) => /extraction/i.test(d.message))).toBe(true);
	});

	it("hands the harness a non-success finish patch it accepts, extraction verdict included", () => {
		// The sure_onboard twin of this patch carried artifacts[].status "blocked", which the
		// normalizer does not accept for an artifact, so the harness dropped the patch whole and
		// extractionStatus="failed" never reached state.json - after which post_finish published
		// candidates no gate had seen. sure_eval's own finish patch has to survive that same trip,
		// and the two skills have diverged on this kind of thing before.
		const fx = fixture("finish-through-harness");
		seedFailedFinish(fx);
		const ctx = finishCtx(fx, "failed");
		expect(persistThroughHarness(fx, preFinish(ctx))).toBeUndefined();
		expect(persistThroughHarness(fx, preFinish(ctx))).toBeUndefined();
		const third = preFinish(ctx);
		expect(third.ok, third.repair).toBe(true);
		expect(persistThroughHarness(fx, third)).toBeUndefined();
		// Read back out of state.json, not out of the patch: this is what post_finish sees.
		const stored = JSON.parse(readFileSync(join(fx.runDir, "state.json"), "utf-8")) as {
			checkpoint: { data: CheckpointData };
		};
		expect(stored.checkpoint.data.memory?.extractionStatus).toBe("failed");
	});

	it("keeps the digest diagnostic on the blocking path", () => {
		// Same as sure_onboard's: the repair sends the agent to artifacts/run_digest.json, and
		// when the build failed only the digest diagnostic says that file holds an error and that
		// no_new_lessons=true citing it is acceptable. failure()'s fixed one-element diagnostics
		// array used to throw it away.
		const fx = fixture("finish-digest-failure");
		seedFailedFinish(fx);
		rmSync(join(fx.packageDir, "scripts", "build_run_digest.py"));

		const blocked = preFinish(finishCtx(fx, "failed"));
		expect(blocked.ok).toBe(false);
		expect(blocked.repair).toContain("run_digest.json");
		const patch = statePatch(blocked);
		expect(patch.phase).toMatchObject({ id: "gate", status: "blocked" });
		expect(patch.diagnostics?.[0]).toMatchObject({
			severity: "error",
			message: "Non-success Eval finish requires an extraction declaration.",
		});
		const digestDiagnostic = patch.diagnostics?.find((item) => item.message.startsWith("memory digest failed"));
		expect(digestDiagnostic?.severity).toBe("warning");
		expect(digestDiagnostic?.repair).toContain("no_new_lessons=true");
		const digest = JSON.parse(readFileSync(join(fx.runDir, "artifacts", "run_digest.json"), "utf-8"));
		expect(Object.keys(digest).sort()).toEqual(["error", "schema"]);
		expect(patch.checkpoint?.data.memory?.finishAttempts).toBe(1);
	});

	it("failed finish with a valid declaration passes on the first call", () => {
		const fx = fixture("finish-declared");
		seedFailedFinish(fx);
		writeDeclaration(fx);
		const result = preFinish(finishCtx(fx, "failed"));
		expect(result.ok, result.repair).toBe(true);
		const patch = statePatch(result);
		expect(patch.checkpoint?.data.memory?.finishAttempts).toBeUndefined();
		expect(patch.checkpoint?.data.memory?.extractionStatus).toBeUndefined();
	});

	it("success finish does not require an extraction declaration", () => {
		// check_run_report.py never sees the finish status, so the failed-shaped
		// report is enough to get past the terminal backstop; the hook logic is what
		// is under test here.
		const fx = fixture("finish-success");
		seedFailedFinish(fx, { currentUnit: "run_report", completedUnits: UNITS_THROUGH_EXTRACT, retries: {} });
		const result = preFinish(finishCtx(fx, "success"));
		expect(result.ok, result.repair).toBe(true);
		expect(statePatch(result).checkpoint?.data.memory?.finishAttempts).toBeUndefined();
		expect(existsSync(join(fx.runDir, "artifacts", "run_digest.json"))).toBe(false);
	});

	it("failed finish after extract_lessons already completed does not ask for a declaration again", () => {
		// The unit passed (or was auto-advanced as exhausted) earlier in the run: no
		// second extraction round at finish, no digest rebuilt, no attempts counted.
		const fx = fixture("finish-after-extract");
		seedFailedFinish(fx, {
			currentUnit: "run_report",
			completedUnits: UNITS_THROUGH_EXTRACT,
			retries: {},
			memory: { digestSha256: "a".repeat(64), digestCutoff: 3, digestPassed: "assessment" },
		});
		const result = preFinish(finishCtx(fx, "failed"));
		expect(result.ok, result.repair).toBe(true);
		const patch = statePatch(result);
		expect(patch.checkpoint?.data.memory?.finishAttempts).toBeUndefined();
		expect(patch.checkpoint?.data.memory?.extractionStatus).toBeUndefined();
		expect(patch.checkpoint?.data.memory?.digestSha256).toBe("a".repeat(64));
		expect(existsSync(join(fx.runDir, "artifacts", "run_digest.json"))).toBe(false);
	});

	it("settles a stuck unit's pending disputed entries only when the finish is accepted", () => {
		const fx = fixture("finish-settle-disputed");
		writeIndex(fx, [indexEntry({ component: "smoke_test" })]);
		writeInjectRow(fx, "smoke_test");
		seedFailedFinish(fx, {
			memory: { injected: { smoke_test: [ENTRY_ID] }, pendingDisputed: { smoke_test: [ENTRY_ID] } },
		});
		// Rejected finish (no declaration yet): the run is not over, nothing settles.
		const rejected = preFinish(finishCtx(fx, "failed"));
		expect(rejected.ok).toBe(false);
		expect(readUsage(fx).filter((row) => row.kind === "settle")).toHaveLength(0);
		persist(fx, rejected);
		// Accepted finish: smoke_test is the unit the run died on, its pending entry is disputed.
		writeDeclaration(fx);
		const result = preFinish(finishCtx(fx, "failed"));
		expect(result.ok, result.repair).toBe(true);
		const settle = readUsage(fx).filter((row) => row.kind === "settle");
		expect(settle).toHaveLength(1);
		expect(settle[0].unit).toBe("smoke_test");
		expect(settle[0].outcome).toBe("disputed");
		expect(statePatch(result).checkpoint?.data.memory?.pendingDisputed?.smoke_test).toBeUndefined();
	});
});

describe.skipIf(!PYTHON_BIN)("sure_eval postFinish / onError memory wiring", () => {
	it("post_finish spawns scripts/publish_memory.py with --run-dir and --repo-root", () => {
		const fx = fixture("publish-called", { publishStub: true });
		passAssessment(fx);
		writeDeclaration(fx);
		persist(fx, postToolResult(fx.ctx));
		const result = postFinish(postFinishCtx(fx));
		expect(result.ok).toBe(true);
		const marker = join(fx.runDir, "publish_called.json");
		expect(existsSync(marker)).toBe(true);
		const argv = JSON.parse(readFileSync(marker, "utf-8")) as string[];
		expect(argv[argv.indexOf("--run-dir") + 1]).toBe(fx.runDir);
		expect(argv[argv.indexOf("--repo-root") + 1]).toBe(fx.repoRoot);
	});

	it("post_finish skips publish when extractionStatus is failed", () => {
		const fx = fixture("publish-skipped", { publishStub: true });
		seedCheckpoint(fx, {
			currentUnit: "run_report",
			completedUnits: [...UNITS_THROUGH_EXTRACT, "run_report"],
			retries: {},
			memory: { extractionStatus: "failed" },
		});
		const result = postFinish(postFinishCtx(fx));
		expect(result.ok).toBe(true);
		expect(existsSync(join(fx.runDir, "publish_called.json"))).toBe(false);
	});

	it("post_finish with the real publish wrapper stays ok on a no_new_lessons run", () => {
		const fx = fixture("publish-real");
		passAssessment(fx);
		writeDeclaration(fx);
		persist(fx, postToolResult(fx.ctx));
		const result = postFinish(postFinishCtx(fx));
		expect(result.ok).toBe(true);
		const errors = (statePatch(result).diagnostics ?? []).filter((d) => d.severity === "error");
		expect(errors).toEqual([]);
	});

	it("on_error leaves a run digest behind and publishes nothing", () => {
		const fx = fixture("on-error-digest");
		appendEvents(fx, BASE_EVENTS.slice(0, 2));
		seedCheckpoint(fx, { currentUnit: "smoke_test", completedUnits: UNITS_BEFORE_SMOKE, retries: { smoke_test: 1 } });
		const result = onError({ ...fx.ctx, point: "on_error", event: { reason: "agent_error", message: "boom" } });
		expect(result.ok).toBe(true);
		expect(statePatch(result).phase?.status).toBe("failed");
		const digestPath = join(fx.runDir, "artifacts", "run_digest.json");
		expect(existsSync(digestPath)).toBe(true);
		const digest = JSON.parse(readFileSync(digestPath, "utf-8")) as { schema: string; run: { cutoff: number } };
		expect(digest.schema).toBe("sure.memory.run_digest.v1");
		expect(digest.run.cutoff).toBe(2);
		expect(existsSync(join(fx.memoryRoot, "decisions.jsonl"))).toBe(false);
	});

	it("on_error keeps a digest the checkpoint's digestSha256 is bound to", () => {
		// The gate validated candidates against this exact file (rule 9). onError writes no
		// checkpoint, so a rebuild here would leave digestSha256 pointing at bytes that no longer
		// exist, and the hand recovery (publish_memory.py --run-dir) would derive hook_trigger
		// from a digest the gate never saw.
		const fx = fixture("on-error-keeps-gated-digest");
		appendEvents(fx, BASE_EVENTS.slice(0, 2));
		const digestPath = join(fx.runDir, "artifacts", "run_digest.json");
		const built = spawnSync(
			PYTHON_BIN ?? "python3",
			[
				join(fx.packageDir, "scripts", "build_run_digest.py"),
				"--run-dir",
				fx.runDir,
				"--repo-root",
				fx.repoRoot,
				"--cutoff",
				"0",
				"--mark-passed",
				"assessment",
			],
			{ encoding: "utf-8" },
		);
		expect(built.status, built.stderr).toBe(0);
		const before = readFileSync(digestPath, "utf-8");
		const sha = createHash("sha256").update(readFileSync(digestPath)).digest("hex");
		seedCheckpoint(fx, {
			currentUnit: "run_report",
			completedUnits: UNITS_THROUGH_EXTRACT,
			retries: {},
			memory: { digestCutoff: 0, digestSha256: sha, digestPassed: "assessment" },
		});
		const result = onError({ ...fx.ctx, point: "on_error", event: { reason: "agent_error", message: "boom" } });
		expect(result.ok).toBe(true);
		expect(readFileSync(digestPath, "utf-8")).toBe(before);
		expect(statePatch(result).diagnostics ?? []).toEqual([]);
	});
});

describe("sure_eval memory helpers", () => {
	it("evalProductDir reads runtime.run_dir from eval_input_resolved.json", () => {
		const fx = fixture("product-dir");
		expect(evalProductDir(fx.ctx)).toBeUndefined();
		writeArtifact(fx, "eval_input_resolved.json", {
			schema: "sure.eval.input_resolved.v1",
			user_input: { model: "demo_asr", datasets: ["aishell1__v1"] },
			runtime: { run_id: "main_agent_demo", run_dir: "/data/results/demo_asr/standard_system/r1" },
		});
		expect(evalProductDir(fx.ctx)).toBe("/data/results/demo_asr/standard_system/r1");
	});
});
