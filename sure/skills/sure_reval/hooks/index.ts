import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import type { SureHookContext, SureHookResult } from "@earendil-works/pi-coding-agent/hooks";
import {
	type HarnessRuntimeContract,
	harnessRuntimeEnv,
	resolveHarnessPython,
} from "../../../runtime/harness/resolve.ts";
import { invokedSkillScripts } from "../../../runtime/script-guard.ts";
import { validateSkillRuntimeBinding, writeSkillRuntimeBinding } from "../../../runtime/usage.ts";
import { requireSitePolicy } from "../../../site/loader.ts";

function parseArgs(raw: string): Record<string, string> {
	const out: Record<string, string> = {};
	const tokens = raw.trim().split(/\s+/).filter(Boolean);
	for (let i = 0; i < tokens.length; i++) {
		const token = tokens[i];
		const eq = token.indexOf("=");
		if (eq >= 0) {
			out[token.slice(0, eq)] = token.slice(eq + 1);
			continue;
		}
		const key = token.replace(/^--?/, "");
		const next = tokens[i + 1];
		if (next !== undefined && !next.startsWith("-")) {
			out[key] = next;
			i++;
		} else {
			out[key] = "true";
		}
	}
	return out;
}

function failure(repair: string): SureHookResult {
	return { ok: false, repair, state_patch: { phase: { id: "blocked", label: "Blocked", status: "blocked" } } };
}

function prepareEvaluationRuntime(
	ctx: SureHookContext,
	harnessRuntime: HarnessRuntimeContract,
): { binding?: Record<string, unknown>; error?: string } {
	const script = resolve(ctx.packageDir, "..", "sure_infer", "scripts", "evaluation_runtime.py");
	const engineRoot = resolve(ctx.packageDir, "..", "..", "external", "sure-evaluation");
	const completed = spawnSync(harnessRuntime.python_executable, [script, "--engine-root", engineRoot, "--prepare"], {
		cwd: ctx.packageDir,
		encoding: "utf-8",
		timeout: 900_000,
		env: { ...process.env, ...harnessRuntimeEnv(harnessRuntime) },
	});
	if (completed.status !== 0) {
		return {
			error:
				completed.stderr.trim() || completed.stdout.trim() || "Failed to prepare the locked Evaluation Runtime.",
		};
	}
	try {
		const parsed: unknown = JSON.parse(completed.stdout);
		if (!isRecord(parsed) || parsed.schema !== "sure.evaluation.runtime.binding.v1") {
			return { error: "Evaluation Runtime resolver returned an invalid binding." };
		}
		return { binding: parsed };
	} catch (error) {
		return {
			error: `Evaluation Runtime resolver returned invalid JSON: ${error instanceof Error ? error.message : String(error)}`,
		};
	}
}

