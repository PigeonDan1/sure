import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { SureHookContext, SureHookResult } from "@earendil-works/pi-coding-agent/hooks";
import { harnessRuntimeEnv, resolveHarnessPython } from "../../../runtime/harness/resolve.ts";
import { invokedSkillScripts } from "../../../runtime/script-guard.ts";
import { requireSitePolicy } from "../../../site/loader.ts";
import {
	advance,
	artifactPath,
	bumpRetry,
	type CheckpointData,
	failure,
	type GateResult,
	readArtifact,
	readCheckpoint,
	retryExhausted,
	runBackend,
} from "./checkpoints.ts";
import { FIRST_UNIT, findUnit, LAST_UNIT, MAIN_FLOW_UNITS, TOTAL_UNITS, type Unit } from "./state-machine.ts";
import { validateProduces } from "./validate.ts";

// SURE-EVAL main-flow skill hooks. Mixed drive with three gates:
//   1. checkpoint lock (currentUnit pins position, advance one step)
//   2. validateProduces on EVERY unit (location/format/value-domain + forbidden fields)
//   3. gateCheck (gate units run a Python semantic script via spawnSync)
// Source of truth: docs/agents/main_flow_agent/AGENTS.md in the sure-eval repo.

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function phaseFor(unit: Unit, status: "running" | "blocked" | "success") {
	return { id: unit.id, label: unit.label, status };
}

export function countersFor(completed: CheckpointData, gateBlocks?: number) {
	const ledgerBlocks = Object.values(completed.retries ?? {}).reduce((sum, n) => sum + (n ?? 0), 0);
	return {
		completed_units: completed.completedUnits.length,
		total_units: TOTAL_UNITS,
		gate_blocks: Math.max(ledgerBlocks, gateBlocks ?? 0),
	};
}

const PROTOCOLS = new Set(["standard_system", "strict_core"]);

function approvedModelsRoot(): string {
	return requireSitePolicy().policy.storage.approved_models_roots[0];
}

function verdictPathFor(modelDir: string): string | undefined {
	for (const candidate of [join(modelDir, "verdict.json"), join(modelDir, "artifacts", "verdict.json")]) {
		if (existsSync(candidate)) {
			return candidate;
		}
	}
	return undefined;
}

function modelDirFor(ctx: SureHookContext, approvedRoot: string): string | undefined {
	const args = parseArgs(ctx.args);
	const model = typeof args.model === "string" ? args.model : undefined;
	return model ? join(approvedRoot, model) : undefined;
}

