import { existsSync, mkdirSync, readdirSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fauxAssistantMessage, fauxToolCall } from "@earendil-works/pi-ai/compat";
import { Type } from "typebox";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createAgentSessionServices } from "../../src/core/agent-session-services.ts";
import type {
	ExtensionAPI,
	ExtensionCommandContext,
	ExtensionFactory,
	ExtensionMode,
	ExtensionUIContext,
} from "../../src/core/extensions/index.ts";
import { emitSessionShutdownEvent } from "../../src/core/extensions/runner.ts";
import { SettingsManager } from "../../src/core/settings-manager.ts";
import { switchToInitializedModel } from "../../src/core/sure/extension.ts";
import { sureExtension } from "../../src/core/sure/index.ts";
import type { SureInitManifest } from "../../src/core/sure/init-types.ts";
import { runPrintMode } from "../../src/modes/print-mode.ts";
import { createHarness, getUserTexts, type Harness } from "./harness.ts";

function writeJson(path: string, value: unknown): void {
	writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, "utf-8");
}

/** Minimal UI context that records notify() calls; every dialog method is a no-op stub. */
function createNotifyCapturingUiContext(
	onNotify: (message: string, type: "info" | "warning" | "error" | undefined) => void,
): ExtensionUIContext {
	return {
		select: async () => undefined,
		confirm: async () => false,
		input: async () => undefined,
		notify: onNotify,
		onTerminalInput: () => () => {},
		setStatus: () => {},
		setWorkingMessage: () => {},
		setWorkingVisible: () => {},
		setWorkingIndicator: () => {},
		setHiddenThinkingLabel: () => {},
		setWidget: () => {},
		setFooter: () => {},
		setHeader: () => {},
		setTitle: () => {},
		custom: async <T>() => undefined as T,
		pasteToEditor: () => {},
		setEditorText: () => {},
		getEditorText: () => "",
		editor: async () => undefined,
		addAutocompleteProvider: () => {},
		setEditorComponent: () => {},
		getEditorComponent: () => undefined,
		theme: {} as ExtensionUIContext["theme"],
		getAllThemes: () => [],
		getTheme: () => undefined,
		setTheme: () => ({ success: false, error: "Theme switching not available in tests" }),
		getToolsExpanded: () => false,
		setToolsExpanded: () => {},
	};
}

function setupSkillPackage(
	tempDir: string,
	options?: {
		dirName?: string;
		name?: string;
		command?: string;
		hook?: string;
		artifacts?: Array<{ type?: string; path?: string; required?: boolean; description?: string }>;
		root?: ".sure/skills" | "sure/skills";
		prompt?: string;
	},
): void {
	const name = options?.name ?? "demo";
	const skillDir = join(tempDir, options?.root ?? ".sure/skills", options?.dirName ?? name);
	mkdirSync(join(skillDir, "skill"), { recursive: true });
	mkdirSync(join(skillDir, "hooks"), { recursive: true });
	writeFileSync(
		join(skillDir, "skill", "SKILL.md"),
		options?.prompt ?? "Collect evidence and write the final manifest.",
		"utf-8",
	);
	if (options?.hook) {
		writeFileSync(join(skillDir, "hooks", "index.ts"), options.hook, "utf-8");
	}
	writeJson(join(skillDir, "sure.skill.json"), {
		name,
		command: options?.command ?? "sure_feed",
		description: "Demo Sure skill",
		prompt: "skill/SKILL.md",
		hooks: options?.hook
			? {
					pre_finish: [{ module: "hooks/index.ts", handler: "preFinish" }],
				}
			: undefined,
		artifacts: options?.artifacts,
	});
}

function linkRepositorySkill(tempDir: string, skillName: string): void {
	const target = resolve(__dirname, "../../../../sure/skills", skillName);
	const parent = join(tempDir, "sure", "skills");
	mkdirSync(parent, { recursive: true });
	symlinkSync(target, join(parent, skillName), "dir");
}

