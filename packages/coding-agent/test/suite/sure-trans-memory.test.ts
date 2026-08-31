import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { loadMemoryConfig } from "../../../../sure/runtime/memory/match.ts";
import type { CheckpointData } from "../../../../sure/skills/sure_trans/hooks/checkpoints.ts";
import { onError, postToolResult, preFinish } from "../../../../sure/skills/sure_trans/hooks/index.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// Wiring tests for the memory system inside the sure_trans hooks (plan §4). The fixture is a
// throwaway repo whose sure/skills/sure_trans is the packageDir the hooks see, so memoryRootFor
// lands inside the temp tree and the scripts/ wrappers call the real shared library; that needs
// nothing but a python 3 on PATH, the memory scripts being stdlib only. Anything that would go
// through runBackend (docker, vc) is out of scope here and covered by the other trans suites.

const REPO_ROOT = resolve(__dirname, "../../../..");
const REAL_PACKAGE_DIR = join(REPO_ROOT, "sure", "skills", "sure_trans");
const TMP = resolve(__dirname, "tmp-trans-memory");
const CONFIG = loadMemoryConfig();

const UNITS_BEFORE_VERDICT = [
	"load_trans_input",
	"inspect_dependencies",
	"detect_framework",
	"prepare_fixture",
	"build_source_image",
	"validate_env_compat",
	"validate_original_inference",
	"stage_model_payload",
	"generate_adapter",
	"build_adapter_image",
	"validate_import",
	"validate_load",
	"validate_infer",
	"validate_contract",
	"validate_mcp",
	"validate_equivalence",
	"package_container",
	"write_runtime_inventory",
];

const MODEL_NAME = "openai__whisper-large-v3";
// /sure_trans is called with host paths: the digest must keep them out of run.args (plan D2).
const TRANS_ARGS = `dockerfile=/srv/build/Dockerfile model=/srv/weights/whisper inference_entrypoint=/srv/code/infer.py framework=pytorch model_framework=transformers model_name=${MODEL_NAME} task_type=asr`;