function parseArgs(raw: string): Record<string, string> {
	// Accept both "key value" and "key=value" forms, plus bare flags.
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

export function preStart(ctx: SureHookContext): SureHookResult {
	const args = parseArgs(ctx.args);
	const missing: string[] = [];
	if (!args.model) {
		missing.push("model");
	}
	const datasetsArg = args.datasets ?? args.dataset;
	if (!datasetsArg) {
		missing.push("datasets");
	}
	if (missing.length > 0) {
		return failure(
			`Missing required /sure_eval parameters: ${missing.join(", ")}. Usage: /sure_eval model=<name> datasets=<source_root[,source_root...]> [datasets_root=<writable_projection_root>] [protocol=standard_system|strict_core] [execution=auto|local|vc] [device=auto|cpu|cuda[:index]] [max_samples=...]`,
			"Missing required parameters.",
		);
	}
	let approvedRoot: string;
	try {
		approvedRoot = approvedModelsRoot();
	} catch (error) {
		return failure(error instanceof Error ? error.message : String(error), "Site policy is not configured.");
	}
	if ("model_dir" in args) {
		return failure(
			`/sure_eval no longer accepts model_dir; approved models are read only from ${approvedRoot}.`,
			"Untrusted path override rejected.",
		);
	}
	const protocol = args.protocol ?? args.protocol_id ?? "standard_system";
	if (!PROTOCOLS.has(protocol)) {
		return failure(
			`Unsupported protocol ${JSON.stringify(protocol)}. Use standard_system (default) or strict_core.`,
			"Unsupported inference protocol.",
		);
	}
	const runtime = resolveHarnessPython(ctx.packageDir);
	if (!runtime.ok || !runtime.contract) {
		return failure(
			runtime.error ?? "Bootstrap the locked common Harness Runtime and retry /sure_eval.",
			"HARNESS_RUNTIME_NOT_READY",
		);
	}

	// Verify the onboarded model dir exists with a verdict.json (cross-skill handoff).
	const modelDir = modelDirFor(ctx, approvedRoot);
	const verdictPath = modelDir ? verdictPathFor(modelDir) : undefined;
	if (!modelDir || !existsSync(modelDir) || !verdictPath) {
		return failure(
			`Model "${args.model}" is not approved: runtime files and verdict.json must exist under ${approvedRoot}/${args.model}. Run /sure_onboard into sure/models first, then complete the human promotion into NFS.`,
			"Model not onboarded (handoff incomplete).",
		);
	}

	const artifactsDir = join(ctx.runDir, "artifacts");
	mkdirSync(artifactsDir, { recursive: true });
	const resolvedInputPath = join(artifactsDir, "eval_input_resolved.json");
	const resolveArgs = [
		join(ctx.packageDir, "scripts", "resolve_eval_input.py"),
		"--model",
		args.model,
		"--datasets",
		datasetsArg,
		"--protocol",
		protocol,
		"--device",
		typeof args.device === "string" ? args.device : "auto",
		"--output",
		resolvedInputPath,
	];
	if (typeof args.max_samples === "string") {
		resolveArgs.push("--max-samples", args.max_samples);
	}
	if (typeof args.metrics === "string") {
		resolveArgs.push("--metrics", args.metrics);
	}
	if (typeof args.execution === "string") {
		resolveArgs.push("--execution", args.execution);
	}
	if (typeof args.execution_path === "string") {
		resolveArgs.push("--execution-path", args.execution_path);
	}
	for (const [argKey, cliKey] of [
		["vc_partition", "--vc-partition"],
		["vc_cpu", "--vc-cpu"],
		["vc_mem", "--vc-mem"],
		["vc_gpu", "--vc-gpu"],
		["vc_image", "--vc-image"],
		["vc_job_name", "--vc-job-name"],
	] as const) {
		if (typeof args[argKey] === "string") {
			resolveArgs.push(cliKey, args[argKey]);
		}
	}
	if (typeof args.run_id === "string") {
		resolveArgs.push("--run-id", args.run_id);
	}
	if (typeof args.output_dir === "string") {
		resolveArgs.push("--output-dir", args.output_dir);
	}
	if (typeof args.evaluation_backend === "string") {
		resolveArgs.push("--evaluation-backend", args.evaluation_backend);
	}
	if (typeof args.evaluation_engine_root === "string") {
		resolveArgs.push("--evaluation-engine-root", args.evaluation_engine_root);
	}
	if (typeof args.config === "string") {
		resolveArgs.push("--config", args.config);
	}
	if (typeof args.datasets_root === "string") {
		resolveArgs.push("--datasets-root", args.datasets_root);
	}
	const resolvedInput = spawnSync(runtime.contract.python_executable, resolveArgs, {
		cwd: ctx.packageDir,
		encoding: "utf-8",
		timeout: 120_000,
		env: { ...process.env, ...harnessRuntimeEnv(runtime.contract) },
	});
	if (resolvedInput.status !== 0) {
		const detail = resolvedInput.stderr.trim() || resolvedInput.stdout.trim() || "resolve_eval_input.py failed";
		return failure(
			`Unable to resolve /sure_eval input into eval_input_resolved.json: ${detail}`,
			"Input resolution failed.",
		);
	}

	// Preflight: verify the evaluation package supports every requested route
	// before any unit runs. An unsupported route is terminal — no retry can
	// make it runnable, so the flow stops here with the fixed reason.
	const preflightPath = join(artifactsDir, "evaluation_preflight.json");
	const preflight = spawnSync(
		runtime.contract.python_executable,
		[
			join(ctx.packageDir, "scripts", "preflight_evaluation_support.py"),
			"--input",
			resolvedInputPath,
			"--output",
			preflightPath,
		],
		{
			cwd: ctx.packageDir,
			encoding: "utf-8",
			timeout: 120_000,
			env: { ...process.env, ...harnessRuntimeEnv(runtime.contract) },
		},
	);
	if (preflight.status !== 0) {
		const detail = preflight.stderr.trim() || preflight.stdout.trim() || "preflight_evaluation_support.py failed";
		if (preflight.status === 3) {
			return failure(
				`The evaluation package does not support the requested evaluation: ${detail} See ${preflightPath}.`,
				"Evaluation package unsupported.",
			);
		}
		return failure(`Unable to complete the evaluation preflight: ${detail}`, "Evaluation preflight failed.");
	}

	// Backend presence check (warn, do not block — gate scripts will surface real failures).
	const scriptsDir = join(ctx.packageDir, "scripts");
	const backendPresent =
		existsSync(scriptsDir) && existsSync(join(scriptsDir, "check_execution_surface_compliance.py"));
	const checkpoint = readCheckpoint(ctx);
	const diagnostics = backendPresent
		? []
		: [
				{
					severity: "warning" as const,
					message: "scripts/ backend is not fully bundled.",
					repair: "Bundle the deterministic Python backend into scripts/ before running a real evaluation.",
				},
			];
	return {
		ok: true,
		state_patch: {
			phase: phaseFor(findUnit(checkpoint.data.currentUnit) ?? FIRST_UNIT, "running"),
			message: `SURE-EVAL main-flow skill loaded for model "${args.model}" from ${modelDir}; Harness Runtime ${runtime.contract.runtime_id}; resolved input: ${resolvedInputPath}.`,
			counters: countersFor(checkpoint.data, 0),
			diagnostics,
			checkpoint,
		},
	};
}

// preToolCall: enforce the per-unit script whitelist. The agent may freely
// read/write/search, but deterministic backend scripts (scripts/*.py) may only
// be invoked from the unit that owns them. This prevents out-of-order script
// calls (e.g. calling evaluate_predictions before execution_readiness passes).
// Resolvers and executors any unit may call; they carry no state-machine position.
const UNIT_AGNOSTIC_SCRIPTS = new Set([
	"scripts/resolve_model_dir.py",
	"scripts/resolve_eval_input.py",
	"scripts/resolve_evaluation_engine.py",
	"scripts/resolve_evaluation_route_plan.py",
	"scripts/preflight_evaluation_support.py",
	"scripts/run_local_execution.py",
	"scripts/run_vc_execution.py",
]);

export function preToolCall(ctx: SureHookContext): SureHookResult {
	const event = isRecord(ctx.event) ? ctx.event : {};
	const toolCall = isRecord(event.toolCall) ? event.toolCall : {};
	const toolName =
		typeof event.toolName === "string" ? event.toolName : typeof toolCall.name === "string" ? toolCall.name : "";
	if (toolName !== "bash") {
		// Only bash tool calls can invoke backend scripts.
		return { ok: true };
	}
	const input = isRecord(event.input) ? event.input : isRecord(toolCall.input) ? toolCall.input : {};
	const command = typeof input.command === "string" ? input.command : "";
	const invokedScripts = invokedSkillScripts(command).filter((script) => !UNIT_AGNOSTIC_SCRIPTS.has(script));
	if (invokedScripts.length === 0) {
		return { ok: true };
	}
	const checkpoint = readCheckpoint(ctx);
	const currentUnit = findUnit(checkpoint.data.currentUnit);
	if (!currentUnit) {
		return { ok: true };
	}
	if (retryExhausted(currentUnit, checkpoint.data)) {
		const attempts = checkpoint.data.retries[currentUnit.id] ?? 0;
		return failure(
			`Unit "${currentUnit.id}" already exhausted ${attempts} attempts. Do not rerun it from unrelated tool calls; persist an accurate failed run report or apply a deliberate repair and reset this unit's retry counter.`,
			`Gate "${currentUnit.id}" is in a terminal failed state.`,
			countersFor(checkpoint.data, attempts),
			checkpoint,
		);
	}
	// A script is allowed if the current unit (or any prior completed unit) owns it.
	const owningUnits = [currentUnit, ...MAIN_FLOW_UNITS_UP_TO(checkpoint.data, currentUnit)];
	const allowed = new Set<string>();
	for (const unit of owningUnits) {
		if (unit.gateScript) {
			allowed.add(`scripts/${unit.gateScript}`);
		}
	}
	const invokedScript = invokedScripts.find((script) => !allowed.has(script));
	if (!invokedScript) {
		return { ok: true };
	}
	return {
		ok: false,
		repair: `Script ${invokedScript} is not permitted from unit "${currentUnit.id}". Only the current unit's owned scripts may run here. ${currentUnit.gateScript ? `This unit owns scripts/${currentUnit.gateScript}.` : "This unit owns no backend scripts."}`,
		state_patch: {
			phase: phaseFor(currentUnit, "blocked"),
			message: `Blocked out-of-order script call: ${invokedScript}`,
			counters: countersFor(checkpoint.data, 1),
			diagnostics: [
				{
					severity: "error",
					message: `Script ${invokedScript} invoked from unit "${currentUnit.id}" — not in this unit's whitelist.`,
					repair: `Stay on unit "${currentUnit.id}"; run only its owned scripts, then produce ${currentUnit.produces}.`,
				},
			],
		},
	};
}

// Completed units up to and including the current one — scripts owned by any
// of these are permitted (a unit may legitimately re-run an earlier step's script).
function MAIN_FLOW_UNITS_UP_TO(completed: CheckpointData, current: Unit): Unit[] {
	const completedSet = new Set(completed.completedUnits);
	return MAIN_FLOW_UNITS.filter((unit) => completedSet.has(unit.id) && unit.id !== current.id);
}

// Run the gate's Python script (if declared) and fold its verdict into the
// in-process gateCheck result.
function runGateScript(ctx: SureHookContext, unit: Unit): GateResult | undefined {
	if (!unit.gateScript) {
		return undefined;
	}
	const produces = artifactPath(ctx, unit.produces);
	const extra = unit.gateScriptArgs ? unit.gateScriptArgs(ctx) : [];
	const r = runBackend(ctx, unit.gateScript, ["--produces", produces, ...extra]);
	if (r.ok) {
		return { ok: true };
	}
	const repair = r.stderr?.trim() || r.stdout?.trim() || `Gate script scripts/${unit.gateScript} exited ${r.status}.`;
	return { ok: false, repair, reason: `gate script ${unit.gateScript} failed` };
}

export function postToolResult(ctx: SureHookContext): SureHookResult {
	const event = isRecord(ctx.event) ? ctx.event : {};
	if (event.isError === true) {
		return {
			ok: true,
			state_patch: {
				phase: { id: "tool_result", label: "Inspecting tool result", status: "blocked" },
				diagnostics: [
					{
						severity: "warning",
						message: "A tool call returned an error during the SURE-EVAL main-flow run.",
						repair: "Inspect the tool output, repair the command or artifact, and continue.",
					},
				],
			},
		};
	}

	const checkpoint = readCheckpoint(ctx);
	const currentUnit = findUnit(checkpoint.data.currentUnit);
	if (!currentUnit) {
		return failure(
			`Unknown current unit "${checkpoint.data.currentUnit}". Reset the checkpoint.`,
			"Lost state-machine position.",
		);
	}

	// Gate 2: validate produces for EVERY unit (linear + gate). If the artifact
	// is not yet produced, stay on the unit (the agent may still be gathering
	// evidence within it).
	const artifact = readArtifact(ctx, currentUnit.produces);
	const unchangedFailure = unchangedFailedArtifact(ctx, currentUnit, checkpoint);
	if (unchangedFailure) {
		return unchangedFailure;
	}
	const producesResult = validateProduces(ctx, currentUnit, artifact);
	if (!producesResult.ok) {
		if (producesResult.missing) {
			// Artifact not produced yet — do not advance, do not block hard; let
			// the agent continue working within the unit.
			return { ok: true };
		}
		return failOrRetry(
			ctx,
			currentUnit,
			checkpoint,
			producesResult.repair ?? `Unit "${currentUnit.id}" produces invalid.`,
			producesResult.reason ?? "produces invalid",
		);
	}

	// Gate 3: gate units run the optional in-process gateCheck (fast structural
	// pre-filter, kept only when it checks something the python script does not)
	// then the authoritative Python semantic script. A gate may have a script, an
	// in-process check, or both — disjoint concerns — so the script runs regardless.
	if (currentUnit.kind === "gate") {
		if (currentUnit.gateCheck) {
			const inProcess = currentUnit.gateCheck(artifact);
			if (!inProcess.ok) {
				return failOrRetry(
					ctx,
					currentUnit,
					checkpoint,
					inProcess.repair ?? `Gate "${currentUnit.id}" failed.`,
					inProcess.reason ?? "gate check failed",
				);
			}
		}
		const scriptResult = runGateScript(ctx, currentUnit);
		if (scriptResult && !scriptResult.ok) {
			return failOrRetry(
				ctx,
				currentUnit,
				checkpoint,
				scriptResult.repair ?? `Gate script "${currentUnit.id}" failed.`,
				scriptResult.reason ?? "gate script failed",
			);
		}
	}

	// All gates passed: advance (clear retry counter for this unit).
	const next = advance(currentUnit, checkpoint.data);
	if (!next) {
		return { ok: true };
	}
	return {
		ok: true,
		state_patch: {
			phase: phaseFor(findUnit(next.data.currentUnit) ?? LAST_UNIT, "running"),
			message: `Advanced to unit "${next.data.currentUnit}".`,
			counters: countersFor(next.data, 0),
			diagnostics: [],
			checkpoint: next,
		},
	};
}

function unchangedFailedArtifact(
	ctx: SureHookContext,
	unit: Unit,
	checkpoint: { data: CheckpointData },
): SureHookResult | undefined {
	const previousDigest = checkpoint.data.failedArtifactDigests?.[unit.id];
	const path = artifactPath(ctx, unit.produces);
	if (!previousDigest || !existsSync(path)) {
		return undefined;
	}
	const digest = createHash("sha256").update(readFileSync(path)).digest("hex");
	if (digest !== previousDigest) {
		return undefined;
	}
	const attempts = checkpoint.data.retries[unit.id] ?? 0;
	return {
		ok: true,
		state_patch: {
			phase: phaseFor(unit, "blocked"),
			message: `Gate "${unit.id}" remains blocked on unchanged artifact content; retry ${attempts} was not consumed again.`,
			counters: countersFor(checkpoint.data, attempts),
			checkpoint,
			diagnostics: [
				{
					severity: "warning",
					message: `Gate "${unit.id}" is still blocked on the same artifact.`,
					repair: `Repair the supporting inputs, then explicitly regenerate ${unit.produces}; diagnostic reads do not rerun the gate.`,
				},
			],
		},
	};
}

// On gate failure: bump retry; if exhausted, mark the unit FAILED (stay, no advance).
function failOrRetry(
	ctx: SureHookContext,
	unit: Unit,
	checkpoint: { data: CheckpointData },
	repair: string,
	reason: string,
): SureHookResult {
	const artifactDigest = createHash("sha256")
		.update(readFileSync(artifactPath(ctx, unit.produces)))
		.digest("hex");
	if (checkpoint.data.failedArtifactDigests?.[unit.id] === artifactDigest) {
		const attempts = checkpoint.data.retries[unit.id] ?? 0;
		return {
			ok: true,
			state_patch: {
				phase: phaseFor(unit, "blocked"),
				message: `Gate "${unit.id}" remains blocked on unchanged artifact content; retry ${attempts} was not consumed again.`,
				counters: countersFor(checkpoint.data, attempts),
				checkpoint,
				diagnostics: [{ severity: "warning", message: reason, repair }],
			},
		};
	}
	const next = bumpRetry(unit, checkpoint.data, artifactDigest);
	const attempts = next.data.retries[unit.id] ?? 1;
	if (retryExhausted(unit, next.data)) {
		return failure(
			`${repair} (unit "${unit.id}" FAILED after ${attempts} retries; classify via failure_taxonomy and either repair manually or finish with status failed.)`,
			`Gate "${unit.id}" exhausted retries: ${reason}`,
			countersFor(next.data, attempts),
			next,
		);
	}
	return {
		ok: false,
		repair,
		state_patch: {
			phase: phaseFor(unit, "blocked"),
			message: `Gate "${unit.id}" blocked (attempt ${attempts}): ${reason}`,
			counters: countersFor(next.data, attempts),
			checkpoint: next,
			diagnostics: [{ severity: "error", message: reason, repair }],
		},
	};
}

export function preFinish(ctx: SureHookContext): SureHookResult {
	const checkpoint = readCheckpoint(ctx);
	const reportArtifact = readArtifact(ctx, LAST_UNIT.produces);
	if (!reportArtifact) {
		return failure(
			`Produce ${LAST_UNIT.produces} under the run artifacts directory before calling sure_finish.`,
			"Missing final run report artifact.",
			countersFor(checkpoint.data, 0),
		);
	}
	// Re-validate the terminal unit produces + gate.
	const producesResult = validateProduces(ctx, LAST_UNIT, reportArtifact);
	if (!producesResult.ok) {
		return failure(
			producesResult.repair ?? "Final report invalid.",
			`Terminal unit "${LAST_UNIT.id}" produces invalid: ${producesResult.reason ?? ""}`,
			countersFor(checkpoint.data, 1),
		);
	}
	// Final backstop: re-run the terminal unit's python gate script (run_report
	// → check_run_report.py) so a report mutated between postToolResult and
	// sure_finish is still caught. run_report has a gateScript, no in-process
	// gateCheck — the python script is the authoritative semantic checker, so it
	// runs here too (mirrors the sure_onboard verdict backstop).
	const gateResult = LAST_UNIT.gateScript ? runGateScript(ctx, LAST_UNIT) : { ok: true };
	if (gateResult && !gateResult.ok) {
		return failure(
			gateResult.repair ?? "Final gate failed.",
			`SURE-EVAL terminal gate "${LAST_UNIT.id}" rejected the finish.`,
			countersFor(checkpoint.data, 1),
		);
	}
	// Red lines re-checked at finish: the report's execution_path_actual must
	// not be a local path unless an approved fallback is documented (already
	// enforced by checkRunReport, but we re-affirm here for the audit trail).
	const finishStatus = isRecord(ctx.event) && isRecord(ctx.event.finish) ? ctx.event.finish.status : undefined;
	if (
		finishStatus === "success" &&
		checkpoint.data.currentUnit !== LAST_UNIT.id &&
		!checkpoint.data.completedUnits.includes(LAST_UNIT.id)
	) {
		return failure(
			`The main-flow state machine has not reached the terminal unit "${LAST_UNIT.id}". Continue from "${checkpoint.data.currentUnit}".`,
			"Run finished before the state machine completed.",
			countersFor(checkpoint.data, 0),
		);
	}
	return {
		ok: true,
		state_patch: {
			phase: { id: LAST_UNIT.id, label: LAST_UNIT.label, status: "success", progress: 1 },
			message: "SURE-EVAL main-flow run validated.",
			counters: countersFor(
				{
					currentUnit: LAST_UNIT.id,
					completedUnits: checkpoint.data.completedUnits.includes(LAST_UNIT.id)
						? checkpoint.data.completedUnits
						: [...checkpoint.data.completedUnits, LAST_UNIT.id],
					retries: checkpoint.data.retries,
					failedArtifactDigests: checkpoint.data.failedArtifactDigests,
				},
				0,
			),
			artifacts: [
				{
					type: "run_report",
					name: "SURE-EVAL run report",
					path: `.sure/runs/${ctx.run.runId}/artifacts/${LAST_UNIT.produces}`,
					status: "ready",
					summary: "Validated SURE-EVAL main-flow run report.",
				},
			],
			diagnostics: [],
		},
	};
}

export function postFinish(ctx: SureHookContext): SureHookResult {
	return {
		ok: true,
		state_patch: {
			phase: { id: "finish", label: "SURE-EVAL finished", status: ctx.run.status, progress: 1 },
			message: ctx.run.summary ?? "SURE-EVAL main-flow run finished.",
		},
	};
}

export function onError(ctx: SureHookContext): SureHookResult {
	return {
		ok: true,
		state_patch: {
			phase: { id: "error", label: "SURE-EVAL interrupted", status: "failed" },
			message: ctx.run.errorSummary ?? ctx.run.lastRepair ?? "SURE-EVAL main-flow stopped before completion.",
		},
	};
}
