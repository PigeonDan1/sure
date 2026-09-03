import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { SureHookContext, SureHookResult } from "@earendil-works/pi-coding-agent/hooks";
import { type HarnessRuntimeContract, resolveHarnessPython } from "../../../runtime/harness/resolve.ts";
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
	type Unit,
} from "./checkpoints.ts";
import { FIRST_UNIT, findUnit, LAST_UNIT, MODEL_FEED_UNITS, TOTAL_UNITS } from "./state-machine.ts";
import { validateProduces } from "./validate.ts";

// SURE model-feed skill hooks. Mixed drive with three gates:
//   1. checkpoint lock (currentUnit pins position, advance one step)
//   2. validateProduces on EVERY unit (location/format/value-domain + forbidden fields)
//   3. gateCheck (gate units run Python semantic scripts)
// Emits artifacts/model_input.yaml and artifacts/feed_report.json for users;
// state-machine JSON artifacts live under artifacts/debug/.

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

// sure_feed has no required parameters (source/watch_mode/query/max_models/
// handoff/output_dir/since are all optional, with sensible defaults), so preStart
// validates nothing — unlike sure_infer/sure_onboard which reject a missing param.
// It still parses ctx.args for the memory target id, and failOrRetry parses them
// again for the optional max_retries override.

// --- memory system glue -------------------------------------------------------
// The shared orchestration lives in sure/runtime/memory/hooks.ts; this file only
// decides WHEN to call it and where the results go (repair text, checkpoint,
// diagnostics). Every memory failure is advisory: it lands in diagnostics and
// never flips ok to false.

function memoryEnv(ctx: SureHookContext, py?: HarnessRuntimeContract): MemoryHookEnv {
	return { ctx, skill: "sure_feed", py };
}

function memoryOf(data: CheckpointData): MemoryCheckpoint {
	return data.memory ?? {};
}

function withMemory(checkpoint: RunCheckpoint, memory: MemoryCheckpoint): RunCheckpoint {
	return { ...checkpoint, data: { ...checkpoint.data, memory } };
}

// A finish accepted while the state machine still sits on an unfinished unit is
// that unit's terminal failure: entries pending on it become disputed rows
// (spec 8.1). Same helper as sure_infer; a no-op when the unit already completed.
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

// injectOnBlock's productDir stays undefined here on purpose: log_paths.json has no
// sure_feed table (no feed unit writes a .log), so no product dir could ever resolve a
// log path and a resolver for it would be dead code.

