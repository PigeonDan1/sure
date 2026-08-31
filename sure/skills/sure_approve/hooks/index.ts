import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { SureHookContext, SureHookResult } from "@earendil-works/pi-coding-agent/hooks";
import { harnessRuntimeEnv, resolveHarnessPython } from "../../../runtime/harness/resolve.ts";
import { invokedSkillScripts } from "../../../runtime/script-guard.ts";
import { advance, bumpRetry, initialCheckpoint, readCheckpoint, retryExhausted } from "./checkpoints.ts";
import { type ApproveMode, findUnit, unitsForMode } from "./state-machine.ts";
import { readArtifact, validateProduces } from "./validate.ts";

function parseArgs(raw: string): Record<string, string> {
	const output: Record<string, string> = {};
	const tokens = raw.trim().split(/\s+/).filter(Boolean);
	for (let index = 0; index < tokens.length; index += 1) {
		const token = tokens[index];
		const equal = token.indexOf("=");
		if (equal >= 0) {
			output[token.slice(0, equal)] = token.slice(equal + 1);
			continue;
		}
		const key = token.replace(/^--?/, "");
		const next = tokens[index + 1];
		if (next && !next.startsWith("-")) {
			output[key] = next;
			index += 1;
		} else {
			output[key] = "true";
		}
	}
	return output;
}

function failure(message: string, repair = message): SureHookResult {
	return {
		ok: false,
		repair,
		state_patch: {
			phase: { id: "approval_gate", label: "Approval gate blocked", status: "blocked" },
			message,
			diagnostics: [{ severity: "error", message, repair }],
		},
	};
}

export function modeFromArgs(raw: string): ApproveMode {
	const args = parseArgs(raw);
	if (args.mode) return args.mode === "approve" ? "approve" : "audit";
	return args.review_manifest || args.decision ? "approve" : "audit";
}

function maxRetriesFromArgs(raw: string): number {
	const value = parseArgs(raw).max_retries;
	return value === undefined ? 3 : Number.parseInt(value, 10);
}

export function preStart(ctx: SureHookContext): SureHookResult {
	const args = parseArgs(ctx.args);
	const mode = modeFromArgs(ctx.args);
	if (args.approve_dir || args["approve-dir"])
		return failure(
			"approve_dir is not supported. Configure storage.approved_models_roots[0] once in the active site policy; /sure_approve and /sure_eval use that root.",
		);
	if (args.mode && args.mode !== "audit" && args.mode !== "approve") return failure("mode must be audit or approve.");
	if (mode === "audit" && !args.model_dir)
		return failure("Audit mode requires model_dir=<completed onboard/trans bundle>.");
	if (mode === "approve") {
		if (!args.review_manifest)
			return failure("Approve mode requires review_manifest=<review_packet.json> from a prior audit.");
		if (args.decision !== "approve" && args.decision !== "reject")
			return failure("Approve mode requires an explicit decision=approve or decision=reject.");
	}
	if (args.repair && args.repair !== "safe" && args.repair !== "none") return failure("repair must be safe or none.");
	if (args.max_retries && (!/^\d+$/.test(args.max_retries) || maxRetriesFromArgs(ctx.args) < 1))
		return failure("max_retries must be a positive integer.");
	const runtime = resolveHarnessPython(ctx.packageDir);
	if (!runtime.ok || !runtime.contract)
		return failure(runtime.error ?? "Bootstrap the locked Harness Runtime before /sure_approve.");
	const checkpoint = initialCheckpoint(mode);
	const totalUnits = mode === "approve" && args.decision === "reject" ? 1 : unitsForMode(mode).length;
	return {
		ok: true,
		state_patch: {
			phase: { id: checkpoint.data.currentUnit, label: unitsForMode(mode)[0].label, status: "running" },
			message:
				mode === "audit"
					? "Approval audit initialized; the source remains read-only."
					: "Approval decision verification initialized from the prior review packet.",
			counters: { completed_units: 0, total_units: totalUnits, gate_blocks: 0 },
			checkpoint,
		},
	};
}

