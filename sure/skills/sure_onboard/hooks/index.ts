import { existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import type { SureHookContext, SureHookResult } from "@earendil-works/pi-coding-agent/hooks";
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
import { FIRST_UNIT, findUnit, LAST_UNIT, MODEL_TOOL_UNITS, TOTAL_UNITS, type Unit } from "./state-machine.ts";
import { validateProduces } from "./validate.ts";

// SURE-EVAL model-tool skill hooks. Mixed drive with three gates:
//   1. checkpoint lock (currentUnit pins position, advance one step)
//   2. validateProduces on EVERY unit (location/format/value-domain + forbidden fields)
//   3. gateCheck (gate units run a Python semantic script via spawnSync)
// Source of truth: docs/agents/model_tool_agent/AGENTS.md in the sure-eval repo.

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function phaseFor(unit: Unit, status: "running" | "blocked" | "success") {
	return { id: unit.id, label: unit.label, status };
}

function countersFor(completed: CheckpointData, gateBlocks: number) {
	return {
		completed_units: completed.completedUnits.length,
		total_units: TOTAL_UNITS,
		gate_blocks: gateBlocks,
	};
}

// Resolve the model artifacts dir. sure_onboard binds model entity products to
// the repo-level sure/models/<model_id>/ directory (product layout decision).
function modelDirFor(ctx: SureHookContext): string | undefined {
	const args = parseArgs(ctx.args);
	const modelId = typeof args.model_id === "string" ? args.model_id : undefined;
	const existing = typeof args.existing_model_dir === "string" ? args.existing_model_dir : undefined;
	if (existing) {
		return existing;
	}
	if (modelId) {
		return join(ctx.cwd, "sure", "models", modelId);
	}
	return undefined;
}

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

export function preStart(ctx: SureHookContext): SureHookResult {
	const args = parseArgs(ctx.args);
	const missing: string[] = [];
	if (!args.model_id) {
		missing.push("model_id");
	}
	if (!args.repo) {
		missing.push("repo");
	}
	if (!args.task_type) {
		missing.push("task_type");
	}
	if (!args.deployment_type) {
		missing.push("deployment_type");
	}
	if (missing.length > 0) {
		return failure(
			`Missing required /sure_onboard parameters: ${missing.join(", ")}. Usage: /sure_onboard model_id=<name> repo=<url|path> task_type=<asr|tts|vc|kws|speech_understanding> deployment_type=<local|api> [preferred_backend=uv|pip|conda|pixi|docker|api] [python_version=...] [weights_source=...] [force_repair=true] [existing_model_dir=...] [max_retries=3]`,
			"Missing required parameters.",
		);
	}

	// Early enum validation — fail fast on a bad task_type/deployment_type
	// instead of letting it surface much later at the classify unit.
	const TASK_TYPES = ["asr", "tts", "vc", "kws", "speech_understanding"];
	const DEPLOYMENT_TYPES = ["local", "api"];
	if (!TASK_TYPES.includes(args.task_type)) {
		return failure(
			`task_type "${args.task_type}" is not one of ${JSON.stringify(TASK_TYPES)}. Correct the task_type and re-run /sure_onboard.`,
			"Invalid task_type.",
		);
	}
	if (!DEPLOYMENT_TYPES.includes(args.deployment_type)) {
		return failure(
			`deployment_type "${args.deployment_type}" is not one of ${JSON.stringify(DEPLOYMENT_TYPES)}. Correct the deployment_type and re-run /sure_onboard.`,
			"Invalid deployment_type.",
		);
	}

	// Ensure the global model root exists; the run will land artifacts there.
	const modelDir = modelDirFor(ctx);
	if (modelDir) {
		try {
			mkdirSync(modelDir, { recursive: true });
		} catch {
			// Non-fatal — the wrapper/save_artifacts units will surface real errors.
		}
	}

	const scriptsDir = join(ctx.packageDir, "scripts");
	const backendPresent = existsSync(scriptsDir) && existsSync(join(scriptsDir, "check_verdict.py"));
	const checkpoint = readCheckpoint(ctx);
	const diagnostics = backendPresent
		? []
		: [
				{
					severity: "warning" as const,
					message: "scripts/ backend is not fully bundled.",
					repair: "Bundle the deterministic Python backend into scripts/ before a real onboard.",
				},
			];
	return {
		ok: true,
		state_patch: {
			phase: phaseFor(findUnit(checkpoint.data.currentUnit) ?? FIRST_UNIT, "running"),
			message: `SURE model-tool skill loaded for model "${args.model_id}" (→ ${modelDir ?? "(no dir)"}).`,
			counters: countersFor(checkpoint.data, 0),
			diagnostics,
			checkpoint,
		},
	};
}

// preToolCall: enforce the per-unit script whitelist.
export function preToolCall(ctx: SureHookContext): SureHookResult {
	const event = isRecord(ctx.event) ? ctx.event : {};
	const toolCall = isRecord(event.toolCall) ? event.toolCall : {};
	const toolName = typeof toolCall.name === "string" ? toolCall.name : "";
	if (toolName !== "bash") {
		return { ok: true };
	}
	const input = isRecord(toolCall.input) ? toolCall.input : {};
	const command = typeof input.command === "string" ? input.command : "";
	const scriptMatch = command.match(/scripts\/([A-Za-z0-9_]+\.py)\b/);
	if (!scriptMatch) {
		return { ok: true };
	}
	const invokedScript = `scripts/${scriptMatch[1]}`;
	const checkpoint = readCheckpoint(ctx);
	const currentUnit = findUnit(checkpoint.data.currentUnit);
	if (!currentUnit) {
		return { ok: true };
	}
	const completedSet = new Set(checkpoint.data.completedUnits);
	const owningUnits = MODEL_TOOL_UNITS.filter(
		(unit) => (completedSet.has(unit.id) && unit.id !== currentUnit.id) || unit.id === currentUnit.id,
	);
	const allowed = new Set<string>();
	for (const unit of owningUnits) {
		if (unit.gateScript) {
			allowed.add(join("scripts", unit.gateScript));
		}
	}
	if (allowed.has(invokedScript)) {
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

// Run the gate's Python script (if declared) and fold its verdict in.
function runGateScript(ctx: SureHookContext, unit: Unit): GateResult | undefined {
	if (!unit.gateScript) {
		return undefined;
	}
	const produces = artifactPath(ctx, unit.produces);
	const extra = unit.gateScriptArgs ? unit.gateScriptArgs(ctx) : [];
	// For run_validate.py the --kind selects which validation gate; pass the
	// unit id as kind so the script routes correctly. Only run_validate.py
	// accepts --kind; the other gate scripts (check_spec.py, check_env.py,
	// check_weights.py, check_env_compat.py, check_verdict.py) would reject it.
	const kindArgs: string[] =
		unit.gateScript === "run_validate.py" && unit.id.startsWith("validate_")
			? ["--kind", unit.id.replace("validate_", "")]
			: [];
	const r = runBackend(ctx, unit.gateScript, ["--produces", produces, ...kindArgs, ...extra]);
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
						message: "A tool call returned an error during the SURE model-tool run.",
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

	const artifact = readArtifact(ctx, currentUnit.produces);
	const producesResult = validateProduces(ctx, currentUnit, artifact);
	if (!producesResult.ok) {
		if (producesResult.missing) {
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

	if (currentUnit.kind === "gate") {
		// In-process gateCheck is the optional fast structural pre-filter (kept only
		// when it checks something the python script does not). If present, run first.
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
		// The python gateScript is the authoritative semantic checker; it runs
		// independently of the in-process check (a gate may have a script, an
		// in-process check, or both — disjoint concerns).
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

function failOrRetry(
	ctx: SureHookContext,
	unit: Unit,
	checkpoint: { data: CheckpointData },
	repair: string,
	reason: string,
): SureHookResult {
	const next = bumpRetry(unit, checkpoint.data);
	const attempts = next.data.retries[unit.id] ?? 1;
	// Honor the user's max_retries parameter (default 3). Read from ctx.args so a
	// run started with /sure_onboard ... max_retries=5 actually gets 5 attempts.
	const args = parseArgs(ctx.args);
	const maxRetries = args.max_retries ? Number.parseInt(args.max_retries, 10) : undefined;
	const effectiveMax = Number.isFinite(maxRetries) && (maxRetries ?? 0) > 0 ? maxRetries : undefined;
	if (retryExhausted(unit, checkpoint.data, effectiveMax)) {
		return failure(
			`${repair} (unit "${unit.id}" FAILED after ${attempts} retries; classify via failure_taxonomy and either repair manually or finish with status failed.)`,
			`Gate "${unit.id}" exhausted retries: ${reason}`,
			countersFor(next.data, attempts),
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
	const verdictArtifact = readArtifact(ctx, LAST_UNIT.produces);
	if (!verdictArtifact) {
		return failure(
			`Produce ${LAST_UNIT.produces} under the run artifacts directory before calling sure_finish.`,
			"Missing final verdict artifact.",
			countersFor(checkpoint.data, 0),
		);
	}
	const producesResult = validateProduces(ctx, LAST_UNIT, verdictArtifact);
	if (!producesResult.ok) {
		return failure(
			producesResult.repair ?? "Final verdict invalid.",
			`Terminal unit "${LAST_UNIT.id}" produces invalid: ${producesResult.reason ?? ""}`,
			countersFor(checkpoint.data, 1),
		);
	}
	// Final backstop: re-run the terminal unit's python gate script (verdict)
	// so a verdict mutated between postToolResult and sure_finish is still caught.
	// The in-process gateCheck was removed (redundant with the python script); the
	// python script is the authoritative semantic checker, so it runs here too.
	const gateResult = LAST_UNIT.gateScript ? runGateScript(ctx, LAST_UNIT) : { ok: true };
	if (gateResult && !gateResult.ok) {
		return failure(
			gateResult.repair ?? "Final gate failed.",
			`SURE onboard terminal gate "${LAST_UNIT.id}" rejected the finish.`,
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
			`The model-tool state machine has not reached the terminal unit "${LAST_UNIT.id}". Continue from "${checkpoint.data.currentUnit}".`,
			"Run finished before the state machine completed.",
			countersFor(checkpoint.data, 0),
		);
	}
	return {
		ok: true,
		state_patch: {
			phase: { id: LAST_UNIT.id, label: LAST_UNIT.label, status: "success", progress: 1 },
			message: "SURE model-tool run validated.",
			counters: countersFor(
				{
					currentUnit: LAST_UNIT.id,
					completedUnits: [...checkpoint.data.completedUnits, LAST_UNIT.id],
					retries: checkpoint.data.retries,
				},
				0,
			),
			artifacts: [
				{
					type: "verdict",
					name: "SURE verdict",
					path: `.sure/runs/${ctx.run.runId}/artifacts/${LAST_UNIT.produces}`,
					status: "ready",
					summary: "Validated SURE model-tool verdict.",
				},
			],
		},
	};
}

export function postFinish(ctx: SureHookContext): SureHookResult {
	return {
		ok: true,
		state_patch: {
			phase: { id: "finish", label: "SURE model-tool finished", status: ctx.run.status, progress: 1 },
			message: ctx.run.summary ?? "SURE model-tool run finished.",
		},
	};
}

export function onError(ctx: SureHookContext): SureHookResult {
	return {
		ok: true,
		state_patch: {
			phase: { id: "error", label: "SURE model-tool interrupted", status: "failed" },
			message: ctx.run.errorSummary ?? ctx.run.lastRepair ?? "SURE model-tool stopped before completion.",
		},
	};
}