export function preStart(ctx: SureHookContext): SureHookResult {
	const runtime = resolveHarnessPython(ctx.packageDir);
	if (!runtime.ok || !runtime.contract) {
		return failure(
			runtime.error ?? "Bootstrap the locked common Harness Runtime and retry /sure_feed.",
			"HARNESS_RUNTIME_NOT_READY",
		);
	}
	let runtimeBindingPath: string;
	try {
		runtimeBindingPath = writeSkillRuntimeBinding({
			runDir: ctx.runDir,
			skill: "sure_feed",
			harnessRuntime: runtime.contract,
			harnessRole: "Agent research, provider access, structured artifact generation, and state-machine gates.",
			modelRuntimeReason: "sure_feed performs no model inference; it only prepares onboarding input.",
			evaluationRuntime: { reason: "sure_feed performs no evaluation." },
		});
	} catch (error) {
		return failure(
			`Failed to write the runtime responsibility declaration: ${error instanceof Error ? error.message : String(error)}`,
			"RUNTIME_BINDING_WRITE_FAILED",
		);
	}
	const scriptsDir = join(ctx.packageDir, "scripts");
	const backendPresent = existsSync(scriptsDir) && existsSync(join(scriptsDir, "sure_feed", "bridge.py"));
	const checkpoint = readCheckpoint(ctx);
	const diagnostics: MemoryDiagnostic[] = backendPresent
		? []
		: [
				{
					severity: "warning",
					message: "scripts/sure_feed/ backend is not bundled.",
					repair: "Bundle the ModelScope feeding backend into scripts/sure_feed/ before a real feed run.",
				},
			];
	// Memory: index freshness check (index.py --check) + fact matching into
	// artifacts/memory_context.json. Advisory; the match text must not carry
	// output_dir, so the raw args are stripped first. sure_feed has no model= to
	// name the target, so the url the caller passed (or its query) stands in. The
	// preferred invocation passes that url as a bare positional token (SKILL.md), which
	// parseArgs reads as a flag rather than as url=, so the token is looked for directly.
	const args = parseArgs(ctx.args);
	const positionalUrl = ctx.args
		.trim()
		.split(/\s+/)
		.find((token) => /^https?:\/\//i.test(token));
	const memoryStart = preStartMemory(memoryEnv(ctx, runtime.contract), {
		targetId: args.url ?? positionalUrl ?? args.query ?? "",
		strippedArgs: stripOutputDir(ctx.args),
	});
	diagnostics.push(...memoryStart.diagnostics);
	return {
		ok: true,
		state_patch: {
			phase: phaseFor(findUnit(checkpoint.data.currentUnit) ?? FIRST_UNIT, "running"),
			message: `SURE model-feed skill loaded with Harness Runtime ${runtime.contract.runtime_id}.`,
			counters: countersFor(checkpoint.data, 0),
			diagnostics,
			artifacts: [
				{
					type: "runtime_binding",
					name: "Skill runtime binding",
					path: runtimeBindingPath,
					status: "ready",
					summary: "Harness Runtime required; Model and Evaluation runtimes are explicitly out of scope.",
				},
			],
			checkpoint,
		},
	};
}

// preToolCall: enforce the per-unit script whitelist.
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
	const invokedScripts = invokedSkillScripts(command);
	if (invokedScripts.length === 0) {
		return { ok: true };
	}
	const checkpoint = readCheckpoint(ctx);
	const currentUnit = findUnit(checkpoint.data.currentUnit);
	if (!currentUnit) {
		return { ok: true };
	}
	const completedSet = new Set(checkpoint.data.completedUnits);
	const owningUnits = MODEL_FEED_UNITS.filter(
		(unit) => (completedSet.has(unit.id) && unit.id !== currentUnit.id) || unit.id === currentUnit.id,
	);
	const allowed = new Set<string>();
	for (const unit of owningUnits) {
		if (unit.gateScript) {
			allowed.add(`scripts/${unit.gateScript}`);
		}
		for (const script of unit.ownedScripts ?? []) {
			allowed.add(`scripts/${script}`);
		}
	}
	const invokedScript = invokedScripts.find((script) => !allowed.has(script));
	if (!invokedScript) {
		return { ok: true };
	}
	return {
		ok: false,
		repair: `Script ${invokedScript} is not permitted from unit "${currentUnit.id}". Only the current unit's owned scripts may run here.`,
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

// Run the gate's Python script (if declared). The extraction gate is stdlib-only and goes
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
						message: "A tool call returned an error during the SURE model-feed run.",
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
				artifact,
				`${currentUnit.produces} is present but is not valid JSON: ${parseError}. Rewrite it as a single JSON object; unit "${currentUnit.id}" cannot advance until it parses.`,
				"produces is not valid JSON",
			);
		}
		return failOrRetry(
			ctx,
			currentUnit,
			checkpoint,
			artifact,
			producesResult.repair ?? `Unit "${currentUnit.id}" produces invalid.`,
			producesResult.reason ?? "produces invalid",
		);
	}

	// Set when the unit ran a gate script: only the memory gate ever comes back ok with
	// ranFailed, and that has to reach the advance patch below.
	let gateRun: GateResult | undefined;
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
					artifact,
					inProcess.repair ?? `Gate "${currentUnit.id}" failed.`,
					inProcess.reason ?? "gate check failed",
				);
			}
		}
		// The python gateScript is the authoritative semantic checker; it runs
		// independently of the in-process check (a gate may have a script, an
		// in-process check, or both — disjoint concerns).
		gateRun = runGateScript(ctx, currentUnit);
		if (gateRun && !gateRun.ok) {
			return failOrRetry(
				ctx,
				currentUnit,
				checkpoint,
				artifact,
				gateRun.repair ?? `Gate script "${currentUnit.id}" failed.`,
				gateRun.reason ?? "gate script failed",
			);
		}
	}

	// All gates passed: settle memory for the unit that just passed, then advance
	// (clearing its retry counter). Entering extract_lessons builds the run digest
	// (cutoff = events so far, mark-passed = the unit that just passed).
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

