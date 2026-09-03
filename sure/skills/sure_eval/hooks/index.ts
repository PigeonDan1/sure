import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import type { SureHookContext, SureHookResult } from "@earendil-works/pi-coding-agent/hooks";
import {
	type HarnessRuntimeContract,
	harnessRuntimeEnv,
	resolveHarnessPython,
} from "../../../runtime/harness/resolve.ts";
import {
	gateUnavailable,
	injectOnBlock,
	isExtractionGateExhausted,
	type MemoryCheckpoint,
	type MemoryDiagnostic,
	type MemoryHookEnv,
	memoryConfigOrUndefined,
	onEnterExtractLessons,
	onErrorDigest,
	postFinishMemory,
	preFinishExtraction,
	preStartMemory,
	runMemoryGate,
	safeGateDigest,
	settleOnPass,
	settleOnTerminalFailure,
	stripOutputDir,
} from "../../../runtime/memory/hooks.ts";
import { invokedSkillScripts } from "../../../runtime/script-guard.ts";
import { validateSkillRuntimeBinding, writeSkillRuntimeBinding } from "../../../runtime/usage.ts";
import {
	advance,
	artifactParseError,
	artifactPath,
	bumpRetry,
	type CheckpointData,
	failure,
	type GateResult,
	type RunCheckpoint,
	readArtifact,
	readCheckpoint,
	retryExhausted,
	runBackend,
} from "./checkpoints.ts";
import { FIRST_UNIT, findUnit, LAST_UNIT, MAIN_FLOW_UNITS, TOTAL_UNITS, type Unit } from "./state-machine.ts";
import { validateProduces } from "./validate.ts";

// SURE-EVAL skill hooks (evaluation-only). Mixed drive with three gates:
//   1. checkpoint lock (currentUnit pins position, advance one step)
//   2. validateProduces on EVERY unit (location/format/value-domain + forbidden fields)
//   3. gateCheck (gate units run a Python semantic script via spawnSync)
//   4. memory (sure/runtime/memory/hooks.ts): digest on entering extract_lessons,
//      injection on gate blocks, settlement, publish; advisory only, never blocks.
// The backend scripts live in ../sure_infer/scripts/; this package only carries
// the gate scripts and the memory wrappers.

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function phaseFor(unit: Unit, status: "running" | "blocked" | "success") {
	return { id: unit.id, label: unit.label, status };
}

export function countersFor(completed: CheckpointData, gateBlocks?: number) {
	// completed.blocks survives advance(); the retry ledger does not, so summing
	// it reported zero for every run that was blocked and then recovered. Older
	// checkpoints carry no blocks key, so fall back to the ledger for those.
	const ledgerBlocks =
		completed.blocks ?? Object.values(completed.retries ?? {}).reduce((sum, n) => sum + (n ?? 0), 0);
	return {
		completed_units: completed.completedUnits.length,
		total_units: TOTAL_UNITS,
		gate_blocks: Math.max(ledgerBlocks, gateBlocks ?? 0),
	};
}

const PROTOCOLS = new Set(["standard_system", "strict_core"]);
const BACKEND_SCRIPTS = (ctx: SureHookContext) => resolve(ctx.packageDir, "..", "sure_infer", "scripts");
const ALLOWED_BACKEND = new Set(["sure_infer/scripts/run_eval.py", "sure_infer/scripts/resolve_prediction_source.py"]);
const INFERENCE_SURFACE = [
	"generate_predictions_via_server.py",
	"run_model_mcp_smoke.py",
	"model_wrapper_mcp_server.py",
	"tools/call",
	"server.py",
	"infer_entrypoint.py",
];