function writeOnboardModelInput(path: string): void {
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
			"weights:",
			"  source: huggingface",
			"environment_hint:",
			"  preferred_backend: uv",
			"  python_version: 3.10",
		].join("\n")}\n`,
		"utf-8",
	);
}

function writeEvalModel(tempDir: string, modelName = "demo-asr"): string {
	const modelDir = join(tempDir, "sure", "models", modelName);
	mkdirSync(modelDir, { recursive: true });
	writeFileSync(
		join(modelDir, "config.yaml"),
		`${["model:", `  name: ${modelName}`, "  task: ASR", "server:", "  command: [python, server.py]"].join("\n")}\n`,
		"utf-8",
	);
	writeFileSync(join(modelDir, "model.py"), "# test fixture\n", "utf-8");
	writeFileSync(join(modelDir, "server.py"), "# test fixture\n", "utf-8");
	writeJson(join(modelDir, "verdict.json"), { status: "success" });
	return modelDir;
}

async function createSureHarness(options?: {
	projectTrusted?: boolean;
	uiContext?: ExtensionUIContext;
	mode?: ExtensionMode;
}): Promise<Harness> {
	const harness = await createHarness({
		extensionFactories: [sureExtension],
		settings: {},
	});
	if (options?.projectTrusted !== undefined) {
		harness.settingsManager.setProjectTrusted(options.projectTrusted);
	}
	await harness.session.bindExtensions({
		uiContext: options?.uiContext,
		mode: options?.mode ?? "tui",
		onError: (error) => {
			throw new Error(JSON.stringify(error));
		},
		commandContextActions: {
			waitForIdle: () => harness.session.agent.waitForIdle(),
			newSession: async () => ({ cancelled: false }),
			fork: async () => ({ cancelled: false }),
			navigateTree: async () => ({ cancelled: false }),
			switchSession: async () => ({ cancelled: false }),
			reload: async () => {},
		},
	});
	return harness;
}

function writeValidManifest(
	tempDir: string,
	runId: string,
	overrides: Partial<{
		schema_version: string;
		run_id: string;
		skill_name: string;
		status: string;
		created_at: string;
		inputs: unknown;
		outputs: unknown;
		validation: unknown;
		artifacts: unknown;
	}> = {},
): void {
	writeJson(join(tempDir, ".sure", "runs", runId, "manifest.json"), {
		schema_version: "1",
		run_id: runId,
		skill_name: "demo",
		status: "success",
		created_at: new Date().toISOString(),
		inputs: {},
		outputs: {},
		validation: {},
		...overrides,
	});
}

async function waitForCondition(predicate: () => boolean): Promise<void> {
	for (let i = 0; i < 100; i++) {
		if (predicate()) {
			return;
		}
		await new Promise((resolve) => setTimeout(resolve, 10));
	}
}

function getOnlyRunId(tempDir: string): string {
	const entries = readdirSync(join(tempDir, ".sure", "runs"), { withFileTypes: true });
	const runIds = entries.filter((entry) => entry.isDirectory()).map((entry) => entry.name);
	expect(runIds).toHaveLength(1);
	return runIds[0];
}

function readRunState(tempDir: string, runId: string): unknown {
	return JSON.parse(readFileSync(join(tempDir, ".sure", "runs", runId, "state.json"), "utf-8"));
}

describe("Sure extension", () => {
	const cleanups: Array<() => void> = [];

	afterEach(() => {
		while (cleanups.length > 0) {
			cleanups.pop()?.();
		}
	});

	it("starts a skill run from a slash command and activates sure_finish only for that run", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir);

		expect(harness.session.getActiveToolNames()).not.toContain("sure_finish");
		harness.setResponses([fauxAssistantMessage("working")]);

		await harness.session.prompt("/sure_feed graph neural networks");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => getUserTexts(harness).length > 0);

		expect(getUserTexts(harness)[0]).toContain("<sure_invocation");
		expect(getUserTexts(harness)[0]).toContain("graph neural networks");
		expect(harness.session.getActiveToolNames()).toContain("sure_finish");
		expect(harness.session.getActiveToolNames()).toContain("sure_update_state");
		expect(existsSync(join(harness.tempDir, ".sure", "runs"))).toBe(true);
	});

	it("starts the real /sure_onboard repository skill and applies MODEL_INPUT pre-start state", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		linkRepositorySkill(harness.tempDir, "sure_onboard");
		const modelInputPath = join(
			harness.tempDir,
			"sure",
			"handoffs",
			"rednote-hilab__dots.tts-base",
			"model_input.yaml",
		);
		writeOnboardModelInput(modelInputPath);
		harness.setResponses([fauxAssistantMessage("working")]);

		await harness.session.prompt("/sure_onboard model=rednote-hilab__dots.tts-base package=none");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => getUserTexts(harness).length > 0);

		const runId = getOnlyRunId(harness.tempDir);
		expect(getUserTexts(harness)[0]).toContain("<sure_invocation");
		expect(getUserTexts(harness)[0]).toContain('skill="sure_onboard" command="/sure_onboard"');
		expect(getUserTexts(harness)[0]).toContain("# /sure_onboard");
		expect(harness.session.getActiveToolNames()).toContain("sure_finish");
		expect(readRunState(harness.tempDir, runId)).toMatchObject({
			phase: { id: "load_model_input", status: "running" },
			checkpoint: {
				id: "main_flow",
				data: {
					currentUnit: "load_model_input",
					completedUnits: [],
				},
			},
		});
		expect(existsSync(join(harness.tempDir, "sure", "models"))).toBe(true);
		expect(existsSync(join(harness.tempDir, "sure", "models", "rednote-hilab__dots.tts-base"))).toBe(false);
	});

	it("starts the real /sure_reval repository skill and resolves an existing prediction source", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		linkRepositorySkill(harness.tempDir, "sure_eval");
		linkRepositorySkill(harness.tempDir, "sure_reval");
		const sourceDir = join(harness.tempDir, "source-result");
		mkdirSync(join(sourceDir, "predictions"), { recursive: true });
		writeFileSync(join(sourceDir, "predictions", "demo_dataset.txt"), "utt1\thello world\n", "utf-8");
		harness.setResponses([fauxAssistantMessage("working")]);

		await harness.session.prompt(
			`/sure_reval source=${sourceDir} model=demo_model datasets=demo_dataset max_samples=1`,
		);
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => getUserTexts(harness).length > 0);

		const runId = getOnlyRunId(harness.tempDir);
		expect(getUserTexts(harness)[0]).toContain("<sure_invocation");
		expect(getUserTexts(harness)[0]).toContain('skill="sure_reval" command="/sure_reval"');
		expect(getUserTexts(harness)[0]).toContain("# /sure_reval");
		expect(harness.session.getActiveToolNames()).toContain("sure_finish");
		expect(
			existsSync(join(harness.tempDir, ".sure", "runs", runId, "artifacts", "prediction_source_resolved.json")),
		).toBe(true);
		expect(readRunState(harness.tempDir, runId)).toMatchObject({
			phase: { id: "prediction_source", status: "running" },
		});
	});

	it("resolves /sure_reval relative source paths against the project cwd", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		linkRepositorySkill(harness.tempDir, "sure_eval");
		linkRepositorySkill(harness.tempDir, "sure_reval");
		const sourceDir = join(harness.tempDir, "relative-source-result");
		mkdirSync(join(sourceDir, "predictions"), { recursive: true });
		writeFileSync(join(sourceDir, "predictions", "demo_dataset.txt"), "utt1\thello world\n", "utf-8");
		harness.setResponses([fauxAssistantMessage("working")]);

		await harness.session.prompt(`/sure_reval source=relative-source-result model=demo_model datasets=demo_dataset`);
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => getUserTexts(harness).length > 0);

		const runId = getOnlyRunId(harness.tempDir);
		const resolved = JSON.parse(
			readFileSync(
				join(harness.tempDir, ".sure", "runs", runId, "artifacts", "prediction_source_resolved.json"),
				"utf-8",
			),
		);
		expect(resolved.source).toBe(sourceDir);
		expect(readRunState(harness.tempDir, runId)).toMatchObject({
			phase: { id: "prediction_source", status: "running" },
		});
	});

	it("passes output_dir from /sure_eval arguments into resolved input", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		linkRepositorySkill(harness.tempDir, "sure_eval");
		writeEvalModel(harness.tempDir);
		const datasetsRoot = join(harness.tempDir, "data", "datasets");
		const jsonlDir = join(datasetsRoot, "sure_benchmark", "jsonl");
		mkdirSync(jsonlDir, { recursive: true });
		const datasetId = "aispeech_phy_librispeech-test-clean__v1.0.1";
		writeFileSync(
			join(jsonlDir, `${datasetId}.jsonl`),
			`${JSON.stringify({
				key: "utt1",
				path: "utt1.wav",
				target: "hello",
				task: "ASR",
				language: "en",
				dataset: datasetId,
				metadata: {
					source: "aispeech_ds_pool",
					source_dataset_root: "/srv/sure/datasets/group/store/ds_pool/example-librispeech-test-clean",
					source_dataset_name: "aispeech_phy_librispeech-test-clean",
					version_id: "v1.0.1",
				},
			})}\n`,
			"utf-8",
		);
		const outputDir = join(harness.tempDir, "custom-eval-output");
		harness.setResponses([fauxAssistantMessage("working")]);
		process.env.SURE_EVAL_DATASETS_ROOT = datasetsRoot;
		try {
			await harness.session.prompt(
				`/sure_eval model=demo-asr datasets=${datasetId} max_samples=1 metrics=wer output_dir=${outputDir}`,
			);
			await harness.session.agent.waitForIdle();
			await waitForCondition(() => getUserTexts(harness).length > 0);
		} finally {
			delete process.env.SURE_EVAL_DATASETS_ROOT;
		}

		const runId = getOnlyRunId(harness.tempDir);
		const resolvedInput = JSON.parse(
			readFileSync(join(harness.tempDir, ".sure", "runs", runId, "artifacts", "eval_input_resolved.json"), "utf-8"),
		);
		expect(resolvedInput.runtime.run_dir).toBe(outputDir);
		expect(existsSync(join(outputDir, "_harness_config.yaml"))).toBe(true);
	});

	it("updates active run display state through sure_update_state", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir);

		harness.setResponses([
			fauxAssistantMessage(
				fauxToolCall("sure_update_state", {
					phase: { id: "scan", label: "Scanning candidates", status: "running", progress: 0.4 },
					message: "Collected 12 candidates.",
					counters: { candidates: 12, target: 50 },
					next_actions: ["Continue metadata collection"],
				}),
			),
			fauxAssistantMessage("still working"),
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		const runId = getOnlyRunId(harness.tempDir);
		expect(readRunState(harness.tempDir, runId)).toMatchObject({
			phase: { id: "scan", label: "Scanning candidates", status: "running", progress: 0.4 },
			message: "Collected 12 candidates.",
			counters: { candidates: 12, target: 50 },
			next_actions: ["Continue metadata collection"],
		});
		expect(
			harness.sessionManager
				.getEntries()
				.some((entry) => entry.type === "custom" && entry.customType === "sure.state"),
		).toBe(true);
	});

	it("rejects invalid sure_update_state payloads without writing state", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir);

		harness.setResponses([
			fauxAssistantMessage(
				fauxToolCall("sure_update_state", {
					counters: { candidates: "twelve" },
				}),
			),
			fauxAssistantMessage("repairing"),
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		const runId = getOnlyRunId(harness.tempDir);
		const toolResult = harness.session.messages.find((message) => message.role === "toolResult");
		expect(toolResult && "isError" in toolResult ? toolResult.isError : false).toBe(true);
		expect(existsSync(join(harness.tempDir, ".sure", "runs", runId, "state.json"))).toBe(false);
	});

	it("rejects checkpoint updates from sure_update_state", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir);

		harness.setResponses([
			fauxAssistantMessage(
				fauxToolCall("sure_update_state", {
					message: "Trying to advance internally.",
					checkpoint: {
						id: "main_flow",
						data: { currentUnit: "run_report", completedUnits: ["all"], retries: {} },
					},
				}),
			),
			fauxAssistantMessage("repairing"),
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		const runId = getOnlyRunId(harness.tempDir);
		const toolResult = harness.session.messages.find((message) => message.role === "toolResult");
		expect(toolResult && "isError" in toolResult ? toolResult.isError : false).toBe(true);
		expect(toolResult && "content" in toolResult ? JSON.stringify(toolResult.content) : "").toContain(
			"cannot update checkpoints",
		);
		expect(existsSync(join(harness.tempDir, ".sure", "runs", runId, "state.json"))).toBe(false);
	});

	it("rejects terminal progress updates from sure_update_state during checkpoint-controlled runs", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		linkRepositorySkill(harness.tempDir, "sure_onboard");
		const modelInputPath = join(
			harness.tempDir,
			"sure",
			"handoffs",
			"rednote-hilab__dots.tts-base",
			"model_input.yaml",
		);
		writeOnboardModelInput(modelInputPath);

		harness.setResponses([
			fauxAssistantMessage(
				fauxToolCall("sure_update_state", {
					phase: { id: "load_model_input", label: "Load input", status: "success" },
					counters: { completed_units: 1, total_units: 9, gate_blocks: 0 },
				}),
			),
			fauxAssistantMessage("repairing"),
		]);

		await harness.session.prompt("/sure_onboard model=rednote-hilab__dots.tts-base package=none");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		const runId = getOnlyRunId(harness.tempDir);
		const toolResult = harness.session.messages.find((message) => message.role === "toolResult");
		expect(toolResult && "isError" in toolResult ? toolResult.isError : false).toBe(true);
		expect(toolResult && "content" in toolResult ? JSON.stringify(toolResult.content) : "").toContain(
			"cannot set terminal phase status",
		);
		expect(readRunState(harness.tempDir, runId)).toMatchObject({
			phase: { id: "load_model_input", status: "running" },
			checkpoint: {
				id: "main_flow",
				data: {
					currentUnit: "load_model_input",
					completedUnits: [],
				},
			},
		});
	});

	it("allows ready checkpoint artifacts when an external absolute path exists", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		linkRepositorySkill(harness.tempDir, "sure_onboard");
		const modelInputPath = join(
			harness.tempDir,
			"sure",
			"handoffs",
			"rednote-hilab__dots.tts-base",
			"model_input.yaml",
		);
		writeOnboardModelInput(modelInputPath);
		const outputDir = join(harness.tempDir, "external-output");
		mkdirSync(outputDir, { recursive: true });

		harness.setResponses([
			fauxAssistantMessage(
				fauxToolCall("sure_update_state", {
					message: "Output copied.",
					artifacts: [
						{
							type: "output_dir",
							name: "Output directory",
							path: outputDir,
							status: "ready",
						},
					],
				}),
			),
			fauxAssistantMessage("still working"),
		]);

		await harness.session.prompt("/sure_onboard model=rednote-hilab__dots.tts-base package=none");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		const runId = getOnlyRunId(harness.tempDir);
		const toolResult = harness.session.messages.find((message) => message.role === "toolResult");
		expect(toolResult && "isError" in toolResult ? toolResult.isError : false).toBe(false);
		expect(readRunState(harness.tempDir, runId)).toMatchObject({
			message: "Output copied.",
			artifacts: [{ type: "output_dir", path: outputDir, status: "ready" }],
		});
	});

	it("persists display state patches returned by hooks", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir, {
			hook: `export function preFinish() { return { ok: false, repair: "need more evidence", state_patch: { phase: { id: "validate", label: "Validating output", status: "blocked" }, counters: { collected: 3, target: 10 }, diagnostics: [{ severity: "warning", message: "Evidence below target.", repair: "Collect additional evidence." }], checkpoint: { id: "hook_gate", label: "Hook-owned checkpoint", data: { currentUnit: "validate" } } } }; }`,
		});

		harness.setResponses([
			() => {
				const runId = getOnlyRunId(harness.tempDir);
				writeValidManifest(harness.tempDir, runId);
				return fauxAssistantMessage(
					fauxToolCall("sure_finish", {
						status: "success",
						manifest_path: `.sure/runs/${runId}/manifest.json`,
						summary: "done",
					}),
				);
			},
			fauxAssistantMessage("will repair"),
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		const runId = getOnlyRunId(harness.tempDir);
		expect(readRunState(harness.tempDir, runId)).toMatchObject({
			phase: { id: "validate", label: "Validating output", status: "blocked" },
			counters: { collected: 3, target: 10 },
			diagnostics: [
				{ severity: "warning", message: "Evidence below target.", repair: "Collect additional evidence." },
			],
			checkpoint: { id: "hook_gate", label: "Hook-owned checkpoint", data: { currentUnit: "validate" } },
		});
	});

	it("does not start a skill run before project trust", async () => {
		const harness = await createSureHarness({ projectTrusted: false });
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir);

		harness.setResponses([fauxAssistantMessage("should not run")]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();

		expect(getUserTexts(harness)).toHaveLength(0);
		expect(existsSync(join(harness.tempDir, ".sure", "runs"))).toBe(false);
		expect(harness.session.getActiveToolNames()).not.toContain("sure_finish");
	});

	it("rejects sure_finish with repair instructions when the manifest is missing", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir);

		harness.setResponses([
			fauxAssistantMessage(
				fauxToolCall("sure_finish", {
					status: "success",
					manifest_path: ".sure/runs/missing/manifest.json",
					summary: "done",
				}),
			),
			fauxAssistantMessage("repairing"),
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		const toolResult = harness.session.messages.find((message) => message.role === "toolResult");
		expect(toolResult).toBeDefined();
		expect(toolResult && "isError" in toolResult ? toolResult.isError : false).toBe(true);
		expect(toolResult && "content" in toolResult ? JSON.stringify(toolResult.content) : "").toContain(
			"Create the final manifest",
		);
		expect(harness.session.getActiveToolNames()).toContain("sure_finish");
	});

	it("finishes a run after manifest validation and restores previous active tools", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir);

		harness.setResponses([
			() => {
				const runId = getOnlyRunId(harness.tempDir);
				writeValidManifest(harness.tempDir, runId);
				return fauxAssistantMessage(
					fauxToolCall("sure_finish", {
						status: "success",
						manifest_path: `.sure/runs/${runId}/manifest.json`,
						summary: "done",
					}),
				);
			},
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		const toolResult = harness.session.messages.find((message) => message.role === "toolResult");
		expect(toolResult && "content" in toolResult ? JSON.stringify(toolResult.content) : "").toContain("finished");
		expect(harness.session.getActiveToolNames()).not.toContain("sure_finish");
		expect(harness.session.getActiveToolNames()).not.toContain("sure_update_state");
	});

	it("clears stale repair instructions after a later successful finish", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir);

		harness.setResponses([
			() => {
				const runId = getOnlyRunId(harness.tempDir);
				return fauxAssistantMessage(
					fauxToolCall("sure_finish", {
						status: "success",
						manifest_path: `.sure/runs/${runId}/missing-manifest.json`,
						summary: "not yet",
					}),
				);
			},
			() => {
				const runId = getOnlyRunId(harness.tempDir);
				writeValidManifest(harness.tempDir, runId);
				return fauxAssistantMessage(
					fauxToolCall("sure_finish", {
						status: "success",
						manifest_path: `.sure/runs/${runId}/manifest.json`,
						summary: "done",
					}),
				);
			},
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(
			() => harness.session.messages.filter((message) => message.role === "toolResult").length >= 2,
		);

		const runId = getOnlyRunId(harness.tempDir);
		const run = JSON.parse(readFileSync(join(harness.tempDir, ".sure", "runs", runId, "run.json"), "utf-8"));
		expect(run.status).toBe("success");
		expect(run.lastRepair).toBeUndefined();
	});

	it("nudges a print-mode run to continue when a turn ends without sure_finish", async () => {
		const harness = await createSureHarness({ mode: "print" });
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir);

		harness.setResponses([
			fauxAssistantMessage("pausing before the manifest"),
			() => {
				const runId = getOnlyRunId(harness.tempDir);
				writeValidManifest(harness.tempDir, runId);
				return fauxAssistantMessage(
					fauxToolCall("sure_finish", {
						status: "success",
						manifest_path: `.sure/runs/${runId}/manifest.json`,
						summary: "done",
					}),
				);
			},
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();

		const runId = getOnlyRunId(harness.tempDir);
		const run = JSON.parse(readFileSync(join(harness.tempDir, ".sure", "runs", runId, "run.json"), "utf-8"));
		expect(run.status).toBe("success");
		expect(getUserTexts(harness).some((text) => text.includes("is still active"))).toBe(true);
	});

	it("stops nudging a print-mode run after the retry budget and fails it on shutdown", async () => {
		const harness = await createSureHarness({ mode: "print" });
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir);

		harness.setResponses([
			fauxAssistantMessage("turn 1"),
			fauxAssistantMessage("turn 2"),
			fauxAssistantMessage("turn 3"),
			fauxAssistantMessage("turn 4"),
			fauxAssistantMessage("turn 5"),
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();

		expect(harness.getPendingResponseCount()).toBe(1);

		await emitSessionShutdownEvent(harness.session.extensionRunner, { type: "session_shutdown", reason: "quit" });

		const runId = getOnlyRunId(harness.tempDir);
		const run = JSON.parse(readFileSync(join(harness.tempDir, ".sure", "runs", runId, "run.json"), "utf-8"));
		expect(run.status).toBe("failed");
		expect(run.lastRepair).toContain("sure_finish");
	});

	it("keeps cancelled status when the run was interrupted rather than left unfinished", async () => {
		const harness = await createSureHarness({ mode: "print" });
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir);

		harness.setResponses([fauxAssistantMessage("interrupted", { stopReason: "aborted" })]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();

		await emitSessionShutdownEvent(harness.session.extensionRunner, { type: "session_shutdown", reason: "quit" });

		const runId = getOnlyRunId(harness.tempDir);
		const run = JSON.parse(readFileSync(join(harness.tempDir, ".sure", "runs", runId, "run.json"), "utf-8"));
		expect(run.status).toBe("cancelled");
	});

	it("accepts required artifacts recorded with concrete run-relative paths", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir, {
			artifacts: [
				{
					type: "verdict",
					path: "artifacts/verdict.json",
					required: true,
					description: "Final verdict.",
				},
			],
		});

		harness.setResponses([
			() => {
				const runId = getOnlyRunId(harness.tempDir);
				mkdirSync(join(harness.tempDir, ".sure", "runs", runId, "artifacts"), { recursive: true });
				writeJson(join(harness.tempDir, ".sure", "runs", runId, "artifacts", "verdict.json"), {
					status: "passed",
				});
				writeValidManifest(harness.tempDir, runId, {
					artifacts: [
						{
							type: "json",
							path: `.sure/runs/${runId}/artifacts/verdict.json`,
						},
					],
				});
				return fauxAssistantMessage(
					fauxToolCall("sure_finish", {
						status: "success",
						manifest_path: `.sure/runs/${runId}/manifest.json`,
						summary: "done",
					}),
				);
			},
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		const toolResult = harness.session.messages.find((message) => message.role === "toolResult");
		expect(toolResult && "content" in toolResult ? JSON.stringify(toolResult.content) : "").toContain("finished");
		expect(harness.session.getActiveToolNames()).not.toContain("sure_finish");
	});

	it("accepts required artifacts recorded in final manifest outputs", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir, {
			artifacts: [
				{
					type: "model_input",
					path: "artifacts/model_input.yaml",
					required: true,
					description: "Run-local MODEL_INPUT YAML.",
				},
			],
		});

		harness.setResponses([
			() => {
				const runId = getOnlyRunId(harness.tempDir);
				mkdirSync(join(harness.tempDir, ".sure", "runs", runId, "artifacts"), { recursive: true });
				writeFileSync(
					join(harness.tempDir, ".sure", "runs", runId, "artifacts", "model_input.yaml"),
					"model_id: demo\n",
				);
				writeValidManifest(harness.tempDir, runId, {
					outputs: {
						model_input: `.sure/runs/${runId}/artifacts/model_input.yaml`,
					},
				});
				return fauxAssistantMessage(
					fauxToolCall("sure_finish", {
						status: "success",
						manifest_path: `.sure/runs/${runId}/manifest.json`,
						summary: "done",
					}),
				);
			},
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		const toolResult = harness.session.messages.find((message) => message.role === "toolResult");
		expect(toolResult && "content" in toolResult ? JSON.stringify(toolResult.content) : "").toContain("finished");
		expect(harness.session.getActiveToolNames()).not.toContain("sure_finish");
	});

	it("removes only sure_finish when a run ends", async () => {
		const extensionFactories: ExtensionFactory[] = [
			sureExtension,
			(pi) => {
				pi.registerTool({
					name: "extra_tool",
					label: "Extra Tool",
					description: "Extra tool enabled during a run",
					promptSnippet: "Run extra test behavior",
					parameters: Type.Object({}),
					execute: async () => ({ content: [{ type: "text", text: "ok" }], details: {} }),
				});
			},
		];
		const harness = await createHarness({ extensionFactories });
		cleanups.push(harness.cleanup);
		await harness.session.bindExtensions({
			commandContextActions: {
				waitForIdle: () => harness.session.agent.waitForIdle(),
				newSession: async () => ({ cancelled: false }),
				fork: async () => ({ cancelled: false }),
				navigateTree: async () => ({ cancelled: false }),
				switchSession: async () => ({ cancelled: false }),
				reload: async () => {},
			},
		});
		setupSkillPackage(harness.tempDir);
		harness.session.setActiveToolsByName(["read"]);

		harness.setResponses([
			() => {
				harness.session.setActiveToolsByName(["read", "extra_tool", "sure_finish", "sure_update_state"]);
				const runId = getOnlyRunId(harness.tempDir);
				writeValidManifest(harness.tempDir, runId);
				return fauxAssistantMessage(
					fauxToolCall("sure_finish", {
						status: "success",
						manifest_path: `.sure/runs/${runId}/manifest.json`,
						summary: "done",
					}),
				);
			},
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		expect(harness.session.getActiveToolNames().sort()).toEqual(["extra_tool", "read"]);
	});

	it("discovers repository skill packages from sure/skills", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir, {
			root: "sure/skills",
			prompt: "Repository skill prompt.",
		});

		harness.setResponses([fauxAssistantMessage("working")]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => getUserTexts(harness).length > 0);

		expect(getUserTexts(harness)[0]).toContain("Repository skill prompt.");
	});

	it("lets project .sure skills override repository skills for the same command", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir, {
			root: "sure/skills",
			dirName: "repo",
			name: "repo",
			prompt: "Repository skill prompt.",
		});
		setupSkillPackage(harness.tempDir, {
			root: ".sure/skills",
			dirName: "project",
			name: "project",
			prompt: "Project skill prompt.",
		});

		harness.setResponses([fauxAssistantMessage("working")]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => getUserTexts(harness).length > 0);

		expect(getUserTexts(harness)[0]).toContain("Project skill prompt.");
		expect(getUserTexts(harness)[0]).not.toContain("Repository skill prompt.");
	});

	it("rejects sure_finish when manifest status does not match the finish status", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir);

		harness.setResponses([
			() => {
				const runId = getOnlyRunId(harness.tempDir);
				writeValidManifest(harness.tempDir, runId, { status: "failed" });
				return fauxAssistantMessage(
					fauxToolCall("sure_finish", {
						status: "success",
						manifest_path: `.sure/runs/${runId}/manifest.json`,
						summary: "done",
					}),
				);
			},
			fauxAssistantMessage("repairing"),
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		const toolResult = harness.session.messages.find((message) => message.role === "toolResult");
		expect(toolResult && "content" in toolResult ? JSON.stringify(toolResult.content) : "").toContain(
			"status must match sure_finish status",
		);
		expect(harness.session.getActiveToolNames()).toContain("sure_finish");
	});

	it("rejects sure_finish when required artifacts are missing", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir, {
			artifacts: [{ type: "report", path: "report.md", required: true }],
		});

		harness.setResponses([
			() => {
				const runId = getOnlyRunId(harness.tempDir);
				writeValidManifest(harness.tempDir, runId, { artifacts: [] });
				return fauxAssistantMessage(
					fauxToolCall("sure_finish", {
						status: "success",
						manifest_path: `.sure/runs/${runId}/manifest.json`,
						summary: "done",
					}),
				);
			},
			fauxAssistantMessage("repairing"),
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		const toolResult = harness.session.messages.find((message) => message.role === "toolResult");
		expect(toolResult && "content" in toolResult ? JSON.stringify(toolResult.content) : "").toContain(
			"missing required artifact",
		);
	});

	it("rejects required artifact declarations without a path", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir, {
			artifacts: [{ type: "report", required: true }],
		});

		harness.setResponses([
			() => {
				const runId = getOnlyRunId(harness.tempDir);
				writeValidManifest(harness.tempDir, runId, { artifacts: [] });
				return fauxAssistantMessage(
					fauxToolCall("sure_finish", {
						status: "success",
						manifest_path: `.sure/runs/${runId}/manifest.json`,
						summary: "done",
					}),
				);
			},
			fauxAssistantMessage("repairing"),
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		const toolResult = harness.session.messages.find((message) => message.role === "toolResult");
		expect(toolResult && "content" in toolResult ? JSON.stringify(toolResult.content) : "").toContain(
			"must declare a path",
		);
	});

	it("rejects final manifest artifacts without paths", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir);

		harness.setResponses([
			() => {
				const runId = getOnlyRunId(harness.tempDir);
				writeValidManifest(harness.tempDir, runId, { artifacts: [{ type: "report" }] });
				return fauxAssistantMessage(
					fauxToolCall("sure_finish", {
						status: "success",
						manifest_path: `.sure/runs/${runId}/manifest.json`,
						summary: "done",
					}),
				);
			},
			fauxAssistantMessage("repairing"),
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		const toolResult = harness.session.messages.find((message) => message.role === "toolResult");
		expect(toolResult && "content" in toolResult ? JSON.stringify(toolResult.content) : "").toContain(
			"must include a path",
		);
	});

	it("runs pre-finish hooks and returns repair without terminating when rejected", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir, {
			hook: `export function preFinish() { return { ok: false, repair: "add required evidence" }; }`,
		});

		harness.setResponses([
			() => {
				const runId = getOnlyRunId(harness.tempDir);
				writeValidManifest(harness.tempDir, runId);
				return fauxAssistantMessage(
					fauxToolCall("sure_finish", {
						status: "success",
						manifest_path: `.sure/runs/${runId}/manifest.json`,
						summary: "done",
					}),
				);
			},
			fauxAssistantMessage("will repair"),
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		await waitForCondition(() => harness.session.messages.some((message) => message.role === "toolResult"));

		const toolResult = harness.session.messages.find((message) => message.role === "toolResult");
		expect(toolResult && "content" in toolResult ? JSON.stringify(toolResult.content) : "").toContain(
			"add required evidence",
		);
		expect(harness.session.getActiveToolNames()).toContain("sure_finish");
	});

	it("skips duplicate and unknown skill commands during discovery", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		setupSkillPackage(harness.tempDir, { dirName: "first", name: "first", command: "sure_feed" });
		setupSkillPackage(harness.tempDir, { dirName: "second", name: "second", command: "sure_feed" });
		setupSkillPackage(harness.tempDir, { dirName: "unknown", name: "unknown", command: "unknown_sure" });

		harness.setResponses([fauxAssistantMessage("should not run")]);
		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();

		expect(getUserTexts(harness)).toHaveLength(0);
		expect(existsSync(join(harness.tempDir, ".sure", "runs"))).toBe(false);
	});

	it("does not inject default Sure extension when extensions are disabled", async () => {
		const services = await createAgentSessionServices({
			cwd: "/tmp",
			resourceLoaderOptions: {
				noExtensions: true,
				noSkills: true,
				noPromptTemplates: true,
				noThemes: true,
				noContextFiles: true,
			},
		});

		expect(services.resourceLoader.getExtensions().extensions).toHaveLength(0);
	});

	it("initializes SURE via /sure_init with headless args", async () => {
		const harness = await createSureHarness();
		cleanups.push(harness.cleanup);
		for (const command of ["sure_feed", "sure_onboard", "sure_eval", "sure_reval"]) {
			setupSkillPackage(harness.tempDir, { dirName: command, name: command, command });
		}

		const inMemorySettings = SettingsManager.inMemory();
		const createSpy = vi.spyOn(SettingsManager, "create").mockReturnValue(inMemorySettings);

		await harness.session.prompt("/sure_init --option kimi-code --api-key sk-test-123 --model kimi-for-coding");
		await harness.session.agent.waitForIdle();

		createSpy.mockRestore();

		const manifestPath = join(harness.tempDir, ".sure", "init.json");
		expect(existsSync(manifestPath)).toBe(true);
		const manifest = JSON.parse(readFileSync(manifestPath, "utf-8"));
		expect(manifest.defaultProvider).toBe("kimi-coding");
		expect(manifest.defaultModel).toBe("kimi-for-coding");
		expect(manifest.availableSkills).toContain("/sure_feed");
		expect(manifest.availableSkills).toContain("/sure_onboard");
		expect(manifest.availableSkills).toContain("/sure_eval");
		expect(manifest.availableSkills).toContain("/sure_reval");

		expect(inMemorySettings.getGlobalSettings().defaultProvider).toBe("kimi-coding");
		expect(inMemorySettings.getGlobalSettings().defaultModel).toBe("kimi-for-coding");

		const stored = harness.authStorage.get("kimi-coding");
		expect(stored?.type).toBe("api_key");
		if (stored?.type === "api_key") {
			expect(stored.key).toBe("sk-test-123");
		}

		expect(harness.session.model?.provider).toBe("kimi-coding");
		expect(harness.session.model?.id).toBe("kimi-for-coding");
	});

	it("switches the active session model and notifies success after /sure_init", async () => {
		const notifications: Array<{ message: string; type: "info" | "warning" | "error" | undefined }> = [];
		const harness = await createSureHarness({
			uiContext: createNotifyCapturingUiContext((message, type) => notifications.push({ message, type })),
		});
		cleanups.push(harness.cleanup);

		const previousModel = harness.session.model;
		expect(previousModel?.provider).not.toBe("kimi-coding");

		await harness.session.prompt("/sure_init --option kimi-code --api-key sk-test-123 --model kimi-for-coding");
		await harness.session.agent.waitForIdle();

		expect(harness.session.model?.provider).toBe("kimi-coding");
		expect(harness.session.model?.id).toBe("kimi-for-coding");

		// The TUI rewrites the status line on every consecutive info notify, so the switch used
		// to erase everything init reported. Both have to arrive in the one info notify.
		const lastInfo = notifications.filter((entry) => entry.type === "info").at(-1);
		expect(lastInfo?.message).toContain("SURE initialized");
		expect(lastInfo?.message).toContain("Switched to kimi-coding/kimi-for-coding.");
	});
});

describe("switchToInitializedModel", () => {
	function makeSwitchContext(found: boolean) {
		const notify = vi.fn();
		const ctx = {
			ui: { notify },
			modelRegistry: {
				refresh: vi.fn(),
				find: vi.fn(() => (found ? { provider: "apifusion", id: "gpt-5.6-sol" } : undefined)),
			},
		} as unknown as ExtensionCommandContext;
		return { ctx, notify };
	}

	it("applies the level init measured and returns the line instead of notifying", async () => {
		const { ctx, notify } = makeSwitchContext(true);
		const setThinkingLevel = vi.fn();
		const pi = { setModel: vi.fn(async () => true), setThinkingLevel } as unknown as ExtensionAPI;

		const line = await switchToInitializedModel(pi, ctx, {
			defaultProvider: "apifusion",
			defaultModel: "gpt-5.6-sol",
			defaultThinkingLevel: "high",
		} as SureInitManifest);

		// Old docs said「init 记 high 但会话停在 medium」: the switch never applied the level.
		expect(setThinkingLevel).toHaveBeenCalledWith("high");
		expect(line).toBe("Switched to apifusion/gpt-5.6-sol.");
		expect(notify).not.toHaveBeenCalled();
	});

	it("leaves the session level alone when init recorded none", async () => {
		const { ctx } = makeSwitchContext(true);
		const setThinkingLevel = vi.fn();
		const pi = { setModel: vi.fn(async () => true), setThinkingLevel } as unknown as ExtensionAPI;

		const line = await switchToInitializedModel(pi, ctx, {
			defaultProvider: "apifusion",
			defaultModel: "gpt-5.6-sol",
		} as SureInitManifest);

		expect(setThinkingLevel).not.toHaveBeenCalled();
		expect(line).toBe("Switched to apifusion/gpt-5.6-sol.");
	});

	it("warns and returns nothing when the model does not resolve", async () => {
		const { ctx, notify } = makeSwitchContext(false);
		const setThinkingLevel = vi.fn();
		const pi = { setModel: vi.fn(async () => true), setThinkingLevel } as unknown as ExtensionAPI;

		const line = await switchToInitializedModel(pi, ctx, {
			defaultProvider: "apifusion",
			defaultModel: "gpt-5.6-sol",
			defaultThinkingLevel: "high",
		} as SureInitManifest);

		expect(line).toBeUndefined();
		expect(setThinkingLevel).not.toHaveBeenCalled();
		expect(notify).toHaveBeenCalledWith(expect.stringContaining("Default model set to"), "warning");
	});
});

describe("print mode extension notify", () => {
	function captureStdout(lines: string[]): () => void {
		const spy = vi.spyOn(process.stdout, "write").mockImplementation(((
			chunk: unknown,
			callback?: unknown,
		): boolean => {
			lines.push(String(chunk));
			if (typeof callback === "function") {
				(callback as () => void)();
			}
			return true;
		}) as typeof process.stdout.write);
		return () => spy.mockRestore();
	}

	it("marks an active run terminal when print mode receives SIGINT", async () => {
		const harness = await createHarness({ extensionFactories: [sureExtension] });
		setupSkillPackage(harness.tempDir);

		let releaseTurn: (() => void) | undefined;
		harness.setResponses([
			() =>
				new Promise((resolve) => {
					releaseTurn = () => resolve(fauxAssistantMessage("interrupted", { stopReason: "aborted" }));
				}),
		]);

		const runtimeHost = {
			session: harness.session,
			setRebindSession: () => {},
			dispose: async () => {
				await emitSessionShutdownEvent(harness.session.extensionRunner, {
					type: "session_shutdown",
					reason: "quit",
				});
			},
		} as unknown as Parameters<typeof runPrintMode>[0];

		const exitCalls: number[] = [];
		const exitSpy = vi.spyOn(process, "exit").mockImplementation(((code?: number) => {
			exitCalls.push(code ?? 0);
			return undefined as never;
		}) as typeof process.exit);
		const previousSigintListeners = process.listeners("SIGINT");
		process.removeAllListeners("SIGINT");

		const runFile = () => {
			const runsDir = join(harness.tempDir, ".sure", "runs");
			if (!existsSync(runsDir)) {
				return undefined;
			}
			const runIds = readdirSync(runsDir);
			if (runIds.length !== 1) {
				return undefined;
			}
			return join(runsDir, runIds[0], "run.json");
		};

		try {
			const printPromise = runPrintMode(runtimeHost, { mode: "text", initialMessage: "/sure_feed topic" });
			await waitForCondition(() => {
				const file = runFile();
				return file !== undefined && JSON.parse(readFileSync(file, "utf-8")).status === "running";
			});

			process.emit("SIGINT");
			await waitForCondition(() => exitCalls.length > 0);

			expect(exitCalls[0]).toBe(130);
			const file = runFile();
			expect(file).toBeDefined();
			expect(JSON.parse(readFileSync(file as string, "utf-8")).status).toBe("cancelled");

			releaseTurn?.();
			await printPromise;
		} finally {
			process.removeAllListeners("SIGINT");
			for (const listener of previousSigintListeners) {
				process.on("SIGINT", listener);
			}
			exitSpy.mockRestore();
			harness.cleanup();
		}
	});

	it("routes /sure notify output to stdout in print mode", async () => {
		const harness = await createHarness({ extensionFactories: [sureExtension] });
		const runtimeHost = {
			session: harness.session,
			setRebindSession: () => {},
			dispose: async () => {},
		} as unknown as Parameters<typeof runPrintMode>[0];

		const lines: string[] = [];
		const restore = captureStdout(lines);
		try {
			await runPrintMode(runtimeHost, { mode: "text", initialMessage: "/sure" });
		} finally {
			restore();
			harness.cleanup();
		}

		expect(lines.join("")).not.toBe("");
		expect(lines.join("")).toContain("No Sure skills found.");
	});
});
