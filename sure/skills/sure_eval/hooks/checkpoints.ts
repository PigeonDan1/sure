import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import type { SureHookContext, SureHookResult } from "@earendil-works/pi-coding-agent/hooks";
import { harnessRuntimeEnv, resolveHarnessPython } from "../../../runtime/harness/resolve.ts";
import { type MemoryCheckpoint, type MemoryDiagnostic, readMemory } from "../../../runtime/memory/hooks.ts";
import { FIRST_UNIT, LAST_UNIT, nextUnit } from "./state-machine.ts";

// Checkpoint persisted in state.json -> checkpoint.data. Drives the mixed
// state machine: linear units LLM self-drives (advance when produces is
// compliant); gate units are hook-enforced via post_tool_result returning
// ok:false + repair (and a Python gate script for semantic checks).
//
// Three gates guarantee correct progression:
//   1. checkpoint lock — currentUnit pins position, advance() is one-step.
//   2. validateProduces — every unit's produces validated (linear + gate).
//   3. gateCheck — gate units run a Python semantic script via spawnSync.

export interface GateResult {
	ok: boolean;
	repair?: string;
	reason?: string;
	/** When true, the artifact simply is not produced yet — stay on unit. */
	missing?: boolean;
	/** The memory gate could not run at all (broken bundle, no interpreter, crash): ok stays true. */
	ranFailed?: boolean;
	/** Advisory memory diagnostics the caller folds into its state_patch. */
	diagnostics?: MemoryDiagnostic[];
}

export interface CheckpointData {
	currentUnit: string;
	completedUnits: string[];
	retries: Record<string, number>;
	/** Every gate block this run has taken. retries is per-unit and advance()
	 *  clears it, so it cannot answer "how blocked was this run". */
	blocks?: number;
	failedArtifactDigests?: Record<string, string>;
	/** Memory-system state (digest, injections, extraction status); see runtime/memory/hooks.ts. */
	memory?: MemoryCheckpoint;
}

export interface RunCheckpoint {
	id: string;
	label: string;
	resumable: boolean;
	resume_hint: string;
	data: CheckpointData;
}

export interface Unit {
	id: string;
	label: string;
	kind: "linear" | "gate";
	produces: string;
	schemaRef?: string;
	/** Required top-level fields (anti step-skip). Falls back to schema `required`. */
	requiredFields?: string[];
	/** Allowed values per field (anti value-domain drift). */
	allowedValues?: Record<string, unknown[]>;
	/** Fields this unit must NOT produce (anti step-merge). */
	forbiddenFields?: string[];
	/** In-process semantic check (over and above validateProduces). */
	gateCheck?: (artifact: unknown) => GateResult;
	/** Python script under scripts/ for semantic gate checks (spawnSync). */
	gateScript?: string;
	/** Extra argv passed to gateScript after --run-dir/--produces. */
	gateScriptArgs?: (ctx: SureHookContext) => string[];
	/** Files or dirs under artifacts/ hashed together with produces (gate re-runs when any of them change). */
	gateInputs?: string[];
}

const DEFAULT_MAX_RETRIES = 2;

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readJson(path: string): unknown {
	return JSON.parse(readFileSync(path, "utf-8"));
}

function stateJsonPath(ctx: SureHookContext): string {
	return join(ctx.runDir, "state.json");
}