function prepareEvaluationRuntime(
	ctx: SureHookContext,
	harnessRuntime: HarnessRuntimeContract,
): { binding?: Record<string, unknown>; error?: string } {
	const script = join(BACKEND_SCRIPTS(ctx), "evaluation_runtime.py");
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

// --- memory system glue -------------------------------------------------------
// The shared orchestration lives in sure/runtime/memory/hooks.ts; this file only
// decides WHEN to call it and where the results go (repair text, checkpoint,
// diagnostics). Every memory failure is advisory: it lands in diagnostics and
// never flips ok to false.

function memoryEnv(ctx: SureHookContext, py?: HarnessRuntimeContract): MemoryHookEnv {
	return { ctx, skill: "sure_eval", py };
}

function memoryOf(data: CheckpointData): MemoryCheckpoint {
	return data.memory ?? {};
}

function withMemory(checkpoint: RunCheckpoint, memory: MemoryCheckpoint): RunCheckpoint {
	return { ...checkpoint, data: { ...checkpoint.data, memory } };
}

// A finish accepted while the state machine still sits on an unfinished unit is
// that unit's terminal failure: entries pending on it become disputed rows, the
// rest of what was injected into it becomes abandoned rows (spec 8.1). Same
// helper as sure_onboard; settleOnTerminalFailure returns early on its own when
// the unit has neither list, so only the completed check is needed here.
function settleStuckUnit(
	env: MemoryHookEnv,
	data: CheckpointData,
	memory: MemoryCheckpoint,
): { memory: MemoryCheckpoint; diagnostics: MemoryDiagnostic[] } {
	const unitId = data.currentUnit;
	if (data.completedUnits.includes(unitId)) {
		return { memory, diagnostics: [] };
	}
	return settleOnTerminalFailure(env, { unitId, memory });
}

// eval's product dir is the source result directory from
// prediction_source_resolved.json (the /sure_infer run being scored). It is only
// used to locate log files for the injection match text; it is never written
// into a digest or an entry.
export function evalProductDir(ctx: SureHookContext): string | undefined {
	const resolved = readArtifact(ctx, "prediction_source_resolved.json");
	if (!isRecord(resolved)) {
		return undefined;
	}
	const sourceDir = resolved.source_results_dir;
	return typeof sourceDir === "string" && sourceDir.length > 0 ? sourceDir : undefined;
}

export function preStart(ctx: SureHookContext): SureHookResult {
	const args = parseArgs(ctx.args);
	const missing = ["model", "datasets"].filter((key) => !args[key]);
	if (missing.length > 0) {
		return failure(
			`Missing required /sure_eval parameter(s): ${missing.join(", ")}. Usage: /sure_eval model=<exact-model-id> datasets=<dataset__version,...> [source=<run_id|abs_dir>] (pipeline_id=<exact-pipeline-id,...> | metrics=<metric,...>) [protocol=standard_system|strict_core] [device=cpu|cuda[:index]] [output_dir=<abs_dir>]`,
			"Missing required parameters.",
		);
	}
	const hasPipeline = typeof args.pipeline_id === "string" && args.pipeline_id.length > 0;
	const hasMetrics = typeof args.metrics === "string" && args.metrics.length > 0;
	if (hasPipeline === hasMetrics) {
		return failure(
			"/sure_eval takes exactly one of pipeline_id=<exact-pipeline-id,...> (route variants) or metrics=<metric,...> (default routes).",
			"Metric selection missing or ambiguous.",
		);
	}
	for (const deprecated of [
		"reuse_predictions_from",
		"model_dir",
		"tmp_root",
		"copy_mode",
		"max_samples",
		"config",
		"evaluation_engine_root",
	]) {
		if (deprecated in args) {
			return failure(
				`/sure_eval does not accept ${deprecated}; the prediction source is the /sure_infer run named by source= (or the unique run under sure/results/<model>/<protocol>/), or a configured approved_results_roots entry.`,
				"Untrusted parameter rejected.",
			);
		}
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

	const artifactsDir = join(ctx.runDir, "artifacts");
	mkdirSync(artifactsDir, { recursive: true });
	const sourcePath = join(artifactsDir, "prediction_source_resolved.json");
	const resolveArgs = [
		join(BACKEND_SCRIPTS(ctx), "resolve_prediction_source.py"),
		"--model",
		args.model,
		"--datasets",
		args.datasets,
		"--protocol-id",
		protocol,
		"--output",
		sourcePath,
	];
	if (typeof args.source === "string" && args.source.length > 0) {
		resolveArgs.push("--source-run", args.source);
	}
	const resolved = spawnSync(runtime.contract.python_executable, resolveArgs, {
		cwd: ctx.packageDir,
		encoding: "utf-8",
		timeout: 120_000,
		env: { ...process.env, ...harnessRuntimeEnv(runtime.contract) },
	});
	if (resolved.status !== 0) {
		const detail = resolved.stderr.trim() || resolved.stdout.trim() || "resolve_prediction_source.py failed";
		return failure(`Unable to resolve the prediction source: ${detail}`, "Prediction source resolution failed.");
	}
	const evaluationRuntime = prepareEvaluationRuntime(ctx, runtime.contract);
	if (!evaluationRuntime.binding) {
		return failure(evaluationRuntime.error ?? "Evaluation Runtime is not ready.", "EVALUATION_RUNTIME_NOT_READY");
	}
	let runtimeBindingPath: string;
	try {
		runtimeBindingPath = writeSkillRuntimeBinding({
			runDir: ctx.runDir,
			skill: "sure_eval",
			harnessRuntime: runtime.contract,
			harnessRole: "Prediction source resolution, orchestration, validation, and the atomic result-bundle append.",
			modelRuntimeReason: "sure_eval scores existing predictions and must not execute model inference.",
			evaluationRuntime: {
				role: "Route resolution, normalization, metric computation, and evaluation reports.",
				binding: evaluationRuntime.binding,
			},
		});
	} catch (error) {
		return failure(
			`Failed to write the runtime responsibility declaration: ${error instanceof Error ? error.message : String(error)}`,
			"Runtime binding not written.",
		);
	}

	// Backend presence check (warn, do not block — gate scripts will surface real failures).
	const backendPresent = existsSync(join(BACKEND_SCRIPTS(ctx), "run_eval.py"));
	const checkpoint = readCheckpoint(ctx);
	const diagnostics: MemoryDiagnostic[] = backendPresent
		? []
		: [
				{
					severity: "warning",
					message: "../sure_infer/scripts/run_eval.py is not bundled.",
					repair: "Install the sure_infer skill package next to this one before running a real evaluation.",
				},
			];
	// Memory: index freshness check (index.py --check) + fact matching into
	// artifacts/memory_context.json. Advisory; the match text must not carry
	// output_dir, so the raw args are stripped first.
	const memoryStart = preStartMemory(memoryEnv(ctx, runtime.contract), {
		targetId: args.model,
		strippedArgs: stripOutputDir(ctx.args),
	});
	diagnostics.push(...memoryStart.diagnostics);
	return {
		ok: true,
		state_patch: {
			phase: phaseFor(findUnit(checkpoint.data.currentUnit) ?? FIRST_UNIT, "running"),
			message: `SURE-EVAL skill loaded for model "${args.model}"; Harness Runtime ${runtime.contract.runtime_id}; prediction source: ${sourcePath}.`,
			counters: countersFor(checkpoint.data, 0),
			diagnostics,
			checkpoint,
			artifacts: [
				{ type: "runtime_binding", name: "Skill runtime binding", path: runtimeBindingPath, status: "ready" },
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

// A non-success finish that already carries eval_run_report.json must not claim
// more than it proved: no inference, no reuse of old scores, no append.
export function incompleteReportError(payload: Record<string, unknown>, finishStatus: unknown): string | undefined {
	if (finishStatus !== "incomplete" && finishStatus !== "failed") {
		return "non-success /sure_eval finish must declare status=incomplete or status=failed";
	}
	if (payload.schema !== "sure.eval.run_report.v1" || payload.status !== finishStatus) {
		return `eval_run_report status must equal requested finish status ${String(finishStatus)}`;
	}
	if (typeof payload.error_code !== "string" || payload.error_code.length === 0) {
		return "non-success eval_run_report must declare error_code";
	}
	if (
		payload.evaluation_only !== true ||
		payload.inference_executed !== false ||
		payload.old_evaluation_reused !== false ||
		payload.append_attempted !== false
	) {
		return "non-success /sure_eval must prove evaluation_only=true, inference_executed=false, old_evaluation_reused=false, and append_attempted=false";
	}
	if (!isRecord(payload.source_identity)) {
		return "non-success eval_run_report must bind the resolved source_identity";
	}
	return undefined;
}

// preToolCall: two tables in front of the per-unit whitelist. Backend scripts
// come from ../sure_infer/scripts and only the evaluation runner and the source
// resolver may be called; anything that could start a model is refused outright.
// Then the package's own gate scripts (scripts/*.py) may only be invoked from
// the unit that owns them.
const UNIT_AGNOSTIC_SCRIPTS = new Set<string>([]);

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
	const forbiddenBackend = invokedSkillScripts(command, "sure_infer/scripts").find(
		(script) => !ALLOWED_BACKEND.has(script),
	);
	if (forbiddenBackend) {
		return failure(
			`/sure_eval must use the run_eval.py backend; direct call to ${forbiddenBackend} is forbidden.`,
			"Forbidden backend script.",
		);
	}
	const inferenceSurface = INFERENCE_SURFACE.find((forbidden) => command.includes(forbidden));
	if (inferenceSurface) {
		return failure(
			`/sure_eval is evaluation-only; inference surface ${inferenceSurface} is forbidden.`,
			"Inference surface forbidden.",
		);
	}
	const invokedScripts = invokedSkillScripts(command).filter((script) => !UNIT_AGNOSTIC_SCRIPTS.has(script));
	if (invokedScripts.length === 0) {
		return { ok: true };
	}
	const checkpoint = readCheckpoint(ctx);
	const currentUnit = findUnit(checkpoint.data.currentUnit);
	if (!currentUnit) {
		return { ok: true };
	}
	// extract_lessons never sits in an exhausted state: its gate exhaustion
	// auto-advances inside postToolResult (see failOrRetry). Skipping it here keeps
	// the digest preview and the gate script usable even if config raises the
	// extraction ceiling above the generic retry ceiling.
	if (currentUnit.id !== "extract_lessons" && retryExhausted(currentUnit, checkpoint.data)) {
		const attempts = checkpoint.data.retries[currentUnit.id] ?? 0;
		return failure(
			`Unit "${currentUnit.id}" already exhausted ${attempts} attempts. Do not rerun it from unrelated tool calls; persist an accurate failed run report or apply a deliberate repair and reset this unit's retry counter.`,
			`Gate "${currentUnit.id}" is in a terminal failed state.`,
			countersFor(checkpoint.data, attempts),
			checkpoint,
		);
	}
	// A script is allowed if the current unit (or any prior completed unit) owns
	// it as its gate script. Helper scripts (today only build_run_digest.py on
	// extract_lessons) are allowed while their own unit is current and never
	// later, same as sure_onboard: they prepare that unit's artifact and have no
	// business once the unit is behind us.
	const owningUnits = [currentUnit, ...MAIN_FLOW_UNITS_UP_TO(checkpoint.data, currentUnit)];
	const allowed = new Set<string>();
	for (const unit of owningUnits) {
		if (unit.gateScript) {
			allowed.add(`scripts/${unit.gateScript}`);
		}
	}
	for (const helperScript of currentUnit.helperScripts ?? []) {
		allowed.add(`scripts/${helperScript}`);
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
// in-process gateCheck result. The extraction gate is stdlib-only and goes
// through the memory python resolver (HARNESS_PYTHON_BIN first) with the same
// --run-dir/--produces contract and stderr-first repair; it never uses runBackend.
function runGateScript(ctx: SureHookContext, unit: Unit): GateResult | undefined {
	if (!unit.gateScript) {
		return undefined;
	}
	const produces = artifactPath(ctx, unit.produces);
	if (unit.gateScript === "check_memory_extraction.py") {
		const r = runMemoryGate(memoryEnv(ctx), produces);
		if (r.ok) {
			return { ok: true };
		}
		if (r.ranFailed) {
			// The gate never judged the declaration (missing wrapper, no interpreter, crash). Its
			// text is a traceback, not a repair, and blocking on it stalls the unit for good:
			// failOrRetry's unchanged-artifact guard consumes no retry when the agent has nothing
			// it can change, so the extraction cap is never reached and the unit never advances.
			// Let it pass and mark the extraction failed, so post_finish publishes nothing that
			// was never gated. Same call preFinishExtraction makes for the finish path.
			return { ok: true, ranFailed: true, diagnostics: [gateUnavailable(r.repair)] };
		}
		return {
			ok: false,
			repair: r.repair ?? `Gate script scripts/${unit.gateScript} failed.`,
			reason: r.reason ?? `gate script ${unit.gateScript} failed`,
		};
	}
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
						message: "A tool call returned an error during the SURE-EVAL run.",
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
			// "missing" also covers "present but not JSON": readArtifact returns undefined for both.
			// Only the first is a unit still in progress; the second has to be repaired, or the gate
			// never runs and nothing ever consumes a retry.
			const parseError = artifactParseError(ctx, currentUnit.produces);
			if (!parseError) {
				return { ok: true };
			}
			return failOrRetry(
				ctx,
				currentUnit,
				checkpoint,
				`${currentUnit.produces} is present but is not valid JSON: ${parseError}. Rewrite it as a single JSON object; unit "${currentUnit.id}" cannot advance until it parses.`,
				"produces is not valid JSON",
			);
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
	// Set when the unit ran a gate script: only the memory gate ever comes back ok with
	// ranFailed, and that has to reach the advance patch below.
	let gateRun: GateResult | undefined;
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
		gateRun = runGateScript(ctx, currentUnit);
		if (gateRun && !gateRun.ok) {
			return failOrRetry(
				ctx,
				currentUnit,
				checkpoint,
				gateRun.repair ?? `Gate script "${currentUnit.id}" failed.`,
				gateRun.reason ?? "gate script failed",
			);
		}
	}

	// All gates passed: settle memory for the unit that just passed, then advance
	// (clearing its retry counter). Entering extract_lessons builds the run
	// digest (cutoff = events so far, mark-passed = the unit that just passed).
	const env = memoryEnv(ctx);
	const settled = settleOnPass(env, { unitId: currentUnit.id, memory: memoryOf(checkpoint.data) });
	const next = advance(currentUnit, { ...checkpoint.data, memory: settled.memory });
	if (!next) {
		return { ok: true };
	}
	const diagnostics: MemoryDiagnostic[] = [...settled.diagnostics];
	let landed = next;
	if (next.data.currentUnit === "extract_lessons") {
		const entered = onEnterExtractLessons(env, currentUnit.id, memoryOf(next.data));
		landed = withMemory(next, entered.memory);
		diagnostics.push(...entered.diagnostics);
	}
	if (gateRun?.ranFailed) {
		// Nothing was gated, so nothing may be published for this run (spec 4.5).
		landed = withMemory(landed, { ...memoryOf(landed.data), extractionStatus: "failed" });
		diagnostics.push(...(gateRun.diagnostics ?? []));
	}
	return {
		ok: true,
		state_patch: {
			phase: phaseFor(findUnit(landed.data.currentUnit) ?? LAST_UNIT, "running"),
			message: `Advanced to unit "${landed.data.currentUnit}".`,
			counters: countersFor(landed.data, 0),
			...(diagnostics.length > 0 ? { diagnostics } : {}),
			checkpoint: landed,
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
	// safeGateDigest, not gateDigest: the gateInputs trees are agent-writable, and a throw here
	// reaches the agent as an unactionable repair with no state_patch, so no retry is ever
	// consumed again.
	const digest = safeGateDigest(ctx, unit);
	if (digest === undefined || digest !== previousDigest) {
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

// On gate failure: inject matching memory into the repair, bump retry; if
// exhausted, mark the unit FAILED (stay, no advance). extract_lessons is the one
// exception: its gate exhaustion auto-advances with extractionStatus=failed so a
// bad extraction can never block the skill's own finish.
function failOrRetry(
	ctx: SureHookContext,
	unit: Unit,
	checkpoint: { data: CheckpointData },
	repair: string,
	reason: string,
): SureHookResult {
	// undefined means the digest could not be taken at all; two missing digests are not evidence
	// of unchanged content, so that case falls through and consumes the retry.
	const artifactDigest = safeGateDigest(ctx, unit);
	if (artifactDigest !== undefined && checkpoint.data.failedArtifactDigests?.[unit.id] === artifactDigest) {
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
	// This attempt really consumes a retry: match memory against the raw repair
	// (+ log tail) BEFORE bumping, so the usage row and the injected list travel
	// with the new checkpoint. `repair` stays raw inside diagnostics; only the
	// top-level repair (what the agent reads) carries the Memory block.
	// producesPath lets hooks.ts read this unit's own log_path from the artifact
	// (execute_wait: execution_result.json) even when log_paths.json has no row.
	const env = memoryEnv(ctx);
	const injected = injectOnBlock(env, {
		unitId: unit.id,
		attempt: (checkpoint.data.retries[unit.id] ?? 0) + 1,
		rawRepair: repair,
		productDir: evalProductDir(ctx),
		producesPath: artifactPath(ctx, unit.produces),
		memory: memoryOf(checkpoint.data),
	});
	const next = bumpRetry(unit, { ...checkpoint.data, memory: injected.memory }, artifactDigest);
	const attempts = next.data.retries[unit.id] ?? 1;
	if (unit.id === "extract_lessons") {
		// memoryConfigOrUndefined never throws (Task 12): an unreadable config.json must not
		// take the run down. With no config there is no extraction cap to read, so this unit
		// falls back to the state machine's own cap; the retryExhausted call below is only
		// reached by non-extraction units, so without this fallback extract_lessons would
		// block for ever.
		const memoryConfig = memoryConfigOrUndefined();
		const extractionExhausted = memoryConfig
			? isExtractionGateExhausted(unit.id, attempts, undefined, memoryConfig)
			: retryExhausted(unit, next.data);
		if (extractionExhausted) {
			// Let the run go on: the extraction is a by-product and must never block
			// the skill's finish. Whatever was injected at this unit is settled as a
			// terminal failure, then the state machine advances with
			// extractionStatus=failed (post_finish skips publish on it). The message
			// is the same fixed sentence as sure_onboard's.
			const settled = settleOnTerminalFailure(env, { unitId: unit.id, memory: injected.memory });
			const memory: MemoryCheckpoint = { ...settled.memory, extractionStatus: "failed" };
			const landed = advance(unit, { ...next.data, memory }) ?? withMemory(next, memory);
			return {
				ok: true,
				state_patch: {
					phase: phaseFor(findUnit(landed.data.currentUnit) ?? LAST_UNIT, "running"),
					message: `Extraction gate "${unit.id}" exhausted ${attempts} blocked attempts; extraction marked failed, advanced to unit "${landed.data.currentUnit}".`,
					counters: countersFor(landed.data, attempts),
					checkpoint: landed,
					diagnostics: [
						{ severity: "warning", message: `extraction: failed (${reason})`, repair },
						...injected.diagnostics,
						...settled.diagnostics,
					],
				},
			};
		}
		return {
			ok: false,
			repair: injected.repair,
			state_patch: {
				phase: phaseFor(unit, "blocked"),
				message: `Gate "${unit.id}" blocked (attempt ${attempts}): ${reason}`,
				counters: countersFor(next.data, attempts),
				checkpoint: next,
				diagnostics: [{ severity: "error", message: reason, repair }, ...injected.diagnostics],
			},
		};
	}
	if (retryExhausted(unit, next.data)) {
		// Same shape as failure(), hand-built so diagnostics keep the raw repair while
		// the top-level repair carries the Memory block. The message must keep the
		// prefix `Gate "<id>" exhausted`: digest.py reads it as the unit's terminal
		// failure. Terminal failure settles the unit's pending entries as disputed.
		const settled = settleOnTerminalFailure(env, { unitId: unit.id, memory: injected.memory });
		const message = `Gate "${unit.id}" exhausted retries: ${reason}`;
		// injectOnBlock returns `rawRepair + "\n\n" + block`, so this slice is "" when
		// nothing was injected. The exhaustion sentence hangs off the RAW repair and the
		// block is re-appended after it: the Memory block has to stay last, otherwise
		// the digest's strip_memory_block eats the sentence together with the block and
		// the next run's prior_runs loses it.
		const memoryBlock = injected.repair.slice(repair.length);
		return {
			ok: false,
			repair: `${repair} (unit "${unit.id}" FAILED after ${attempts} retries; classify via failure_taxonomy and either repair manually or finish with status failed.)${memoryBlock}`,
			state_patch: {
				phase: { id: "gate", label: "SURE eval gate blocked", status: "blocked" },
				message,
				counters: countersFor(next.data, attempts),
				checkpoint: withMemory(next, settled.memory),
				diagnostics: [{ severity: "error", message, repair }, ...injected.diagnostics, ...settled.diagnostics],
			},
		};
	}
	return {
		ok: false,
		repair: injected.repair,
		state_patch: {
			phase: phaseFor(unit, "blocked"),
			message: `Gate "${unit.id}" blocked (attempt ${attempts}): ${reason}`,
			counters: countersFor(next.data, attempts),
			checkpoint: next,
			diagnostics: [{ severity: "error", message: reason, repair }, ...injected.diagnostics],
		},
	};
}

export function preFinish(ctx: SureHookContext): SureHookResult {
	const runtimeBindingError = validateSkillRuntimeBinding(
		join(ctx.runDir, "artifacts", "runtime_binding.json"),
		"sure_eval",
		true,
	);
	if (runtimeBindingError) {
		return failure(runtimeBindingError, "Runtime binding invalid.");
	}
	const checkpoint = readCheckpoint(ctx);
	const finishStatusEarly = isRecord(ctx.event) && isRecord(ctx.event.finish) ? ctx.event.finish.status : undefined;
	if (finishStatusEarly !== "success") {
		// A run that already wrote eval_run_report.json must leave an honest one behind.
		const evalReport = join(ctx.runDir, "artifacts", "eval_run_report.json");
		if (existsSync(evalReport)) {
			let payload: Record<string, unknown>;
			try {
				const parsed: unknown = JSON.parse(readFileSync(evalReport, "utf-8"));
				payload = isRecord(parsed) ? parsed : {};
			} catch (error) {
				return failure(
					`artifacts/eval_run_report.json is not valid JSON: ${error instanceof Error ? error.message : String(error)}`,
					"Evaluation report invalid.",
				);
			}
			if (payload.status !== "success") {
				const reportError = incompleteReportError(payload, finishStatusEarly);
				if (reportError) {
					return failure(reportError, "Evaluation report contradicts the finish.");
				}
				const source = readArtifact(ctx, "prediction_source_resolved.json");
				const identity = payload.source_identity as Record<string, unknown>;
				if (!isRecord(source)) {
					return failure(
						"Non-success /sure_eval must retain artifacts/prediction_source_resolved.json.",
						"Prediction source missing.",
					);
				}
				for (const key of ["model_fingerprint", "protocol_id", "dataset_set_digest", "source_report_sha256"]) {
					if (identity[key] !== source[key]) {
						return failure(
							`Non-success /sure_eval source_identity.${key} differs from the resolved prediction source.`,
							"Source identity drift.",
						);
					}
				}
			}
		}
	}
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
	// Memory at finish (non-success only; a success finish changes nothing here).
	// 1. A failed / incomplete run that never reached extract_lessons must still
	//    leave an extraction declaration behind (spec 4.5): two repairs, the third
	//    finish is let through with extractionStatus=failed. Runs that passed (or
	//    exhausted) the unit are exempt. preFinishExtraction itself is a no-op for
	//    any finishStatus other than failed / incomplete, so undefined (older
	//    callers, existing tests) keeps the old behaviour.
	// 2. Only once the finish is accepted: the unit the run died on is a terminal
	//    failure for the entries still pending on it (spec 8.1). A rejected finish
	//    settles nothing, the unit may still pass later.
	const env = memoryEnv(ctx);
	let memory = memoryOf(checkpoint.data);
	const memoryDiagnostics: MemoryDiagnostic[] = [];
	if (finishStatus !== "success") {
		if (!checkpoint.data.completedUnits.includes("extract_lessons")) {
			const extraction = preFinishExtraction(env, { finishStatus: String(finishStatus), memory });
			if (!extraction.ok) {
				// Hand-built copy of failure()'s patch, which hard-codes a single-element diagnostics
				// array: the repair tells the agent to read artifacts/run_digest.json first, and when
				// the digest build failed the only thing that says that file holds nothing but an
				// error - and that no_new_lessons=true citing it is acceptable - is
				// extraction.diagnostics. Dropping it leaves the agent reading an error stub blind.
				const repair =
					extraction.repair ??
					"Produce artifacts/extraction_declaration.json per sure/runtime/memory/EXTRACTION.md, then call sure_finish again.";
				const message = "Non-success Eval finish requires an extraction declaration.";
				return {
					ok: false,
					repair,
					state_patch: {
						phase: { id: "gate", label: "SURE eval gate blocked", status: "blocked" },
						message,
						counters: countersFor(checkpoint.data, 1),
						checkpoint: withMemory(checkpoint, extraction.memory),
						diagnostics: [{ severity: "error", message, repair }, ...extraction.diagnostics],
					},
				};
			}
			memory = extraction.memory;
			memoryDiagnostics.push(...extraction.diagnostics);
		}
		const stuck = settleStuckUnit(env, checkpoint.data, memory);
		memory = stuck.memory;
		memoryDiagnostics.push(...stuck.diagnostics);
	}
	return {
		ok: true,
		state_patch: {
			phase: { id: LAST_UNIT.id, label: LAST_UNIT.label, status: "success", progress: 1 },
			message: "SURE-EVAL run validated.",
			counters: countersFor(
				{
					currentUnit: LAST_UNIT.id,
					completedUnits: checkpoint.data.completedUnits.includes(LAST_UNIT.id)
						? checkpoint.data.completedUnits
						: [...checkpoint.data.completedUnits, LAST_UNIT.id],
					retries: checkpoint.data.retries,
					failedArtifactDigests: checkpoint.data.failedArtifactDigests,
					memory,
				},
				0,
			),
			artifacts: [
				{
					type: "run_report",
					name: "SURE-EVAL run report",
					path: `.sure/runs/${ctx.run.runId}/artifacts/${LAST_UNIT.produces}`,
					status: "ready",
					summary: "Validated SURE-EVAL run report.",
				},
			],
			...(memoryDiagnostics.length > 0 ? { diagnostics: memoryDiagnostics } : {}),
			checkpoint: withMemory(checkpoint, memory),
		},
	};
}

export function postFinish(ctx: SureHookContext): SureHookResult {
	// Publish this run's candidates into sure/memory/provisional (skipped when the
	// extraction failed), then promote and rebuild the index. Advisory only.
	const memory = memoryOf(readCheckpoint(ctx).data);
	const published = postFinishMemory(memoryEnv(ctx), memory);
	return {
		ok: true,
		state_patch: {
			phase: { id: "finish", label: "SURE-EVAL finished", status: ctx.run.status, progress: 1 },
			message: ctx.run.summary ?? "SURE-EVAL run finished.",
			...(published.diagnostics.length > 0 ? { diagnostics: published.diagnostics } : {}),
		},
	};
}

export function onError(ctx: SureHookContext): SureHookResult {
	// Leave a digest behind so the next run on the same target sees where this
	// one stopped (prior_runs); nothing is published from here. The checkpoint's memory
	// goes in so a digest the extraction gate already validated is kept as it is.
	const env = memoryEnv(ctx);
	const checkpoint = readCheckpoint(ctx);
	const memory = memoryOf(checkpoint.data);
	// No pre_finish ever runs on this path, so this is the last chance to close the unit the run
	// died on. The patch below carries no checkpoint, so its lists are never cleared on disk:
	// idempotency rests entirely on the settle rows already in usage/<run_id>.jsonl.
	const stuck = settleStuckUnit(env, checkpoint.data, memory);
	const digest = onErrorDigest(env, memory);
	const diagnostics = [...stuck.diagnostics, ...digest.diagnostics];
	return {
		ok: true,
		state_patch: {
			phase: { id: "error", label: "SURE-EVAL interrupted", status: "failed" },
			message: ctx.run.errorSummary ?? ctx.run.lastRepair ?? "SURE-EVAL run stopped before completion.",
			...(diagnostics.length > 0 ? { diagnostics } : {}),
		},
	};
}