function toolCommand(ctx: SureHookContext): string | undefined {
	const event = typeof ctx.event === "object" && ctx.event !== null ? (ctx.event as Record<string, unknown>) : {};
	const call =
		typeof event.toolCall === "object" && event.toolCall !== null ? (event.toolCall as Record<string, unknown>) : {};
	const tool = typeof event.toolName === "string" ? event.toolName : call.name;
	if (tool !== "bash") return undefined;
	const inputValue = typeof event.input === "object" && event.input !== null ? event.input : call.input;
	const input = typeof inputValue === "object" && inputValue !== null ? (inputValue as Record<string, unknown>) : {};
	return typeof input.command === "string" ? input.command : "";
}

export function preToolCall(ctx: SureHookContext): SureHookResult {
	const command = toolCommand(ctx);
	if (command === undefined) return { ok: true };
	const mode = modeFromArgs(ctx.args);
	const checkpoint = readCheckpoint(ctx, mode);
	const unit = findUnit(checkpoint.data.currentUnit);
	if (!unit) return failure(`Unknown approval unit "${checkpoint.data.currentUnit}".`);
	if (retryExhausted(checkpoint, maxRetriesFromArgs(ctx.args)))
		return failure(
			`Gate "${unit.id}" exhausted ${checkpoint.data.retries[unit.id]} blocked attempts. Start a new run after repairing the reported input.`,
		);
	const invoked = invokedSkillScripts(command);
	const disallowed = invoked.find((script) => script !== `scripts/${unit.gateScript}`);
	return disallowed
		? failure(`${disallowed} is not permitted from unit "${unit.id}". Run only scripts/${unit.gateScript}.`)
		: { ok: true };
}

function failOrRetry(
	ctx: SureHookContext,
	checkpoint: ReturnType<typeof readCheckpoint>,
	artifact: unknown,
	repair: string,
): SureHookResult {
	const unit = findUnit(checkpoint.data.currentUnit)!;
	const digest = createHash("sha256").update(JSON.stringify(artifact)).digest("hex");
	if (checkpoint.data.failedArtifactDigests[unit.id] === digest) {
		const attempts = checkpoint.data.retries[unit.id] ?? 0;
		return {
			ok: true,
			state_patch: {
				phase: { id: unit.id, label: unit.label, status: "blocked" },
				message: `Gate "${unit.id}" remains blocked on unchanged artifact content; retry ${attempts} was not consumed again.`,
				counters: {
					completed_units: checkpoint.data.completedUnits.length,
					total_units: unitsForMode(checkpoint.data.mode).length,
					gate_blocks: attempts,
				},
				checkpoint,
				diagnostics: [{ severity: "warning", message: repair, repair }],
			},
		};
	}
	const next = bumpRetry(checkpoint, digest);
	const attempts = next.data.retries[unit.id] ?? 1;
	const exhausted = retryExhausted(next, maxRetriesFromArgs(ctx.args));
	if (exhausted) {
		next.resumable = false;
		next.resume_hint = `Gate "${unit.id}" exhausted ${attempts} blocked attempts.`;
	}
	return {
		ok: false,
		repair: exhausted ? `${repair} Start a new run after repairing the reported input.` : repair,
		state_patch: {
			phase: { id: unit.id, label: unit.label, status: "blocked" },
			message: exhausted
				? `Gate "${unit.id}" exhausted ${attempts} blocked attempts.`
				: `Gate "${unit.id}" blocked (attempt ${attempts}).`,
			counters: {
				completed_units: next.data.completedUnits.length,
				total_units: unitsForMode(next.data.mode).length,
				gate_blocks: attempts,
			},
			checkpoint: next,
			diagnostics: [{ severity: "error", message: repair, repair }],
		},
	};
}

function checkBackend(ctx: SureHookContext, script: string, produces: string, extra: string[]): string | undefined {
	const runtime = resolveHarnessPython(ctx.packageDir);
	if (!runtime.ok || !runtime.contract) return runtime.error ?? "Harness Runtime is unavailable.";
	const path = join(ctx.packageDir, "scripts", script);
	if (!existsSync(path)) return `Missing approval backend scripts/${script}.`;
	const result = spawnSync(
		runtime.contract.python_executable,
		[path, "--run-dir", ctx.runDir, "--produces", produces, "--check", ...extra],
		{
			cwd: ctx.packageDir,
			encoding: "utf-8",
			timeout: 120_000,
			env: { ...process.env, ...harnessRuntimeEnv(runtime.contract) },
		},
	);
	return result.status === 0
		? undefined
		: result.stderr.trim() || result.stdout.trim() || `${script} rejected ${produces}.`;
}