// Read the persisted checkpoint from state.json. Falls back to the first unit
// when no checkpoint exists yet (fresh run).
export function readCheckpoint(ctx: SureHookContext): RunCheckpoint {
	const startUnit: CheckpointData = {
		currentUnit: FIRST_UNIT.id,
		completedUnits: [],
		retries: {},
		failedArtifactDigests: {},
	};
	try {
		const state = readJson(stateJsonPath(ctx));
		const root = isRecord(state) ? state : {};
		const checkpoint = isRecord(root.checkpoint) ? root.checkpoint : {};
		const data = isRecord(checkpoint.data) ? checkpoint.data : {};
		const currentUnit = typeof data.currentUnit === "string" ? data.currentUnit : FIRST_UNIT.id;
		const blocks = typeof data.blocks === "number" ? data.blocks : undefined;
		const completedUnits = Array.isArray(data.completedUnits)
			? data.completedUnits.filter((entry): entry is string => typeof entry === "string")
			: [];
		const retriesRaw = isRecord(data.retries) ? data.retries : {};
		const retries: Record<string, number> = {};
		for (const [key, value] of Object.entries(retriesRaw)) {
			if (typeof value === "number") {
				retries[key] = value;
			}
		}
		const failedArtifactDigestsRaw = isRecord(data.failedArtifactDigests) ? data.failedArtifactDigests : {};
		const failedArtifactDigests: Record<string, string> = {};
		for (const [key, value] of Object.entries(failedArtifactDigestsRaw)) {
			if (typeof value === "string") {
				failedArtifactDigests[key] = value;
			}
		}
		// Older checkpoints have no memory key; keep them byte-for-byte (no empty object added).
		const memory = isRecord(data.memory) ? readMemory(data) : undefined;
		return {
			id: "main_flow",
			label: "SURE-EVAL main-flow state machine",
			resumable: true,
			resume_hint: `Resume at unit "${currentUnit}".`,
			data: { currentUnit, completedUnits, retries, blocks, failedArtifactDigests, memory },
		};
	} catch {
		return {
			id: "main_flow",
			label: "SURE-EVAL main-flow state machine",
			resumable: true,
			resume_hint: `Start at unit "${FIRST_UNIT.id}".`,
			data: startUnit,
		};
	}
}

// Build a state_patch.checkpoint that advances to the next unit and marks the
// given unit complete (clearing its retry counter). Returns undefined when
// already at the last unit.
export function advance(unit: Unit, completed: CheckpointData): RunCheckpoint | undefined {
	const next = nextUnit(unit.id);
	const completedUnits = completed.completedUnits.includes(unit.id)
		? completed.completedUnits
		: [...completed.completedUnits, unit.id];
	const retries = { ...completed.retries };
	const failedArtifactDigests = { ...completed.failedArtifactDigests };
	delete retries[unit.id];
	delete failedArtifactDigests[unit.id];
	if (!next) {
		return {
			id: "main_flow",
			label: "SURE-EVAL main-flow state machine",
			resumable: false,
			resume_hint: "State machine reached the terminal unit.",
			data: {
				currentUnit: LAST_UNIT.id,
				completedUnits,
				retries,
				blocks: completed.blocks,
				failedArtifactDigests,
				memory: completed.memory,
			},
		};
	}
	return {
		id: "main_flow",
		label: "SURE-EVAL main-flow state machine",
		resumable: true,
		resume_hint: `Advanced to unit "${next.id}".`,
		data: {
			currentUnit: next.id,
			completedUnits,
			retries,
			blocks: completed.blocks,
			failedArtifactDigests,
			memory: completed.memory,
		},
	};
}

// Bump the retry counter for a unit; return the new checkpoint (no advance).
export function bumpRetry(unit: Unit, current: CheckpointData, artifactDigest?: string): RunCheckpoint {
	const retries = { ...current.retries };
	const failedArtifactDigests = { ...current.failedArtifactDigests };
	retries[unit.id] = (retries[unit.id] ?? 0) + 1;
	if (artifactDigest) {
		failedArtifactDigests[unit.id] = artifactDigest;
	}
	return {
		id: "main_flow",
		label: "SURE-EVAL main-flow state machine",
		resumable: true,
		resume_hint: `Retry unit "${unit.id}" (attempt ${retries[unit.id]}).`,
		data: {
			currentUnit: unit.id,
			completedUnits: current.completedUnits,
			retries,
			blocks: (current.blocks ?? 0) + 1,
			failedArtifactDigests,
			memory: current.memory,
		},
	};
}

export function retryExhausted(unit: Unit, current: CheckpointData, max = DEFAULT_MAX_RETRIES): boolean {
	return (current.retries[unit.id] ?? 0) >= max;
}

export function artifactPath(ctx: SureHookContext, produces: string): string {
	return join(ctx.runDir, "artifacts", produces);
}

