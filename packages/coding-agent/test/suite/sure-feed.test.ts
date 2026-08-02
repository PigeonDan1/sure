import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { advance, type CheckpointData } from "../../../../sure/skills/sure_feed/hooks/checkpoints.ts";
import { postToolResult, preFinish, preToolCall } from "../../../../sure/skills/sure_feed/hooks/index.ts";
import {
	FIRST_UNIT,
	findUnit,
	LAST_UNIT,
	MODEL_FEED_UNITS,
	nextUnit,
} from "../../../../sure/skills/sure_feed/hooks/state-machine.ts";
import { validateProduces } from "../../../../sure/skills/sure_feed/hooks/validate.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// sure_feed skill package root (repo-relative from the test file).
const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_feed");
const SCRIPTS_DIR = join(PACKAGE_DIR, "scripts");

// Minimal SureHookContext mock. runDir is a temp dir we populate with artifacts.
function makeCtx(runDir: string): SureHookContext {
	return {
		point: "post_tool_result",
		run: { id: "test-feed", command: "/sure_feed", status: "running" } as never,
		skill: { name: "sure_feed", command: "/sure_feed" } as never,
		cwd: PACKAGE_DIR,
		packageDir: PACKAGE_DIR,
		runDir,
		args: "",
	};
}

function writeArtifact(runDir: string, produces: string, value: unknown): void {
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	writeFileSync(join(runDir, "artifacts", produces), JSON.stringify(value, null, 2), "utf-8");
}

function writeDebugArtifact(runDir: string, produces: string, value: unknown): void {
	mkdirSync(join(runDir, "artifacts", "debug"), { recursive: true });
	writeFileSync(join(runDir, "artifacts", "debug", produces), JSON.stringify(value, null, 2), "utf-8");
}

// Run a gate script the same way the hook does (spawnSync, exit 0 = pass).
function runGate(script: string, runDir: string, produces: string): { ok: boolean; stderr: string } {
	const r = spawnSync(
		"python3",
		[join(SCRIPTS_DIR, script), "--run-dir", runDir, "--produces", join(runDir, "artifacts", produces)],
		{ cwd: PACKAGE_DIR, encoding: "utf-8", timeout: 30_000 },
	);
	return { ok: r.status === 0, stderr: r.stderr ?? "" };
}

function canonicalModelInput(
	modelId: string,
	options?: {
		repoUrl?: string;
		weightsSource?: string;
		taskType?: string;
		inputType?: string;
		primaryField?: string;
	},
): Record<string, unknown> {
	const taskType = options?.taskType ?? "asr";
	const primaryField = options?.primaryField ?? (taskType === "tts" || taskType === "vc" ? "audio_path" : "text");
	return {
		model_id: modelId,
		model_name: modelId.replace(/[^A-Za-z0-9]+/g, "__"),
		task_type: taskType,
		deployment_type: "local",
		repo: {
			url: options?.repoUrl ?? `https://huggingface.co/${modelId}`,
			commit: null,
		},
		weights: {
			source: options?.weightsSource ?? "huggingface",
			local_path: null,
			required: true,
			cache_policy: "model_local_first",
			local_dir_name: "checkpoints",
		},
		environment_hint: {
			preferred_backend: "uv",
			python_version: "3.10",
			requires_gpu: true,
			system_packages: ["ffmpeg", "libsndfile1"],
		},
		phase1_runtime_target: [
			"import the package",
			"load the model with minimal config",
			"run inference on a short fixture",
		],
		entrypoints: {
			import_test: "import package",
			load_test: "model = package.load_model('tiny', 'cpu')",
			infer_test: "model.transcribe('fixtures/tasks/asr/qwen3_asr_smoke/asr_en/sample_1_367-130732-0006.wav')",
		},
		fixture: {
			fixture_id: "asr/qwen3_asr_smoke/asr_en",
			fixture_source: "task_registry",
			fixture_index: "fixtures/tasks/asr/README.md",
			fixture_root: "fixtures/tasks/asr/qwen3_asr_smoke/asr_en",
			gt: "fixtures/tasks/asr/qwen3_asr_smoke/asr_en/gt.jsonl",
			audio: "fixtures/tasks/asr/qwen3_asr_smoke/asr_en/sample_1_367-130732-0006.wav",
			sample_count: 3,
			task_specific: true,
			fallback_allowed: false,
		},
		io_contract: {
			input_type: options?.inputType ?? "audio_path",
			output_type: "json",
			primary_field: primaryField,
			required_fields: [primaryField],
			nonempty_fields: [primaryField],
			json_serializable: true,
		},
	};
}

