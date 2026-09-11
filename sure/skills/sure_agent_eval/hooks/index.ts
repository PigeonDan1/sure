import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import type { SureHookContext, SureHookResult } from "@earendil-works/pi-coding-agent/hooks";
import { harnessRuntimeEnv, resolveHarnessPython } from "../../../runtime/harness/resolve.ts";
import { invokedSkillScripts } from "../../../runtime/script-guard.ts";
import {
	advance,
	artifactParseError,
	artifactPath,
	bumpRetry,
	type CheckpointData,
	failure,
	type GateResult,
	gateDigest,
	readArtifact,
	readCheckpoint,
	retryExhausted,
	runBackend,
} from "./checkpoints.ts";
import { FIRST_UNIT, findUnit, LAST_UNIT, MAIN_FLOW_UNITS, TOTAL_UNITS, type Unit } from "./state-machine.ts";
import { validateProduces } from "./validate.ts";

// SURE-AGENT-EVAL skill hooks. Mixed drive with three gates:
//   1. checkpoint lock (currentUnit pins position, advance one step)
//   2. validateProduces on EVERY unit (location/format/value-domain + forbidden fields)
//   3. gateScript (gate units run a Python semantic script via spawnSync)
// The skill package carries its own backend scripts (resolve_agent.py,
// agent_runner.py, run_agent_eval.py); they reuse the sure_infer evaluation
// code by import, never by direct agent invocation.

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

// The sure_infer inference surface stays off-limits: the agent runs its own
// scripts/agent_runner.py, which drives the stage models itself. A direct call
// would bypass the chain contract and the bundle layout this skill guarantees.
const INFERENCE_SURFACE = [
	"generate_predictions_via_server.py",
	"run_model_mcp_smoke.py",
	"model_wrapper_mcp_server.py",
	"tools/call",
	"server.py",
	"infer_entrypoint.py",
	"run_infer.py",
	"run_eval.py",
];

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
	const missing = ["agent", "datasets", "metrics"].filter((key) => !args[key]);
	if (missing.length > 0) {
		return failure(
			`Missing required /sure_agent_eval parameter(s): ${missing.join(", ")}. Usage: /sure_agent_eval agent=<path-to-agent.yaml> datasets=<source[@version],...> metrics=<metric,...> [dataset_source_key=<key>] [max_samples=<n>] [device=cpu|cuda[:index]] [output_dir=<abs_dir>]`,
			"Missing required parameters.",
		);
	}
	for (const deprecated of ["model", "model_dir", "source", "pipeline_id", "config", "evaluation_engine_root"]) {
		if (deprecated in args) {
			return failure(
				`/sure_agent_eval does not accept ${deprecated}; the models come from the agent spec's stages, datasets from allowed_source_roots, and metrics resolve to the engine's default pipelines.`,
				"Untrusted parameter rejected.",
			);
		}
	}
	const runtime = resolveHarnessPython(ctx.packageDir);
	if (!runtime.ok || !runtime.contract) {
		return failure(
			runtime.error ?? "Bootstrap the locked common Harness Runtime and retry /sure_agent_eval.",
			"HARNESS_RUNTIME_NOT_READY",
		);
	}

	const artifactsDir = join(ctx.runDir, "artifacts");
	mkdirSync(artifactsDir, { recursive: true });
	const resolvedPath = join(artifactsDir, "agent_spec_resolved.json");
	const resolveArgs = [
		join(ctx.packageDir, "scripts", "resolve_agent.py"),
		"--agent",
		args.agent,
		"--datasets",
		args.datasets,
		"--metrics",
		args.metrics,
		"--run-id",
		ctx.run.runId,
		"--output",
		resolvedPath,
	];
	if (typeof args.dataset_source_key === "string" && args.dataset_source_key.length > 0) {
		resolveArgs.push("--dataset-source-key", args.dataset_source_key);
	}
	if (typeof args.output_dir === "string" && args.output_dir.length > 0) {
		resolveArgs.push("--output-dir", args.output_dir);
	}
	const resolved = spawnSync(runtime.contract.python_executable, resolveArgs, {
		cwd: ctx.packageDir,
		encoding: "utf-8",
		timeout: 120_000,
		env: { ...process.env, ...harnessRuntimeEnv(runtime.contract) },
	});
	if (resolved.status !== 0) {
		const detail = resolved.stderr.trim() || resolved.stdout.trim() || "resolve_agent.py failed";
		return failure(`Unable to resolve the agent spec: ${detail}`, "Agent spec resolution failed.");
	}

	const checkpoint = readCheckpoint(ctx);
	return {
		ok: true,
		state_patch: {
			phase: phaseFor(findUnit(checkpoint.data.currentUnit) ?? FIRST_UNIT, "running"),
			message: `SURE-AGENT-EVAL skill loaded for agent spec "${args.agent}"; resolved plan: ${resolvedPath}.`,
			counters: countersFor(checkpoint.data, 0),
			checkpoint,
			artifacts: [
				{
					type: "agent_spec_resolved",
					name: "Resolved agent spec",
					path: `.sure/runs/${ctx.run.runId}/artifacts/agent_spec_resolved.json`,
					status: "ready",
				},
			],
		},
	};
}