export function readArtifact(ctx: SureHookContext, produces: string): unknown | undefined {
	const path = artifactPath(ctx, produces);
	if (!existsSync(path)) {
		return undefined;
	}
	try {
		return readJson(path);
	} catch {
		return undefined;
	}
}

/**
 * The JSON syntax error of a produces file that exists but does not parse, or undefined when it
 * is absent or parses.
 *
 * readArtifact() cannot tell the two apart — it returns undefined for both — and validateProduces
 * then reports missing:true, so postToolResult answers ok:true with no repair, no diagnostic and
 * no retry consumed: the gate never runs, its cap is never reached, and on a success finish the
 * agent is only told the state machine has not reached the terminal unit, never that its file is
 * broken. extract_lessons is the one gated unit whose produces the agent writes by hand.
 */
export function artifactParseError(ctx: SureHookContext, produces: string): string | undefined {
	const path = artifactPath(ctx, produces);
	if (!existsSync(path)) {
		return undefined;
	}
	try {
		readJson(path);
		return undefined;
	} catch (error) {
		if (!(error instanceof SyntaxError)) {
			// An fs error message carries the absolute host path of the file; report only the fact.
			return `${produces} exists but could not be read`;
		}
		// A JSON syntax error names the position and quotes the agent's own text, never a path.
		const message = error.message;
		return message.length > 200 ? `${message.slice(0, 200)}...` : message;
	}
}

// Spawn a Python backend gate script. The script receives --run-dir and
// --produces (absolute path) plus any extra args the unit declares. exit 0 =
// pass; non-zero = fail (stdout carries the repair text).
export function runBackend(
	ctx: SureHookContext,
	script: string,
	args: string[],
): { ok: boolean; stdout: string; stderr: string; status: number | null } {
	const py = join(ctx.packageDir, "scripts", script);
	if (!existsSync(py)) {
		return {
			ok: false,
			status: null,
			stdout: "",
			stderr: `Backend script not found: scripts/${script}. Bundle the Python backend into the skill package.`,
		};
	}
	const produces = args.find((a) => a === "--produces");
	const runDir = args.find((a) => a === "--run-dir");
	// Always pass --run-dir (gate scripts declare it required). --produces is the
	// absolute path to the artifact; the run dir lets scripts locate sibling
	// artifacts (e.g. run_evaluation.sh next to execution_surface.json).
	const finalArgs = runDir ? [...args] : ["--run-dir", ctx.runDir, ...args];
	// Defensive: if a caller passed --produces without an absolute path, resolve
	// it under the run artifacts dir so the script can read it reliably.
	if (produces) {
		const idx = finalArgs.indexOf("--produces");
		const val = finalArgs[idx + 1];
		if (typeof val === "string" && !isAbsolute(val)) {
			finalArgs[idx + 1] = join(ctx.runDir, "artifacts", val);
		}
	}
	const runtime = resolveHarnessPython(ctx.packageDir);
	if (!runtime.ok || !runtime.contract) {
		return {
			ok: false,
			status: null,
			stdout: "",
			stderr: runtime.error ?? "HARNESS_RUNTIME_NOT_READY",
		};
	}
	const r = spawnSync(runtime.contract.python_executable, [py, ...finalArgs], {
		cwd: ctx.packageDir,
		encoding: "utf-8",
		timeout: 300_000,
		env: { ...process.env, ...harnessRuntimeEnv(runtime.contract) },
	});
	return {
		ok: r.status === 0,
		stdout: r.stdout ?? "",
		stderr: r.stderr ?? "",
		status: r.status,
	};
}

export function failure(
	repair: string,
	message: string,
	counters?: Record<string, number>,
	checkpoint?: RunCheckpoint,
): SureHookResult {
	return {
		ok: false,
		repair,
		state_patch: {
			phase: { id: "gate", label: "SURE eval gate blocked", status: "blocked" },
			message,
			counters,
			checkpoint,
			diagnostics: [{ severity: "error", message, repair }],
		},
	};
}