function modelInputEnvelope(
	modelId: string,
	source: "modelscope" | "huggingface" | "github",
	weightsSource: string,
	repoUrl: string,
	taskType = "asr",
): Record<string, unknown> {
	const modelInput = canonicalModelInput(modelId, { repoUrl, weightsSource, taskType });
	const fieldEvidence: Array<Record<string, unknown>> = [
		["repo.url", repoUrl],
		["weights.source", weightsSource],
		["environment_hint.preferred_backend", "uv"],
		["environment_hint.python_version", "3.10"],
		["environment_hint.requires_gpu", true],
		["phase1_runtime_target", modelInput.phase1_runtime_target],
		["entrypoints.import_test", (modelInput.entrypoints as Record<string, unknown>).import_test],
		["entrypoints.load_test", (modelInput.entrypoints as Record<string, unknown>).load_test],
		["entrypoints.infer_test", (modelInput.entrypoints as Record<string, unknown>).infer_test],
		["io_contract", modelInput.io_contract],
	].map(([field, value]) => ({
		source,
		field: "test.fixture",
		model_input_field: field,
		value,
		strength: "strong",
	}));
	fieldEvidence.push({
		source: "local",
		field: "fixture_registry.index",
		model_input_field: "fixture",
		value: "fixtures/tasks/asr/README.md",
		strength: "strong",
	});
	return {
		model_id: modelId,
		source,
		model_input: modelInput,
		evidence: [
			{
				source,
				field: source === "huggingface" ? "pipeline_tag" : source === "github" ? "README" : "tasks",
				value: taskType === "asr" ? "automatic-speech-recognition" : taskType,
				strength: "strong",
			},
			...fieldEvidence,
		],
		confidence: { overall: 0.91 },
		missing_or_weak_fields: [],
	};
}

const cleanups: Array<() => void> = [];

afterEach(() => {
	while (cleanups.length) {
		const clean = cleanups.pop();
		if (clean) clean();
	}
});

describe("sure_feed state machine", () => {
	it("declares the 7 units in scan→match→collect→convert→model-input→rank→emit order", () => {
		const ids = MODEL_FEED_UNITS.map((u) => u.id);
		expect(ids).toEqual([
			"scan_modelscope",
			"match_task",
			"collect_metadata",
			"convert_to_oref",
			"synthesize_model_input",
			"rank_and_select",
			"emit_handoff_manifest",
		]);
		// Linear/gate kinds: match_task, synthesize_model_input, and rank_and_select are gates.
		const gates = MODEL_FEED_UNITS.filter((u) => u.kind === "gate").map((u) => u.id);
		expect(gates).toEqual(["match_task", "synthesize_model_input", "rank_and_select"]);
		expect(MODEL_FEED_UNITS.length).toBe(7);
		expect(FIRST_UNIT.id).toBe("scan_modelscope");
		expect(LAST_UNIT.id).toBe("emit_handoff_manifest");
	});

	it("advances strictly one step (no skipping)", () => {
		const completed: CheckpointData = {
			currentUnit: "match_task",
			completedUnits: ["scan_modelscope"],
			retries: {},
		};
		const next = advance(findUnit("match_task")!, completed);
		expect(next?.data.currentUnit).toBe("collect_metadata");
		expect(next?.data.completedUnits).toContain("match_task");

		// nextUnit must not jump past the immediate neighbour.
		expect(nextUnit("scan_modelscope")?.id).toBe("match_task");
		expect(nextUnit("convert_to_oref")?.id).toBe("synthesize_model_input");
		expect(nextUnit("synthesize_model_input")?.id).toBe("rank_and_select");
	});

	it("clears the retry counter for a unit once it advances", () => {
		const completed: CheckpointData = {
			currentUnit: "match_task",
			completedUnits: ["scan_modelscope"],
			retries: { match_task: 2 },
		};
		const next = advance(findUnit("match_task")!, completed);
		expect(next?.data.retries.match_task).toBeUndefined();
	});
});

