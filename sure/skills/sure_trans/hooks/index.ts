import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import type { SureHookContext, SureHookResult } from "@earendil-works/pi-coding-agent/hooks";
import { agentBinDir, demoteAgentBinDir } from "../../../runtime/agent-path.ts";
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
import { requireSitePolicy } from "../../../site/loader.ts";
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
import { FIRST_UNIT, findUnit, LAST_UNIT, TOTAL_UNITS, TRANS_UNITS } from "./state-machine.ts";
import { validateProduces } from "./validate.ts";

// SURE model transformation skill hooks. Mixed drive with three gates:
//   1. checkpoint lock (currentUnit pins position, advance one step)
//   2. validateProduces on EVERY unit (location/format/value-domain + forbidden fields)
//   3. gateCheck (gate units run Python semantic scripts)
// Produces the same container-only runtime contract consumed by /sure_eval.
//   4. memory (sure/runtime/memory/hooks.ts): digest on entering extract_lessons,
//      injection on gate blocks, settlement, publish; advisory only, never blocks.

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

// --- memory system glue -------------------------------------------------------
// The shared orchestration lives in sure/runtime/memory/hooks.ts; this file only
// decides WHEN to call it and where the results go (repair text, checkpoint,
// diagnostics). Every memory failure is advisory: it lands in diagnostics and
// never flips ok to false.

function memoryEnv(ctx: SureHookContext, py?: HarnessRuntimeContract): MemoryHookEnv {
	return { ctx, skill: "sure_trans", py };
}

function memoryOf(data: CheckpointData): MemoryCheckpoint {
	return data.memory ?? {};
}

function withMemory(checkpoint: RunCheckpoint, memory: MemoryCheckpoint): RunCheckpoint {
	return { ...checkpoint, data: { ...checkpoint.data, memory } };
}

// A finish accepted while the state machine still sits on an unfinished unit is
// that unit's terminal failure: entries pending on it become disputed rows
// (spec 8.1). Same helper as sure_onboard and sure_eval; a no-op once the unit
// completed, and idempotent through the settle rows already on disk.
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

// D1: extract_lessons is the one unit whose evidence lives beside its produces, in
// artifacts/candidates/ and artifacts/memory_evidence/. Hashing only the parsed declaration
// would let an edited candidate look like "nothing changed", and the gate would never run
// again. Every other unit keeps the artifact-object digest it always had. Ceiling: the memory
// side reads artifacts/<produces> while artifactPath prefers artifacts/debug/, so a declaration
// written under debug/ takes no digest at all and degrades to no unchanged-artifact guard.
function gateArtifactDigest(ctx: SureHookContext, unit: Unit, artifact: unknown): string | undefined {
	return unit.gateInputs ? safeGateDigest(ctx, unit) : digestOf(artifact);
}