export function postToolResult(ctx: SureHookContext): SureHookResult {
	const mode = modeFromArgs(ctx.args);
	const checkpoint = readCheckpoint(ctx, mode);
	const unit = findUnit(checkpoint.data.currentUnit);
	if (!unit) return failure(`Unknown approval unit "${checkpoint.data.currentUnit}".`);
	const artifact = readArtifact(ctx.runDir, unit.produces);
	if (artifact === undefined) return { ok: true };
	const invalid = validateProduces(unit, artifact);
	if (invalid) return failOrRetry(ctx, checkpoint, artifact, invalid);
	const produces = join(ctx.runDir, "artifacts", unit.produces);
	const rejected = checkBackend(ctx, unit.gateScript, produces, unit.gateScriptArgs ?? []);
	if (rejected) return failOrRetry(ctx, checkpoint, artifact, rejected);
	const explicitRejection =
		unit.id === "verify_decision" &&
		typeof artifact === "object" &&
		artifact !== null &&
		(artifact as Record<string, unknown>).status === "rejected";
	const next = explicitRejection
		? {
				...checkpoint,
				resumable: false,
				resume_hint: "Human rejection recorded; publication is forbidden.",
				data: {
					...checkpoint.data,
					completedUnits: checkpoint.data.completedUnits.includes(unit.id)
						? checkpoint.data.completedUnits
						: [...checkpoint.data.completedUnits, unit.id],
				},
			}
		: advance(checkpoint);
	const nextUnit = findUnit(next.data.currentUnit);
	return {
		ok: true,
		state_patch: {
			phase: {
				id: next.data.currentUnit,
				label: nextUnit?.label ?? unit.label,
				status: next.resumable ? "running" : "success",
				progress: next.resumable ? undefined : 1,
			},
			message: explicitRejection
				? "Human rejection recorded; no publication was attempted."
				: next.resumable
					? `Advanced to unit "${next.data.currentUnit}".`
					: mode === "audit"
						? "Audit complete; explicit human approval is required in a new run."
						: "Approval publication verified.",
			counters: {
				completed_units: next.data.completedUnits.length,
				total_units: explicitRejection ? 1 : unitsForMode(mode).length,
				gate_blocks: 0,
			},
			checkpoint: next,
		},
	};
}

export function preFinish(ctx: SureHookContext): SureHookResult {
	const mode = modeFromArgs(ctx.args);
	const checkpoint = readCheckpoint(ctx, mode);
	const terminal =
		mode === "approve" && parseArgs(ctx.args).decision === "reject"
			? findUnit("verify_decision")!
			: unitsForMode(mode).at(-1)!;
	if (!checkpoint.data.completedUnits.includes(terminal.id))
		return failure(`Complete terminal unit "${terminal.id}" before finishing /sure_approve.`);
	const artifact = readArtifact(ctx.runDir, terminal.produces);
	const invalid = validateProduces(terminal, artifact);
	if (invalid) return failure(invalid);
	const status =
		typeof ctx.event === "object" &&
		ctx.event !== null &&
		typeof (ctx.event as Record<string, unknown>).finish === "object"
			? (ctx.event as Record<string, { status?: unknown }>).finish?.status
			: undefined;
	return status === "success" ? { ok: true } : failure("A completed approval flow must finish with status=success.");
}

export function postFinish(ctx: SureHookContext): SureHookResult {
	return {
		ok: true,
		state_patch: {
			phase: { id: "finish", label: "SURE approval finished", status: ctx.run.status, progress: 1 },
			message: ctx.run.summary ?? "SURE approval flow finished.",
		},
	};
}

export function onError(ctx: SureHookContext): SureHookResult {
	return {
		ok: true,
		state_patch: {
			phase: { id: "error", label: "SURE approval interrupted", status: "failed" },
			message: ctx.run.errorSummary ?? ctx.run.lastRepair ?? "SURE approval stopped before completion.",
		},
	};
}