describe("sure_feed validateProduces (three-gate tiers)", () => {
	it("treats a missing artifact as stay-on-unit (not a hard failure)", () => {
		const ctx = makeCtx(resolve(__dirname, "tmp-missing"));
		const unit = findUnit("scan_modelscope")!;
		const result = validateProduces(ctx, unit, undefined);
		expect(result.ok).toBe(false);
		expect(result.missing).toBe(true);
	});

	it("rejects a produces missing a required field (anti step-skip)", () => {
		const ctx = makeCtx(resolve(__dirname, "tmp-reqfield"));
		const unit = findUnit("scan_modelscope")!;
		// candidates field absent.
		const result = validateProduces(ctx, unit, { scan_summary: "x" });
		expect(result.ok).toBe(false);
		expect(result.reason).toContain("missing field candidates");
	});

	it("rejects a produces carrying a forbidden later-unit field (anti step-merge)", () => {
		const ctx = makeCtx(resolve(__dirname, "tmp-forbidden"));
		const unit = findUnit("scan_modelscope")!; // forbiddenFields: selected, handoff_manifest_path
		const result = validateProduces(ctx, unit, {
			candidates: [],
			selected: [{ model_id: "m1" }], // belongs to rank_select_result
		});
		expect(result.ok).toBe(false);
		expect(result.reason).toContain("forbidden field selected");
	});

	it("accepts a compliant scan_result", () => {
		const ctx = makeCtx(resolve(__dirname, "tmp-ok"));
		const unit = findUnit("scan_modelscope")!;
		const result = validateProduces(ctx, unit, {
			source: "multi",
			candidates: [
				{ model_id: "m1", source: "modelscope", repo: "https://modelscope.cn/x/m1" },
				{ model_id: "m2", source: "huggingface", repo: "https://huggingface.co/x/m2" },
				{ model_id: "m3", source: "github", repo: "https://github.com/x/m3" },
			],
		});
		expect(result.ok).toBe(true);
	});

	it("accepts nullable schema fields declared as type arrays", () => {
		const ctx = makeCtx(resolve(__dirname, "tmp-nullable"));
		const unit = findUnit("collect_metadata")!;
		const result = validateProduces(ctx, unit, {
			models: [{ model_id: "m1", repo: "https://huggingface.co/x/m1", weights_source: null, license: null }],
		});
		expect(result.ok).toBe(true);
	});
});