export function preStart(ctx: SureHookContext): SureHookResult {
	// Seven of the twenty-one units drive docker straight from bash, so the shadowing
	// has to be cleared on the environment itself, not just in the skill scripts.
	demoteAgentBinDir(process.env, agentBinDir());
	const args = parseArgs(ctx.args);
	const dockerfile = args.dockerfile;
	const pythonExecutable = args.python_executable;
	const lockfile = args.lockfile;
	const packageProfile = args.package ?? args.package_profile ?? "docker-registry";
	const modelPath = args.model ?? args.model_path;
	const inferenceEntrypoint = args.inference_entrypoint ?? args.inference_code;
	const missing = [
		["model", modelPath],
		["inference_entrypoint", inferenceEntrypoint],
		["framework", args.framework],
		["model_framework", args.model_framework],
		["model_name", args.model_name],
	]
		.filter((entry) => !entry[1])
		.map((entry) => entry[0]);
	if (missing.length > 0) {
		return failure(
			`Provide required parameters: ${missing.join(", ")}. Example: ` +
				"/sure_trans dockerfile=/abs/Dockerfile model=/abs/model " +
				"inference_entrypoint=/abs/infer.py framework=pytorch model_framework=transformers " +
				"model_name=organization__model",
			"TRANS_INPUT_MISSING",
		);
	}
	if (Boolean(dockerfile) === Boolean(pythonExecutable)) {
		return failure("Provide exactly one of dockerfile or python_executable", "TRANS_INPUT_INVALID");
	}
	if (pythonExecutable && !lockfile) {
		return failure("Python input requires lockfile", "TRANS_INPUT_MISSING");
	}
	if (!new Set(["docker-registry", "none"]).has(packageProfile)) {
		return failure("package must be docker-registry or none", "TRANS_INPUT_INVALID");
	}
	if (dockerfile && packageProfile === "none") {
		return failure("package=none requires Python input", "TRANS_INPUT_INVALID");
	}
	if (!new Set(["pytorch", "torch"]).has(args.framework?.toLowerCase() ?? "")) {
		return failure("framework must be pytorch (torch is accepted as an alias)", "TRANS_INPUT_INVALID");
	}
	if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(args.model_framework ?? "")) {
		return failure(
			"model_framework must be a non-empty identifier using letters, digits, '.', '_' or '-'",
			"TRANS_INPUT_INVALID",
		);
	}
	const inputPaths: Array<readonly [string, string | undefined]> = [
		[dockerfile ? "dockerfile" : "python_executable", dockerfile ?? pythonExecutable],
		["model", modelPath],
		["inference_entrypoint", inferenceEntrypoint],
	];
	if (pythonExecutable) {
		inputPaths.push(["lockfile", lockfile]);
	}
	for (const [name, value] of inputPaths) {
		if (!value || !isAbsolute(value) || !existsSync(value)) {
			return failure(`${name} must be an existing absolute path: ${value ?? ""}`, "TRANS_INPUT_PATH_INVALID");
		}
	}
	const device = args.device ?? "auto";
	const vcPartition = args.vc_partition;
	const vcMemoryGb = args.vc_memory_gb;
	const vcGpus = args.vc_gpus;
	const imageVersion = args.image_version;
	if (!/^[A-Za-z0-9][A-Za-z0-9.-]*__[A-Za-z0-9][A-Za-z0-9._-]*$/.test(args.model_name ?? "")) {
		return failure("model_name must use <organization>__<model_name>", "TRANS_INPUT_INVALID");
	}
	const safeTag = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
	if (vcPartition !== undefined && !safeTag.test(vcPartition)) {
		return failure(`vc_partition must be a registry-style safe name: ${vcPartition}`, "TRANS_INPUT_INVALID");
	}
	if (imageVersion !== undefined && !safeTag.test(imageVersion)) {
		return failure(`image_version must be a registry-style safe tag: ${imageVersion}`, "TRANS_INPUT_INVALID");
	}
	if (vcMemoryGb !== undefined && (!Number.isInteger(Number(vcMemoryGb)) || Number(vcMemoryGb) < 1)) {
		return failure(`vc_memory_gb must be a positive integer: ${vcMemoryGb}`, "TRANS_INPUT_INVALID");
	}
	if (vcGpus !== undefined && (!Number.isInteger(Number(vcGpus)) || Number(vcGpus) < 1)) {
		return failure(`vc_gpus must be a positive integer: ${vcGpus}`, "TRANS_INPUT_INVALID");
	}
	const runtime = resolveHarnessPython(ctx.packageDir);
	if (!runtime.ok || !runtime.contract) {
		return failure(
			runtime.error ?? "Bootstrap the locked common Harness Runtime and retry /sure_trans.",
			"HARNESS_RUNTIME_NOT_READY",
		);
	}
	let runtimeBindingPath: string;
	try {
		runtimeBindingPath = writeSkillRuntimeBinding({
			runDir: ctx.runDir,
			skill: "sure_trans",
			harnessRuntime: runtime.contract,
			harnessRole: "Input materialization, dependency inspection, artifact validation, and state-machine gates.",
			modelRuntimeReason:
				dockerfile
					? "Model inference runs in the source and adapter containers built from the supplied Dockerfile."
					: "Model inference starts from the supplied local Python runtime and locked dependencies.",
			evaluationRuntime: { reason: "sure_trans performs no evaluation." },
		});
	} catch (error) {
		return failure(
			`Failed to write the runtime responsibility declaration: ${error instanceof Error ? error.message : String(error)}`,
			"RUNTIME_BINDING_WRITE_FAILED",
		);
	}
	const scriptsDir = join(ctx.packageDir, "scripts");
	const backendPresent = existsSync(scriptsDir) && existsSync(join(scriptsDir, "materialize_trans_inputs.py"));
	const checkpoint = readCheckpoint(ctx);
	const diagnostics: MemoryDiagnostic[] = backendPresent
		? []
		: [
				{
					severity: "warning",
					message: "sure_trans deterministic scripts are not bundled.",
					repair: "Restore scripts/materialize_trans_inputs.py before running /sure_trans.",
				},
			];
	// Memory: index freshness check (index.py --check) + fact matching into
	// artifacts/memory_context.json. Advisory. The target id is model_name, the same
	// namespace /sure_eval uses; args.model is the host path to the weights.
	const memoryStart = preStartMemory(memoryEnv(ctx, runtime.contract), {
		targetId: args.model_name,
		strippedArgs: stripOutputDir(ctx.args),
	});
	diagnostics.push(...memoryStart.diagnostics);
	return {
		ok: true,
		state_patch: {
			phase: phaseFor(findUnit(checkpoint.data.currentUnit) ?? FIRST_UNIT, "running"),
			message:
				`SURE model transformation skill loaded with Harness Runtime ${runtime.contract.runtime_id}.` +
				(device === "cpu" || pythonExecutable
					? ""
					: ` GPU validation submits VC jobs to ${vcPartition ?? requireSitePolicy().policy.execution.vc_default_partition ?? "the configured VC partition"}.`),
			counters: countersFor(checkpoint.data, 0),
			diagnostics,
			artifacts: [
				{
					type: "runtime_binding",
					name: "Skill runtime binding",
					path: runtimeBindingPath,
					status: "ready",
					summary: `Harness Runtime controls gates; source execution is ${dockerfile ? "container" : "local Python"}.`,
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
	const owningUnits = TRANS_UNITS.filter(
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

function runGateScript(ctx: SureHookContext, unit: Unit): GateResult | undefined {
	if (!unit.gateScript) {
		return undefined;
	}
	const produces = artifactPath(ctx, unit.produces);
	// The extraction gate is stdlib-only and needs --repo-root, which runBackend does not pass;
	// runBackend would also hand it the hour-long vc budget and SURE_TRANS_GATE_BUDGET_SECONDS,
	// neither of which means anything to it. It goes through the memory python resolver instead.
	if (unit.gateScript === "check_memory_extraction.py") {
		const r = runMemoryGate(memoryEnv(ctx), produces);
		if (r.ok) {
			return { ok: true };
		}
		if (r.ranFailed) {
			// The gate never judged the declaration (missing wrapper, no interpreter, crash). Its
			// text is a traceback, not a repair, and blocking on it stalls the unit for good: the
			// unchanged-artifact guard consumes no retry when the agent has nothing it can change,
			// so the extraction cap is never reached and the unit never advances. Let it pass and
			// mark the extraction failed, so post_finish publishes nothing that was never gated.
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

// A gate script is an executor, not just a checker: it pushes images, submits vc
// jobs and writes its own result over the artifact the agent produced. When the
// two disagree the agent has to be told, or it keeps reasoning from the record it
// wrote while the state machine advanced on the one the gate wrote.
export function gateRewriteNotice(
	unit: { id: string; produces: string },
	before: unknown,
	after: unknown,
): string | undefined {
	if (JSON.stringify(before) === JSON.stringify(after)) {
		return undefined;
	}
	const statusOf = (value: unknown) => (isRecord(value) && typeof value.status === "string" ? value.status : "absent");
	return (
		`Gate "${unit.id}" rewrote ${unit.produces} while it ran: status ${statusOf(before)} became ${statusOf(after)}. ` +
		`The gate result is what the state machine advanced on.`
	);
}

export function digestOf(value: unknown): string {
	return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

// A gate script is an executor: rerunning it resubmits the vc job and spends a
// retry. Every successful tool call reaches post_tool_result, so reading a log to
// work out why the gate failed used to run the gate again. Hold it back until the
// unit produces something the gate has not already rejected.
export function gateAlreadyRejected(
	checkpointData: { failedArtifactDigests: Record<string, string> },
	unitId: string,
	digest: string | undefined,
): boolean {
	// undefined means no digest could be taken at all; two missing digests are not evidence of
	// unchanged content, so that case has to fall through and let the gate run.
	return digest !== undefined && checkpointData.failedArtifactDigests[unitId] === digest;
}

// Honor the user's max_retries parameter (default 3). Read from ctx.args so a
// run started with /sure_trans ... max_retries=5 actually gets 5 attempts.
function maxRetriesFor(ctx: SureHookContext): number | undefined {
	const args = parseArgs(ctx.args);
	const parsed = args.max_retries ? Number.parseInt(args.max_retries, 10) : undefined;
	return Number.isFinite(parsed) && (parsed ?? 0) > 0 ? parsed : undefined;
}

// A gate script is an executor: every run resubmits a vc job. Reaching the retry
// ceiling used to change only the wording, so each further tool result bought
// another job and counted one higher — one run reached attempt 18 that way. Stop
// on the ceiling instead and leave the counter where it stands.
function noRetriesLeft(unit: Unit, checkpoint: RunCheckpoint): SureHookResult {
	const attempts = checkpoint.data.retries[unit.id] ?? 0;
	return failure(
		`Unit "${unit.id}" has no retries left after ${attempts} blocked attempts, so its gate will not run again. ` +
			"Finish the run with status=failed or status=incomplete, or start a fresh run with a higher max_retries.",
		`Gate "${unit.id}" stopped after ${attempts} blocked attempts`,
		countersFor(checkpoint.data, attempts),
		checkpoint,
	);
}

// A gate check can report that the unit is still mid-production rather than
// wrong. Tell the agent what is left to do, but do not spend a retry on it: the
// artifact is on its way, it has not failed an attempt.
function stayOnUnit(unit: Unit, checkpoint: RunCheckpoint, result: GateResult): SureHookResult {
	const reason = result.reason ?? "artifact is still in progress";
	const repair = result.repair ?? `Unit "${unit.id}" has not finished ${unit.produces} yet.`;
	return {
		ok: false,
		repair,
		state_patch: {
			phase: phaseFor(unit, "running"),
			message: `Unit "${unit.id}" is not finished yet: ${reason}`,
			counters: countersFor(checkpoint.data),
			checkpoint,
			diagnostics: [{ severity: "warning", message: reason, repair }],
		},
	};
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
						message: "A tool call returned an error during the SURE model transformation run.",
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
	// extract_lessons never sits in an exhausted state: its own cap auto-advances inside
	// failOrRetry. Stopping here instead would deadlock the run on the one unit that must
	// never block the finish, because noRetriesLeft neither checks the artifact nor advances.
	if (currentUnit.id !== "extract_lessons" && retryExhausted(currentUnit, checkpoint.data, maxRetriesFor(ctx))) {
		return noRetriesLeft(currentUnit, checkpoint);
	}

	const artifact = readArtifact(ctx, currentUnit.produces);
	const producesResult = validateProduces(ctx, currentUnit, artifact);
	if (!producesResult.ok) {
		if (producesResult.missing) {
			// "missing" also covers "present but not JSON": readArtifact returns undefined for both.
			// Only extract_lessons, because for every other unit failOrRetry would reach
			// digestOf(undefined), which throws: JSON.stringify(undefined) is not a string.
			const parseError =
				currentUnit.id === "extract_lessons" ? artifactParseError(ctx, currentUnit.produces) : undefined;
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

	let rewriteNotice: string | undefined;
	// Set when the unit ran a gate script: only the memory gate ever comes back ok with
	// ranFailed, and that has to reach the advance patch below.
	let gateRun: GateResult | undefined;
	if (currentUnit.kind === "gate") {
		if (gateAlreadyRejected(checkpoint.data, currentUnit.id, gateArtifactDigest(ctx, currentUnit, artifact))) {
			return failOrRetry(
				ctx,
				currentUnit,
				checkpoint,
				artifact,
				`Gate "${currentUnit.id}" already rejected this exact ${currentUnit.produces}. Change it before the gate runs again.`,
				"gate not rerun on an unchanged artifact",
			);
		}
		// In-process gateCheck is the optional fast structural pre-filter (kept only
		// when it checks something the python script does not). If present, run first.
		if (currentUnit.gateCheck) {
			const inProcess = currentUnit.gateCheck(artifact);
			if (!inProcess.ok && inProcess.missing) {
				return stayOnUnit(currentUnit, checkpoint, inProcess);
			}
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
		const afterGate = readArtifact(ctx, currentUnit.produces);
		rewriteNotice = gateRewriteNotice(currentUnit, artifact, afterGate);
		if (gateRun && !gateRun.ok) {
			// Remember what the gate left behind, not what it was handed, so the
			// next tool call can tell whether anything has changed since.
			return failOrRetry(
				ctx,
				currentUnit,
				checkpoint,
				afterGate,
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
	if (rewriteNotice) {
		diagnostics.push({
			severity: "warning",
			message: rewriteNotice,
			repair: `Re-read ${currentUnit.produces} before acting on what you wrote.`,
		});
	}
	return {
		ok: true,
		state_patch: {
			phase: phaseFor(findUnit(landed.data.currentUnit) ?? LAST_UNIT, "running"),
			message: rewriteNotice
				? `${rewriteNotice} Advanced to unit "${landed.data.currentUnit}".`
				: `Advanced to unit "${landed.data.currentUnit}".`,
			counters: countersFor(landed.data, 0),
			checkpoint: landed,
			...(diagnostics.length > 0 ? { diagnostics } : {}),
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
	const artifactDigest = gateArtifactDigest(ctx, unit, artifact);
	if (gateAlreadyRejected(checkpoint.data, unit.id, artifactDigest)) {
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
	// This attempt really consumes a retry: match memory against the raw repair (+ log tail)
	// BEFORE bumping, so the usage row and the injected list travel with the new checkpoint.
	// `repair` stays raw inside diagnostics; only the top-level repair carries the Memory block.
	// sure_trans registers no product dir (everything it writes stays under the run's
	// artifacts/), and producesPath is passed because artifactPath looks in artifacts/debug/
	// first while the memory side only knows artifacts/.
	const env = memoryEnv(ctx);
	const injected = injectOnBlock(env, {
		unitId: unit.id,
		attempt: (checkpoint.data.retries[unit.id] ?? 0) + 1,
		rawRepair: repair,
		producesPath: artifactPath(ctx, unit.produces),
		memory: memoryOf(checkpoint.data),
	});
	const next = bumpRetry(unit, { ...checkpoint.data, memory: injected.memory }, artifactDigest);
	const attempts = next.data.retries[unit.id] ?? 1;
	const effectiveMax = maxRetriesFor(ctx);
	if (unit.id === "extract_lessons") {
		// The extraction gate never fails the run (spec 4.5): at the cap the unit is closed as
		// "extraction failed" and the state machine moves on. max_retries= may only raise the cap.
		// memoryConfigOrUndefined never throws: a config.json that no longer parses must not take
		// the skill down, and without a config there is no extraction cap to read, so the unit
		// falls back to the state machine's own.
		const memoryConfig = memoryConfigOrUndefined();
		const extractionExhausted = memoryConfig
			? isExtractionGateExhausted(unit.id, attempts, effectiveMax, memoryConfig)
			: retryExhausted(unit, next.data, effectiveMax);
		if (extractionExhausted) {
			const settled = settleOnTerminalFailure(env, { unitId: unit.id, memory: injected.memory });
			const memory: MemoryCheckpoint = { ...settled.memory, extractionStatus: "failed" };
			const landed = advance(unit, { ...next.data, memory }) ?? withMemory(next, memory);
			// This is not the unit's terminal failure (it advances), so the phase is the next
			// unit's running phase and the message must NOT start with `Gate "<id>" exhausted`,
			// the prefix digest.py reads as "unit failed for good".
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
	} else if (retryExhausted(unit, next.data, effectiveMax)) {
		// Same shape as failure(), hand-built so diagnostics keep the raw repair while the
		// top-level repair carries the Memory block. injectOnBlock returns `rawRepair + "\n\n" +
		// block`, so this slice is "" when nothing was injected; the block has to stay last or
		// the digest's strip_memory_block eats the exhaustion sentence together with it.
		const settled = settleOnTerminalFailure(env, { unitId: unit.id, memory: injected.memory });
		const memoryBlock = injected.repair.slice(repair.length);
		const message = `Gate "${unit.id}" exhausted ${attempts} blocked attempts: ${reason}`;
		return {
			ok: false,
			repair:
				`${repair} Blocked because: ${reason}. After ${attempts} consecutive blocked attempts, ` +
				`/sure_trans still cannot produce a valid artifact for unit "${unit.id}". ` +
				"Ask the user to confirm the supplied Dockerfile, model path, inference entrypoint, " +
				`dependency paths, registry access, and framework declaration.${memoryBlock}`,
			state_patch: {
				phase: { id: "gate", label: "SURE transformation gate blocked", status: "blocked" },
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

export function blockedDeploymentError(deployment: unknown, finishStatus: unknown): string | undefined {
	if (finishStatus !== "incomplete" && finishStatus !== "failed") {
		return "non-success transformation finish must declare status=incomplete or status=failed";
	}
	if (!isRecord(deployment) || deployment.status !== "blocked") {
		return "non-success transformation finish requires deployment_ready.status=blocked";
	}
	if (typeof deployment.blocked_reason !== "string" || deployment.blocked_reason.trim() === "") {
		return "blocked deployment marker must record blocked_reason";
	}
	const policy = deployment.execution_policy;
	if (!isRecord(policy) || policy.container_only !== false) {
		return "blocked deployment marker must not claim container_only Eval readiness";
	}
	return undefined;
}

export function preFinish(ctx: SureHookContext): SureHookResult {
	const runtimeBindingError = validateSkillRuntimeBinding(
		join(ctx.runDir, "artifacts", "runtime_binding.json"),
		"sure_trans",
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
			"Missing deployment readiness artifact.",
			countersFor(checkpoint.data, 0),
		);
	}
	const producesResult = validateProduces(ctx, LAST_UNIT, manifestArtifact);
	if (!producesResult.ok) {
		return failure(
			producesResult.repair ?? "Deployment readiness artifact invalid.",
			`Terminal unit "${LAST_UNIT.id}" produces invalid: ${producesResult.reason ?? ""}`,
			countersFor(checkpoint.data, 1),
		);
	}
	const finishStatus = isRecord(ctx.event) && isRecord(ctx.event.finish) ? ctx.event.finish.status : undefined;
	if (finishStatus !== "success") {
		const blockedError = blockedDeploymentError(manifestArtifact, finishStatus);
		if (blockedError) {
			return failure(
				blockedError,
				"Invalid non-success model transformation terminal evidence.",
				countersFor(checkpoint.data, 1),
			);
		}
		// Memory at a non-success finish. 1. A run that never reached extract_lessons must still
		// leave an extraction declaration behind (spec 4.5): two repairs, the third finish is let
		// through with extractionStatus=failed. 2. Only once the finish is accepted, the unit the
		// run died on is a terminal failure for the entries still pending on it (spec 8.1); a
		// rejected finish settles nothing, the unit may still pass later.
		const env = memoryEnv(ctx);
		let memory = memoryOf(checkpoint.data);
		const memoryDiagnostics: MemoryDiagnostic[] = [];
		if (!checkpoint.data.completedUnits.includes("extract_lessons")) {
			const extraction = preFinishExtraction(env, { finishStatus: String(finishStatus), memory });
			if (!extraction.ok) {
				// Hand-built copy of failure()'s patch, which hard-codes a single-element diagnostics
				// array: the repair sends the agent to artifacts/run_digest.json, and when the digest
				// build failed the only thing saying that file holds nothing but an error is
				// extraction.diagnostics. This branch also has to carry the checkpoint, or
				// finishAttempts never reaches disk and the second finish repeats the first.
				const repair =
					extraction.repair ??
					"Produce artifacts/extraction_declaration.json per sure/runtime/memory/EXTRACTION.md, then call sure_finish again.";
				const message = "Non-success model transformation finish requires an extraction declaration.";
				return {
					ok: false,
					repair,
					state_patch: {
						phase: { id: "gate", label: "SURE transformation gate blocked", status: "blocked" },
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
		return {
			ok: true,
			state_patch: {
				phase: {
					id: "finish",
					label: "SURE model transformation stopped before delivery",
					status: finishStatus as "failed" | "incomplete",
					progress: 1,
				},
				message: "Transformation evidence retained; no Eval-ready bundle was claimed.",
				counters: countersFor(checkpoint.data, 1),
				artifacts: [
					{
						type: "deployment_ready",
						name: "Blocked deployment marker",
						path: `.sure/runs/${ctx.run.runId}/artifacts/${LAST_UNIT.produces}`,
						status: "incomplete",
						summary: "Transformation stopped before a digest-pinned Eval bundle existed.",
					},
				],
				...(memoryDiagnostics.length > 0 ? { diagnostics: memoryDiagnostics } : {}),
				checkpoint: withMemory(checkpoint, memory),
			},
		};
	}
	if (checkpoint.data.currentUnit !== LAST_UNIT.id && !checkpoint.data.completedUnits.includes(LAST_UNIT.id)) {
		return failure(
			`The model transformation state machine has not reached the terminal unit "${LAST_UNIT.id}". Continue from "${checkpoint.data.currentUnit}".`,
			"Run finished before the state machine completed.",
			countersFor(checkpoint.data, 0),
		);
	}
	// Backstop: re-run the terminal gate so edits made after the state machine
	// advanced cannot carry a hand-written readiness marker past sure_finish.
	const terminalGate = runGateScript(ctx, LAST_UNIT);
	if (terminalGate && !terminalGate.ok) {
		return failure(
			terminalGate.repair ?? "Terminal gate rejected the finished bundle.",
			`Terminal gate "${LAST_UNIT.id}" rejected the finish.`,
			countersFor(checkpoint.data, 1),
		);
	}
	// Count the terminal unit through the checkpoint that is actually written,
	// so completed_units and the resumable unit list cannot disagree.
	const finished = advance(LAST_UNIT, checkpoint.data) ?? checkpoint;
	return {
		ok: true,
		state_patch: {
			phase: { id: LAST_UNIT.id, label: LAST_UNIT.label, status: "success", progress: 1 },
			message: "SURE model transformation run validated.",
			counters: countersFor(finished.data, 0),
			checkpoint: finished,
			artifacts: [
				{
					type: "runtime_binding",
					name: "Skill runtime binding",
					path: `.sure/runs/${ctx.run.runId}/artifacts/runtime_binding.json`,
					status: "ready",
					summary: "Verified Harness Runtime binding and explicit non-required runtime slots.",
				},
				{
					type: "runtime_inventory",
					name: "Eval runtime inventory",
					path: `.sure/runs/${ctx.run.runId}/artifacts/runtime_inventory.json`,
					status: "ready",
					summary: "Digest-pinned container-only execution binding.",
				},
				{
					type: "verdict",
					name: "Transformation verdict",
					path: `.sure/runs/${ctx.run.runId}/artifacts/verdict.json`,
					status: "ready",
					summary: "Original and adapter inference validation verdict.",
				},
				{
					type: "deployment_ready",
					name: "Deployment readiness",
					path: `.sure/runs/${ctx.run.runId}/artifacts/deployment_ready.json`,
					status: "ready",
					summary: "Terminal Eval-ready bundle marker.",
				},
			],
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
			phase: { id: "finish", label: "SURE model transformation finished", status: ctx.run.status, progress: 1 },
			message: ctx.run.summary ?? "SURE model transformation run finished.",
			...(published.diagnostics.length > 0 ? { diagnostics: published.diagnostics } : {}),
		},
	};
}

export function onError(ctx: SureHookContext): SureHookResult {
	// Leave a digest behind so the next run on the same target sees where this one stopped
	// (prior_runs); nothing is published from here. No pre_finish ever runs on this path, so
	// this is the last chance to close the unit the run died on. The patch carries no
	// checkpoint; idempotency rests on the settle rows on disk.
	const env = memoryEnv(ctx);
	const checkpoint = readCheckpoint(ctx);
	const memory = memoryOf(checkpoint.data);
	const digest = onErrorDigest(env, memory);
	const stuck = settleStuckUnit(env, checkpoint.data, memory);
	const diagnostics = [...digest.diagnostics, ...stuck.diagnostics];
	return {
		ok: true,
		state_patch: {
			phase: { id: "error", label: "SURE model transformation interrupted", status: "failed" },
			message: ctx.run.errorSummary ?? ctx.run.lastRepair ?? "SURE model transformation stopped before completion.",
			...(diagnostics.length > 0 ? { diagnostics } : {}),
		},
	};
}