function failOrRetry(
	ctx: SureHookContext,
	unit: Unit,
	checkpoint: { data: CheckpointData },
	artifact: unknown,
	repair: string,
	reason: string,
): SureHookResult {
	// D1: a unit that declares gateInputs is gated on more than its own produces, so the
	// "unchanged, do not rerun" guard has to hash those files too. Hashing the parsed
	// declaration alone let an agent repair a candidate under artifacts/candidates/ without
	// the digest moving, and extract_lessons then blocked for ever on a retry it never
	// consumed. The same happens on any unit whose produces exists but does not parse:
	// readArtifact hands back undefined for that, so every corrupt rewrite hashed the same
	// sha256("null") and the guard held from the second attempt on. Both cases take the digest
	// off the file bytes instead. undefined means no digest could be taken at all; two missing
	// digests are not evidence of unchanged content, so that case falls through and consumes
	// the retry.
	const artifactDigest =
		unit.gateInputs || artifact === undefined
			? safeGateDigest(ctx, unit)
			: createHash("sha256").update(JSON.stringify(artifact)).digest("hex");
	if (artifactDigest !== undefined && checkpoint.data.failedArtifactDigests[unit.id] === artifactDigest) {
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
	// This attempt really consumes a retry: match memory against the raw repair BEFORE
	// bumping, so the usage row and the injected list travel with the new checkpoint.
	// `repair` stays raw inside diagnostics; only the top-level repair (what the agent
	// reads) carries the Memory block.
	const env = memoryEnv(ctx);
	const injected = injectOnBlock(env, {
		unitId: unit.id,
		attempt: (checkpoint.data.retries[unit.id] ?? 0) + 1,
		producesPath: artifactPath(ctx, unit.produces),
		memory: memoryOf(checkpoint.data),
		rawRepair: repair,
	});
	const next = bumpRetry(unit, { ...checkpoint.data, memory: injected.memory }, artifactDigest);
	const attempts = next.data.retries[unit.id] ?? 1;
	// Honor the user's max_retries parameter (default 3). Read from ctx.args so a
	// run started with /sure_feed ... max_retries=5 actually gets 5 attempts.
	const args = parseArgs(ctx.args);
	const maxRetries = args.max_retries ? Number.parseInt(args.max_retries, 10) : undefined;
	const effectiveMax = Number.isFinite(maxRetries) && (maxRetries ?? 0) > 0 ? maxRetries : undefined;
	if (unit.id === "extract_lessons") {
		// The extraction cap is the memory system's own (config.json
		// extraction_gate_max_failures), never the user's max_retries=: the extraction is a
		// by-product and a caller asking the feed gates for ten attempts must not get ten
		// rounds of it. memoryConfigOrUndefined never throws, and with no config there is no
		// cap to read, so the state machine's own default stands in.
		const memoryConfig = memoryConfigOrUndefined();
		const extractionExhausted = memoryConfig
			? isExtractionGateExhausted(unit.id, attempts, undefined, memoryConfig)
			: retryExhausted(unit, next.data);
		if (extractionExhausted) {
			// Let the run go on: the extraction must never block the skill's finish. Whatever
			// was injected at this unit is settled as a terminal failure, then the state
			// machine advances with extractionStatus=failed (post_finish skips publish on it).
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
	if (retryExhausted(unit, next.data, effectiveMax)) {
		// Same shape as failure(), hand-built so diagnostics keep the raw repair while the
		// top-level repair carries the Memory block. The message must keep the prefix
		// `Gate "<id>" exhausted`: digest.py reads it as the unit's terminal failure, which
		// also settles the unit's pending entries as disputed.
		const settled = settleOnTerminalFailure(env, { unitId: unit.id, memory: injected.memory });
		const message = `Gate "${unit.id}" exhausted ${attempts} blocked attempts: ${reason}`;
		// injectOnBlock returns `rawRepair + "\n\n" + block`, so this slice is "" when nothing
		// was injected. The exhaustion sentence hangs off the RAW repair and the block is
		// re-appended after it: the Memory block has to stay last, otherwise the digest's
		// strip_memory_block eats the sentence together with the block and the next run's
		// prior_runs loses it.
		const memoryBlock = injected.repair.slice(repair.length);
		return {
			ok: false,
			repair: `${repair} Blocked because: ${reason}. After ${attempts} consecutive blocked attempts, /sure_feed still cannot produce a valid artifact for unit "${unit.id}". If the blocking reason above points at the model link rather than the artifact you wrote, ask the user to confirm access permissions and whether the model card/README covers install, load, and inference.${memoryBlock}`,
			state_patch: {
				phase: { id: "gate", label: "SURE feed gate blocked", status: "blocked" },
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
		"sure_feed",
		false,
	);
	if (runtimeBindingError) {
		return failure(runtimeBindingError, "Runtime responsibility declaration is invalid.");
	}
	const checkpoint = readCheckpoint(ctx);
	const manifestArtifact = readArtifact(ctx, LAST_UNIT.produces);
	if (!manifestArtifact) {
		return failure(
			`Produce ${LAST_UNIT.produces} under the run artifacts directory before calling sure_finish.`,
			"Missing handoff manifest artifact.",
			countersFor(checkpoint.data, 0),
		);
	}
	const producesResult = validateProduces(ctx, LAST_UNIT, manifestArtifact);
	if (!producesResult.ok) {
		return failure(
			producesResult.repair ?? "Handoff manifest invalid.",
			`Terminal unit "${LAST_UNIT.id}" produces invalid: ${producesResult.reason ?? ""}`,
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
			`The model-feed state machine has not reached the terminal unit "${LAST_UNIT.id}". Continue from "${checkpoint.data.currentUnit}".`,
			"Run finished before the state machine completed.",
			countersFor(checkpoint.data, 0),
		);
	}
	// Memory at finish (non-success only; a success finish changes nothing here).
	// 1. A failed / incomplete run that never reached extract_lessons must still leave an
	//    extraction declaration behind (spec 4.5): two repairs, the third finish is let
	//    through with extractionStatus=failed.
	// 2. Only once the finish is accepted: the unit the run died on is a terminal failure
	//    for the entries still pending on it (spec 8.1). A rejected finish settles nothing,
	//    the unit may still pass later.
	const env = memoryEnv(ctx);
	let memory = memoryOf(checkpoint.data);
	const memoryDiagnostics: MemoryDiagnostic[] = [];
	if (finishStatus !== "success") {
		if (!checkpoint.data.completedUnits.includes("extract_lessons")) {
			const extraction = preFinishExtraction(env, { finishStatus: String(finishStatus), memory });
			if (!extraction.ok) {
				// Hand-built copy of failure()'s patch, which hard-codes a single-element
				// diagnostics array: the repair tells the agent to read artifacts/run_digest.json
				// first, and when the digest build failed the only thing that says that file holds
				// nothing but an error is extraction.diagnostics.
				const repair =
					extraction.repair ??
					"Produce artifacts/extraction_declaration.json per sure/runtime/memory/EXTRACTION.md, then call sure_finish again.";
				const message = "Non-success Feed finish requires an extraction declaration.";
				return {
					ok: false,
					repair,
					state_patch: {
						phase: { id: "gate", label: "SURE feed gate blocked", status: "blocked" },
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
			message: "SURE model-feed run validated.",
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
					type: "runtime_binding",
					name: "Skill runtime binding",
					path: `.sure/runs/${ctx.run.runId}/artifacts/runtime_binding.json`,
					status: "ready",
					summary: "Verified Harness Runtime binding and explicit non-required runtime slots.",
				},
				{
					type: "model_input",
					name: "SURE MODEL_INPUT",
					path: `.sure/runs/${ctx.run.runId}/artifacts/model_input.yaml`,
					status: "ready",
					summary: "Single canonical MODEL_INPUT YAML for /sure_onboard.",
				},
				{
					type: "feed_report",
					name: "SURE feed report",
					path: `.sure/runs/${ctx.run.runId}/artifacts/feed_report.json`,
					status: "ready",
					summary: "Discovery summary, selected model, evidence, diagnostics, and next action.",
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
	const published = postFinishMemory(memoryEnv(ctx), memoryOf(readCheckpoint(ctx).data));
	return {
		ok: true,
		state_patch: {
			phase: { id: "finish", label: "SURE model-feed finished", status: ctx.run.status, progress: 1 },
			message: ctx.run.summary ?? "SURE model-feed run finished.",
			...(published.diagnostics.length > 0 ? { diagnostics: published.diagnostics } : {}),
		},
	};
}

export function onError(ctx: SureHookContext): SureHookResult {
	// Leave a digest behind so the next run on the same target sees where this one stopped
	// (prior_runs); nothing is published from here. No pre_finish ever runs on this path, so
	// this is the last chance to close the unit the run died on: the patch carries no
	// checkpoint, and the settle rows already on disk are what keeps that idempotent.
	const checkpoint = readCheckpoint(ctx);
	const env = memoryEnv(ctx);
	const memory = memoryOf(checkpoint.data);
	const digest = onErrorDigest(env, memory);
	const stuck = settleStuckUnit(env, checkpoint.data, memory);
	const diagnostics = [...digest.diagnostics, ...stuck.diagnostics];
	return {
		ok: true,
		state_patch: {
			phase: { id: "error", label: "SURE model-feed interrupted", status: "failed" },
			message: ctx.run.errorSummary ?? ctx.run.lastRepair ?? "SURE model-feed stopped before completion.",
			...(diagnostics.length > 0 ? { diagnostics } : {}),
		},
	};
}