// preToolCall: the per-unit whitelist in front of two fixed tables. The
// sure_infer backend is import-only for this skill (any direct call is
// refused), and the package's own scripts may only be invoked from the unit
// that owns them.
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
	const backendScripts = invokedSkillScripts(command, "sure_infer/scripts");
	if (backendScripts.length > 0) {
		return failure(
			`/sure_agent_eval must use its own scripts (agent_runner.py, run_agent_eval.py); direct call to ${backendScripts[0]} is forbidden.`,
			"Forbidden backend script.",
		);
	}
	const inferenceSurface = INFERENCE_SURFACE.find((forbidden) => command.includes(forbidden));
	if (inferenceSurface) {
		return failure(
			`/sure_agent_eval drives the stage models only through scripts/agent_runner.py; inference surface ${inferenceSurface} is forbidden.`,
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
	if (retryExhausted(currentUnit, checkpoint.data)) {
		const attempts = checkpoint.data.retries[currentUnit.id] ?? 0;
		return failure(
			`Unit "${currentUnit.id}" already exhausted ${attempts} attempts. Do not rerun it from unrelated tool calls; persist an accurate failed run report or apply a deliberate repair and reset this unit's retry counter.`,
			`Gate "${currentUnit.id}" is in a terminal failed state.`,
			countersFor(checkpoint.data, attempts),
			checkpoint,
		);
	}
	// A script is allowed if the current unit (or any prior completed unit) owns
	// it as its gate script. Helper scripts (the unit's runnable backend) are
	// allowed while their own unit is current and never later.
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
		repair: `Script ${invokedScript} is not permitted from unit "${currentUnit.id}". Only the current unit's owned scripts may run here. ${currentUnit.helperScripts?.length ? `This unit owns ${currentUnit.helperScripts.map((s) => `scripts/${s}`).join(", ")}.` : "This unit owns no runnable backend scripts."}`,
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
						message: "A tool call returned an error during the SURE-AGENT-EVAL run.",
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
	// then the authoritative Python semantic script.
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

	// All gates passed: advance (clearing the unit's retry counter).
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
	const digest = gateDigest(ctx, unit);
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

// On gate failure: bump retry; if exhausted, mark the unit FAILED (stay, no advance).
function failOrRetry(
	ctx: SureHookContext,
	unit: Unit,
	checkpoint: { data: CheckpointData },
	repair: string,
	reason: string,
): SureHookResult {
	// undefined means the digest could not be taken at all; two missing digests are not evidence
	// of unchanged content, so that case falls through and consumes the retry.
	const artifactDigest = gateDigest(ctx, unit);
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
	const next = bumpRetry(unit, checkpoint.data, artifactDigest);
	const attempts = next.data.retries[unit.id] ?? 1;
	if (retryExhausted(unit, next.data)) {
		// The message must keep the prefix `Gate "<id>" exhausted`: digest.py reads it as the
		// unit's terminal failure.
		const message = `Gate "${unit.id}" exhausted retries: ${reason}`;
		return {
			ok: false,
			repair: `${repair} (unit "${unit.id}" FAILED after ${attempts} retries; either repair manually or finish with status failed.)`,
			state_patch: {
				phase: { id: "gate", label: "SURE agent-eval gate blocked", status: "blocked" },
				message,
				counters: countersFor(next.data, attempts),
				checkpoint: next,
				diagnostics: [{ severity: "error", message, repair }],
			},
		};
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
	// Final backstop: re-run the terminal unit's python gate script so a report
	// mutated between postToolResult and sure_finish is still caught.
	const gateResult = LAST_UNIT.gateScript ? runGateScript(ctx, LAST_UNIT) : { ok: true };
	if (gateResult && !gateResult.ok) {
		return failure(
			gateResult.repair ?? "Final gate failed.",
			`SURE-AGENT-EVAL terminal gate "${LAST_UNIT.id}" rejected the finish.`,
			countersFor(checkpoint.data, 1),
		);
	}
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
			message: "SURE-AGENT-EVAL run validated.",
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
					name: "SURE-AGENT-EVAL run report",
					path: `.sure/runs/${ctx.run.runId}/artifacts/${LAST_UNIT.produces}`,
					status: "ready",
					summary: "Validated SURE-AGENT-EVAL run report.",
				},
			],
			checkpoint,
		},
	};
}

export function postFinish(ctx: SureHookContext): SureHookResult {
	return {
		ok: true,
		state_patch: {
			phase: { id: "finish", label: "SURE-AGENT-EVAL finished", status: ctx.run.status, progress: 1 },
			message: ctx.run.summary ?? "SURE-AGENT-EVAL run finished.",
		},
	};
}

export function onError(ctx: SureHookContext): SureHookResult {
	return {
		ok: true,
		state_patch: {
			phase: { id: "error", label: "SURE-AGENT-EVAL interrupted", status: "failed" },
			message: ctx.run.errorSummary ?? ctx.run.lastRepair ?? "SURE-AGENT-EVAL run stopped before completion.",
		},
	};
}