const ENTRY_ID = "sure_trans/contract-run-command";
const INDEX_ENTRY = {
	entry_id: ENTRY_ID,
	type: "bad_case",
	status: "confirmed",
	target_skill: "sure_trans",
	applies_to: ["sure_trans"],
	component: "validate_contract",
	cause: "wrong_entrypoint",
	trigger: ['missing required field "run_command"'],
	hook_trigger: ['missing required field "run_command"'],
	scope: null,
	title: "The contract stage needs the command it actually ran",
	path: "sure/skills/sure_trans/references/memory/bad_cases/contract-run-command.md",
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

beforeAll(() => {
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
	// Same shape as the shipped wrapper except the sys.path entry is absolute: this fake
	// package does not sit inside the real repo, so parents[3] would miss sure/runtime.
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

function fakeRepo(name: string, args = TRANS_ARGS): Fixture {
	const base = join(TMP, name);
	rmSync(base, { recursive: true, force: true });
	const repoRoot = join(base, "repo");
	const packageDir = join(repoRoot, "sure", "skills", "sure_trans");
	const runDir = join(repoRoot, ".sure", "runs", name);
	mkdirSync(join(packageDir, "scripts"), { recursive: true });
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	cpSync(join(REAL_PACKAGE_DIR, "schemas"), join(packageDir, "schemas"), { recursive: true });
	writeWrapper(packageDir, "build_run_digest.py", "digest");
	writeWrapper(packageDir, "check_memory_extraction.py", "proposals");
	// Records its argv; publish semantics are covered by sure/runtime/memory/test_publish.py.
	writeFileSync(
		join(packageDir, "scripts", "publish_memory.py"),
		["import json, sys", "from pathlib import Path", "args = sys.argv[1:]", "print(json.dumps(args))", ""].join("\n"),
		"utf-8",
	);
	const ctx: SureHookContext = {
		point: "post_tool_result",
		run: { id: name, command: "/sure_trans", status: "running" } as never,
		skill: { name: "sure_trans", command: "/sure_trans" } as never,
		cwd: repoRoot,
		packageDir,
		runDir,
		args,
		event: { isError: false },
	} as SureHookContext;
	return { ctx, runDir, repoRoot, packageDir, memoryRoot: join(repoRoot, "sure", "memory") };
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

function seedEvents(fixture: Fixture, count: number): void {
	const lines: string[] = [];
	for (let i = 0; i < count; i++) {
		const timestamp = `2026-08-31T00:00:${String(i).padStart(2, "0")}Z`;
		if (i === 0) {
			lines.push(
				JSON.stringify({
					type: "created",
					timestamp,
					data: { runId: basename(fixture.runDir), skillName: "sure_trans", args: fixture.ctx.args },
				}),
			);
		} else {
			lines.push(
				JSON.stringify({
					type: i % 2 === 1 ? "tool_call" : "tool_result",
					timestamp,
					data: { toolName: "bash", toolCallId: `c${i - (i % 2 === 1 ? 0 : 1)}`, isError: false },
				}),
			);
		}
	}
	writeFileSync(join(fixture.runDir, "events.jsonl"), `${lines.join("\n")}\n`, "utf-8");
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

// no_new_lessons together with a declared candidate violates gate rule 10.
function inconsistentDeclaration(): Record<string, unknown> {
	return { ...validDeclaration(), candidates: ["01-demo"] };
}

// Everything the state machine needs to stand on extract_lessons: the resolved input the digest
// reads the target id from, an events file to take a cutoff from, and the digest itself.
function seedExtractLessons(fixture: Fixture): string {
	writeArtifact(fixture.runDir, "trans_input_resolved.json", {
		schema: "sure.trans.input.v2",
		model_name: MODEL_NAME,
		model_dir: join(fixture.repoRoot, "sure", "models", MODEL_NAME),
	});
	seedEvents(fixture, 4);
	const r = spawnSync(
		process.env.HARNESS_PYTHON_BIN ?? "python3",
		[
			join(fixture.packageDir, "scripts", "build_run_digest.py"),
			"--run-dir",
			fixture.runDir,
			"--repo-root",
			fixture.repoRoot,
			"--cutoff",
			"4",
			"--skill",
			"sure_trans",
			"--mark-passed",
			"verdict",
		],
		{ encoding: "utf-8" },
	);
	expect(r.status, r.stderr || r.stdout).toBe(0);
	const sha = JSON.parse(r.stdout).sha256 as string;
	seedCheckpoint(fixture.runDir, {
		currentUnit: "extract_lessons",
		completedUnits: [...UNITS_BEFORE_VERDICT, "verdict"],
		retries: {},
		failedArtifactDigests: {},
		memory: { digestCutoff: 4, digestSha256: sha, digestPassed: "verdict" },
	});
	return sha;
}

// preFinish's own preconditions, none of them memory: a valid runtime binding and a blocked
// deployment marker. Only then does the extraction requirement get a say.
function seedNonSuccessFinish(fixture: Fixture): void {
	const manifest = join(fixture.repoRoot, "runtime-manifest.json");
	writeFileSync(manifest, JSON.stringify({ runtime_id: "harness-test", lock_sha256: "a".repeat(64) }), "utf-8");
	writeArtifact(fixture.runDir, "runtime_binding.json", {
		schema: "sure.skill.runtime_binding.v1",
		skill: "sure_trans",
		runtimes: {
			harness: {
				required: true,
				role: "Gates.",
				binding: {
					schema: "sure.harness.runtime.binding.v1",
					runtime_type: "harness_python",
					runtime_id: "harness-test",
					python_executable: manifest,
					manifest_path: manifest,
					lock_sha256: "a".repeat(64),
				},
			},
			model: { required: false, reason: "Container only." },
			evaluation: { required: false, reason: "sure_trans performs no evaluation." },
		},
	});
	writeArtifact(fixture.runDir, "deployment_ready.json", {
		schema: "sure.onboard.deployment_ready.v1",
		generated_at: "2026-08-31T00:00:00Z",
		status: "blocked",
		blocked_reason: "source image never built",
		model_name: MODEL_NAME,
		package_profile: "none",
		execution_policy: { container_only: false },
		required_artifact_sha256: {},
		bundle_identity_sha256: "0".repeat(64),
	});
}

describe("sure_trans memory wiring: the extraction gate", () => {
	it("passes a valid declaration and keeps checkpoint memory across advance", () => {
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

	it("advances with extractionStatus failed when the gate script is not there to run", () => {
		const fixture = fakeRepo("gate-unavailable");
		seedExtractLessons(fixture);
		rmSync(join(fixture.packageDir, "scripts", "check_memory_extraction.py"));
		writeArtifact(fixture.runDir, "extraction_declaration.json", inconsistentDeclaration());

		const result = postToolResult(fixture.ctx);

		expect(result.ok, result.repair).toBe(true);
		const patch = statePatch(result);
		expect(patch.checkpoint?.data.currentUnit).toBe("finalize_model_bundle");
		expect(patch.checkpoint?.data.memory?.extractionStatus).toBe("failed");
		expect(patch.diagnostics?.some((d) => d.message.includes("gate could not run"))).toBe(true);
	});

	it("auto-advances with extractionStatus failed once the extraction cap is reached", () => {
		const fixture = fakeRepo("gate-exhausted");
		seedExtractLessons(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", inconsistentDeclaration());

		let data: CheckpointData | undefined;
		for (let attempt = 1; attempt <= CONFIG.extraction_gate_max_failures; attempt++) {
			// Each attempt has to change the artifact, or the unchanged-artifact guard eats it.
			writeArtifact(fixture.runDir, "extraction_declaration.json", {
				...inconsistentDeclaration(),
				no_lessons_reason: `attempt ${attempt}`,
			});
			data = persist(fixture.runDir, postToolResult(fixture.ctx));
		}

		expect(data?.currentUnit).toBe("finalize_model_bundle");
		expect(data?.memory?.extractionStatus).toBe("failed");
		expect(data?.retries.extract_lessons).toBeUndefined();
	});

	it("repairs a malformed extraction_declaration.json instead of stalling on it", () => {
		// readArtifact returns undefined for "absent" and for "present but not JSON" alike, so
		// validateProduces reported missing and postToolResult answered ok with no repair, no
		// diagnostic and no retry: the gate never ran, its cap was never reached, and the run
		// could never finish successfully. extract_lessons is the one gated unit whose produces
		// the agent writes by hand.
		const fixture = fakeRepo("declaration-not-json");
		seedExtractLessons(fixture);
		writeFileSync(
			join(fixture.runDir, "artifacts", "extraction_declaration.json"),
			'{\n  "schema": "sure.memory.extraction.v2",\n  "no_new_lessons": true,\n',
			"utf-8",
		);

		const blocked = postToolResult(fixture.ctx);
		expect(blocked.ok).toBe(false);
		expect(blocked.repair).toContain("extraction_declaration.json is present but is not valid JSON");
		const afterBlock = persist(fixture.runDir, blocked);
		expect(afterBlock?.currentUnit).toBe("extract_lessons");
		expect(afterBlock?.retries.extract_lessons).toBe(1);

		writeArtifact(fixture.runDir, "extraction_declaration.json", validDeclaration());
		const fixed = postToolResult(fixture.ctx);
		expect(fixed.ok, fixed.repair).toBe(true);
		expect(statePatch(fixed).checkpoint?.data.currentUnit).toBe("finalize_model_bundle");
	});

	it("re-runs the gate when only a candidate file changed", () => {
		// The declaration is unchanged, so digestOf(artifact) would say "nothing moved" and the
		// unit would sit there for ever. gateInputs makes the digest cover candidates/ as well.
		const fixture = fakeRepo("gate-inputs-rerun", `${TRANS_ARGS} max_retries=4`);
		seedExtractLessons(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", inconsistentDeclaration());
		const candidate = join(fixture.runDir, "artifacts", "candidates", "01-demo");
		mkdirSync(candidate, { recursive: true });
		writeFileSync(join(candidate, "proposal.md"), "# Demo\n", "utf-8");
		persist(fixture.runDir, postToolResult(fixture.ctx));

		const unchanged = postToolResult(fixture.ctx);
		expect(statePatch(unchanged).message).toContain("unchanged artifact content");

		writeFileSync(join(candidate, "proposal.md"), "# Demo\n\nedited body\n", "utf-8");
		const rerun = postToolResult(fixture.ctx);

		expect(rerun.ok).toBe(false);
		expect(statePatch(rerun).checkpoint?.data.retries.extract_lessons).toBe(2);
	});

	it("never stops extract_lessons on the retry ceiling the other units stop on", () => {
		// noRetriesLeft returns a hard failure for any later tool result without looking at the
		// artifact. extract_lessons would deadlock there: its cap advances, it does not fail.
		const fixture = fakeRepo("no-retries-left");
		seedExtractLessons(fixture);
		writeArtifact(fixture.runDir, "extraction_declaration.json", validDeclaration());
		const seeded: CheckpointData = {
			currentUnit: "extract_lessons",
			completedUnits: [...UNITS_BEFORE_VERDICT, "verdict"],
			retries: { extract_lessons: 3, validate_contract: 3 },
			failedArtifactDigests: {},
		};
		seedCheckpoint(fixture.runDir, seeded);

		const extraction = postToolResult(fixture.ctx);
		expect(extraction.ok, extraction.repair).toBe(true);
		expect(statePatch(extraction).checkpoint?.data.currentUnit).toBe("finalize_model_bundle");

		seedCheckpoint(fixture.runDir, { ...seeded, currentUnit: "validate_contract" });
		writeArtifact(fixture.runDir, "contract_result.json", { status: "failed", run_command: ["true"] });
		const other = postToolResult(fixture.ctx);
		expect(other.ok).toBe(false);
		expect(other.repair).toContain("no retries left");
	});
});

describe("sure_trans memory wiring: injection, finish and error", () => {
	it("appends the memory block after the raw repair and records an inject row", () => {
		const fixture = fakeRepo("inject-on-block");
		seedIndex(fixture.memoryRoot, [INDEX_ENTRY]);
		seedEvents(fixture, 2);
		seedCheckpoint(fixture.runDir, {
			currentUnit: "validate_contract",
			completedUnits: [],
			retries: {},
			failedArtifactDigests: {},
		});
		writeArtifact(fixture.runDir, "contract_result.json", { status: "failed" });

		const result = postToolResult(fixture.ctx);

		expect(result.ok).toBe(false);
		const repair = result.repair ?? "";
		expect(repair).toContain('missing required field "run_command"');
		expect(repair).toContain(CONFIG.inject_header);
		expect(repair.indexOf(CONFIG.inject_header)).toBeGreaterThan(repair.indexOf("run_command"));
		expect(repair).toContain(ENTRY_ID);
		const usage = readFileSync(join(fixture.memoryRoot, "usage", `${basename(fixture.runDir)}.jsonl`), "utf-8");
		expect(usage).toContain('"kind":"inject"');
		expect(statePatch(result).checkpoint?.data.memory?.injected?.validate_contract).toEqual([ENTRY_ID]);
	});

	it("asks a non-success finish for the extraction declaration, then lets it through", () => {
		const fixture = fakeRepo("finish-needs-declaration");
		seedEvents(fixture, 2);
		seedNonSuccessFinish(fixture);
		seedCheckpoint(fixture.runDir, {
			currentUnit: "build_source_image",
			completedUnits: UNITS_BEFORE_VERDICT.slice(0, 4),
			retries: {},
			failedArtifactDigests: {},
		});
		const finishCtx = { ...fixture.ctx, point: "pre_finish", event: { finish: { status: "failed" } } } as never;

		const blocked = preFinish(finishCtx);
		expect(blocked.ok).toBe(false);
		expect(blocked.repair).toContain("extraction_declaration.json");
		const afterBlock = persist(fixture.runDir, blocked);
		expect(afterBlock?.memory?.finishAttempts).toBe(1);
		expect(existsSync(join(fixture.runDir, "artifacts", "run_digest.json"))).toBe(true);

		writeArtifact(fixture.runDir, "extraction_declaration.json", validDeclaration());
		const accepted = preFinish(finishCtx);
		expect(accepted.ok, accepted.repair).toBe(true);
		expect(statePatch(accepted).checkpoint?.data.currentUnit).toBe("build_source_image");
	});

	it("settles the unit the run died on and leaves a digest behind on_error", () => {
		const fixture = fakeRepo("on-error");
		seedIndex(fixture.memoryRoot, [INDEX_ENTRY]);
		seedEvents(fixture, 2);
		writeArtifact(fixture.runDir, "trans_input_resolved.json", {
			schema: "sure.trans.input.v2",
			model_name: MODEL_NAME,
		});
		seedCheckpoint(fixture.runDir, {
			currentUnit: "validate_contract",
			completedUnits: [],
			retries: { validate_contract: 1 },
			failedArtifactDigests: {},
			memory: { injected: { validate_contract: [ENTRY_ID] }, pendingDisputed: { validate_contract: [ENTRY_ID] } },
		});

		const result = onError({ ...fixture.ctx, point: "on_error" } as never);

		expect(result.ok).toBe(true);
		expect(existsSync(join(fixture.runDir, "artifacts", "run_digest.json"))).toBe(true);
		const usage = readFileSync(join(fixture.memoryRoot, "usage", `${basename(fixture.runDir)}.jsonl`), "utf-8");
		expect(usage).toContain('"outcome":"disputed"');
	});

	it("keeps the host paths of the invocation out of the digest it writes", () => {
		// /sure_trans takes a Dockerfile, a weights directory and an entrypoint, all absolute host
		// paths. run.args travels into the next run's prior_runs, so they are masked (plan D2).
		const fixture = fakeRepo("digest-args-masked");
		seedExtractLessons(fixture);
		const digest = JSON.parse(readFileSync(join(fixture.runDir, "artifacts", "run_digest.json"), "utf-8"));

		expect(digest.run.args).toBe(
			`dockerfile=<path> model=<path> inference_entrypoint=<path> framework=pytorch model_framework=transformers model_name=${MODEL_NAME} task_type=asr`,
		);
		expect(digest.run.target).toEqual({ kind: "model", id: MODEL_NAME });
		expect(digest.run.skill).toBe("sure_trans");
	});
});