// Repair-message quality: validate.ts must surface the CORRECT schema (expected
// type, enum, declared fields) inline so the agent can self-repair without
// re-reading SKILL.md. This is the explicit quality bar for schema-managing hooks.
describe("sure_feed validateProduces repair message quality", () => {
	it("missing-field repair names the field, its expected shape, AND the full schema summary", () => {
		const ctx = makeCtx(resolve(__dirname, "tmp-rq-missing"));
		const unit = findUnit("scan_modelscope")!; // schema: scan_result.schema.json
		const result = validateProduces(ctx, unit, { source: "modelscope" }); // candidates absent
		expect(result.ok).toBe(false);
		expect(result.reason).toContain("missing field candidates");
		expect(result.repair).toContain("candidates");
		expect(result.repair).toContain("Expected candidates:");
		expect(result.repair).toContain("array"); // expected type from schema
		expect(result.repair).toContain("Full expected shape:"); // full schema summary
	});

	it("value-out-of-domain repair names the field, the allowed enum, AND the bad value", () => {
		const ctx = makeCtx(resolve(__dirname, "tmp-rq-enum"));
		const unit = findUnit("scan_modelscope")!; // schema enum: source ∈ [modelscope, huggingface, github, multi]
		const result = validateProduces(ctx, unit, {
			source: "gitlab", // illegal — schema enum must reject this
			candidates: [{ model_id: "m1" }],
		});
		expect(result.ok).toBe(false);
		expect(result.reason).toContain("value out of domain");
		expect(result.repair).toContain("source");
		expect(result.repair).toContain("modelscope");
		expect(result.repair).toContain("huggingface");
		expect(result.repair).toContain("github");
		expect(result.repair).toContain("multi");
		expect(result.repair).toContain("gitlab");
	});

	it("additionalProperties:false repair lists the undeclared field AND the declared set", () => {
		const ctx = makeCtx(resolve(__dirname, "tmp-rq-extra"));
		const unit = findUnit("scan_modelscope")!; // schema sets additionalProperties:false
		const result = validateProduces(ctx, unit, {
			source: "modelscope",
			candidates: [],
			bogus_later_field: "leak", // not declared — must be caught
		});
		expect(result.ok).toBe(false);
		expect(result.reason).toContain("additional properties");
		expect(result.repair).toContain("bogus_later_field");
		expect(result.repair).toContain("additionalProperties:false");
		expect(result.repair).toContain("source"); // declared set listed
		expect(result.repair).toContain("candidates");
	});
});

