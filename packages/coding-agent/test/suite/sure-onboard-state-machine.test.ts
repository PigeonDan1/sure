import { spawnSync } from "node:child_process";
import { chmodSync, existsSync, lstatSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { CheckpointData } from "../../../../sure/skills/sure_onboard/hooks/checkpoints.ts";
import {
	countersFor,
	postToolResult,
	preFinish,
	preStart,
	preToolCall,
} from "../../../../sure/skills/sure_onboard/hooks/index.ts";
import {
	FIRST_UNIT,
	findUnit,
	LAST_UNIT,
	MODEL_TOOL_UNITS,
} from "../../../../sure/skills/sure_onboard/hooks/state-machine.ts";
import { validateProduces } from "../../../../sure/skills/sure_onboard/hooks/validate.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// sure_onboard skill package root (repo-relative from the test file).
const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_onboard");

type StatePatchForTest = {
	message?: string;
	diagnostics?: Array<{ message: string }>;
	phase?: { status?: string };
};

function statePatch(result: { state_patch?: unknown }): StatePatchForTest {
	return (result.state_patch ?? {}) as StatePatchForTest;
}

function freshCtx(name: string): { ctx: SureHookContext; runDir: string } {
	const runDir = resolve(__dirname, "tmp-ob", name);
	rmSync(runDir, { recursive: true, force: true });
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	const ctx: SureHookContext = {
		point: "post_tool_result",
		run: { id: "test-ob", command: "/sure_onboard", status: "running" } as never,
		skill: { name: "sure_onboard", command: "/sure_onboard" } as never,
		cwd: PACKAGE_DIR,
		packageDir: PACKAGE_DIR,
		runDir,
		args: "",
	};
	return { ctx, runDir };
}

function seedCheckpoint(runDir: string, data: CheckpointData): void {
	writeFileSync(join(runDir, "state.json"), JSON.stringify({ checkpoint: { data } }, null, 2), "utf-8");
}

function writeArtifact(runDir: string, produces: string, value: unknown): void {
	writeFileSync(join(runDir, "artifacts", produces), JSON.stringify(value, null, 2), "utf-8");
}

function persistCheckpointFrom(runDir: string, result: unknown): CheckpointData | undefined {
	const patch = (result as { state_patch?: { checkpoint?: { data: CheckpointData } } }).state_patch;
	const data = patch?.checkpoint?.data;
	if (data) {
		seedCheckpoint(runDir, data);
	}
	return data;
}

function writeJson(path: string, value: unknown): void {
	mkdirSync(resolve(path, ".."), { recursive: true });
	writeFileSync(path, JSON.stringify(value, null, 2), "utf-8");
}

function writePythonShim(modelDir: string): string {
	const binDir = join(modelDir, ".venv", "bin");
	mkdirSync(binDir, { recursive: true });
	const pythonPath = join(binDir, "python");
	writeFileSync(pythonPath, '#!/usr/bin/env sh\nexec python3 "$@"\n', "utf-8");
	chmodSync(pythonPath, 0o755);
	return pythonPath;
}

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

function seedModelInputResolved(runDir: string, overrides: Record<string, unknown> = {}): void {
	writeArtifact(runDir, "model_input_resolved.json", {
		model_id: "owner/model",
		model_name: "owner__model",
		model_dir: join(runDir, "model-dir"),
		repo_url: "https://example.test/owner/model",
		task_type: "asr",
		deployment_type: "local",
		package_profile: "none",
		device: "auto",
		max_retries: 3,
		cpu_fallback_after_cuda_failures: 2,
		cuda_repair_attempts_before_cpu: 3,
		normalized_model_input: {},
		...overrides,
	});
}

function seedLocalReadyArtifacts(runDir: string): string {
	const modelDir = join(runDir, "model-dir");
	const modelArtifacts = join(modelDir, "artifacts");
	mkdirSync(modelArtifacts, { recursive: true });
	for (const name of ["model.spec.yaml", "model.py", "server.py", "__init__.py", "validate.py", "config.yaml"]) {
		writeFileSync(join(modelDir, name), `${name}\n`, "utf-8");
	}

	const stageResults: Record<string, unknown> = {
		"env_compat_result.json": { compat_ok: true },
		"import_result.json": { import_passed: true },
		"load_result.json": { load_passed: true },
		"infer_result.json": { infer_passed: true },
		"contract_result.json": { contract_passed: true, io_contract_satisfied: true },
	};
	for (const [name, value] of Object.entries(stageResults)) {
		writeArtifact(runDir, name, value);
		writeJson(join(modelArtifacts, name), value);
	}
	writeArtifact(runDir, "sample_output.json", { text: "hello" });
	writeJson(join(modelArtifacts, "sample_output.json"), { text: "hello" });
	writeFileSync(join(modelArtifacts, "validation.log"), "validation ok\n", "utf-8");

	const manifest = {
		model_dir: modelDir,
		artifacts: {
			required: {
				spec: { path: "model.spec.yaml" },
				model_py: { path: "model.py" },
				server_py: { path: "server.py" },
				package_init: { path: "__init__.py" },
				validate_py: { path: "validate.py" },
				config: { path: "config.yaml" },
				import_result: { path: "artifacts/import_result.json" },
				load_result: { path: "artifacts/load_result.json" },
				infer_result: { path: "artifacts/infer_result.json" },
				contract_result: { path: "artifacts/contract_result.json" },
			},
			conditional: {},
			optional: {
				sample_output: { path: "artifacts/sample_output.json" },
				validation_log: { path: "artifacts/validation.log" },
			},
		},
	};
	writeArtifact(runDir, "artifact_manifest.json", manifest);
	writeJson(join(modelArtifacts, "artifact_manifest.json"), manifest);
	return modelDir;
}

function seedPreparedFixture(runDir: string, modelDir = join(runDir, "model-dir"), task = "asr"): string {
	const fixtureDir = join(modelDir, "fixture", task, "smoke");
	mkdirSync(fixtureDir, { recursive: true });
	writeFileSync(join(fixtureDir, "sample.wav"), "not real audio\n", "utf-8");
	const row =
		task === "sa_asr"
			? {
					key: "sample",
					audio: "sample.wav",
					segments: [
						{ speaker: "spk1", start: 0.0, end: 1.0, text: "hello" },
						{ speaker: "spk2", start: 1.0, end: 2.0, text: "world" },
					],
				}
			: {
					key: "sample",
					audio: "sample.wav",
					ground_truth: "hello",
				};
	writeFileSync(join(fixtureDir, "gt.jsonl"), `${JSON.stringify(row)}\n`, "utf-8");
	writeArtifact(runDir, "fixture_manifest.json", {
		model_id: "owner/model",
		model_name: "model",
		model_dir: modelDir,
		task_type: task,
		source_dir: fixtureDir,
		staged_dir: fixtureDir,
		gt_jsonl: join(fixtureDir, "gt.jsonl"),
		sample_count: 1,
		link_policy: "copy",
		samples: [
			{
				key: "sample",
				audio: "sample.wav",
				audio_path: join(fixtureDir, "sample.wav"),
				annotation_fields: task === "sa_asr" ? ["segments"] : ["ground_truth"],
			},
		],
	});
	return modelDir;
}

function preStartCtx(name: string, args: string): { ctx: SureHookContext; cwd: string; runDir: string } {
	const root = resolve(__dirname, "tmp-ob-prestart", name);
	rmSync(root, { recursive: true, force: true });
	const cwd = join(root, "cwd");
	const runDir = join(root, "run");
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	mkdirSync(cwd, { recursive: true });
	const ctx: SureHookContext = {
		point: "pre_start",
		run: { id: "test-ob-prestart", command: "/sure_onboard", status: "running" } as never,
		skill: { name: "sure_onboard", command: "/sure_onboard" } as never,
		cwd,
		packageDir: PACKAGE_DIR,
		runDir,
		args,
	};
	return { ctx, cwd, runDir };
}

function writeModelInput(path: string): void {
	mkdirSync(resolve(path, ".."), { recursive: true });
	writeFileSync(
		path,
		`${[
			"model_id: rednote-hilab/dots.tts-base",
			"model_name: rednote-hilab__dots.tts-base",
			"task_type: tts",
			"deployment_type: local",
			"repo:",
			'  url: "https://huggingface.co/rednote-hilab/dots.tts-base"',
			"  commit: null",
			"weights:",
			"  source: huggingface",
			"  local_path: null",
			"environment_hint:",
			"  preferred_backend: uv",
			"  python_version: 3.10",
			"  requires_gpu: true",
		].join("\n")}\n`,
		"utf-8",
	);
}

describe("sure_onboard MODEL_INPUT startup", () => {
	it("loads model_input_path and auto-fills the required startup arguments", () => {
		const { ctx, cwd } = preStartCtx("model-input-path", "");
		const modelInputPath = join(cwd, "artifacts", "model_input.yaml");
		writeModelInput(modelInputPath);
		ctx.args = `model_input_path=${modelInputPath}`;
		const result = preStart(ctx);
		const patch = statePatch(result);
		expect(result.ok).toBe(true);
		expect(patch.message).toContain("rednote-hilab/dots.tts-base");
		expect(patch.message).toContain('as "rednote-hilab__dots.tts-base"');
		expect(patch.message).toContain("sure/models/rednote-hilab__dots.tts-base");
		expect(patch.message).toContain("from MODEL_INPUT");
		expect(patch.diagnostics?.some((item) => item.message.includes("Loaded MODEL_INPUT"))).toBe(true);
	});

	it("does not create the concrete model directory during preStart", () => {
		const { ctx, cwd } = preStartCtx("prestart-no-model-dir", "");
		const modelInputPath = join(cwd, "artifacts", "model_input.yaml");
		writeModelInput(modelInputPath);
		ctx.args = `model_input_path=${modelInputPath}`;
		const result = preStart(ctx);
		expect(result.ok).toBe(true);
		expect(existsSync(join(cwd, "sure", "models"))).toBe(true);
		expect(existsSync(join(cwd, "sure", "models", "rednote-hilab__dots.tts-base"))).toBe(false);
	});

	it("accepts a positional model_input.yaml path relative to cwd", () => {
		const { ctx, cwd } = preStartCtx("positional-model-input", "artifacts/model_input.yaml");
		writeModelInput(join(cwd, "artifacts", "model_input.yaml"));
		const result = preStart(ctx);
		const patch = statePatch(result);
		expect(result.ok).toBe(true);
		expect(patch.message).toContain("rednote-hilab/dots.tts-base");
	});

	it("resolves /sure_feed handoff folders by model name", () => {
		const { ctx, cwd } = preStartCtx("handoff-model-name", "model=rednote-hilab__dots.tts-base");
		const modelInputPath = join(cwd, "sure", "handoffs", "rednote-hilab__dots.tts-base", "model_input.yaml");
		writeModelInput(modelInputPath);
		const result = preStart(ctx);
		const patch = statePatch(result);
		expect(result.ok).toBe(true);
		expect(patch.message).toContain("rednote-hilab/dots.tts-base");
		expect(patch.diagnostics?.some((item) => item.message.includes(modelInputPath))).toBe(true);
	});

	it("normalizes URL-like model arguments to the handoff folder name", () => {
		const { ctx, cwd } = preStartCtx("handoff-url-name", "model=https://huggingface.co/rednote-hilab/dots.tts-base");
		writeModelInput(join(cwd, "sure", "handoffs", "rednote-hilab__dots.tts-base", "model_input.yaml"));
		const result = preStart(ctx);
		const patch = statePatch(result);
		expect(result.ok).toBe(true);
		expect(patch.message).toContain("rednote-hilab/dots.tts-base");
	});

	it("still rejects startup when neither MODEL_INPUT nor explicit required args are present", () => {
		const { ctx } = preStartCtx("missing-required", "");
		const result = preStart(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("model=<handoff_name>");
	});

	it("rejects an unsupported package profile before the run starts", () => {
		const { ctx } = preStartCtx(
			"bad-package",
			"model_id=owner/model repo=https://example.test/model task_type=asr deployment_type=local package=vc",
		);
		const result = preStart(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain('package "vc" is not one of');
	});
});

describe("sure_onboard aligned state machine", () => {
	it("uses explicit load/context/build-plan/package-gate units around the original model-tool chain", () => {
		expect(FIRST_UNIT.id).toBe("load_model_input");
		expect(LAST_UNIT.id).toBe("verdict");
		expect(MODEL_TOOL_UNITS.map((unit) => unit.id)).toEqual([
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
			"save_artifacts",
			"package_gate",
			"verdict",
		]);
	});

	it("accepts a local-first enriched repo_summary artifact", () => {
		const { ctx } = freshCtx("repo-summary-local-first");
		const unit = findUnit("discover")!;
		const result = validateProduces(ctx, unit, {
			timestamp: "2026-07-16T00:00:00Z",
			model_id: "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
			model_name: "Qwen__Qwen3-TTS-12Hz-1.7B-Base",
			repo_url: "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base",
			repo_commit: null,
			discovery_strategy: "model_local_first",
			model_dir: "/tmp/project/sure/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base",
			handoff_artifacts_dir: "/tmp/project/sure/handoffs/Qwen__Qwen3-TTS-12Hz-1.7B-Base/artifacts",
			local_path: "/tmp/project/sure/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base",
			evidence_sources: ["model_dir", "handoff_artifacts", "repo_url"],
			file_inventory: {
				wrapper_files: ["model.py", "validate.py"],
				artifacts_dir: "artifacts/",
			},
			language: "python",
			notes: "Existing local model inspected before remote evidence.",
		});
		expect(result.ok).toBe(true);
	});

	it("reports every type mismatch in one pass, not just the first", () => {
		const { ctx } = freshCtx("repo-summary-multi-type-mismatch");
		const unit = findUnit("discover")!;
		const result = validateProduces(ctx, unit, {
			repo_url: "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base",
			notes: ["a"], // should be string
			language: { x: 1 }, // should be string
		});
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("notes");
		expect(result.repair).toContain("language");
	});

	it("blocks unbounded filesystem search during discover", () => {
		const { ctx, runDir } = freshCtx("discover-wide-find");
		seedCheckpoint(runDir, {
			currentUnit: "discover",
			completedUnits: ["load_model_input", "context_selection"],
			retries: {},
		});
		writeArtifact(runDir, "model_input_resolved.json", {
			model_id: "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
			model_name: "Qwen__Qwen3-TTS-12Hz-1.7B-Base",
			model_dir: "/tmp/project/sure/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base",
			repo_url: "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base",
			task_type: "tts",
			deployment_type: "local",
			package_profile: "none",
			source: {
				handoff_artifacts_dir: "/tmp/project/sure/handoffs/Qwen__Qwen3-TTS-12Hz-1.7B-Base/artifacts",
			},
		});
		ctx.point = "pre_tool_call";
		ctx.event = {
			toolCall: {
				name: "bash",
				input: {
					command: 'find /mnt -maxdepth 6 -type d -name "Qwen3-TTS"',
				},
			},
		} as never;

		const result = preToolCall(ctx);
		const patch = statePatch(result);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("Blocked unbounded discover search");
		expect(result.repair).toContain("/tmp/project/sure/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base");
		expect(result.repair).toContain("/tmp/project/sure/handoffs/Qwen__Qwen3-TTS-12Hz-1.7B-Base/artifacts");
		expect(patch.message).toContain("Blocked unbounded discover search");
	});

	it("blocks configured shared-root filesystem search during discover", () => {
		const previousRoots = process.env.SURE_ONBOARD_BLOCKED_SEARCH_ROOTS;
		process.env.SURE_ONBOARD_BLOCKED_SEARCH_ROOTS = "/shared/site-root";
		try {
			const { ctx, runDir } = freshCtx("discover-configured-wide-find");
			seedCheckpoint(runDir, {
				currentUnit: "discover",
				completedUnits: ["load_model_input", "context_selection"],
				retries: {},
			});
			writeArtifact(runDir, "model_input_resolved.json", {
				model_id: "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
				model_name: "Qwen__Qwen3-TTS-12Hz-1.7B-Base",
				model_dir: "/tmp/project/sure/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base",
				repo_url: "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base",
				task_type: "tts",
				deployment_type: "local",
				package_profile: "none",
				source: {},
			});
			ctx.point = "pre_tool_call";
			ctx.event = {
				toolCall: {
					name: "bash",
					input: {
						command: 'find /shared/site-root/models -maxdepth 6 -type d -name "Qwen3-TTS"',
					},
				},
			} as never;

			const result = preToolCall(ctx);
			const patch = statePatch(result);
			expect(result.ok).toBe(false);
			expect(result.repair).toContain("Blocked unbounded discover search");
			expect(patch.message).toContain("Blocked unbounded discover search");
		} finally {
			if (previousRoots === undefined) {
				delete process.env.SURE_ONBOARD_BLOCKED_SEARCH_ROOTS;
			} else {
				process.env.SURE_ONBOARD_BLOCKED_SEARCH_ROOTS = previousRoots;
			}
		}
	});

	it("blocks checkpoint downloads during discover", () => {
		const { ctx, runDir } = freshCtx("discover-snapshot-download");
		seedCheckpoint(runDir, {
			currentUnit: "discover",
			completedUnits: ["load_model_input", "context_selection"],
			retries: {},
		});
		ctx.point = "pre_tool_call";
		ctx.event = {
			toolCall: {
				name: "bash",
				input: {
					command:
						"python3 -c \"from huggingface_hub import snapshot_download; snapshot_download('OpenMOSS-Team/MOSS-Transcribe-Diarize')\"",
				},
			},
		} as never;

		const result = preToolCall(ctx);
		const patch = statePatch(result);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("Blocked checkpoint download during discover");
		expect(result.repair).toContain("fetch_weights");
		expect(patch.message).toContain("Blocked heavy discover operation");
	});

	it("blocks base-python runtime dependency probes during discover", () => {
		const { ctx, runDir } = freshCtx("discover-base-python-runtime-probe");
		seedCheckpoint(runDir, {
			currentUnit: "discover",
			completedUnits: ["load_model_input", "context_selection"],
			retries: {},
		});
		ctx.point = "pre_tool_call";
		ctx.event = {
			toolCall: {
				name: "bash",
				input: {
					command: 'python3 -c "import transformers; print(transformers.__version__)"',
				},
			},
		} as never;

		const result = preToolCall(ctx);
		const patch = statePatch(result);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("Blocked runtime dependency import probe during discover");
		expect(result.repair).toContain("base Python");
		expect(result.repair).toContain("build_env");
		expect(result.repair).toContain("validate_import");
		expect(patch.message).toContain("Blocked discover runtime probe");
	});

	it("blocks long sleep polling during discover", () => {
		const { ctx, runDir } = freshCtx("discover-long-sleep");
		seedCheckpoint(runDir, {
			currentUnit: "discover",
			completedUnits: ["load_model_input", "context_selection"],
			retries: {},
		});
		ctx.point = "pre_tool_call";
		ctx.event = {
			toolCall: {
				name: "bash",
				input: {
					command: "sleep 300 && ls sure/models/OpenMOSS-Team__MOSS-Transcribe-Diarize/checkpoints",
				},
			},
		} as never;

		const result = preToolCall(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("Blocked long sleep (300s) during discover");
		expect(result.repair).toContain("repo_summary.json");
	});
});

describe("sure_onboard MODEL_INPUT materializer", () => {
	it("materializes model_input_resolved.json and context_selection.json from a handoff MODEL_INPUT", () => {
		const { ctx, cwd, runDir } = preStartCtx("materialize-inputs", "");
		const modelInputPath = join(cwd, "sure", "handoffs", "rednote-hilab__dots.tts-base", "model_input.yaml");
		writeModelInput(modelInputPath);

		const proc = spawnSync(
			"python3",
			[
				join(PACKAGE_DIR, "scripts", "materialize_onboard_inputs.py"),
				"--model-input-path",
				modelInputPath,
				"--run-dir",
				runDir,
				"--repo-root",
				cwd,
				"--package-profile",
				"none",
				"--raw-args",
				"model=rednote-hilab__dots.tts-base",
			],
			{ encoding: "utf-8" },
		);
		expect(proc.status, proc.stderr || proc.stdout).toBe(0);

		const resolvedPath = join(runDir, "artifacts", "model_input_resolved.json");
		const contextPath = join(runDir, "artifacts", "context_selection.json");
		expect(existsSync(resolvedPath)).toBe(true);
		expect(existsSync(contextPath)).toBe(true);
		const resolved = JSON.parse(readFileSync(resolvedPath, "utf-8"));
		const context = JSON.parse(readFileSync(contextPath, "utf-8"));
		expect(validateProduces(ctx, findUnit("load_model_input")!, resolved).ok).toBe(true);
		expect(validateProduces(ctx, findUnit("context_selection")!, context).ok).toBe(true);
		expect(resolved.model_id).toBe("rednote-hilab/dots.tts-base");
		expect(resolved.model_name).toBe("rednote-hilab__dots.tts-base");
		expect(resolved.model_dir).toBe(join(cwd, "sure", "models", "rednote-hilab__dots.tts-base"));
		expect(resolved.repo_url).toBe("https://huggingface.co/rednote-hilab/dots.tts-base");
		expect(existsSync(join(cwd, "sure", "models"))).toBe(true);
		expect(existsSync(join(cwd, "sure", "models", "rednote-hilab__dots.tts-base"))).toBe(false);
		expect(context.selected_references.task_playbooks).toContain("references/task_playbooks/TTS.md");
		expect(context.selected_references.environment_playbooks).toContain("references/playbooks/env_uv.md");
	});

	it("falls back to scalar parsing for legacy handoffs with unquoted multiline code", () => {
		const { ctx, cwd, runDir } = preStartCtx("materialize-legacy-yaml", "");
		const modelInputPath = join(
			cwd,
			"sure",
			"handoffs",
			"OpenMOSS-Team__MOSS-Transcribe-Diarize",
			"model_input.yaml",
		);
		mkdirSync(resolve(modelInputPath, ".."), { recursive: true });
		writeFileSync(
			modelInputPath,
			`${[
				"model_id: OpenMOSS-Team/MOSS-Transcribe-Diarize",
				"model_name: OpenMOSS-Team__MOSS-Transcribe-Diarize",
				"task_type: sa_asr",
				"deployment_type: local",
				"repo:",
				'  url: "https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize"',
				"  commit: null",
				"environment_hint:",
				"  preferred_backend: conda",
				"  python_version: 3.12",
				"entrypoints:",
				"  infer_test: result = generate_transcription(",
				"    model,",
				"    processor,",
				")",
				"fixture:",
				"  task_specific: true",
			].join("\n")}\n`,
			"utf-8",
		);
		const proc = spawnSync(
			"python3",
			[
				join(PACKAGE_DIR, "scripts", "materialize_onboard_inputs.py"),
				"--model-input-path",
				modelInputPath,
				"--run-dir",
				runDir,
				"--repo-root",
				cwd,
			],
			{ encoding: "utf-8" },
		);
		expect(proc.status, proc.stderr || proc.stdout).toBe(0);
		const resolved = JSON.parse(readFileSync(join(runDir, "artifacts", "model_input_resolved.json"), "utf-8"));
		const context = JSON.parse(readFileSync(join(runDir, "artifacts", "context_selection.json"), "utf-8"));
		expect(validateProduces(ctx, findUnit("load_model_input")!, resolved).ok).toBe(true);
		expect(validateProduces(ctx, findUnit("context_selection")!, context).ok).toBe(true);
		expect(resolved.normalized_model_input._parsed_with).toBe("scalar_fallback");
		expect(context.selected_references.task_playbooks).toContain("references/task_playbooks/SPEECH_UNDERSTANDING.md");
		expect(context.selected_references.task_playbooks).toContain("references/task_playbooks/ASR.md");
		expect(context.selected_references.environment_playbooks).toContain("references/playbooks/env_conda.md");
	});

	// sure/handoffs/ is listed in .gitignore, so these fixtures exist only on a
	// machine that has already run /sure_feed. Skip rather than fail when the
	// directory is absent: a fresh clone cannot produce it, and hand-writing a
	// model_input.yaml would test a fixture we invented rather than the artifact
	// the pipeline actually emits.
	const HANDOFFS_DIR = resolve(__dirname, "../../../../sure/handoffs");

	it.skipIf(!existsSync(HANDOFFS_DIR)).each([
		["Qwen__Qwen3-ASR-1.7B", "Qwen/Qwen3-ASR-1.7B", "asr", "references/task_playbooks/ASR.md"],
		["Qwen__Qwen3-TTS-12Hz-1.7B-Base", "Qwen/Qwen3-TTS-12Hz-1.7B-Base", "tts", "references/task_playbooks/TTS.md"],
	])("materializes checked-in Qwen handoff %s", (modelName, modelId, taskType, taskPlaybook) => {
		const { ctx, cwd, runDir } = preStartCtx(`materialize-${modelName}`, "");
		const modelInputPath = join(HANDOFFS_DIR, modelName, "model_input.yaml");
		const proc = spawnSync(
			"python3",
			[
				join(PACKAGE_DIR, "scripts", "materialize_onboard_inputs.py"),
				"--model-input-path",
				modelInputPath,
				"--run-dir",
				runDir,
				"--repo-root",
				cwd,
				"--package-profile",
				"none",
				"--raw-args",
				`model=${modelName}`,
			],
			{ encoding: "utf-8" },
		);
		expect(proc.status, proc.stderr || proc.stdout).toBe(0);

		const resolved = JSON.parse(readFileSync(join(runDir, "artifacts", "model_input_resolved.json"), "utf-8"));
		const context = JSON.parse(readFileSync(join(runDir, "artifacts", "context_selection.json"), "utf-8"));
		expect(validateProduces(ctx, findUnit("load_model_input")!, resolved).ok).toBe(true);
		expect(validateProduces(ctx, findUnit("context_selection")!, context).ok).toBe(true);
		expect(resolved.model_id).toBe(modelId);
		expect(resolved.model_name).toBe(modelName);
		expect(resolved.task_type).toBe(taskType);
		expect(resolved.package_profile).toBe("none");
		expect(resolved.model_dir).toBe(join(cwd, "sure", "models", modelName));
		expect(context.selected_references.task_playbooks).toContain(taskPlaybook);
		expect(context.selected_references.environment_playbooks).toContain("references/playbooks/env_uv.md");
	});

	it("allows the materializer helper script during LOAD_MODEL_INPUT", () => {
		const { ctx, runDir } = freshCtx("allow-materializer");
		ctx.point = "pre_tool_call";
		ctx.event = {
			toolCall: {
				name: "bash",
				input: {
					command: "python3 scripts/materialize_onboard_inputs.py --model-input-path x --run-dir y --repo-root z",
				},
			},
		} as never;
		seedCheckpoint(runDir, { currentUnit: "load_model_input", completedUnits: [], retries: {} });
		const result = preToolCall(ctx);
		expect(result.ok).toBe(true);
	});

	it("advances from LOAD_MODEL_INPUT after materialized model_input_resolved.json passes the gate", () => {
		const { ctx, runDir } = freshCtx("load-model-input-pass");
		const modelDir = join(ctx.cwd, "sure", "models", "rednote-hilab__dots.tts-base");
		seedCheckpoint(runDir, { currentUnit: "load_model_input", completedUnits: [], retries: {} });
		writeArtifact(runDir, "model_input_resolved.json", {
			model_input_path: null,
			model_id: "rednote-hilab/dots.tts-base",
			model_name: "rednote-hilab__dots.tts-base",
			model_dir: modelDir,
			repo_url: "https://huggingface.co/rednote-hilab/dots.tts-base",
			task_type: "tts",
			deployment_type: "local",
			package_profile: "none",
			path_policy: {
				model_dir_is_harness_owned: true,
				model_dir_symlink_allowed: false,
				allowed_model_root: join(ctx.cwd, "sure", "models"),
			},
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("context_selection");
	});
});

describe("sure_onboard end-to-end state-machine replay", () => {
	it("replays MODEL_INPUT to final sure_finish through all gates", () => {
		const { ctx, cwd, runDir } = preStartCtx("full-replay-reference-adoption", "");
		const modelInputPath = join(cwd, "sure", "handoffs", "rednote-hilab__dots.tts-base", "model_input.yaml");
		writeModelInput(modelInputPath);
		ctx.args = `model_input_path=${modelInputPath} package=none`;
		const start = preStart(ctx);
		expect(start.ok).toBe(true);

		const materialize = spawnSync(
			"python3",
			[
				join(PACKAGE_DIR, "scripts", "materialize_onboard_inputs.py"),
				"--model-input-path",
				modelInputPath,
				"--run-dir",
				runDir,
				"--repo-root",
				cwd,
				"--package-profile",
				"none",
				"--raw-args",
				ctx.args,
			],
			{ encoding: "utf-8" },
		);
		expect(materialize.status, materialize.stderr || materialize.stdout).toBe(0);

		ctx.point = "post_tool_result";
		const advance = (expectedCurrentUnit: string): CheckpointData => {
			const result = postToolResult(ctx);
			expect(result.ok, result.repair).toBe(true);
			const checkpoint = persistCheckpointFrom(runDir, result);
			expect(checkpoint?.currentUnit).toBe(expectedCurrentUnit);
			return checkpoint!;
		};

		advance("context_selection");
		advance("discover");

		const resolved = JSON.parse(readFileSync(join(runDir, "artifacts", "model_input_resolved.json"), "utf-8"));
		const modelDir = String(resolved.model_dir);
		const modelArtifacts = join(modelDir, "artifacts");
		mkdirSync(modelArtifacts, { recursive: true });
		for (const name of ["model.spec.yaml", "model.py", "server.py", "__init__.py", "validate.py", "config.yaml"]) {
			const content = name === "model.py" ? "class Model:\n\tpass\n" : `${name}\n`;
			writeFileSync(join(modelDir, name), content, "utf-8");
		}
		const checkpointDir = join(modelDir, "checkpoints", "tiny");
		mkdirSync(checkpointDir, { recursive: true });
		writeFileSync(join(checkpointDir, "config.json"), "{}\n", "utf-8");
		writeFileSync(join(runDir, "artifacts", "build.log"), "build ok\n", "utf-8");
		writeFileSync(join(modelArtifacts, "build.log"), "build ok\n", "utf-8");
		writeFileSync(join(modelArtifacts, "validation.log"), "validation ok\n", "utf-8");

		writeArtifact(runDir, "repo_summary.json", {
			repo_url: resolved.repo_url,
			commit: null,
			local_path: modelDir,
			file_inventory: ["model.py", "validate.py"],
			language: "python",
		});
		advance("classify");

		writeArtifact(runDir, "classification.json", {
			task_type: "tts",
			deployment_type: "local",
			input_modality: "text_with_reference_audio",
			output_modality: "audio",
			rationale: "MODEL_INPUT declares a TTS local deployment.",
		});
		advance("plan");

		writeArtifact(runDir, "backend_choice.json", {
			model_id: resolved.model_id,
			model_name: resolved.model_name,
			backend: "uv",
			choice_reason: "Replay uses a lightweight local validation command.",
			python_version: "3.11",
			requires_gpu: false,
			deployment_type: "local",
			package_profile: "none",
			context_selection_path: "artifacts/context_selection.json",
			evidence: ["model_input_resolved.json"],
		});
		advance("build_plan");

		writeArtifact(runDir, "build_plan.json", {
			model_id: resolved.model_id,
			model_name: resolved.model_name,
			model_dir: modelDir,
			artifacts_dir: modelArtifacts,
			backend: "uv",
			deployment_type: "local",
			package_profile: "none",
			python_version: "3.11",
			requires_gpu: false,
			weights: {
				source: "local",
				required: true,
				target: checkpointDir,
				cache_policy: "replay",
				fallback_to_host_global: false,
			},
			context_selection_path: "artifacts/context_selection.json",
			backend_choice_path: "artifacts/backend_choice.json",
			steps: [
				{ state: "validate_spec", action: "check generated spec" },
				{ state: "prepare_fixture", action: "stage task fixture into model_dir" },
				{ state: "build_env", action: "reuse test python" },
				{ state: "fetch_weights", action: "use local tiny checkpoint" },
				{ state: "generate_wrapper", action: "write model-local wrapper before validation" },
				{ state: "validate_import", action: "run lightweight import command" },
				{ state: "validate_load", action: "run lightweight load command" },
				{ state: "validate_infer", action: "run lightweight infer command" },
				{ state: "validate_contract", action: "validate sample output" },
			],
			planned_outputs: ["artifacts/verdict.json"],
			blockers: [],
		});
		writeJson(
			join(modelArtifacts, "build_plan.json"),
			JSON.parse(readFileSync(join(runDir, "artifacts", "build_plan.json"), "utf-8")),
		);
		advance("validate_spec");

		const passedCheck = { passed: true };
		writeArtifact(runDir, "spec_validation.json", {
			status: "passed",
			checks: {
				spec_completeness: passedCheck,
				evidence_sufficiency: passedCheck,
				conflict_resolution: passedCheck,
				build_plan_executable: passedCheck,
				fixture_availability: { passed: true, fixture_path: "fixture.wav", source: "test" },
				io_contract_sufficient: passedCheck,
				preflight_compatible: passedCheck,
			},
			ready_for_build_env: true,
			warnings: [],
			blockers: [],
		});
		advance("prepare_fixture");

		const fixtureDir = join(modelDir, "fixture", "tts", "tts_smoke");
		mkdirSync(fixtureDir, { recursive: true });
		writeFileSync(join(fixtureDir, "sample.wav"), "not real audio\n", "utf-8");
		writeFileSync(
			join(fixtureDir, "gt.jsonl"),
			`${JSON.stringify({
				key: "sample",
				audio: "sample.wav",
				target_text: "hello from fixture",
				language: "en",
			})}\n`,
			"utf-8",
		);
		writeArtifact(runDir, "fixture_manifest.json", {
			model_id: resolved.model_id,
			model_name: resolved.model_name,
			model_dir: modelDir,
			task_type: "tts",
			source_dir: fixtureDir,
			staged_dir: fixtureDir,
			gt_jsonl: join(fixtureDir, "gt.jsonl"),
			sample_count: 1,
			link_policy: "copy",
			samples: [
				{
					key: "sample",
					audio: "sample.wav",
					audio_path: join(fixtureDir, "sample.wav"),
					annotation_fields: ["target_text"],
				},
			],
		});
		advance("build_env");

		const pythonExecutable = writePythonShim(modelDir);
		writeArtifact(runDir, "build_env_result.json", {
			env_ready: true,
			backend: "uv",
			python_version: "3.11",
			python_executable: pythonExecutable,
			model_dir: modelDir,
			artifacts_dir: modelArtifacts,
			lockfile_path: null,
			duration_seconds: 0,
			log_path: "build.log",
			failures: [],
		});
		advance("fetch_weights");

		writeArtifact(runDir, "weights_manifest.json", {
			weights_ready: true,
			status: "fetched",
			required: true,
			source: "local",
			model_dir: modelDir,
			resolved_local_model_path: checkpointDir,
			checkpoint_root: join(modelDir, "checkpoints"),
			model_local_first: true,
			fallback_to_host_global: false,
		});
		writeJson(
			join(modelArtifacts, "weights_manifest.json"),
			JSON.parse(readFileSync(join(runDir, "artifacts", "weights_manifest.json"), "utf-8")),
		);
		advance("validate_env_compat");

		writeArtifact(runDir, "env_compat_result.json", {
			compat_ok: true,
			device: "cuda",
			python_version_match: true,
			adapter_protocol_supported: true,
			weights_loadable: true,
			incompatibilities: [],
		});
		advance("generate_wrapper");

		writeArtifact(runDir, "wrapper_manifest.json", {
			wrapper_path: modelDir,
			model_py: "model.py",
			server_py: "server.py",
			init_py: "__init__.py",
			validate_py: "validate.py",
			config_yaml: "config.yaml",
		});
		writeJson(
			join(modelArtifacts, "wrapper_manifest.json"),
			JSON.parse(readFileSync(join(runDir, "artifacts", "wrapper_manifest.json"), "utf-8")),
		);
		advance("validate_import");

		const validationCommand = ["python3", "-c", "print('sure replay validation ok')"];
		writeArtifact(runDir, "import_result.json", {
			import_passed: true,
			model_dir: modelDir,
			run_command: validationCommand,
		});
		writeJson(
			join(modelArtifacts, "import_result.json"),
			JSON.parse(readFileSync(join(runDir, "artifacts", "import_result.json"), "utf-8")),
		);
		advance("validate_load");

		writeArtifact(runDir, "load_result.json", {
			load_passed: true,
			model_dir: modelDir,
			run_command: validationCommand,
		});
		writeJson(
			join(modelArtifacts, "load_result.json"),
			JSON.parse(readFileSync(join(runDir, "artifacts", "load_result.json"), "utf-8")),
		);
		advance("validate_infer");

		writeArtifact(runDir, "sample_output.json", { text: "hello from replay" });
		writeJson(join(modelArtifacts, "sample_output.json"), { text: "hello from replay" });
		writeArtifact(runDir, "infer_result.json", {
			infer_passed: true,
			model_dir: modelDir,
			sample_output_path: "sample_output.json",
			run_command: validationCommand,
		});
		writeJson(
			join(modelArtifacts, "infer_result.json"),
			JSON.parse(readFileSync(join(runDir, "artifacts", "infer_result.json"), "utf-8")),
		);
		advance("validate_contract");

		const ioContract = {
			output_type: "json",
			primary_field: "text",
			required_fields: ["text"],
			nonempty_fields: ["text"],
			json_serializable: true,
		};
		writeArtifact(runDir, "contract_result.json", {
			contract_passed: true,
			io_contract_satisfied: true,
			io_contract: ioContract,
			model_dir: modelDir,
			sample_output_path: "sample_output.json",
			run_command: validationCommand,
		});
		writeJson(
			join(modelArtifacts, "contract_result.json"),
			JSON.parse(readFileSync(join(runDir, "artifacts", "contract_result.json"), "utf-8")),
		);
		advance("save_artifacts");

		const manifest = {
			model_dir: modelDir,
			instance_id: "rednote-hilab__dots.tts-base-replay",
			model_id: resolved.model_id,
			model_name: resolved.model_name,
			phase: "local_onboard_replay",
			status: "staged",
			artifacts: {
				required: {
					spec: { path: "model.spec.yaml" },
					model_py: { path: "model.py" },
					server_py: { path: "server.py" },
					package_init: { path: "__init__.py" },
					validate_py: { path: "validate.py" },
					config: { path: "config.yaml" },
					build_plan: { path: "artifacts/build_plan.json" },
					weights_manifest: { path: "artifacts/weights_manifest.json" },
					import_result: { path: "artifacts/import_result.json" },
					load_result: { path: "artifacts/load_result.json" },
					infer_result: { path: "artifacts/infer_result.json" },
					contract_result: { path: "artifacts/contract_result.json" },
					sample_output: { path: "artifacts/sample_output.json" },
					manifest: { path: "artifacts/artifact_manifest.json" },
				},
				conditional: {},
				optional: {},
			},
		};
		writeArtifact(runDir, "artifact_manifest.json", manifest);
		writeJson(join(modelArtifacts, "artifact_manifest.json"), manifest);
		advance("package_gate");

		writeArtifact(runDir, "package_gate.json", {
			status: "passed",
			package_profile: "none",
			model_dir: modelDir,
			artifact_manifest_path: join(runDir, "artifacts", "artifact_manifest.json"),
			readiness: {
				local_ready: true,
				docker_ready: false,
				registry_ready: false,
				vc_ready: null,
			},
			local: {
				validation_passed: true,
				artifacts_complete: true,
				evidence: ["artifacts/artifact_manifest.json", "artifacts/sample_output.json"],
			},
		});
		advance("verdict");

		writeArtifact(runDir, "verdict.json", {
			status: "passed",
			model_id: resolved.model_id,
			model_name: resolved.model_name,
			package: { profile: "none" },
			readiness: {
				local_ready: true,
				docker_ready: false,
				registry_ready: false,
				vc_ready: null,
			},
			build: { success: true, log_path: "artifacts/build.log" },
			validation: {
				import_test: { passed: true },
				load_test: { passed: true },
				infer_test: { passed: true },
				contract_test: { passed: true },
			},
			artifacts: {
				spec_path: "model.spec.yaml",
				wrapper_path: "model.py",
				artifact_manifest_path: "artifacts/artifact_manifest.json",
				validation_log_path: "artifacts/validation.log",
				sample_output_path: "artifacts/sample_output.json",
			},
		});
		const terminal = advance("verdict");
		expect(terminal.completedUnits).toContain("verdict");

		ctx.point = "pre_finish";
		ctx.event = { finish: { status: "success" } } as never;
		const finish = preFinish(ctx);
		const patch = statePatch(finish);
		expect(finish.ok, finish.repair).toBe(true);
		expect(patch.phase?.status).toBe("success");
	});
});

// Regression guard for the --kind routing bug: runGateScript injected
// ["--kind", "<unit_suffix>"] for EVERY unit whose id starts with "validate_",
// but only run_validate.py accepts --kind. check_spec.py / check_env_compat.py
// crashed with "unrecognized arguments: --kind spec", exhausting retries and
// marking validate_spec FAILED — even on a fully compliant spec_validation.json.
// Fixed: --kind is injected ONLY when gateScript === "run_validate.py".
describe("sure_onboard gate --kind routing (regression)", () => {
	// The four run_validate.py gates must each receive their --kind.
	it.each([
		["validate_import", "import"],
		["validate_load", "load"],
		["validate_infer", "infer"],
		["validate_contract", "contract"],
	])("%s routes to run_validate.py with --kind %s", (unitId, kind) => {
		const unit = findUnit(unitId)!;
		expect(unit.gateScript).toBe("run_validate.py");
		// The kind is derived from the unit id suffix in index.ts runGateScript.
		expect(unit.id.replace("validate_", "")).toBe(kind);
	});

	it("validate_spec uses check_spec.py (NOT run_validate.py) — no --kind injected", () => {
		const unit = findUnit("validate_spec")!;
		expect(unit.gateScript).toBe("check_spec.py");
		// A compliant spec_validation.json must advance cleanly, NOT crash on --kind.
		const { ctx, runDir } = freshCtx("spec-pass");
		seedCheckpoint(runDir, {
			currentUnit: "validate_spec",
			completedUnits: ["load_model_input", "context_selection", "discover", "classify", "plan", "build_plan"],
			retries: {},
		});
		writeArtifact(runDir, "spec_validation.json", {
			status: "passed",
			checks: {
				spec_completeness: { passed: true },
				evidence_sufficiency: { passed: true },
				conflict_resolution: { passed: true },
				build_plan_executable: { passed: true },
				fixture_availability: { passed: true },
				io_contract_sufficient: { passed: true },
				preflight_compatible: { passed: true },
			},
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		expect(result.repair).toBeUndefined();
		// Must have advanced past validate_spec to prepare_fixture.
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("prepare_fixture");
		expect(checkpoint?.data.completedUnits).toContain("validate_spec");
	});

	it("validate_env_compat uses check_env_compat.py (NOT run_validate.py)", () => {
		const unit = findUnit("validate_env_compat")!;
		expect(unit.gateScript).toBe("check_env_compat.py");
		const { ctx, runDir } = freshCtx("envcompat-pass");
		seedCheckpoint(runDir, {
			currentUnit: "validate_env_compat",
			completedUnits: [
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
			],
			retries: {},
		});
		writeArtifact(runDir, "env_compat_result.json", { compat_ok: true });
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("generate_wrapper");
	});

	it("fails clearly on the third consecutive blocked onboard gate attempt", () => {
		const { ctx, runDir } = freshCtx("envcompat-third-block");
		seedCheckpoint(runDir, {
			currentUnit: "validate_env_compat",
			completedUnits: [
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
			],
			retries: { validate_env_compat: 2 },
		});
		writeArtifact(runDir, "env_compat_result.json", { compat_ok: false });
		const result = postToolResult(ctx);
		const patch = statePatch(result);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("After 3 consecutive blocked attempts");
		expect(result.repair).toContain("model_input_path or repo link");
		expect(result.repair).toContain("confirm access permissions");
		expect(patch.message).toContain("exhausted 3 blocked attempts");
	});

	it("puts the actual blocking reason before the generic checklist in the terminal repair message", () => {
		const { ctx, runDir } = freshCtx("envcompat-reason-first");
		seedCheckpoint(runDir, {
			currentUnit: "validate_env_compat",
			completedUnits: [
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
			],
			retries: { validate_env_compat: 2 },
		});
		writeArtifact(runDir, "env_compat_result.json", { compat_ok: false });
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("Blocked because: gate script check_env_compat.py failed");
		const reasonIndex = result.repair?.indexOf("Blocked because:") ?? -1;
		const checklistIndex = result.repair?.indexOf("model_input_path or repo link") ?? -1;
		expect(reasonIndex).toBeGreaterThanOrEqual(0);
		expect(reasonIndex).toBeLessThan(checklistIndex);
	});
});

describe("sure_onboard countersFor", () => {
	it("keeps gate_blocks consistent with the retry ledger", () => {
		const data: CheckpointData = {
			currentUnit: "discover",
			completedUnits: [],
			retries: { discover: 4, classify: 2 },
		};
		expect(countersFor(data, 0).gate_blocks).toBe(6);
	});
});

describe("sure_onboard new alignment gates", () => {
	function seedFetchWeightsCheckpoint(runDir: string): void {
		seedCheckpoint(runDir, {
			currentUnit: "fetch_weights",
			completedUnits: [
				"load_model_input",
				"context_selection",
				"discover",
				"classify",
				"plan",
				"build_plan",
				"validate_spec",
				"prepare_fixture",
				"build_env",
			],
			retries: {},
		});
	}

	it("advances from build_plan only when build_plan.json is executable and VC-free", () => {
		const { ctx, runDir } = freshCtx("build-plan-pass");
		seedCheckpoint(runDir, {
			currentUnit: "build_plan",
			completedUnits: ["load_model_input", "context_selection", "discover", "classify", "plan"],
			retries: {},
		});
		writeArtifact(runDir, "build_plan.json", {
			model_id: "Qwen/Qwen3-ASR-1.7B",
			model_name: "Qwen__Qwen3-ASR-1.7B",
			model_dir: "/tmp/sure/models/Qwen__Qwen3-ASR-1.7B",
			backend: "uv",
			deployment_type: "local",
			package_profile: "none",
			steps: [
				{ state: "BUILD_ENV", action: "create uv env", command: "uv sync" },
				{ state: "VALIDATE_IMPORT", action: "run documented import command" },
			],
			blockers: [],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("validate_spec");
	});

	it("blocks build_plan when VC/HPC is made a required core step", () => {
		const { ctx, runDir } = freshCtx("build-plan-vc-block");
		seedCheckpoint(runDir, {
			currentUnit: "build_plan",
			completedUnits: ["load_model_input", "context_selection", "discover", "classify", "plan"],
			retries: {},
		});
		writeArtifact(runDir, "build_plan.json", {
			model_id: "Qwen/Qwen3-ASR-1.7B",
			model_dir: "/tmp/sure/models/Qwen__Qwen3-ASR-1.7B",
			backend: "uv",
			package_profile: "none",
			steps: [{ state: "VC_SUBMIT_VALIDATE", action: "submit vc job" }],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("VC/HPC submission is not part of core /sure_onboard");
	});

	it("accepts build_env paths declared relative to the repository root", () => {
		const root = resolve(__dirname, "tmp-ob", "build-env-repo-root");
		rmSync(root, { recursive: true, force: true });
		const runDir = join(root, ".sure", "runs", "run-1");
		const artifactsDir = join(runDir, "artifacts");
		const modelDir = join(root, "sure", "models", "Qwen__Qwen3-TTS-12Hz-1.7B-Base");
		const modelArtifacts = join(modelDir, "artifacts");
		mkdirSync(modelArtifacts, { recursive: true });
		mkdirSync(artifactsDir, { recursive: true });
		writePythonShim(modelDir);
		writeFileSync(join(modelArtifacts, "requirements.lock"), "qwen-tts==0.0.0\n", "utf-8");
		writeFileSync(join(modelArtifacts, "build.log"), "env ready\n", "utf-8");
		const produces = join(artifactsDir, "build_env_result.json");
		writeJson(produces, {
			env_ready: true,
			backend: "uv",
			model_dir: "sure/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base",
			lockfile_path: "sure/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base/artifacts/requirements.lock",
			log_path: "sure/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base/artifacts/build.log",
		});

		const proc = spawnSync(
			"python3",
			[join(PACKAGE_DIR, "scripts", "check_env.py"), "--run-dir", runDir, "--produces", produces],
			{ encoding: "utf-8" },
		);
		expect(proc.status, proc.stderr || proc.stdout).toBe(0);
	});

	it("allows the fixture preparation helper during PREPARE_FIXTURE", () => {
		const { ctx, runDir } = freshCtx("allow-prepare-fixture");
		ctx.point = "pre_tool_call";
		ctx.event = {
			toolCall: {
				name: "bash",
				input: {
					command: "python3 scripts/prepare_fixture.py --run-dir x --produces x/artifacts/fixture_manifest.json",
				},
			},
		} as never;
		seedCheckpoint(runDir, {
			currentUnit: "prepare_fixture",
			completedUnits: [
				"load_model_input",
				"context_selection",
				"discover",
				"classify",
				"plan",
				"build_plan",
				"validate_spec",
			],
			retries: {},
		});
		const result = preToolCall(ctx);
		expect(result.ok).toBe(true);
	});

	it("stages the selected task fixture into model_dir and passes check_fixture", () => {
		const root = resolve(__dirname, "tmp-ob", "prepare-fixture-pass");
		rmSync(root, { recursive: true, force: true });
		const runDir = join(root, ".sure", "runs", "run-1");
		const artifactsDir = join(runDir, "artifacts");
		const modelDir = join(root, "sure", "models", "FixtureModel");
		const fixtureSource = join(root, "fixtures", "tasks", "asr", "asr_smoke");
		mkdirSync(artifactsDir, { recursive: true });
		mkdirSync(fixtureSource, { recursive: true });
		writeFileSync(join(fixtureSource, "sample.wav"), "not real audio\n", "utf-8");
		writeFileSync(
			join(fixtureSource, "gt.jsonl"),
			`${JSON.stringify({ key: "sample", audio: "sample.wav", ground_truth: "hello" })}\n`,
			"utf-8",
		);
		writeJson(join(artifactsDir, "model_input_resolved.json"), {
			model_id: "owner/FixtureModel",
			model_name: "FixtureModel",
			model_dir: modelDir,
			repo_url: "https://example.test/fixture-model",
			task_type: "asr",
			deployment_type: "local",
			package_profile: "none",
		});
		writeJson(join(artifactsDir, "spec_validation.json"), {
			status: "passed",
			checks: {
				fixture_availability: { passed: true, fixture_path: "fixtures/tasks/asr/asr_smoke" },
			},
		});
		const produces = join(artifactsDir, "fixture_manifest.json");
		const prepare = spawnSync(
			"python3",
			[join(PACKAGE_DIR, "scripts", "prepare_fixture.py"), "--run-dir", runDir, "--produces", produces],
			{ encoding: "utf-8" },
		);
		expect(prepare.status, prepare.stderr || prepare.stdout).toBe(0);
		expect(existsSync(join(modelDir, "fixture", "asr", "asr_smoke", "gt.jsonl"))).toBe(true);
		const manifest = JSON.parse(readFileSync(produces, "utf-8"));
		expect(manifest.link_policy).toBe("copy");
		expect(manifest.sample_count).toBe(1);

		const gate = spawnSync(
			"python3",
			[join(PACKAGE_DIR, "scripts", "check_fixture.py"), "--run-dir", runDir, "--produces", produces],
			{ encoding: "utf-8" },
		);
		expect(gate.status, gate.stderr || gate.stdout).toBe(0);
	});

	it("blocks sa_asr fixtures without speaker-attributed segments", () => {
		const root = resolve(__dirname, "tmp-ob", "prepare-fixture-saasr-no-segments");
		rmSync(root, { recursive: true, force: true });
		const runDir = join(root, ".sure", "runs", "run-1");
		const artifactsDir = join(runDir, "artifacts");
		const modelDir = join(root, "sure", "models", "BadSaAsr");
		const fixtureDir = join(modelDir, "fixture", "sa_asr", "bad");
		mkdirSync(fixtureDir, { recursive: true });
		mkdirSync(artifactsDir, { recursive: true });
		writeFileSync(join(fixtureDir, "sample.wav"), "not real audio\n", "utf-8");
		writeFileSync(
			join(fixtureDir, "gt.jsonl"),
			`${JSON.stringify({ key: "sample", audio: "sample.wav", ground_truth: "hello" })}\n`,
			"utf-8",
		);
		const produces = join(artifactsDir, "fixture_manifest.json");
		writeJson(produces, {
			model_id: "owner/BadSaAsr",
			model_name: "BadSaAsr",
			model_dir: modelDir,
			task_type: "sa_asr",
			source_dir: fixtureDir,
			staged_dir: fixtureDir,
			gt_jsonl: join(fixtureDir, "gt.jsonl"),
			sample_count: 1,
			link_policy: "copy",
			samples: [
				{
					key: "sample",
					audio: "sample.wav",
					audio_path: join(fixtureDir, "sample.wav"),
					annotation_fields: ["ground_truth"],
				},
			],
		});
		const gate = spawnSync(
			"python3",
			[join(PACKAGE_DIR, "scripts", "check_fixture.py"), "--run-dir", runDir, "--produces", produces],
			{ encoding: "utf-8" },
		);
		expect(gate.status).not.toBe(0);
		expect(gate.stderr).toContain("sa_asr requires speaker-attributed segments");
	});

	it("blocks fetch_weights when required weights have no resolved local path", () => {
		const { ctx, runDir } = freshCtx("weights-required-missing-path");
		seedFetchWeightsCheckpoint(runDir);
		writeArtifact(runDir, "model_input_resolved.json", {
			model_id: "Qwen/Qwen3-ASR-1.7B",
			model_name: "Qwen__Qwen3-ASR-1.7B",
			model_dir: "/tmp/sure/models/Qwen__Qwen3-ASR-1.7B",
			repo_url: "https://modelscope.cn/models/Qwen/Qwen3-ASR-1.7B",
			task_type: "asr",
			deployment_type: "local",
			package_profile: "none",
			normalized_model_input: {
				weights: { required: true, source: "modelscope" },
			},
		});
		writeArtifact(runDir, "weights_manifest.json", {
			weights_ready: true,
			source: "modelscope",
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("resolved_local_model_path is missing");
	});

	it("advances fetch_weights for a model-local resolved checkpoint path", () => {
		const { ctx, runDir } = freshCtx("weights-model-local-pass");
		seedFetchWeightsCheckpoint(runDir);
		const modelDir = join(runDir, "repo", "sure", "models", "Qwen__Qwen3-ASR-1.7B");
		const checkpointPath = join(modelDir, "checkpoints", "Qwen3-ASR-1.7B");
		mkdirSync(checkpointPath, { recursive: true });
		writeArtifact(runDir, "model_input_resolved.json", {
			model_id: "Qwen/Qwen3-ASR-1.7B",
			model_name: "Qwen__Qwen3-ASR-1.7B",
			model_dir: modelDir,
			repo_url: "https://modelscope.cn/models/Qwen/Qwen3-ASR-1.7B",
			task_type: "asr",
			deployment_type: "local",
			package_profile: "none",
			normalized_model_input: {
				weights: { required: true, source: "modelscope" },
			},
		});
		writeArtifact(runDir, "weights_manifest.json", {
			weights_ready: true,
			source: "modelscope",
			resolved_local_model_path: checkpointPath,
			checkpoint_root: join(modelDir, "checkpoints"),
			model_local_first: true,
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("validate_env_compat");
	});

	it("accepts fetch_weights paths declared relative to the repository root", () => {
		const root = resolve(__dirname, "tmp-ob", "weights-repo-root");
		rmSync(root, { recursive: true, force: true });
		const runDir = join(root, ".sure", "runs", "run-1");
		const artifactsDir = join(runDir, "artifacts");
		const checkpointPath = join(
			root,
			"sure",
			"models",
			"Qwen__Qwen3-TTS-12Hz-1.7B-Base",
			"checkpoints",
			"Qwen3-TTS-12Hz-1.7B-Base",
		);
		mkdirSync(checkpointPath, { recursive: true });
		mkdirSync(artifactsDir, { recursive: true });
		writeJson(join(artifactsDir, "model_input_resolved.json"), {
			model_id: "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
			model_name: "Qwen__Qwen3-TTS-12Hz-1.7B-Base",
			model_dir: "sure/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base",
			repo_url: "https://github.com/QwenLM/Qwen3-TTS",
			task_type: "tts",
			deployment_type: "local",
			package_profile: "none",
			normalized_model_input: {
				weights: { required: true, source: "modelscope" },
			},
		});
		const produces = join(artifactsDir, "weights_manifest.json");
		writeJson(produces, {
			status: "fetched",
			source: "modelscope_fallback",
			required: true,
			resolved_local_model_path: "sure/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base/checkpoints/Qwen3-TTS-12Hz-1.7B-Base",
			checkpoint_root: "sure/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base/checkpoints",
			dependencies: [
				{
					repo_id: "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
					local_path: "sure/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base/checkpoints/Qwen3-TTS-12Hz-1.7B-Base",
					exists: true,
				},
			],
		});

		const proc = spawnSync(
			"python3",
			[join(PACKAGE_DIR, "scripts", "check_weights.py"), "--run-dir", runDir, "--produces", produces],
			{ encoding: "utf-8" },
		);
		expect(proc.status, proc.stderr || proc.stdout).toBe(0);
	});

	it("accepts upstream-style status=fetched weights manifests with rich fields", () => {
		const { ctx, runDir } = freshCtx("weights-upstream-fetched-pass");
		seedFetchWeightsCheckpoint(runDir);
		const modelDir = join(runDir, "repo", "sure", "models", "Qwen__Qwen3-TTS-12Hz-1.7B-Base");
		const checkpointPath = join(modelDir, "checkpoints", "Qwen3-TTS-12Hz-1.7B-Base");
		mkdirSync(checkpointPath, { recursive: true });
		writeArtifact(runDir, "model_input_resolved.json", {
			model_id: "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
			model_name: "Qwen__Qwen3-TTS-12Hz-1.7B-Base",
			model_dir: modelDir,
			repo_url: "https://github.com/QwenLM/Qwen3-TTS",
			task_type: "tts",
			deployment_type: "local",
			package_profile: "none",
			normalized_model_input: {
				weights: { required: true, source: "huggingface" },
			},
		});
		writeArtifact(runDir, "weights_manifest.json", {
			status: "fetched",
			required: true,
			source: "modelscope_fallback",
			repo_id: "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
			resolved_local_model_path: checkpointPath,
			checkpoint_root: join(modelDir, "checkpoints"),
			dependencies: [{ repo_id: "Qwen/Qwen3-TTS-12Hz-1.7B-Base", local_path: checkpointPath, exists: true }],
			model_local_first: true,
			source_attempts: [
				{ source: "huggingface", status: "failed" },
				{ source: "modelscope", status: "succeeded" },
			],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("validate_env_compat");
	});

	it("blocks external checkpoint paths unless host-global fallback is documented", () => {
		const { ctx, runDir } = freshCtx("weights-external-no-fallback");
		seedFetchWeightsCheckpoint(runDir);
		const modelDir = join(runDir, "repo", "sure", "models", "ExternalWeights");
		const externalPath = join(runDir, "external-cache", "model");
		mkdirSync(externalPath, { recursive: true });
		writeArtifact(runDir, "model_input_resolved.json", {
			model_id: "owner/model",
			model_name: "ExternalWeights",
			model_dir: modelDir,
			repo_url: "https://example.test/model",
			task_type: "asr",
			deployment_type: "local",
			package_profile: "none",
			normalized_model_input: {
				weights: { required: true, source: "local" },
			},
		});
		writeArtifact(runDir, "weights_manifest.json", {
			weights_ready: true,
			source: "local",
			resolved_local_model_path: externalPath,
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("outside model_dir");
	});

	it("blocks env compatibility when compat_ok contradicts explicit false checks", () => {
		const { ctx, runDir } = freshCtx("envcompat-false-field");
		seedCheckpoint(runDir, {
			currentUnit: "validate_env_compat",
			completedUnits: [
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
			],
			retries: {},
		});
		writeArtifact(runDir, "env_compat_result.json", {
			compat_ok: true,
			device: "cuda",
			python_version_match: true,
			adapter_protocol_supported: false,
			weights_loadable: true,
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("adapter_protocol_supported");
	});

	it("blocks direct CPU env compatibility on a CUDA host when device=auto", () => {
		const { ctx, runDir } = freshCtx("envcompat-cuda-first-blocks-cpu");
		seedCheckpoint(runDir, {
			currentUnit: "validate_env_compat",
			completedUnits: [
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
			],
			retries: {},
		});
		seedModelInputResolved(runDir, { device: "auto" });
		writeArtifact(runDir, "env_compat_result.json", {
			compat_ok: true,
			device: "cpu",
			python_version_match: true,
			adapter_protocol_supported: true,
			weights_loadable: true,
			incompatibilities: [],
		});
		const result = withEnv("SURE_HOST_CUDA_AVAILABLE", "1", () => postToolResult(ctx));
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("CPU fallback requires recorded CUDA-first evidence");
	});

	it("allows CPU fallback on a CUDA host only after recorded CUDA failures and three repair attempts", () => {
		const { ctx, runDir } = freshCtx("envcompat-cuda-first-allows-recorded-cpu-fallback");
		seedCheckpoint(runDir, {
			currentUnit: "validate_env_compat",
			completedUnits: [
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
			],
			retries: {},
		});
		seedModelInputResolved(runDir, { device: "auto", cpu_fallback_after_cuda_failures: 2 });
		writeArtifact(runDir, "env_compat_result.json", {
			compat_ok: true,
			device: "cpu",
			cuda_available: true,
			device_policy: {
				requested_device: "auto",
				selected_device: "cpu",
				cuda_first: true,
				cuda_available: true,
				cuda_attempts: 2,
				min_cuda_attempts_before_cpu: 2,
				cuda_failures: [
					{ attempt: 1, failure_type: "cuda_version_mismatch", message: "torch build cannot initialize CUDA" },
					{ attempt: 2, failure_type: "cuda_version_mismatch", message: "cu128 reinstall still fails" },
				],
				cuda_repair_attempts: [
					{
						action: "install_torch_for_host_cuda",
						target: "torch+torchaudio cu128",
						status: "failed",
						evidence: "uv pip install from https://download.pytorch.org/whl/cu128 timed out",
					},
					{
						action: "retry_torch_install_with_pip_timeout",
						target: "torch+torchaudio cu128",
						status: "failed",
						evidence: "pip install with an extended timeout from the cu128 index timed out",
					},
					{
						action: "dry_run_torch_for_host_cuda",
						target: "torch+torchaudio cu128",
						status: "failed",
						evidence: "pip dry-run against the cu128 index failed before modifying the environment",
					},
				],
				fallback_reason: "CUDA runtime cannot initialize with the host driver; CPU uses the same repo-native path.",
			},
			python_version_match: true,
			adapter_protocol_supported: true,
			weights_loadable: true,
			incompatibilities: [],
		});
		const result = withEnv("SURE_HOST_CUDA_AVAILABLE", "1", () => postToolResult(ctx));
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("generate_wrapper");
	});

	it("blocks CPU fallback when CUDA failed but no CUDA environment repair was attempted", () => {
		const { ctx, runDir } = freshCtx("envcompat-cpu-fallback-needs-cuda-repair");
		seedCheckpoint(runDir, {
			currentUnit: "validate_env_compat",
			completedUnits: [
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
			],
			retries: {},
		});
		seedModelInputResolved(runDir, { device: "auto", cpu_fallback_after_cuda_failures: 2 });
		writeArtifact(runDir, "env_compat_result.json", {
			compat_ok: true,
			device: "cpu",
			cuda_available: true,
			device_policy: {
				requested_device: "auto",
				selected_device: "cpu",
				cuda_first: true,
				cuda_available: true,
				cuda_attempts: 2,
				min_cuda_attempts_before_cpu: 2,
				cuda_failures: [
					{ attempt: 1, failure_type: "cuda_version_mismatch", message: "torch build cannot initialize CUDA" },
					{ attempt: 2, failure_type: "cuda_version_mismatch", message: "same bad .venv still fails" },
				],
				fallback_reason: "CUDA runtime cannot initialize with the current .venv.",
			},
			python_version_match: true,
			adapter_protocol_supported: true,
			weights_loadable: true,
			incompatibilities: [],
		});
		const result = withEnv("SURE_HOST_CUDA_AVAILABLE", "1", () => postToolResult(ctx));
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("cuda_repair_attempts");
	});

	it("passes the env compatibility device into validation commands", () => {
		const { ctx, runDir } = freshCtx("validate-load-inherits-cuda-device");
		const modelDir = join(runDir, "model-dir");
		mkdirSync(modelDir, { recursive: true });
		writeFileSync(join(modelDir, "model.py"), "class Model: pass\n", "utf-8");
		seedCheckpoint(runDir, {
			currentUnit: "validate_load",
			completedUnits: [
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
			],
			retries: {},
		});
		seedModelInputResolved(runDir, { device: "auto", model_dir: modelDir });
		writeArtifact(runDir, "env_compat_result.json", {
			compat_ok: true,
			device: "cuda",
			python_version_match: true,
			adapter_protocol_supported: true,
			weights_loadable: true,
		});
		writeArtifact(runDir, "load_result.json", {
			load_passed: true,
			model_dir: modelDir,
			run_command: [
				"python3",
				"-c",
				"import os; assert os.environ.get('DEVICE') == 'cuda'; assert os.environ.get('SURE_DEVICE') == 'cuda'",
			],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("validate_infer");
	});

	it("rejects validation artifacts that override the selected CUDA device with CPU", () => {
		const { ctx, runDir } = freshCtx("validate-load-rejects-cpu-override");
		const modelDir = join(runDir, "model-dir");
		mkdirSync(modelDir, { recursive: true });
		writeFileSync(join(modelDir, "model.py"), "class Model: pass\n", "utf-8");
		seedCheckpoint(runDir, {
			currentUnit: "validate_load",
			completedUnits: [
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
			],
			retries: {},
		});
		seedModelInputResolved(runDir, { device: "auto", model_dir: modelDir });
		writeArtifact(runDir, "env_compat_result.json", {
			compat_ok: true,
			device: "cuda",
			python_version_match: true,
			adapter_protocol_supported: true,
			weights_loadable: true,
		});
		writeArtifact(runDir, "load_result.json", {
			load_passed: true,
			model_dir: modelDir,
			env: { DEVICE: "cpu" },
			run_command: ["python3", "-c", "print('should not run')"],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("env_compat_result selected device='cuda'");
	});

	it("requires validation gates to execute a command, not just carry a passed boolean", () => {
		const { ctx, runDir } = freshCtx("validate-import-no-command");
		seedCheckpoint(runDir, {
			currentUnit: "validate_import",
			completedUnits: [
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
			],
			retries: {},
		});
		writeArtifact(runDir, "import_result.json", { import_passed: true });
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("run_command or validate_py");
	});

	it("advances validation gates after the gate executes a successful command", () => {
		const { ctx, runDir } = freshCtx("validate-import-command");
		seedCheckpoint(runDir, {
			currentUnit: "validate_import",
			completedUnits: [
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
			],
			retries: {},
		});
		writeArtifact(runDir, "import_result.json", {
			import_passed: true,
			run_command: ["python3", "-c", "print('import smoke ok')"],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("validate_load");
	});

	it("prefers model-local .venv python for validation run_command", () => {
		const root = resolve(__dirname, "tmp-ob", "validate-local-python");
		rmSync(root, { recursive: true, force: true });
		const runDir = join(root, ".sure", "runs", "run-1");
		const artifactsDir = join(runDir, "artifacts");
		const modelDir = join(root, "sure", "models", "local-python-model");
		mkdirSync(join(modelDir, ".venv", "bin"), { recursive: true });
		mkdirSync(join(modelDir, "artifacts"), { recursive: true });
		mkdirSync(artifactsDir, { recursive: true });
		const pythonPath = spawnSync("python3", ["-c", "import sys; print(sys.executable)"], {
			encoding: "utf-8",
		}).stdout.trim();
		const localPython = join(modelDir, ".venv", "bin", "python");
		writeFileSync(
			localPython,
			`${[
				"#!/usr/bin/env bash",
				"mkdir -p artifacts",
				'printf "%s\\n" "$0" > artifacts/python_used.txt',
				`exec ${pythonPath} "$@"`,
			].join("\n")}\n`,
			"utf-8",
		);
		chmodSync(localPython, 0o755);
		const produces = join(artifactsDir, "import_result.json");
		writeJson(produces, {
			import_passed: true,
			model_dir: modelDir,
			run_command: ["python3", "-c", "print('import ok')"],
			log_path: "artifacts/import_execution.log",
		});

		const proc = spawnSync(
			"python3",
			[
				join(PACKAGE_DIR, "scripts", "run_validate.py"),
				"--kind",
				"import",
				"--run-dir",
				runDir,
				"--produces",
				produces,
			],
			{ encoding: "utf-8" },
		);
		expect(proc.status, proc.stderr || proc.stdout).toBe(0);
		expect(readFileSync(join(modelDir, "artifacts", "python_used.txt"), "utf-8")).toContain(".venv/bin/python");
	});

	it("normalizes repo-root relative sure/models paths inside validation commands", () => {
		const root = resolve(__dirname, "tmp-ob", "validate-repo-relative-command");
		rmSync(root, { recursive: true, force: true });
		const runDir = join(root, ".sure", "runs", "run-1");
		const artifactsDir = join(runDir, "artifacts");
		const modelDir = join(root, "sure", "models", "local-path-model");
		mkdirSync(join(modelDir, "checkpoints", "ckpt"), { recursive: true });
		mkdirSync(artifactsDir, { recursive: true });
		writeFileSync(join(modelDir, "model.py"), "# wrapper placeholder\n", "utf-8");
		const produces = join(artifactsDir, "load_result.json");
		writeJson(produces, {
			load_passed: true,
			model_dir: modelDir,
			run_command: [
				"python3",
				"-c",
				"from pathlib import Path; assert Path('sure/models/local-path-model/checkpoints/ckpt').exists()",
			],
			log_path: "artifacts/load_execution.log",
		});

		const proc = spawnSync(
			"python3",
			[
				join(PACKAGE_DIR, "scripts", "run_validate.py"),
				"--kind",
				"load",
				"--run-dir",
				runDir,
				"--produces",
				produces,
			],
			{ encoding: "utf-8" },
		);
		expect(proc.status, proc.stderr || proc.stdout).toBe(0);
	});

	it("blocks infer validation before execution when the prepared fixture manifest is missing", () => {
		const { ctx, runDir } = freshCtx("validate-infer-no-fixture");
		seedCheckpoint(runDir, {
			currentUnit: "validate_infer",
			completedUnits: [
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
			],
			retries: {},
		});
		writeArtifact(runDir, "infer_result.json", {
			infer_passed: true,
			run_command: ["python3", "-c", "print('infer smoke ok')"],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("prepared model-local fixture");
	});

	it("blocks infer validation when command succeeds but sample_output.json is missing", () => {
		const { ctx, runDir } = freshCtx("validate-infer-no-sample-output");
		seedCheckpoint(runDir, {
			currentUnit: "validate_infer",
			completedUnits: [
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
			],
			retries: {},
		});
		seedPreparedFixture(runDir);
		writeArtifact(runDir, "infer_result.json", {
			infer_passed: true,
			run_command: ["python3", "-c", "print('infer smoke ok')"],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("sample_output.json is required");
	});

	it("advances infer validation only after sample_output.json exists", () => {
		const { ctx, runDir } = freshCtx("validate-infer-with-sample-output");
		seedCheckpoint(runDir, {
			currentUnit: "validate_infer",
			completedUnits: [
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
			],
			retries: {},
		});
		seedPreparedFixture(runDir);
		writeArtifact(runDir, "sample_output.json", { text: "hello" });
		writeArtifact(runDir, "infer_result.json", {
			infer_passed: true,
			run_command: ["python3", "-c", "print('infer smoke ok')"],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("validate_contract");
	});

	it("blocks contract validation when sample_output violates MODEL_INPUT io_contract", () => {
		const { ctx, runDir } = freshCtx("validate-contract-bad-output");
		seedCheckpoint(runDir, {
			currentUnit: "validate_contract",
			completedUnits: [
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
			],
			retries: {},
		});
		writeArtifact(runDir, "model_input_resolved.json", {
			model_id: "Qwen/Qwen3-ASR-1.7B",
			model_name: "Qwen__Qwen3-ASR-1.7B",
			model_dir: "/tmp/sure/models/Qwen__Qwen3-ASR-1.7B",
			repo_url: "https://modelscope.cn/models/Qwen/Qwen3-ASR-1.7B",
			task_type: "asr",
			deployment_type: "local",
			package_profile: "none",
			normalized_model_input: {
				io_contract: {
					output_type: "json",
					primary_field: "text",
					required_fields: ["text"],
					nonempty_fields: ["text"],
					json_serializable: true,
				},
			},
		});
		writeArtifact(runDir, "sample_output.json", { label: "wrong field" });
		writeArtifact(runDir, "contract_result.json", {
			contract_passed: true,
			io_contract_satisfied: true,
			run_command: ["python3", "-c", "print('contract smoke ok')"],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("required field missing: text");
	});

	it("advances contract validation when sample_output satisfies MODEL_INPUT io_contract", () => {
		const { ctx, runDir } = freshCtx("validate-contract-good-output");
		seedCheckpoint(runDir, {
			currentUnit: "validate_contract",
			completedUnits: [
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
			],
			retries: {},
		});
		writeArtifact(runDir, "model_input_resolved.json", {
			model_id: "Qwen/Qwen3-ASR-1.7B",
			model_name: "Qwen__Qwen3-ASR-1.7B",
			model_dir: "/tmp/sure/models/Qwen__Qwen3-ASR-1.7B",
			repo_url: "https://modelscope.cn/models/Qwen/Qwen3-ASR-1.7B",
			task_type: "asr",
			deployment_type: "local",
			package_profile: "none",
			normalized_model_input: {
				io_contract: {
					output_type: "json",
					primary_field: "text",
					required_fields: ["text"],
					nonempty_fields: ["text"],
					json_serializable: true,
				},
			},
		});
		writeArtifact(runDir, "sample_output.json", { text: "hello" });
		writeArtifact(runDir, "contract_result.json", {
			contract_passed: true,
			io_contract_satisfied: true,
			run_command: ["python3", "-c", "print('contract smoke ok')"],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("save_artifacts");
	});

	it("accepts contract_result.json with an explicit io_contract", () => {
		const { ctx, runDir } = freshCtx("validate-contract-direct-io-contract");
		seedCheckpoint(runDir, {
			currentUnit: "validate_contract",
			completedUnits: [
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
			],
			retries: {},
		});
		writeArtifact(runDir, "sample_output.json", { text: "hello" });
		writeArtifact(runDir, "contract_result.json", {
			contract_passed: true,
			io_contract_satisfied: true,
			io_contract: {
				output_type: "json",
				primary_field: "text",
				required_fields: ["text"],
				nonempty_fields: ["text"],
				json_serializable: true,
			},
			run_command: ["python3", "-c", "print('contract smoke ok')"],
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("save_artifacts");
	});

	it("generated validate.py template supports the stage CLI and writes validation artifacts", () => {
		const { cwd } = preStartCtx("validate-template-stage-cli", "");
		const modelDir = join(cwd, "sure", "models", "TemplateSmoke");
		mkdirSync(join(modelDir, "fixture", "asr", "asr_en"), { recursive: true });
		const template = readFileSync(join(PACKAGE_DIR, "scripts", "templates", "validate.py"), "utf-8")
			.replaceAll("__MODEL_ID__", "dummy/model")
			.replaceAll("__TASK_TYPE__", "ASR")
			.replaceAll("__WRAPPER_CLASS__", "ModelWrapper")
			.replaceAll("__PREDICT_METHOD__", "predict")
			.replaceAll(
				"__IO_CONTRACT_JSON__",
				JSON.stringify({
					output_type: "json",
					primary_field: "text",
					required_fields: ["text"],
					nonempty_fields: ["text"],
					json_serializable: true,
				}),
			);
		writeFileSync(join(modelDir, "validate.py"), template, "utf-8");
		writeFileSync(
			join(modelDir, "model.py"),
			`${[
				"class ModelWrapper:",
				"    def __init__(self, *args, **kwargs):",
				"        self.loaded = False",
				"    def load(self):",
				"        self.loaded = True",
				"    def predict(self, payload):",
				"        return {'text': payload.get('text') or 'hello'}",
			].join("\n")}\n`,
			"utf-8",
		);
		writeFileSync(join(modelDir, "fixture", "asr", "asr_en", "sample.wav"), "not real audio\n", "utf-8");
		writeFileSync(
			join(modelDir, "fixture", "asr", "asr_en", "gt.jsonl"),
			`${JSON.stringify({
				key: "sample",
				audio: "sample.wav",
				ground_truth: "hello from fixture",
				language: "en",
			})}\n`,
			"utf-8",
		);

		const proc = spawnSync("python3", ["validate.py", "--stage", "all"], {
			cwd: modelDir,
			encoding: "utf-8",
		});
		expect(proc.status, proc.stderr || proc.stdout).toBe(0);
		for (const name of ["import_result.json", "load_result.json", "infer_result.json", "contract_result.json"]) {
			expect(existsSync(join(modelDir, "artifacts", name))).toBe(true);
		}
		const sample = JSON.parse(readFileSync(join(modelDir, "artifacts", "sample_output.json"), "utf-8"));
		const contract = JSON.parse(readFileSync(join(modelDir, "artifacts", "contract_result.json"), "utf-8"));
		expect(sample.text).toBe("hello from fixture");
		expect(contract.contract_passed).toBe(true);
		expect(contract.io_contract_satisfied).toBe(true);
	});

	it("advances package_gate for package=none when local_ready is true", () => {
		const { ctx, runDir } = freshCtx("package-gate-none-pass");
		const modelDir = seedLocalReadyArtifacts(runDir);
		seedCheckpoint(runDir, {
			currentUnit: "package_gate",
			completedUnits: [
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
				"save_artifacts",
			],
			retries: {},
		});
		writeArtifact(runDir, "package_gate.json", {
			status: "passed",
			package_profile: "none",
			model_dir: modelDir,
			artifact_manifest_path: join(runDir, "artifacts", "artifact_manifest.json"),
			readiness: {
				local_ready: true,
				docker_ready: false,
				registry_ready: false,
				vc_ready: null,
			},
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("verdict");
	});

	it("blocks package_gate when local_ready is claimed without an artifact manifest", () => {
		const { ctx, runDir } = freshCtx("package-gate-missing-manifest");
		writeArtifact(runDir, "import_result.json", { import_passed: true });
		writeArtifact(runDir, "load_result.json", { load_passed: true });
		writeArtifact(runDir, "infer_result.json", { infer_passed: true });
		writeArtifact(runDir, "contract_result.json", { contract_passed: true });
		writeArtifact(runDir, "sample_output.json", { text: "hello" });
		seedCheckpoint(runDir, {
			currentUnit: "package_gate",
			completedUnits: [
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
				"save_artifacts",
			],
			retries: {},
		});
		writeArtifact(runDir, "package_gate.json", {
			status: "passed",
			package_profile: "none",
			readiness: {
				local_ready: true,
				docker_ready: false,
				registry_ready: false,
				vc_ready: null,
			},
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("artifact_manifest.json is required");
	});
});

describe("sure_onboard artifact manifest structure compatibility", () => {
	it("allows the artifact staging helper during SAVE_ARTIFACTS", () => {
		const { ctx, runDir } = freshCtx("allow-stage-artifacts");
		ctx.point = "pre_tool_call";
		ctx.event = {
			toolCall: {
				name: "bash",
				input: {
					command:
						"python3 scripts/stage_model_artifacts.py --run-dir x --produces x/artifacts/artifact_manifest.json",
				},
			},
		} as never;
		seedCheckpoint(runDir, {
			currentUnit: "save_artifacts",
			completedUnits: [
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
			],
			retries: {},
		});
		const result = preToolCall(ctx);
		expect(result.ok).toBe(true);
	});

	it("allows the reference adoption helper during SAVE_ARTIFACTS", () => {
		const { ctx, runDir } = freshCtx("allow-adopt-reference-model");
		ctx.point = "pre_tool_call";
		ctx.event = {
			toolCall: {
				name: "bash",
				input: {
					command:
						"python3 scripts/adopt_reference_model.py --reference-model-dir x --target-model-dir y --model-id a/b --model-name a__b",
				},
			},
		} as never;
		seedCheckpoint(runDir, {
			currentUnit: "save_artifacts",
			completedUnits: [
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
			],
			retries: {},
		});
		const result = preToolCall(ctx);
		expect(result.ok).toBe(true);
	});

	it("adopts an upstream reference model into a harness-local package-gated model dir", () => {
		const { cwd } = preStartCtx("adopt-reference-model", "");
		const referenceDir = join(cwd, "reference", "models", "AdoptedModel");
		const targetDir = join(cwd, "sure", "models", "AdoptedModel");
		mkdirSync(join(referenceDir, "artifacts"), { recursive: true });
		mkdirSync(join(referenceDir, ".runtime", "weights"), { recursive: true });
		for (const name of ["model.spec.yaml", "model.py", "server.py", "__init__.py", "validate.py", "config.yaml"]) {
			writeFileSync(join(referenceDir, name), `${name}\n`, "utf-8");
		}
		writeJson(join(referenceDir, "artifacts", "backend_choice.json"), {
			chosen_backend: "uv",
			reason: "reference pass",
		});
		writeFileSync(join(referenceDir, "artifacts", "build.log"), "build ok\n", "utf-8");
		writeFileSync(join(referenceDir, "artifacts", "validation.log"), "validation ok\n", "utf-8");
		writeJson(join(referenceDir, "artifacts", "sample_output.json"), { text: "hello" });
		writeJson(join(referenceDir, "artifacts", "verdict.json"), {
			status: "PASS",
			checks: {
				import: "PASS",
				load: "PASS",
				infer: "PASS",
				contract: "PASS",
			},
			weights: {
				source: "local",
				local_path: ".runtime/weights",
				verified: true,
			},
		});
		symlinkSync(join(cwd, "missing-reference-venv"), join(referenceDir, ".venv"));

		const adopt = spawnSync(
			"python3",
			[
				join(PACKAGE_DIR, "scripts", "adopt_reference_model.py"),
				"--reference-model-dir",
				referenceDir,
				"--target-model-dir",
				targetDir,
				"--model-id",
				"owner/AdoptedModel",
				"--model-name",
				"AdoptedModel",
				"--replace-existing-adoption",
			],
			{ encoding: "utf-8" },
		);
		expect(adopt.status, adopt.stderr || adopt.stdout).toBe(0);
		expect(existsSync(join(targetDir, "model.py"))).toBe(true);
		expect(lstatSync(join(targetDir, "validate.py")).isSymbolicLink()).toBe(false);
		expect(existsSync(join(targetDir, ".venv"))).toBe(false);
		expect(existsSync(join(targetDir, "artifacts", "package_gate.json"))).toBe(true);

		for (const [script, artifact] of [
			["check_artifact_manifest.py", "artifact_manifest.json"],
			["check_package_gate.py", "package_gate.json"],
			["check_verdict.py", "verdict.json"],
		]) {
			const gate = spawnSync(
				"python3",
				[
					join(PACKAGE_DIR, "scripts", script),
					"--run-dir",
					targetDir,
					"--produces",
					join(targetDir, "artifacts", artifact),
				],
				{ encoding: "utf-8" },
			);
			expect(gate.status, gate.stderr || gate.stdout).toBe(0);
		}
	});

	it("stages run artifacts into the model directory and writes a gate-valid preferred manifest", () => {
		const { cwd, runDir } = preStartCtx("stage-model-artifacts", "");
		const modelName = "Qwen__Qwen3-ASR-1.7B";
		const modelDir = join(cwd, "sure", "models", modelName);
		mkdirSync(join(runDir, "artifacts"), { recursive: true });
		mkdirSync(modelDir, { recursive: true });
		for (const name of ["model.spec.yaml", "model.py", "server.py", "__init__.py", "validate.py", "config.yaml"]) {
			writeFileSync(join(modelDir, name), `${name}\n`, "utf-8");
		}
		writeArtifact(runDir, "model_input_resolved.json", {
			model_id: "Qwen/Qwen3-ASR-1.7B",
			model_name: modelName,
			model_dir: modelDir,
			repo_url: "https://modelscope.cn/models/Qwen/Qwen3-ASR-1.7B",
			task_type: "asr",
			deployment_type: "local",
			package_profile: "none",
		});
		for (const name of [
			"context_selection.json",
			"repo_summary.json",
			"classification.json",
			"backend_choice.json",
			"build_plan.json",
			"spec_validation.json",
			"fixture_manifest.json",
			"build_env_result.json",
			"weights_manifest.json",
			"env_compat_result.json",
			"import_result.json",
			"load_result.json",
			"infer_result.json",
			"contract_result.json",
			"wrapper_manifest.json",
		]) {
			writeArtifact(runDir, name, { artifact: name, status: "passed" });
		}
		writeFileSync(join(runDir, "artifacts", "build.log"), "build ok\n", "utf-8");
		writeFileSync(join(runDir, "artifacts", "validation.log"), "validation ok\n", "utf-8");
		writeArtifact(runDir, "sample_output.json", { text: "hello" });

		const manifestPath = join(runDir, "artifacts", "artifact_manifest.json");
		const stage = spawnSync(
			"python3",
			[join(PACKAGE_DIR, "scripts", "stage_model_artifacts.py"), "--run-dir", runDir, "--produces", manifestPath],
			{ encoding: "utf-8" },
		);
		expect(stage.status, stage.stderr || stage.stdout).toBe(0);
		expect(existsSync(join(modelDir, "artifacts", "build_plan.json"))).toBe(true);
		expect(existsSync(join(modelDir, "artifacts", "artifact_manifest.json"))).toBe(true);

		const gate = spawnSync(
			"python3",
			[join(PACKAGE_DIR, "scripts", "check_artifact_manifest.py"), "--run-dir", runDir, "--produces", manifestPath],
			{ encoding: "utf-8" },
		);
		expect(gate.status, gate.stderr || gate.stdout).toBe(0);
		const manifest = JSON.parse(readFileSync(manifestPath, "utf-8"));
		expect(manifest.model_dir).toBe(modelDir);
		expect(manifest.artifacts.required.build_plan_json.path).toBe("artifacts/build_plan.json");
		expect(manifest.artifacts.optional.validation_log.path).toBe("artifacts/validation.log");
	});

	it("save_artifacts accepts the older upstream artifacts[] manifest shape structurally", () => {
		const { ctx } = freshCtx("manifest-upstream-list");
		const unit = findUnit("save_artifacts")!;
		const result = validateProduces(ctx, unit, {
			model: "asr_qwen3",
			phase: "phase1",
			artifacts: ["model.spec.yaml", "model.py", "server.py", "config.yaml", "__init__.py"],
		});
		expect(result.ok).toBe(true);
	});

	it("save_artifacts accepts a manifest with both model_dir AND artifacts", () => {
		const { ctx } = freshCtx("manifest-ok");
		const unit = findUnit("save_artifacts")!;
		const result = validateProduces(ctx, unit, {
			model_dir: "sure/models/x",
			artifacts: { spec_path: "model.spec.yaml", wrapper_path: "model.py" },
		});
		expect(result.ok).toBe(true);
	});
});

describe("sure_onboard verdict structure compatibility", () => {
	it("accepts the older upstream PASS/checks verdict shape structurally", () => {
		const { ctx } = freshCtx("verdict-upstream-pass");
		const unit = findUnit("verdict")!;
		const result = validateProduces(ctx, unit, {
			model: "asr_qwen3",
			status: "PASS",
			phase: "phase1",
			checks: {
				import: "PASS",
				load: "PASS",
				infer: "PASS",
				contract: "PASS",
			},
		});
		expect(result.ok).toBe(true);
	});

	it("accepts upstream PASSED/steps verdicts semantically without requiring package_gate", () => {
		const { runDir } = freshCtx("verdict-upstream-passed-steps");
		const verdictPath = join(runDir, "artifacts", "verdict.json");
		writeArtifact(runDir, "verdict.json", {
			status: "PASSED",
			workflow_completed: true,
			steps: {
				BUILD_ENV: "PASS_DOCKER_AFTER_UV_NETWORK_FAILURE",
				VALIDATE_IMPORT: "PASS",
				VALIDATE_LOAD: "PASS",
				VALIDATE_INFER: "PASS",
				VALIDATE_CONTRACT: "PASS",
			},
			validation: {
				wrapper: {
					import: "PASS",
					load: "PASS",
					infer: "PASS",
					contract: "PASS",
				},
			},
		});
		const proc = spawnSync(
			"python3",
			[join(PACKAGE_DIR, "scripts", "check_verdict.py"), "--run-dir", runDir, "--produces", verdictPath],
			{ encoding: "utf-8" },
		);
		expect(proc.status, proc.stderr || proc.stdout).toBe(0);
	});

	it("advances verdict only when package_gate and declared local artifacts agree", () => {
		const { ctx, runDir } = freshCtx("verdict-local-ready-pass");
		const modelDir = seedLocalReadyArtifacts(runDir);
		seedCheckpoint(runDir, {
			currentUnit: "verdict",
			completedUnits: [
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
				"save_artifacts",
				"package_gate",
			],
			retries: {},
		});
		writeArtifact(runDir, "package_gate.json", {
			status: "passed",
			package_profile: "none",
			model_dir: modelDir,
			artifact_manifest_path: join(runDir, "artifacts", "artifact_manifest.json"),
			readiness: {
				local_ready: true,
				docker_ready: false,
				registry_ready: false,
				vc_ready: null,
			},
		});
		writeArtifact(runDir, "verdict.json", {
			status: "passed",
			package: { profile: "none" },
			readiness: {
				local_ready: true,
				docker_ready: false,
				registry_ready: false,
				vc_ready: null,
			},
			build: { success: true },
			validation: {
				import_test: { passed: true },
				load_test: { passed: true },
				infer_test: { passed: true },
				contract_test: { passed: true },
			},
			artifacts: {
				spec_path: "model.spec.yaml",
				wrapper_path: "model.py",
				artifact_manifest_path: "artifacts/artifact_manifest.json",
				validation_log_path: "artifacts/validation.log",
				sample_output_path: "artifacts/sample_output.json",
			},
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
	});

	it("accepts verdict artifact paths relative to the repository root", () => {
		const root = resolve(__dirname, "tmp-ob", "verdict-repo-root");
		rmSync(root, { recursive: true, force: true });
		const runDir = join(root, ".sure", "runs", "run-1");
		const artifactsDir = join(runDir, "artifacts");
		const modelDir = join(root, "sure", "models", "verdict-model");
		const modelArtifacts = join(modelDir, "artifacts");
		mkdirSync(modelArtifacts, { recursive: true });
		mkdirSync(artifactsDir, { recursive: true });
		for (const name of ["model.spec.yaml", "model.py"]) {
			writeFileSync(join(modelDir, name), `${name}\n`, "utf-8");
		}
		for (const name of ["artifact_manifest.json", "validation.log", "sample_output.json"]) {
			writeFileSync(join(modelArtifacts, name), `${name}\n`, "utf-8");
		}
		writeJson(join(artifactsDir, "package_gate.json"), {
			status: "passed",
			package_profile: "none",
			model_dir: "sure/models/verdict-model",
			artifact_manifest_path: "sure/models/verdict-model/artifacts/artifact_manifest.json",
			readiness: { local_ready: true, docker_ready: false, registry_ready: false },
		});
		const produces = join(artifactsDir, "verdict.json");
		writeJson(produces, {
			status: "passed",
			package: { profile: "none" },
			readiness: { local_ready: true, docker_ready: false, registry_ready: false },
			build: { success: true },
			validation: {
				import_test: { passed: true },
				load_test: { passed: true },
				infer_test: { passed: true },
				contract_test: { passed: true },
			},
			artifacts: {
				spec_path: "model.spec.yaml",
				wrapper_path: "model.py",
				artifact_manifest_path: "artifacts/artifact_manifest.json",
				validation_log_path: "artifacts/validation.log",
				sample_output_path: "artifacts/sample_output.json",
			},
		});

		const proc = spawnSync(
			"python3",
			[join(PACKAGE_DIR, "scripts", "check_verdict.py"), "--run-dir", runDir, "--produces", produces],
			{ encoding: "utf-8" },
		);
		expect(proc.status, proc.stderr || proc.stdout).toBe(0);
	});

	it("blocks verdict when a declared sample_output_path does not exist", () => {
		const { ctx, runDir } = freshCtx("verdict-missing-sample-path");
		const modelDir = seedLocalReadyArtifacts(runDir);
		seedCheckpoint(runDir, {
			currentUnit: "verdict",
			completedUnits: [
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
				"save_artifacts",
				"package_gate",
			],
			retries: {},
		});
		writeArtifact(runDir, "package_gate.json", {
			status: "passed",
			package_profile: "none",
			model_dir: modelDir,
			artifact_manifest_path: join(runDir, "artifacts", "artifact_manifest.json"),
			readiness: {
				local_ready: true,
				docker_ready: false,
				registry_ready: false,
				vc_ready: null,
			},
		});
		writeArtifact(runDir, "verdict.json", {
			status: "passed",
			package: { profile: "none" },
			readiness: {
				local_ready: true,
				docker_ready: false,
				registry_ready: false,
				vc_ready: null,
			},
			build: { success: true },
			validation: {
				import_test: { passed: true },
				load_test: { passed: true },
				infer_test: { passed: true },
				contract_test: { passed: true },
			},
			artifacts: {
				spec_path: "model.spec.yaml",
				wrapper_path: "model.py",
				artifact_manifest_path: "artifacts/artifact_manifest.json",
				validation_log_path: "artifacts/validation.log",
				sample_output_path: "artifacts/missing_sample_output.json",
			},
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("sample_output_path");
	});
});