export function preStart(ctx: SureHookContext): SureHookResult {
	const args = parseArgs(ctx.args);
	const missing = ["model", "datasets", "pipeline_id"].filter((key) => !args[key]);
	if (missing.length > 0) {
		return failure(
			`Missing required /sure_reval parameter(s): ${missing.join(", ")}. Usage: /sure_reval model=<exact-model-id> datasets=<dataset__version,...> pipeline_id=<exact-pipeline-id,...> [protocol=standard_system|strict_core].`,
		);
	}
	let approvedModelsRoot: string;
	let approvedResultsRoot: string | undefined;
	try {
		const policy = requireSitePolicy().policy;
		approvedModelsRoot = policy.storage.approved_models_roots[0];
		approvedResultsRoot = policy.storage.approved_results_roots[0];
	} catch (error) {
		return failure(error instanceof Error ? error.message : String(error));
	}
	for (const deprecated of [
		"source",
		"reuse_predictions_from",
		"model_dir",
		"tmp_root",
		"copy_mode",
		"max_samples",
		"metrics",
		"config",
		"evaluation_engine_root",
	]) {
		if (deprecated in args) {
			return failure(
				`/sure_reval no longer accepts ${deprecated}; approved inputs resolve only from ${approvedModelsRoot} and ${approvedResultsRoot ?? "storage.approved_results_roots (unset in the active site policy)"}.`,
			);
		}
	}
	const protocol = args.protocol ?? args.protocol_id ?? "standard_system";
	if (protocol !== "standard_system" && protocol !== "strict_core") {
		return failure("protocol must be standard_system or strict_core.");
	}
	const runtime = resolveHarnessPython(ctx.packageDir);
	if (!runtime.ok || !runtime.contract) {
		return failure(runtime.error ?? "Bootstrap the locked common Harness Runtime and retry /sure_reval.");
	}
	const artifactsDir = join(ctx.runDir, "artifacts");
	mkdirSync(artifactsDir, { recursive: true });
	const resolver = resolve(ctx.packageDir, "..", "sure_infer", "scripts", "resolve_prediction_source.py");
	const output = join(artifactsDir, "prediction_source_resolved.json");
	const argv = [
		resolver,
		"--model",
		args.model,
		"--datasets",
		args.datasets,
		"--protocol-id",
		protocol,
		"--output",
		output,
	];
	const completed = spawnSync(runtime.contract.python_executable, argv, {
		cwd: ctx.packageDir,
		encoding: "utf-8",
		timeout: 120_000,
		env: { ...process.env, ...harnessRuntimeEnv(runtime.contract) },
	});
	if (completed.status !== 0) {
		return failure(completed.stderr.trim() || completed.stdout.trim() || "Failed to resolve prediction source.");
	}
	const evaluationRuntime = prepareEvaluationRuntime(ctx, runtime.contract);
	if (!evaluationRuntime.binding) {
		return failure(evaluationRuntime.error ?? "Evaluation Runtime is not ready.");
	}
	let runtimeBindingPath: string;
	try {
		runtimeBindingPath = writeSkillRuntimeBinding({
			runDir: ctx.runDir,
			skill: "sure_reval",
			harnessRuntime: runtime.contract,
			harnessRole: "Approved source resolution, orchestration, validation, and atomic result-bundle append.",
			modelRuntimeReason: "sure_reval reuses approved predictions and must not execute model inference.",
			evaluationRuntime: {
				role: "Route resolution, normalization, metric computation, and evaluation reports.",
				binding: evaluationRuntime.binding,
			},
		});
	} catch (error) {
		return failure(
			`Failed to write the runtime responsibility declaration: ${error instanceof Error ? error.message : String(error)}`,
		);
	}
	return {
		ok: true,
		state_patch: {
			phase: { id: "verify_source_identity", label: "Approved source identity verified", status: "running" },
			message: `Resolved prediction source with Harness Runtime ${runtime.contract.runtime_id}: ${output}`,
			counters: { completed_units: 3, total_units: 7, gate_blocks: 0 },
			artifacts: [
				{
					type: "runtime_binding",
					name: "Skill runtime binding",
					path: runtimeBindingPath,
					status: "ready",
				},
				{
					type: "prediction_source_resolved",
					name: "Prediction source",
					path: `.sure/runs/${ctx.run.runId}/artifacts/prediction_source_resolved.json`,
					status: "ready",
				},
			],
		},
	};
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function incompleteReportError(payload: Record<string, unknown>, finishStatus: unknown): string | undefined {
	if (finishStatus !== "incomplete" && finishStatus !== "failed") {
		return "non-success Reval finish must declare status=incomplete or status=failed";
	}
	if (payload.schema !== "sure.reval.run_report.v1" || payload.status !== finishStatus) {
		return `reval_run_report status must equal requested finish status ${String(finishStatus)}`;
	}
	if (typeof payload.error_code !== "string" || payload.error_code.length === 0) {
		return "non-success reval_run_report must declare error_code";
	}
	if (
		payload.evaluation_only !== true ||
		payload.inference_executed !== false ||
		payload.old_evaluation_reused !== false ||
		payload.append_attempted !== false
	) {
		return "non-success Reval must prove evaluation_only=true, inference_executed=false, old_evaluation_reused=false, and append_attempted=false";
	}
	if (!isRecord(payload.source_identity)) {
		return "non-success reval_run_report must bind the approved source_identity";
	}
	return undefined;
}

export function preToolCall(ctx: SureHookContext): SureHookResult {
	const event = isRecord(ctx.event) ? ctx.event : {};
	const toolCall = isRecord(event.toolCall) ? event.toolCall : {};
	const toolName =
		typeof event.toolName === "string" ? event.toolName : typeof toolCall.name === "string" ? toolCall.name : "";
	if (toolName !== "bash") {
		return { ok: true };
	}
	const input = isRecord(event.input) ? event.input : isRecord(toolCall.input) ? toolCall.input : {};
	const command = typeof input.command === "string" ? input.command : "";
	const backendScripts = invokedSkillScripts(command, "sure_infer/scripts");
	const forbiddenBackend = backendScripts.find(
		(script) =>
			script !== "sure_infer/scripts/run_reval.py" && script !== "sure_infer/scripts/resolve_prediction_source.py",
	);
	if (forbiddenBackend) {
		return failure(
			`/sure_reval must use the atomic run_reval.py backend; direct call to ${forbiddenBackend} is forbidden.`,
		);
	}
	for (const forbidden of [
		"generate_predictions_via_server.py",
		"run_model_mcp_smoke.py",
		"model_wrapper_mcp_server.py",
		"tools/call",
		"server.py",
	]) {
		if (command.includes(forbidden)) {
			return failure(`/sure_reval is evaluation-only; inference surface ${forbidden} is forbidden.`);
		}
	}
	return { ok: true };
}

export function postToolResult(ctx: SureHookContext): SureHookResult {
	const report = join(ctx.runDir, "artifacts", "reval_run_report.json");
	if (!existsSync(report)) {
		return { ok: true };
	}
	const payload = JSON.parse(readFileSync(report, "utf-8")) as Record<string, unknown>;
	if (payload.status !== "success") {
		return {
			ok: true,
			state_patch: {
				phase: { id: "blocked", label: "Re-evaluation incomplete", status: "blocked" },
				message: `Re-evaluation stopped without append: ${String(payload.error_code ?? "UNKNOWN")}`,
				counters: { completed_units: 3, total_units: 7, gate_blocks: 1 },
			},
		};
	}
	return {
		ok: true,
		state_patch: {
			phase: { id: "append_local_result_bundle", label: "Local result bundle materialized", status: "running" },
			message: `Re-evaluation backend produced ${report}; terminal identity gate is pending.`,
			counters: { completed_units: 6, total_units: 7, gate_blocks: 0 },
		},
	};
}

export function preFinish(ctx: SureHookContext): SureHookResult {
	const runtimeBindingError = validateSkillRuntimeBinding(
		join(ctx.runDir, "artifacts", "runtime_binding.json"),
		"sure_reval",
		true,
	);
	if (runtimeBindingError) {
		return failure(runtimeBindingError);
	}
	const report = join(ctx.runDir, "artifacts", "reval_run_report.json");
	if (!existsSync(report)) {
		return failure("Write artifacts/reval_run_report.json before finishing /sure_reval.");
	}
	const finishStatus = isRecord(ctx.event) && isRecord(ctx.event.finish) ? ctx.event.finish.status : undefined;
	const payload = JSON.parse(readFileSync(report, "utf-8")) as Record<string, unknown>;
	if (finishStatus !== "success") {
		const reportError = incompleteReportError(payload, finishStatus);
		if (reportError) {
			return failure(reportError);
		}
		const sourcePath = join(ctx.runDir, "artifacts", "prediction_source_resolved.json");
		if (!existsSync(sourcePath)) {
			return failure("Non-success Reval must retain artifacts/prediction_source_resolved.json.");
		}
		const source = JSON.parse(readFileSync(sourcePath, "utf-8")) as Record<string, unknown>;
		const identity = payload.source_identity as Record<string, unknown>;
		for (const key of ["model_fingerprint", "protocol_id", "dataset_set_digest", "source_report_sha256"]) {
			if (identity[key] !== source[key]) {
				return failure(`Non-success Reval source_identity.${key} differs from the approved resolver artifact.`);
			}
		}
		return {
			ok: true,
			state_patch: {
				phase: {
					id: "finish",
					label: "SURE reval stopped without append",
					status: finishStatus as "failed" | "incomplete",
				},
				message: `SURE reval ${String(payload.error_code)}; no inference or result append occurred.`,
				counters: { completed_units: 3, total_units: 7, gate_blocks: 1 },
			},
		};
	}
	const checker = join(ctx.packageDir, "scripts", "check_reval_run_report.py");
	const runtime = resolveHarnessPython(ctx.packageDir);
	if (!runtime.ok || !runtime.contract) {
		return failure(runtime.error ?? "HARNESS_RUNTIME_NOT_READY");
	}
	const completed = spawnSync(runtime.contract.python_executable, [checker, "--report", report], {
		cwd: ctx.packageDir,
		encoding: "utf-8",
		timeout: 120_000,
		env: { ...process.env, ...harnessRuntimeEnv(runtime.contract) },
	});
	if (completed.status !== 0) {
		return failure(
			completed.stderr.trim() || completed.stdout.trim() || "reval_run_report.json did not pass validation.",
		);
	}
	const successPayload = JSON.parse(readFileSync(report, "utf-8")) as { run_dir?: string };
	return {
		ok: true,
		state_patch: {
			phase: { id: "finish", label: "SURE reval validated", status: "success", progress: 1 },
			message: `SURE reval completed: ${successPayload.run_dir ?? report}`,
			counters: { completed_units: 7, total_units: 7, gate_blocks: 0 },
			artifacts: [
				{
					type: "runtime_binding",
					name: "Skill runtime binding",
					path: `.sure/runs/${ctx.run.runId}/artifacts/runtime_binding.json`,
					status: "ready",
				},
				{
					type: "prediction_source_resolved",
					name: "Prediction source",
					path: `.sure/runs/${ctx.run.runId}/artifacts/prediction_source_resolved.json`,
					status: "ready",
				},
				{
					type: "reval_run_report",
					name: "SURE reval report",
					path: `.sure/runs/${ctx.run.runId}/artifacts/reval_run_report.json`,
					status: "ready",
				},
			],
		},
	};
}

export function postFinish(ctx: SureHookContext): SureHookResult {
	return { ok: true, state_patch: { phase: { id: "finish", label: "SURE reval finished", status: ctx.run.status } } };
}

export function onError(ctx: SureHookContext): SureHookResult {
	return {
		ok: true,
		state_patch: {
			phase: { id: "error", label: "SURE reval interrupted", status: "failed" },
			message: ctx.run.errorSummary ?? "SURE reval stopped before completion.",
		},
	};
}