describe("sure_feed gate scripts (real python3 spawnSync)", () => {
	const tmpRoot = resolve(__dirname, "tmp-gate");

	function freshRunDir(name: string): string {
		const dir = join(tmpRoot, name);
		mkdirSync(join(dir, "artifacts"), { recursive: true });
		return dir;
	}

	it("check_match_task passes when every matched candidate has match_source", () => {
		const runDir = freshRunDir("match-pass");
		writeArtifact(runDir, "match_task_result.json", {
			candidates: [
				{ model_id: "m1", match: { matched: true, match_source: "tasks", task_type: "asr" } },
				{ model_id: "m2", match: { matched: true, match_source: "custom_tag", task_type: "asr" } },
			],
		});
		const r = runGate("check_match_task.py", runDir, "match_task_result.json");
		expect(r.ok).toBe(true);
	});

	it("check_match_task fails when a matched candidate omits match_source", () => {
		const runDir = freshRunDir("match-fail");
		writeArtifact(runDir, "match_task_result.json", {
			candidates: [{ model_id: "m1", match: { matched: true } }],
		});
		const r = runGate("check_match_task.py", runDir, "match_task_result.json");
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("match_source");
	});

	it("check_model_input passes for canonical ModelScope, HuggingFace, and GitHub-derived MODEL_INPUT", () => {
		const runDir = freshRunDir("model-input-pass");
		writeArtifact(runDir, "model_input_result.json", {
			source_query: { source: "multi", query: "speech models", max_models: 3 },
			model_inputs: [
				modelInputEnvelope(
					"damo/speech_paraformer_asr",
					"modelscope",
					"modelscope",
					"https://modelscope.cn/models/damo/speech_paraformer_asr",
				),
				modelInputEnvelope(
					"openai/whisper-tiny",
					"huggingface",
					"huggingface",
					"https://huggingface.co/openai/whisper-tiny",
				),
				modelInputEnvelope(
					"owner/speech-toolkit",
					"github",
					"release_or_pypi",
					"https://github.com/owner/speech-toolkit",
				),
			],
		});
		const r = runGate("check_model_input.py", runDir, "model_input_result.json");
		expect(r.ok).toBe(true);
	});

	it("check_model_input accepts policy-resolved entrypoints only with runtime_strategy", () => {
		const runDir = freshRunDir("model-input-runtime-strategy");
		const envelope = modelInputEnvelope(
			"GilgameshWind/X-ASR-zh-en",
			"huggingface",
			"huggingface",
			"https://huggingface.co/GilgameshWind/X-ASR-zh-en",
		);
		const modelInput = envelope.model_input as Record<string, unknown>;
		modelInput.entrypoints = {
			import_test:
				"policy_resolved: use the sherpa-onnx runtime import or CLI surface selected during /sure_onboard",
			load_test: "policy_resolved: load ONNX checkpoint files from the model repository with sherpa-onnx",
			infer_test:
				"policy_resolved: run sherpa-onnx ASR inference through its Python API or CLI on the selected SURE fixture",
		};
		modelInput.runtime_strategy = {
			type: "cli_or_library",
			framework: "sherpa-onnx",
			load_surface: "load ONNX checkpoint files from the model repository with sherpa-onnx",
			inference_surface: "run sherpa-onnx ASR inference through its Python API or CLI on the selected SURE fixture",
			evidence_terms: ["sherpa-onnx", "onnx"],
		};
		const evidence = envelope.evidence as Array<Record<string, unknown>>;
		evidence.push({
			source: "local",
			field: "sure_policy.runtime_strategy",
			model_input_field: "runtime_strategy",
			value: modelInput.runtime_strategy,
			strength: "medium",
		});
		writeArtifact(runDir, "model_input_result.json", { model_inputs: [envelope] });
		const r = runGate("check_model_input.py", runDir, "model_input_result.json");
		expect(r.ok, r.stderr).toBe(true);

		const failRunDir = freshRunDir("model-input-runtime-strategy-missing");
		const brokenEnvelope = JSON.parse(JSON.stringify(envelope));
		delete brokenEnvelope.model_input.runtime_strategy;
		writeArtifact(failRunDir, "model_input_result.json", { model_inputs: [brokenEnvelope] });
		const broken = runGate("check_model_input.py", failRunDir, "model_input_result.json");
		expect(broken.ok).toBe(false);
		expect(broken.stderr).toContain("runtime_strategy is required");
	});

	it("check_model_input rejects GitHub as a default weights source", () => {
		const runDir = freshRunDir("model-input-github-weights");
		writeArtifact(runDir, "model_input_result.json", {
			model_inputs: [
				modelInputEnvelope("owner/speech-toolkit", "github", "github", "https://github.com/owner/speech-toolkit"),
			],
		});
		const r = runGate("check_model_input.py", runDir, "model_input_result.json");
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("weights.source");
		expect(r.stderr).toContain("github is a repo/evidence source");
	});

	it("check_model_input rejects partial onboarding inputs before handoff", () => {
		const runDir = freshRunDir("model-input-missing-entrypoints");
		const modelInput = canonicalModelInput("openai/whisper-tiny");
		delete (modelInput as { entrypoints?: unknown }).entrypoints;
		writeArtifact(runDir, "model_input_result.json", {
			model_inputs: [
				{
					model_id: "openai/whisper-tiny",
					source: "huggingface",
					model_input: modelInput,
					evidence: [{ source: "huggingface", field: "pipeline_tag", value: "automatic-speech-recognition" }],
				},
			],
		});
		const r = runGate("check_model_input.py", runDir, "model_input_result.json");
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("entrypoints");
	});

	it("provider unit tests pass without network", () => {
		const r = spawnSync("python3", [join(SCRIPTS_DIR, "test_providers.py")], {
			cwd: PACKAGE_DIR,
			encoding: "utf-8",
			timeout: 30_000,
		});
		expect(r.status, r.stderr || r.stdout).toBe(0);
	});

	it("check_rank_select passes for a non-empty selection with repo + score", () => {
		const runDir = freshRunDir("rank-pass");
		writeArtifact(runDir, "rank_select_result.json", {
			selected: [{ model_id: "m1", repo: "https://modelscope.cn/x/m1", score: 1.5, rank_reason: "top" }],
		});
		const r = runGate("check_rank_select.py", runDir, "rank_select_result.json");
		expect(r.ok).toBe(true);
	});

	it("check_rank_select fails when a selected candidate has no repo (handoff needs it)", () => {
		const runDir = freshRunDir("rank-fail");
		writeArtifact(runDir, "rank_select_result.json", {
			selected: [{ model_id: "m1", score: 1.5 }],
		});
		const r = runGate("check_rank_select.py", runDir, "rank_select_result.json");
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("repo");
	});

	it("check_rank_select repair names the score domain (≥ 0) and the bad value", () => {
		const runDir = freshRunDir("rank-score");
		writeArtifact(runDir, "rank_select_result.json", {
			selected: [{ model_id: "m1", repo: "https://modelscope.cn/x/m1", score: -3 }],
		});
		const r = runGate("check_rank_select.py", runDir, "rank_select_result.json");
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("score");
		expect(r.stderr).toContain(">="); // domain: score ≥ 0
		expect(r.stderr).toContain("-3"); // the offending value is echoed back
	});
});

