import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { CheckpointData, RunCheckpoint } from "../../../../sure/skills/sure_eval/hooks/checkpoints.ts";
import { advance, bumpRetry } from "../../../../sure/skills/sure_eval/hooks/checkpoints.ts";
import { countersFor, postToolResult, preFinish, preToolCall } from "../../../../sure/skills/sure_eval/hooks/index.ts";
import { findUnit, LAST_UNIT, type Unit } from "../../../../sure/skills/sure_eval/hooks/state-machine.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// sure_eval skill package root (repo-relative from the test file).
const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_eval");

type StatePatchForTest = {
	counters?: { completed_units?: number; total_units?: number; gate_blocks?: number };
	message?: string;
	checkpoint?: { data: CheckpointData };
};

function statePatch(result: { state_patch?: unknown }): StatePatchForTest {
	return (result.state_patch ?? {}) as StatePatchForTest;
}

function freshCtx(name: string): { ctx: SureHookContext; runDir: string } {
	const runDir = resolve(__dirname, "tmp-sm", name);
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	const ctx: SureHookContext = {
		point: "post_tool_result",
		run: { id: "test-eval-sm", command: "/sure_eval", status: "running" } as never,
		skill: { name: "sure_eval", command: "/sure_eval" } as never,
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

function seedCompletedEvaluationArtifacts(runDir: string): string {
	const root = join(runDir, "model-eval-run");
	const dataset = "aishell1";
	const metric = "cer";
	mkdirSync(join(root, "predictions"), { recursive: true });
	mkdirSync(join(root, "metrics", dataset, metric), { recursive: true });
	mkdirSync(join(root, "sample_reports", dataset), { recursive: true });
	const row = {
		schema: "sure.eval.payload.dataset_metric.v2",
		dataset,
		task: "ASR",
		language: "zh",
		metric,
		pipeline_id: "asr.zh.cer.wetext_zh_itn.wenet_cer",
		evaluation_backend: "external",
		result: { score: 0, score_key: "cer", cer: 0 },
		pipeline: { pipeline_id: "asr.zh.cer.wetext_zh_itn.wenet_cer" },
		artifacts: {},
	};
	const predictionTxt = join(root, "predictions", `${dataset}.txt`);
	const predictionJsonl = join(root, "predictions", `${dataset}.jsonl`);
	const standardRow = {
		schema: "sure.eval.report.dataset_metric.v1",
		run: { run_id: "model-eval-run", protocol_id: "strict_core" },
		model: { model_name: "fixture_model", model_dir: "", tool_name: "fixture_model" },
		dataset: {
			name: dataset,
			task: "ASR",
			language: "zh",
			jsonl_path: "/tmp/aishell1.jsonl",
			num_samples: 1,
		},
		prediction: {
			file: predictionTxt,
			validation: {
				expected_samples: 1,
				provided_predictions: 1,
				missing_keys: [],
				extra_keys: [],
				duplicate_keys: [],
				empty_prediction_keys: [],
				structured_missing_keys: [],
				structured_extra_keys: [],
				structured_duplicate_keys: [],
				invalid_structured_rows: [],
				structured_projection_mismatch_keys: [],
				contract_violation_keys: [],
				is_valid: true,
				prediction_jsonl_path: predictionJsonl,
				format_used: "jsonl+txt",
			},
		},
		metric: { name: metric, score: 0, unit: "fraction", display: "0.00%", higher_is_better: false, score_key: "cer" },
		baseline: null,
		rps: null,
		pipeline: {
			pipeline_id: "asr.zh.cer.wetext_zh_itn.wenet_cer",
			report_path: join(root, "metrics", dataset, metric, "report.json"),
			description_path: join(root, "metrics", dataset, metric, "pipeline_description.json"),
			nodes: [{ node_id: "wetext_zh_itn" }, { node_id: "wenet_cer" }],
			conversion_steps: [],
		},
		versions: { evaluation_backend: "external", evaluator_version: "sure-evaluation" },
		artifacts: {
			metric_artifact_dir: join(root, "metrics", dataset, metric),
			report: join(root, "metrics", dataset, metric, "report.json"),
			pipeline_description: join(root, "metrics", dataset, metric, "pipeline_description.json"),
			sample_report: join(root, "sample_reports", dataset, `${metric}.jsonl`),
		},
		status: "success",
	};
	writeFileSync(
		join(root, "evaluation_payload.json"),
		JSON.stringify({ schema: "sure.eval.payload.v2", evaluation_backend: "external", results: [row] }, null, 2),
		"utf-8",
	);
	writeFileSync(join(root, "report.jsonl"), `${JSON.stringify(standardRow)}\n`, "utf-8");
	writeFileSync(
		join(root, "protocol.yaml"),
		[
			"schema: sure.eval.inference_protocol.v1",
			"protocol_id: strict_core",
			"run:",
			"  run_id: model-eval-run",
			`  run_dir: ${root}`,
			"  created_at: '2026-07-12T00:00:00Z'",
			"model:",
			"  model_name: fixture_model",
			"  model_dir: ''",
			"  mcp_tool_name: fixture_model",
			"  server_config: {}",
			"protocol_selection:",
			"  protocol_id: strict_core",
			"  standard_params: {}",
			"  resolved_model_params: {}",
			"  unmapped: {}",
			"inference_environment:",
			"  execution_path: vc_submit",
			"  vc: {}",
			"  container:",
			"    image_ref: registry.example.com/sure/fixture@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
			"    execution_mode: container_only",
			"    host_python_fallback: false",
			"  harness_runtime:",
			"    schema: sure.harness.runtime.binding.v1",
			"    runtime_id: sure-harness-test",
			"    python_executable: /opt/sure-harness/bin/python",
			"    lock_sha256: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
			"    manifest_path: /opt/sure-harness/runtime-manifest.json",
			"    runtime_root: /opt/sure-harness",
			"  server: {}",
			"  env: {}",
			"  runtime_inventory:",
			"    schema: sure.onboard.runtime_inventory.v2",
			"  mount_policy:",
			"    nfs_models_read_only: true",
			"inference_constraints: {}",
			"inference_parameters:",
			"  source_priority:",
			"    - prediction_generation_status.json",
			"  protocol_id: strict_core",
			"  protocol_resolution:",
			"    status: resolved",
			"    standard_params: {}",
			"    model_params: {}",
			"    unmapped: {}",
			"  argument_policy:",
			"    argument_keys:",
			"      - audio_path",
			"    dynamic_argument_fields:",
			"      - audio_path",
			"execution_surface: {}",
			"prediction_reuse:",
			"  enabled: false",
			"  generation_policy: generated_by_model_server",
			"  old_evaluation_reused: false",
			"prediction_contract:",
			"  compatibility_tsv: predictions/<dataset>.txt",
			"  structured_jsonl: predictions/<dataset>.jsonl",
			"  format_used: jsonl+txt",
			"  generated_by: scripts/generate_predictions_via_server.py",
			"provenance:",
			`  prediction_generation_status: ${join(root, "prediction_generation_status.json")}`,
			"  deployment_ready: /approved/model/artifacts/deployment_ready.json",
			"  package_gate: /approved/model/artifacts/package_gate.json",
			"  raw_response_source_of_truth: false",
			"notes:",
			"  - inference-only fixture",
			"",
		].join("\n"),
		"utf-8",
	);
	writeFileSync(
		join(root, "prediction_generation_status.json"),
		JSON.stringify({ schema: "sure.eval.prediction_generation_status.v2" }, null, 2),
		"utf-8",
	);
	writeFileSync(predictionTxt, "utt\t文本\n", "utf-8");
	writeFileSync(
		predictionJsonl,
		`${JSON.stringify({ key: "utt", prediction: { text: "文本" }, normalized_prediction: "文本" })}\n`,
		"utf-8",
	);
	writeFileSync(
		join(root, "predictions", "manifest.json"),
		JSON.stringify(
			{
				schema: "sure.eval.prediction_manifest.v1",
				generated_at: "2026-07-12T00:00:00Z",
				run_id: "model-eval-run",
				predictions_dir: join(root, "predictions"),
				datasets: [
					{
						dataset,
						task: "ASR",
						language: "zh",
						format_used: "jsonl+txt",
						txt: predictionTxt,
						jsonl: predictionJsonl,
						num_rows: 1,
					},
				],
			},
			null,
			2,
		),
		"utf-8",
	);
	writeFileSync(
		join(root, "predictions", "conversion_manifest.json"),
		JSON.stringify(
			{
				schema: "sure.eval.prediction_conversion_manifest.v1",
				generated_at: "2026-07-12T00:00:00Z",
				run_id: "model-eval-run",
				generated_by: "fixture",
				predictions_dir: join(root, "predictions"),
				datasets: [{ dataset, source_format: "fixture", format_used: "jsonl+txt", steps: [], num_rows: 1 }],
			},
			null,
			2,
		),
		"utf-8",
	);
	writeFileSync(join(root, "metrics", dataset, metric, "report.json"), JSON.stringify({ score: 0 }), "utf-8");
	writeFileSync(
		join(root, "metrics", dataset, metric, "pipeline_description.json"),
		JSON.stringify({ pipeline_id: row.pipeline_id }),
		"utf-8",
	);
	writeFileSync(
		join(root, "sample_reports", dataset, `${metric}.jsonl`),
		`${JSON.stringify({ key: "utt", score: 0 })}\n`,
		"utf-8",
	);
	writeFileSync(
		join(root, "report_snapshot.md"),
		[
			"# fixture_model Evaluation Snapshot",
			"## Basic Information",
			"## Formatting Policy",
			"## Evaluation Scope",
			"## Dataset Scope",
			"## Result Summary",
			"## Per-Dataset Test Results",
			"## Metric Details",
			"## Validation Summary",
			"## Evaluation Pipeline",
			"## Pipeline Trace Details",
			"## Evaluation Runtime And Tool Versions",
			"## Output Artifacts",
			"## Artifact Groups",
			"## Test Notes",
			"",
		].join("\n"),
		"utf-8",
	);
	return root;
}

// Regression guard for the gateCheck/gateScript split: tool_readiness_routing is
// now a gate-without-script (kind: "gate", no gateScript) so its handoff block
// actually fires. Previously it was linear → the block was dead code, so a run
// with handoff_to_tool_agent=true advanced straight past onboard handoff.
describe("sure_eval tool_readiness_routing handoff gate", () => {
	it("is a gate unit with an in-process gateCheck and no gateScript", () => {
		const unit = findUnit("tool_readiness_routing")!;
		expect(unit.kind).toBe("gate");
		expect(unit.gateCheck).toBeDefined();
		expect(unit.gateScript).toBeUndefined();
	});

	it("blocks (no advance) when handoff_to_tool_agent=true — hand off to /sure_onboard", () => {
		const { ctx, runDir } = freshCtx("handoff-true");
		seedCheckpoint(runDir, {
			currentUnit: "tool_readiness_routing",
			completedUnits: ["task_classification"],
			retries: {},
		});
		writeArtifact(runDir, "tool_readiness_routing.json", {
			readiness: "needs_onboarding",
			model_dir: "sure/models/missing_model",
			handoff_to_tool_agent: true,
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain("handoff_to_tool_agent");
		expect(result.repair).toContain("/sure_onboard");
		// Did NOT advance past tool_readiness_routing.
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("tool_readiness_routing");
	});

	it("advances when readiness=ready and no handoff_to_tool_agent", () => {
		const { ctx, runDir } = freshCtx("ready");
		seedCheckpoint(runDir, {
			currentUnit: "tool_readiness_routing",
			completedUnits: ["task_classification"],
			retries: {},
		});
		writeArtifact(runDir, "tool_readiness_routing.json", {
			readiness: "ready",
			model_dir: "sure/models/asr_qwen3",
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("plan");
	});

	it("does not consume retries again when an invalid artifact is unchanged", () => {
		const { ctx, runDir } = freshCtx("handoff-unchanged");
		seedCheckpoint(runDir, {
			currentUnit: "tool_readiness_routing",
			completedUnits: ["task_classification"],
			retries: {},
		});
		writeArtifact(runDir, "tool_readiness_routing.json", {
			readiness: "needs_onboarding",
			model_dir: "sure/models/missing_model",
			handoff_to_tool_agent: true,
		});

		const first = postToolResult(ctx);
		const firstCheckpoint = statePatch(first).checkpoint;
		expect(first.ok).toBe(false);
		expect(firstCheckpoint?.data.retries.tool_readiness_routing).toBe(1);
		seedCheckpoint(runDir, firstCheckpoint!.data);

		const unchanged = postToolResult({ ...ctx, event: { toolName: "read", isError: false } });
		expect(unchanged.ok).toBe(true);
		expect(statePatch(unchanged).message).toContain("unchanged artifact content");
		expect(statePatch(unchanged).checkpoint?.data.retries.tool_readiness_routing).toBe(1);
	});

	it("consumes a new retry when invalid artifact content changes", () => {
		const { ctx, runDir } = freshCtx("handoff-changed");
		seedCheckpoint(runDir, {
			currentUnit: "tool_readiness_routing",
			completedUnits: ["task_classification"],
			retries: {},
		});
		writeArtifact(runDir, "tool_readiness_routing.json", {
			readiness: "needs_onboarding",
			model_dir: "sure/models/missing_model",
			handoff_to_tool_agent: true,
		});
		const first = postToolResult(ctx);
		seedCheckpoint(runDir, statePatch(first).checkpoint!.data);
		writeArtifact(runDir, "tool_readiness_routing.json", {
			readiness: "needs_onboarding",
			model_dir: "sure/models/missing_model",
			handoff_to_tool_agent: true,
			routing_reason: "new evidence",
		});

		const changed = postToolResult(ctx);
		expect(changed.ok).toBe(false);
		expect(statePatch(changed).checkpoint?.data.retries.tool_readiness_routing).toBe(2);
	});

	it("does not rerun an expensive gate for an unchanged failed artifact", () => {
		const { ctx, runDir } = freshCtx("smoke-unchanged");
		writeArtifact(runDir, "smoke_test_result.json", {
			smoke_passed: false,
			sample_count: 0,
			exit_code: 23,
			stdout_excerpt: "failed",
			stderr_excerpt: "",
			failures: ["fixture failure"],
		});
		const artifactPath = join(runDir, "artifacts", "smoke_test_result.json");
		const digest = createHash("sha256").update(readFileSync(artifactPath)).digest("hex");
		seedCheckpoint(runDir, {
			currentUnit: "smoke_test",
			completedUnits: ["execution_readiness"],
			retries: { smoke_test: 1 },
			failedArtifactDigests: { smoke_test: digest },
		});

		const unchanged = postToolResult({ ...ctx, event: { toolName: "read", isError: false } });
		expect(unchanged.ok).toBe(true);
		expect(statePatch(unchanged).message).toContain("unchanged artifact content");
		expect(statePatch(unchanged).checkpoint?.data.retries.smoke_test).toBe(1);
	});
});

// Gate-with-script units (submit_vc_run, execute_wait, smoke_test, assessment, run_report,
// script_routing) must have NO in-process gateCheck — the python gateScript is
// the single authoritative semantic checker. This is the redundancy-removal
// contract: no duplicated === true vs truthy logic, no duplicated constant lists.
describe("sure_eval gate-with-script units have no in-process gateCheck (python is sole authority)", () => {
	it.each([
		["script_routing", "check_script_routing.py"],
		["execution_readiness", "check_execution_surface_compliance.py"],
		["smoke_test", "run_smoke.py"],
		["submit_vc_run", "vc_check.py"],
		["execute_wait", "wait_vc_execution.py"],
		["assessment", "check_assessment.py"],
		["extract_lessons", "check_memory_extraction.py"],
		["run_report", "check_run_report.py"],
	])("%s delegates semantics to its python gateScript (no in-process gateCheck)", (unitId, script) => {
		const unit = findUnit(unitId)!;
		expect(unit.kind).toBe("gate");
		expect(unit.gateScript).toBe(script);
	});
	// execution_readiness is the ONE exception that keeps an in-process gateCheck:
	// it checks self-reported readiness/audit booleans, disjoint from the python
	// script's template-provenance audit and from the following smoke_test gate.
	// All other gate-with-script units must NOT.
	it("execution_readiness keeps its in-process gateCheck (disjoint from python)", () => {
		expect(findUnit("execution_readiness")!.gateCheck).toBeDefined();
	});
	it("gate-with-script units drop the in-process gateCheck (incl. extract_lessons)", () => {
		for (const id of [
			"script_routing",
			"smoke_test",
			"submit_vc_run",
			"execute_wait",
			"assessment",
			"extract_lessons",
			"run_report",
		]) {
			expect(findUnit(id)!.gateCheck).toBeUndefined();
		}
	});
});

// Regression guard for the preFinish terminal-gate backstop. run_report is a
// gate-with-script (check_run_report.py) with NO in-process gateCheck. The old
// preFinish read `LAST_UNIT.gateCheck ? ... : {ok:true}` — but run_report has no
// gateCheck, so the backstop was DEAD CODE: a run_report.json mutated between
// postToolResult and sure_finish (e.g. report_persisted flipped to false, or
// execution_path_actual emptied) would sail through sure_finish unchecked. Fixed:
// preFinish now re-runs `runGateScript(ctx, LAST_UNIT)` (check_run_report.py),
// mirroring the sure_onboard verdict backstop.
describe("sure_eval preFinish terminal-gate backstop (regression)", () => {
	const SCRIPTS_DIR = join(PACKAGE_DIR, "scripts");

	function finishCtx(name: string): { ctx: SureHookContext; runDir: string } {
		const runDir = resolve(__dirname, "tmp-finish", name);
		mkdirSync(join(runDir, "artifacts"), { recursive: true });
		const ctx: SureHookContext = {
			point: "pre_finish",
			run: { id: "test-eval-finish", command: "/sure_eval", status: "running" } as never,
			skill: { name: "sure_eval", command: "/sure_eval" } as never,
			cwd: PACKAGE_DIR,
			packageDir: PACKAGE_DIR,
			runDir,
			args: "",
		};
		return { ctx, runDir };
	}

	it("LAST_UNIT (run_report) has a gateScript and no gateCheck — so the backstop MUST call runGateScript", () => {
		expect(LAST_UNIT.id).toBe("run_report");
		expect(LAST_UNIT.gateScript).toBe("check_run_report.py");
		expect(LAST_UNIT.gateCheck).toBeUndefined();
		// scripts/check_run_report.py must actually exist so runGateScript can spawn it.
		expect(existsSync(join(SCRIPTS_DIR, "check_run_report.py"))).toBe(true);
	});

	it("rejects a tampered run_report (report_persisted=false) at finish — backstop re-runs the python gate", () => {
		const { ctx, runDir } = finishCtx("tampered");
		seedCheckpoint(runDir, {
			currentUnit: "run_report",
			completedUnits: [
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
				"assessment",
				"extract_lessons",
			],
			retries: {},
		});
		// A report that violates check_run_report.py: report_persisted is false.
		writeArtifact(runDir, "main_agent_run_report.json", {
			report_persisted: false,
			execution_path_actual: "vc_submit",
		});
		const result = preFinish(ctx);
		expect(result.ok).toBe(false);
		// The repair must come from check_run_report.py, naming report_persisted.
		expect(result.repair).toContain("report_persisted");
	});

	it("accepts a compliant run_report at finish", () => {
		const { ctx, runDir } = finishCtx("clean");
		const artifactRoot = seedCompletedEvaluationArtifacts(runDir);
		seedCheckpoint(runDir, {
			currentUnit: "run_report",
			completedUnits: [
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
				"assessment",
				"extract_lessons",
			],
			retries: {},
		});
		// A fully schema-compliant run_report (the union fix means ALL schema
		// required fields must be present, not just the unit's requiredFields).
		writeArtifact(runDir, "main_agent_run_report.json", {
			run_id: "test-eval-finish",
			timestamp: "2026-07-12T00:00:00Z",
			task_type: "asr",
			goal: "smoke eval",
			selected_datasets: ["aishell1"],
			executed_steps: [{ name: "evaluate_predictions", script: "scripts/evaluate_predictions.py" }],
			status: "success",
			report_persisted: true,
			execution_path_actual: "vc_submit",
			artifact_root: artifactRoot,
		});
		const result = preFinish(ctx);
		expect(result.ok).toBe(true);
	});

	it("does not double-count run_report when finish runs after the terminal checkpoint", () => {
		const { ctx, runDir } = finishCtx("terminal-already-counted");
		const artifactRoot = seedCompletedEvaluationArtifacts(runDir);
		seedCheckpoint(runDir, {
			currentUnit: "run_report",
			completedUnits: [
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
				"assessment",
				"extract_lessons",
				"run_report",
			],
			retries: {},
		});
		writeArtifact(runDir, "main_agent_run_report.json", {
			run_id: "test-eval-finish",
			timestamp: "2026-07-12T00:00:00Z",
			task_type: "asr",
			goal: "smoke eval",
			selected_datasets: ["aishell1"],
			executed_steps: [{ name: "evaluate_predictions", script: "scripts/evaluate_predictions.py" }],
			status: "success",
			report_persisted: true,
			execution_path_actual: "vc_submit",
			artifact_root: artifactRoot,
		});
		const result = preFinish(ctx);
		const patch = statePatch(result);
		expect(result.ok).toBe(true);
		expect(patch.counters?.completed_units).toBe(13);
		expect(patch.counters?.total_units).toBe(13);
	});
});

describe("sure_eval countersFor", () => {
	it("keeps gate_blocks consistent with the retry ledger", () => {
		const data: CheckpointData = {
			currentUnit: "run_task",
			completedUnits: [],
			retries: { discover: 4, classify: 2 },
		};
		expect(countersFor(data, 0).gate_blocks).toBe(6);
	});

	it("keeps counting blocks after the blocked unit passes", () => {
		const unit = findUnit("task_classification");
		expect(unit).toBeDefined();
		let data: CheckpointData = {
			currentUnit: "task_classification",
			completedUnits: [],
			retries: {},
		};
		data = bumpRetry(unit as Unit, data).data;
		data = bumpRetry(unit as Unit, data).data;
		expect(countersFor(data, 0).gate_blocks).toBe(2);

		// advance() clears the unit's retry entry, which is right for the retry
		// budget and wrong for a run-long tally: a run that was blocked twice and
		// then finished used to report zero blocks.
		data = (advance(unit as Unit, data) as RunCheckpoint).data;
		expect(countersFor(data, 0).gate_blocks).toBe(2);
	});
});

// Spec §4.1: extract_lessons declares helperScripts (build_run_digest.py, for the
// --out preview only) and eval's preToolCall must honour them the way
// sure_onboard already does: allowed only while that unit is current, never
// from another unit. Gate scripts of completed units stay allowed as before.
describe("sure_eval preToolCall honours helperScripts", () => {
	const UP_TO_ASSESSMENT = [
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
		"assessment",
	];

	function toolCtx(name: string, currentUnit: string, completedUnits: string[], command: string): SureHookContext {
		const { ctx, runDir } = freshCtx(name);
		seedCheckpoint(runDir, { currentUnit, completedUnits, retries: {} });
		ctx.point = "pre_tool_call";
		ctx.event = { toolName: "bash", input: { command } };
		return ctx;
	}

	it("extract_lessons declares build_run_digest.py as its only helper script", () => {
		const unit = findUnit("extract_lessons");
		expect(unit).toBeDefined();
		expect(unit?.helperScripts).toEqual(["build_run_digest.py"]);
		expect(unit?.gateScript).toBe("check_memory_extraction.py");
	});

	it("allows the digest preview helper while extract_lessons is current", () => {
		const ctx = toolCtx(
			"helper-allowed",
			"extract_lessons",
			UP_TO_ASSESSMENT,
			"python3 scripts/build_run_digest.py --run-dir .sure/runs/x --repo-root . --out artifacts/run_digest.preview.json",
		);
		expect(preToolCall(ctx).ok).toBe(true);
	});

	it("still allows the unit's own gate script", () => {
		const ctx = toolCtx(
			"gate-allowed",
			"extract_lessons",
			UP_TO_ASSESSMENT,
			"python3 scripts/check_memory_extraction.py --run-dir .sure/runs/x --produces artifacts/extraction_declaration.json",
		);
		expect(preToolCall(ctx).ok).toBe(true);
	});

	it("rejects the helper from an earlier unit", () => {
		const ctx = toolCtx(
			"helper-too-early",
			"assessment",
			UP_TO_ASSESSMENT.slice(0, -1),
			"python3 scripts/build_run_digest.py --run-dir .sure/runs/x --repo-root .",
		);
		const result = preToolCall(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain('is not permitted from unit "assessment"');
	});

	it("rejects the helper from a later unit even though extract_lessons is completed", () => {
		const ctx = toolCtx(
			"helper-too-late",
			"run_report",
			[...UP_TO_ASSESSMENT, "extract_lessons"],
			"python3 scripts/build_run_digest.py --run-dir .sure/runs/x --repo-root .",
		);
		const result = preToolCall(ctx);
		expect(result.ok).toBe(false);
		expect(result.repair).toContain('is not permitted from unit "run_report"');
	});
});
