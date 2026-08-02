import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import type { SureHookContext, SureHookResult } from "@earendil-works/pi-coding-agent/hooks";

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

export function preStart(ctx: SureHookContext): SureHookResult {
	const args = parseArgs(ctx.args);
	const source = args.source ?? args.reuse_predictions_from;
	if (!source) {
		return failure("Missing required /sure_reval parameter: source=<results_dir|run_dir|predictions_dir>.");
	}
	const artifactsDir = join(ctx.runDir, "artifacts");
	mkdirSync(artifactsDir, { recursive: true });
	const resolver = resolve(ctx.packageDir, "..", "sure_eval", "scripts", "resolve_prediction_source.py");
	const output = join(artifactsDir, "prediction_source_resolved.json");
	const argv = [resolver, "--source", source, "--output", output];
	if (args.model) {
		argv.push("--model", args.model);
	}
	if (args.datasets ?? args.dataset) {
		argv.push("--datasets", args.datasets ?? args.dataset);
	}
	if (args.protocol_id) {
		argv.push("--protocol-id", args.protocol_id);
	}
	const completed = spawnSync("python3", argv, { cwd: ctx.packageDir, encoding: "utf-8", timeout: 120_000 });
	if (completed.status !== 0) {
		return failure(completed.stderr.trim() || completed.stdout.trim() || "Failed to resolve prediction source.");
	}
	return {
		ok: true,
		state_patch: {
			phase: { id: "prediction_source", label: "Prediction source resolved", status: "running" },
			message: `Resolved prediction source: ${output}`,
			counters: { completed_units: 1, total_units: 2, gate_blocks: 0 },
			artifacts: [
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

export function preToolCall(_ctx: SureHookContext): SureHookResult {
	return { ok: true };
}

export function postToolResult(_ctx: SureHookContext): SureHookResult {
	return { ok: true };
}

export function preFinish(ctx: SureHookContext): SureHookResult {
	const report = join(ctx.runDir, "artifacts", "reval_run_report.json");
	if (!existsSync(report)) {
		return failure("Write artifacts/reval_run_report.json before finishing /sure_reval.");
	}
	const checker = join(ctx.packageDir, "scripts", "check_reval_run_report.py");
	const completed = spawnSync("python3", [checker, "--report", report], {
		cwd: ctx.packageDir,
		encoding: "utf-8",
		timeout: 120_000,
	});
	if (completed.status !== 0) {
		return failure(
			completed.stderr.trim() || completed.stdout.trim() || "reval_run_report.json did not pass validation.",
		);
	}
	const payload = JSON.parse(readFileSync(report, "utf-8")) as { run_dir?: string };
	return {
		ok: true,
		state_patch: {
			phase: { id: "finish", label: "SURE reval validated", status: "success", progress: 1 },
			message: `SURE reval completed: ${payload.run_dir ?? report}`,
			counters: { completed_units: 2, total_units: 2, gate_blocks: 0 },
			artifacts: [
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