describe("sure_feed preToolCall script ownership", () => {
	function toolCtx(command: string): SureHookContext {
		return {
			...makeCtx(resolve(__dirname, "tmp-pretool")),
			event: { type: "tool_call", toolCallId: "t1", toolName: "bash", input: { command } },
		} as SureHookContext;
	}

	it("allows the online discovery backend from the scan unit", () => {
		const result = preToolCall(toolCtx("python3 scripts/sure_feed_online_discover.py --source huggingface"));
		expect(result.ok).toBe(true);
	});

	it("blocks an out-of-order gate script from the scan unit", () => {
		const result = preToolCall(toolCtx("python3 scripts/check_rank_select.py --run-dir x --produces y"));
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("check_rank_select.py");
	});
});

// End-to-end hook pipeline: drives the REAL postToolResult through
// validateProduces + in-process gateCheck + runGateScript (which calls runBackend
// → python3 scripts/check_match_task.py --produces <abs>, with runBackend
// injecting --run-dir). This is the path that was broken before the B1 fix
// (--run-dir was never injected → argparse crash → gate always failed).
describe("sure_feed postToolResult end-to-end (real hook → gate script → advance)", () => {
	function freshCtx(name: string): { ctx: SureHookContext; runDir: string } {
		const runDir = resolve(__dirname, "tmp-e2e", name);
		mkdirSync(join(runDir, "artifacts"), { recursive: true });
		const ctx: SureHookContext = {
			point: "post_tool_result",
			run: { id: "test-e2e", command: "/sure_feed", status: "running" } as never,
			skill: { name: "sure_feed", command: "/sure_feed" } as never,
			cwd: PACKAGE_DIR,
			packageDir: PACKAGE_DIR,
			runDir,
			args: "",
		};
		return { ctx, runDir };
	}

	it("advances from match_task → collect_metadata when the gate passes (gate script actually runs)", () => {
		const { ctx, runDir } = freshCtx("advance-pass");
		// Seed the checkpoint at match_task (scan_modelscope already done).
		writeFileSync(
			join(runDir, "state.json"),
			JSON.stringify(
				{ checkpoint: { data: { currentUnit: "match_task", completedUnits: ["scan_modelscope"], retries: {} } } },
				null,
				2,
			),
			"utf-8",
		);
		writeArtifact(runDir, "match_task_result.json", {
			candidates: [{ model_id: "m1", match: { matched: true, match_source: "tasks", task_type: "asr" } }],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		// Must have advanced past match_task to collect_metadata.
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("collect_metadata");
		expect(checkpoint?.data.completedUnits).toContain("match_task");
		expect(checkpoint?.data.retries.match_task).toBeUndefined();
	});

	it("resolves --produces for the gate script without doubling the run-dir prefix on an absolute path (Windows regression)", () => {
		// runBackend's produces guard used `!val.startsWith("/")` to decide whether
		// a --produces value needed joining under runDir/artifacts/. artifactPath()
		// already returns an absolute path (e.g. Windows "E:\...\artifacts\..."),
		// which does not start with "/", so the guard joined it a second time and
		// the gate script could never find the artifact.
		const { ctx, runDir } = freshCtx("advance-absolute-produces");
		writeFileSync(
			join(runDir, "state.json"),
			JSON.stringify(
				{ checkpoint: { data: { currentUnit: "match_task", completedUnits: ["scan_modelscope"], retries: {} } } },
				null,
				2,
			),
			"utf-8",
		);
		writeArtifact(runDir, "match_task_result.json", {
			candidates: [{ model_id: "m1", match: { matched: true, match_source: "tasks", task_type: "asr" } }],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		expect(result.repair).toBeUndefined();
	});

	it("advances when the gate artifact lives under artifacts/debug", () => {
		const { ctx, runDir } = freshCtx("advance-debug-artifact-pass");
		writeFileSync(
			join(runDir, "state.json"),
			JSON.stringify(
				{ checkpoint: { data: { currentUnit: "match_task", completedUnits: ["scan_modelscope"], retries: {} } } },
				null,
				2,
			),
			"utf-8",
		);
		writeDebugArtifact(runDir, "match_task_result.json", {
			candidates: [{ model_id: "m1", match: { matched: true, match_source: "pipeline_tag", task_type: "asr" } }],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("collect_metadata");
		expect(checkpoint?.data.completedUnits).toContain("match_task");
	});

	it("advances from synthesize_model_input → rank_and_select when MODEL_INPUT gate passes", () => {
		const { ctx, runDir } = freshCtx("advance-model-input-pass");
		writeFileSync(
			join(runDir, "state.json"),
			JSON.stringify(
				{
					checkpoint: {
						data: {
							currentUnit: "synthesize_model_input",
							completedUnits: ["scan_modelscope", "match_task", "collect_metadata", "convert_to_oref"],
							retries: {},
						},
					},
				},
				null,
				2,
			),
			"utf-8",
		);
		writeArtifact(runDir, "model_input_result.json", {
			model_inputs: [
				modelInputEnvelope(
					"openai/whisper-tiny",
					"huggingface",
					"huggingface",
					"https://huggingface.co/openai/whisper-tiny",
				),
			],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("rank_and_select");
		expect(checkpoint?.data.completedUnits).toContain("synthesize_model_input");
		expect(checkpoint?.data.retries.synthesize_model_input).toBeUndefined();
	});

	it("blocks (no advance) when the gate script rejects the artifact", () => {
		const { ctx, runDir } = freshCtx("advance-block");
		writeFileSync(
			join(runDir, "state.json"),
			JSON.stringify(
				{ checkpoint: { data: { currentUnit: "match_task", completedUnits: ["scan_modelscope"], retries: {} } } },
				null,
				2,
			),
			"utf-8",
		);
		// matched:true but no match_source → gate script rejects.
		writeArtifact(runDir, "match_task_result.json", {
			candidates: [{ model_id: "m1", match: { matched: true } }],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("match_source");
		// Still on match_task (did not advance).
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("match_task");
	});

	it("fails clearly on the third consecutive blocked gate attempt", () => {
		const { ctx, runDir } = freshCtx("advance-third-block");
		writeFileSync(
			join(runDir, "state.json"),
			JSON.stringify(
				{
					checkpoint: {
						data: {
							currentUnit: "match_task",
							completedUnits: ["scan_modelscope"],
							retries: { match_task: 2 },
						},
					},
				},
				null,
				2,
			),
			"utf-8",
		);
		writeArtifact(runDir, "match_task_result.json", {
			candidates: [{ model_id: "m1", match: { matched: true } }],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("After 3 consecutive blocked attempts");
		expect(result.repair).toContain("confirm the model link");
		expect(result.state_patch?.message).toContain("exhausted 3 blocked attempts");
	});

	it("honors max_retries from the /sure_feed command line", () => {
		const { ctx, runDir } = freshCtx("advance-max-retries");
		// Same state as "fails clearly on the third consecutive blocked gate
		// attempt": retries.match_task = 2, so this call is the third attempt.
		writeFileSync(
			join(runDir, "state.json"),
			JSON.stringify(
				{
					checkpoint: {
						data: {
							currentUnit: "match_task",
							completedUnits: ["scan_modelscope"],
							retries: { match_task: 2 },
						},
					},
				},
				null,
				2,
			),
			"utf-8",
		);
		writeArtifact(runDir, "match_task_result.json", {
			candidates: [{ model_id: "m1", match: { matched: true } }],
		});
		const result = postToolResult({ ...ctx, args: "max_retries=5" });
		expect(result.ok).toBe(false);
		// Quota raised to 5: the third attempt is blocked, not terminal.
		expect(result.repair).not.toContain("After 3 consecutive blocked attempts");
		expect(result.repair).not.toContain("confirm the model link");
		// Still the real gate rejection (missing match_source), not the
		// retry-exhaustion wrapper — proves it took the blocked-but-not-exhausted branch.
		expect(result.repair).toContain("match_source");
	});

	it("stays on the unit (no block, no advance) when the artifact is not yet produced", () => {
		const { ctx, runDir } = freshCtx("advance-missing");
		writeFileSync(
			join(runDir, "state.json"),
			JSON.stringify(
				{ checkpoint: { data: { currentUnit: "match_task", completedUnits: ["scan_modelscope"], retries: {} } } },
				null,
				2,
			),
			"utf-8",
		);
		// No match_task_result.json written.
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
	});
});

describe("sure_feed preFinish terminal state", () => {
	function finishCtx(name: string): { ctx: SureHookContext; runDir: string } {
		const runDir = resolve(__dirname, "tmp-finish", name);
		mkdirSync(join(runDir, "artifacts", "debug"), { recursive: true });
		const ctx: SureHookContext = {
			point: "pre_finish",
			run: { id: "test-feed-finish", command: "/sure_feed", status: "running" } as never,
			skill: { name: "sure_feed", command: "/sure_feed" } as never,
			cwd: PACKAGE_DIR,
			packageDir: PACKAGE_DIR,
			runDir,
			args: "",
		};
		return { ctx, runDir };
	}

	function seedTerminalFeedRun(runDir: string, completedUnits: string[]): void {
		writeFileSync(
			join(runDir, "state.json"),
			JSON.stringify(
				{
					checkpoint: {
						data: {
							currentUnit: "emit_handoff_manifest",
							completedUnits,
							retries: {},
						},
					},
				},
				null,
				2,
			),
			"utf-8",
		);
		writeDebugArtifact(runDir, "handoff_manifest.json", {
			manifest_path: "sure/handoffs/Qwen__Qwen3-ASR-0.6B-hf/artifacts/handoff_manifest.json",
			source: "huggingface",
			models: [
				{
					model_id: "Qwen/Qwen3-ASR-0.6B-hf",
					repo: "https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf",
					weights_source: "huggingface",
					task_type: "asr",
					score: 1.4485,
					model_input_path: "sure/handoffs/Qwen__Qwen3-ASR-0.6B-hf/model_input.yaml",
				},
			],
			next_action: "/sure_onboard model=Qwen__Qwen3-ASR-0.6B-hf",
		});
	}

	it("does not double-count emit_handoff_manifest when finish runs after the terminal checkpoint", () => {
		const { ctx, runDir } = finishCtx("terminal-already-counted");
		seedTerminalFeedRun(runDir, [
			"scan_modelscope",
			"match_task",
			"collect_metadata",
			"convert_to_oref",
			"synthesize_model_input",
			"rank_and_select",
			"emit_handoff_manifest",
		]);
		const result = preFinish(ctx);
		expect(result.ok).toBe(true);
		expect(result.state_patch?.counters?.completed_units).toBe(7);
		expect(result.state_patch?.counters?.total_units).toBe(7);
	});
});
