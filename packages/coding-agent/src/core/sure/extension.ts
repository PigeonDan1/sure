import { existsSync, readFileSync } from "node:fs";
import { basename, isAbsolute, relative, resolve } from "node:path";
import { StringEnum } from "@earendil-works/pi-ai";
import { Type } from "typebox";
import {
	defineTool,
	type ExtensionAPI,
	type ExtensionCommandContext,
	type ExtensionContext,
	type ExtensionFactory,
} from "../extensions/index.ts";
import { SureHookRunner } from "./hooks.ts";
import { runSureInit } from "./init.ts";
import type { SureInitManifest } from "./init-types.ts";
import { discoverSureSkillPackages, SURE_COMMANDS, type SureDiscoveryDiagnostic } from "./manifest.ts";
import { resolveOutputDir, stripOutputDir } from "./output-dir.ts";
import { SureRunManager } from "./run-manager.ts";
import { formatSureDisplayStatus, normalizeSureDisplayStatePatch } from "./state.ts";
import type {
	SureArtifactRequirement,
	SureDisplayState,
	SureFinishDetails,
	SureFinishParams,
	SureManifestEnvelope,
	SureRunRecord,
	SureSkillPackage,
	SureToolContent,
	SureUpdateStateDetails,
} from "./types.ts";

const FINISH_TOOL_NAME = "sure_finish";
const UPDATE_STATE_TOOL_NAME = "sure_update_state";
const STATUS_KEY = "sure";

const finishToolParams = Type.Object({
	status: StringEnum(["success", "incomplete", "failed"] as const, {
		description: "Final Sure run status.",
	}),
	manifest_path: Type.String({
		description: "Path to the final artifact manifest, relative to the project cwd or absolute under the project.",
	}),
	summary: Type.String({ description: "Concise final summary of the Sure run." }),
	artifacts: Type.Optional(Type.Unsafe<unknown>({ description: "Structured final artifact summary." })),
	error_summary: Type.Optional(Type.String({ description: "Failure or incompleteness summary, when relevant." })),
	next_actions: Type.Optional(Type.Array(Type.String(), { description: "Concrete follow-up actions." })),
});

const displayPhaseParams = Type.Object({
	id: Type.Optional(Type.String({ description: "Skill-defined phase id." })),
	label: Type.Optional(Type.String({ description: "Human-readable phase label." })),
	status: Type.Optional(
		StringEnum(["pending", "running", "success", "failed", "incomplete", "skipped", "blocked"] as const, {
			description: "Skill-defined phase status.",
		}),
	),
	progress: Type.Optional(Type.Number({ description: "Optional phase progress value." })),
});

const displayDiagnosticParams = Type.Object({
	severity: Type.Optional(StringEnum(["info", "warning", "error"] as const)),
	code: Type.Optional(Type.String({ description: "Skill-defined diagnostic code." })),
	message: Type.String({ description: "Human-readable diagnostic message." }),
	repair: Type.Optional(Type.String({ description: "Suggested repair action." })),
	data: Type.Optional(Type.Unsafe<unknown>({ description: "Optional skill-defined diagnostic payload." })),
});

const displayArtifactParams = Type.Object({
	type: Type.Optional(Type.String({ description: "Skill-defined artifact type." })),
	name: Type.Optional(Type.String({ description: "Human-readable artifact name." })),
	path: Type.Optional(Type.String({ description: "Artifact path relative to the project or run workspace." })),
	status: Type.Optional(StringEnum(["draft", "ready", "failed", "incomplete"] as const)),
	summary: Type.Optional(Type.String({ description: "Artifact summary." })),
	metadata: Type.Optional(Type.Record(Type.String(), Type.Unsafe<unknown>({}))),
});

const updateStateToolParams = Type.Object({
	phase: Type.Optional(displayPhaseParams),
	message: Type.Optional(Type.String({ description: "Current run message for TUI/WebUI display." })),
	progress: Type.Optional(Type.Number({ description: "Optional overall progress value." })),
	counters: Type.Optional(Type.Record(Type.String(), Type.Number())),
	diagnostics: Type.Optional(Type.Array(displayDiagnosticParams)),
	artifacts: Type.Optional(Type.Array(displayArtifactParams)),
	next_actions: Type.Optional(Type.Array(Type.String())),
});

interface ActiveRun {
	record: SureRunRecord;
	skillPackage: SureSkillPackage;
	hooks: SureHookRunner;
	previousActiveTools: string[];
	/** Set when an agent turn ended without sure_finish (excluding user aborts). */
	finishMissing?: boolean;
	/** Headless continuation prompts sent for this run. */
	finishNudges?: number;
}

/** Cap on automatic continuation prompts for headless (print/json) runs. */
const MAX_FINISH_NUDGES = 3;

function buildFinishNudge(run: SureRunRecord): string {
	return [
		`Sure run ${run.runId} is still active, but the last turn ended without calling ${FINISH_TOOL_NAME}.`,
		`Continue the run now: complete the remaining work, then call ${FINISH_TOOL_NAME} with the final manifest.`,
		`If the run cannot proceed, call ${FINISH_TOOL_NAME} with status "failed" and summarize the blocker.`,
	].join(" ");
}

function formatDiagnostics(diagnostics: SureDiscoveryDiagnostic[]): string {
	return diagnostics.map((diagnostic) => `${diagnostic.type}: ${diagnostic.message}`).join("\n");
}

export function buildInvocationPrompt(skillPackage: SureSkillPackage, run: SureRunRecord): string {
	const relativePackage = relative(run.cwd, skillPackage.packageDir) || ".";
	const agentArgs = stripOutputDir(run.args);
	return [
		`<sure_invocation run_id="${run.runId}" skill="${skillPackage.manifest.name}" command="/${skillPackage.manifest.command}">`,
		`Package directory: ${relativePackage}`,
		`Run directory: ${relative(run.cwd, run.runDir)}`,
		`User arguments: ${agentArgs || "(none)"}`,
		"",
		"Run this Sure skill as a scientific task. Produce durable artifacts in the run directory or project workspace.",
		"Use the skill instructions below. References are relative to the package directory.",
		"",
		skillPackage.prompt,
		"",
		"Completion protocol:",
		`- Finish by calling ${FINISH_TOOL_NAME} as a standalone final tool call.`,
		"- Do not emit a normal final answer instead of the finish tool.",
		"- If the finish tool returns repair instructions, fix the artifacts and call it again.",
		"- The final manifest must include schema_version, run_id, skill_name, status, created_at, inputs, outputs, and validation.",
		"</sure_invocation>",
	].join("\n");
}

export function buildResumePrompt(
	skillPackage: SureSkillPackage,
	run: SureRunRecord,
	state: SureDisplayState | undefined,
): string {
	const checkpoint = state?.checkpoint;
	return [
		buildInvocationPrompt(skillPackage, run),
		"",
		`<sure_resume run_id="${run.runId}">`,
		"This run already produced work in its run directory. Pick it back up rather than starting over.",
		`Checkpoint: ${checkpoint?.label ?? checkpoint?.id ?? "(unnamed)"}`,
		`Where it stopped: ${state?.message ?? "(not recorded)"}`,
		`Resume from: ${checkpoint?.resume_hint ?? "(no hint recorded; read the run directory to see what is already done)"}`,
		"Read the existing artifacts before writing anything, and do not redo units that are already complete.",
		"</sure_resume>",
	].join("\n");
}

function toolText(text: string): SureToolContent[] {
	return [{ type: "text", text }];
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null;
}

function isStringRecord(value: unknown): value is Record<string, unknown> {
	return isRecord(value) && !Array.isArray(value);
}

function isPathInside(baseDir: string, candidate: string): boolean {
	const rel = relative(baseDir, candidate);
	return rel === "" || (!rel.startsWith("..") && !rel.startsWith("/") && rel !== "..");
}

function isManifestArtifact(value: unknown): value is { type?: string; path?: string } {
	if (!isStringRecord(value)) {
		return false;
	}
	return (
		(value.type === undefined || typeof value.type === "string") &&
		(value.path === undefined || typeof value.path === "string")
	);
}

function hasCheckpointPatch(value: unknown): boolean {
	return isStringRecord(value) && value.checkpoint !== undefined;
}

const CHECKPOINT_COUNTER_KEYS = new Set(["completed_units", "total_units", "gate_blocks"]);
const CHECKPOINT_TERMINAL_PHASE_STATUSES = new Set(["success", "failed", "incomplete", "skipped"]);

function validateAgentStateUpdatePolicy(
	runManager: SureRunManager,
	active: ActiveRun,
	patch: SureDisplayState,
): string | undefined {
	const currentState = runManager.readState(active.record);
	if (!currentState?.checkpoint) {
		return undefined;
	}
	const phaseStatus = patch.phase?.status;
	if (phaseStatus && CHECKPOINT_TERMINAL_PHASE_STATUSES.has(phaseStatus)) {
		return `${UPDATE_STATE_TOOL_NAME} cannot set terminal phase status "${phaseStatus}" during checkpoint-controlled Sure runs; produce the required artifact and let hooks advance or call ${FINISH_TOOL_NAME}.`;
	}
	for (const key of Object.keys(patch.counters ?? {})) {
		if (CHECKPOINT_COUNTER_KEYS.has(key)) {
			return `${UPDATE_STATE_TOOL_NAME} cannot update checkpoint counter "${key}"; checkpoint counters are controlled by Sure hooks.`;
		}
	}
	for (const artifact of patch.artifacts ?? []) {
		if (artifact.status !== "ready") {
			continue;
		}
		if (!artifact.path) {
			return `${UPDATE_STATE_TOOL_NAME} cannot mark an artifact ready without a path during checkpoint-controlled Sure runs.`;
		}
		const resolved = isAbsolute(artifact.path)
			? resolve(artifact.path)
			: runManager.resolveRunPath(active.record, artifact.path);
		if (!resolved || !existsSync(resolved)) {
			return `${UPDATE_STATE_TOOL_NAME} cannot mark missing artifact "${artifact.path}" as ready during checkpoint-controlled Sure runs.`;
		}
	}
	return undefined;
}

function setRunStatusText(ctx: ExtensionContext, run: SureRunRecord, state?: SureDisplayState): void {
	ctx.ui.setStatus(STATUS_KEY, formatSureDisplayStatus(run.command, run.runId, state));
}

function applyStatePatch(
	pi: ExtensionAPI,
	ctx: ExtensionContext,
	active: ActiveRun,
	patchValue: unknown,
	eventType: string,
): { ok: boolean; state?: SureDisplayState; repair?: string } {
	if (patchValue === undefined) {
		return { ok: true };
	}
	const normalized = normalizeSureDisplayStatePatch(patchValue);
	if (!normalized.ok || !normalized.state) {
		const repair = normalized.message ?? "Invalid Sure state patch.";
		pi.appendEntry("sure.diagnostic", {
			type: "warning",
			message: repair,
			path: active.skillPackage.manifestPath,
		});
		return { ok: false, repair };
	}
	const runManager = new SureRunManager(active.record.cwd);
	const state = runManager.updateState(active.record, normalized.state, eventType);
	pi.appendEntry("sure.state", {
		runId: active.record.runId,
		command: active.record.command,
		skillName: active.record.skillName,
		state,
	});
	setRunStatusText(ctx, active.record, state);
	return { ok: true, state };
}

function activeToolNames(active: ActiveRun): string[] {
	return [...new Set([...active.previousActiveTools, FINISH_TOOL_NAME, UPDATE_STATE_TOOL_NAME])];
}

function possibleArtifactPaths(runManager: SureRunManager, run: SureRunRecord, pathValue: string): string[] {
	const paths: string[] = [];
	const projectOrAbsolutePath = runManager.resolveRunPath(run, pathValue);
	if (projectOrAbsolutePath) {
		paths.push(projectOrAbsolutePath);
	}
	if (!isAbsolute(pathValue)) {
		const runRelativePath = resolve(run.runDir, pathValue);
		if (isPathInside(run.runDir, runRelativePath)) {
			paths.push(runRelativePath);
		}
	}
	return [...new Set(paths)];
}

function artifactPathExists(runManager: SureRunManager, run: SureRunRecord, pathValue: string): boolean {
	return possibleArtifactPaths(runManager, run, pathValue).some((artifactPath) => existsSync(artifactPath));
}

function artifactPathMatchesRequirement(
	runManager: SureRunManager,
	run: SureRunRecord,
	artifactPath: string | undefined,
	requirementPath: string,
): boolean {
	if (!artifactPath) {
		return false;
	}
	if (artifactPath === requirementPath) {
		return true;
	}
	const artifactPaths = new Set(possibleArtifactPaths(runManager, run, artifactPath));
	return possibleArtifactPaths(runManager, run, requirementPath).some((path) => artifactPaths.has(path));
}

function artifactSatisfiesRequirement(
	runManager: SureRunManager,
	run: SureRunRecord,
	artifact: { type?: string; path?: string },
	requirement: SureArtifactRequirement,
): boolean {
	const requirementPath = requirement.path ?? "";
	if (artifactPathMatchesRequirement(runManager, run, artifact.path, requirementPath)) {
		return true;
	}
	if (!artifact.path || !artifactPathExists(runManager, run, artifact.path)) {
		return false;
	}
	if (requirementPath !== "" && basename(artifact.path) === basename(requirementPath)) {
		return true;
	}
	if (requirement.type && artifact.type !== requirement.type) {
		return false;
	}
	return false;
}

function formatArtifactDescription(artifact: { type?: string; path?: string }, index: number): string {
	if (artifact.type) {
		return `"${artifact.type}"`;
	}
	if (artifact.path) {
		return `"${artifact.path}"`;
	}
	return `at index ${index}`;
}

function validateManifestArtifacts(
	manifest: SureManifestEnvelope,
	runManager: SureRunManager,
	run: SureRunRecord,
): string | undefined {
	for (const [index, artifact] of (manifest.artifacts ?? []).entries()) {
		const description = formatArtifactDescription(artifact, index);
		if (!artifact.path || artifact.path.trim() === "") {
			return `Final manifest artifact ${description} must include a path.`;
		}
		if (!artifactPathExists(runManager, run, artifact.path)) {
			return `Manifest artifact path does not exist: ${artifact.path}.`;
		}
	}
	return undefined;
}

function outputArtifacts(manifest: SureManifestEnvelope): Array<{ type?: string; path?: string }> {
	const artifacts: Array<{ type?: string; path?: string }> = [];
	for (const [key, value] of Object.entries(manifest.outputs ?? {})) {
		if (typeof value === "string" && value.trim() !== "") {
			artifacts.push({ type: key, path: value });
			continue;
		}
		if (isStringRecord(value) && typeof value.path === "string" && value.path.trim() !== "") {
			artifacts.push({
				type: typeof value.type === "string" ? value.type : key,
				path: value.path,
			});
		}
	}
	return artifacts;
}

function validateRequiredArtifact(
	requirement: SureArtifactRequirement,
	manifest: SureManifestEnvelope,
	runManager: SureRunManager,
	run: SureRunRecord,
): string | undefined {
	if (!requirement.required) {
		return undefined;
	}
	if (!requirement.path || requirement.path.trim() === "") {
		const label = requirement.type
			? `"${requirement.type}"`
			: requirement.description
				? `"${requirement.description}"`
				: "";
		return `Required artifact ${label} must declare a path in sure.skill.json.`;
	}

	const artifacts = [...(manifest.artifacts ?? []), ...outputArtifacts(manifest)];
	const candidate = artifacts.find((artifact) => artifactSatisfiesRequirement(runManager, run, artifact, requirement));
	if (!candidate) {
		const description = requirement.description ? ` (${requirement.description})` : "";
		return `Final manifest is missing required artifact${description}.`;
	}
	if (!candidate.path || !artifactPathExists(runManager, run, candidate.path)) {
		return `Required artifact path does not exist: ${candidate.path ?? requirement.path}.`;
	}
	return undefined;
}

function validateManifestEnvelope(
	value: unknown,
	run: SureRunRecord,
	finish: SureFinishParams,
	runManager: SureRunManager,
	skillPackage: SureSkillPackage,
): string | undefined {
	if (!isRecord(value)) {
		return "Final manifest must be a JSON object.";
	}
	const required = [
		"schema_version",
		"run_id",
		"skill_name",
		"status",
		"created_at",
		"inputs",
		"outputs",
		"validation",
	];
	for (const key of required) {
		if (!(key in value)) {
			return `Final manifest is missing required field "${key}".`;
		}
	}
	if (value.run_id !== run.runId) {
		return `Final manifest run_id must be "${run.runId}".`;
	}
	if (value.skill_name !== run.skillName) {
		return `Final manifest skill_name must be "${run.skillName}".`;
	}
	if (typeof value.schema_version !== "string" || value.schema_version.trim() === "") {
		return 'Final manifest field "schema_version" must be a non-empty string.';
	}
	if (value.status !== finish.status) {
		return `Final manifest status must match sure_finish status "${finish.status}".`;
	}
	if (typeof value.created_at !== "string" || Number.isNaN(Date.parse(value.created_at))) {
		return 'Final manifest field "created_at" must be an ISO date string.';
	}
	if (!isStringRecord(value.inputs)) {
		return 'Final manifest field "inputs" must be a JSON object.';
	}
	if (!isStringRecord(value.outputs)) {
		return 'Final manifest field "outputs" must be a JSON object.';
	}
	if (!isStringRecord(value.validation)) {
		return 'Final manifest field "validation" must be a JSON object.';
	}
	if (value.artifacts !== undefined) {
		if (!Array.isArray(value.artifacts) || !value.artifacts.every(isManifestArtifact)) {
			return 'Final manifest field "artifacts" must be an array of objects with optional string type/path fields.';
		}
	}
	const manifest = value as unknown as SureManifestEnvelope;
	const artifactError = validateManifestArtifacts(manifest, runManager, run);
	if (artifactError) {
		return artifactError;
	}
	// Required artifacts describe what a completed run delivers. Demanding them
	// from a run that stopped early leaves the agent no way to report the failure
	// except by fabricating the evidence, so only a success finish is held to them.
	if (finish.status === "success") {
		for (const requirement of skillPackage.manifest.artifacts ?? []) {
			const requirementError = validateRequiredArtifact(requirement, manifest, runManager, run);
			if (requirementError) {
				return requirementError;
			}
		}
	}
	return undefined;
}

async function startRun(
	pi: ExtensionAPI,
	ctx: ExtensionCommandContext,
	skillPackage: SureSkillPackage,
	args: string,
	setActiveRun: (run: ActiveRun | undefined) => void,
): Promise<void> {
	if (!ctx.isIdle()) {
		ctx.ui.notify("Cannot start a Sure run while the agent is busy.", "warning");
		return;
	}
	if (!ctx.isProjectTrusted()) {
		ctx.ui.notify("Cannot start a Sure run until this project is trusted.", "error");
		return;
	}

	const outputDir = resolveOutputDir(args);
	if (!outputDir.ok) {
		ctx.ui.notify(outputDir.error ?? "Sure rejected the requested output_dir.", "error");
		return;
	}

	const runManager = new SureRunManager(ctx.cwd);
	const record = runManager.createRun(skillPackage, args, outputDir.dir);
	const active: ActiveRun = {
		record,
		skillPackage,
		hooks: new SureHookRunner(skillPackage),
		previousActiveTools: pi.getActiveTools(),
	};
	setActiveRun(active);

	const preStart = await active.hooks.run("pre_start", {
		run: active.record,
		skill: skillPackage.manifest,
		cwd: ctx.cwd,
		packageDir: skillPackage.packageDir,
		runDir: active.record.runDir,
		args,
	});
	applyStatePatch(pi, ctx, active, preStart.state_patch, "pre_start_state");
	if (!preStart.ok) {
		active.record = runManager.updateRun(
			active.record,
			{ status: "failed", finishedAt: new Date().toISOString(), lastRepair: preStart.repair ?? preStart.message },
			"pre_start_repair",
			preStart,
		);
		ctx.ui.notify(preStart.repair ?? preStart.message ?? "Sure pre-start gate rejected the run.", "error");
		pi.appendEntry("sure.run", active.record);
		pi.setActiveTools(active.previousActiveTools);
		setActiveRun(undefined);
		return;
	}

	active.record = runManager.setStatus(active.record, "running", "started");
	pi.setActiveTools(activeToolNames(active));
	setRunStatusText(ctx, active.record, runManager.readState(active.record));
	pi.appendEntry("sure.run", active.record);
	try {
		await pi.sendUserMessage(buildInvocationPrompt(skillPackage, active.record));
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		active.record = runManager.updateRun(
			active.record,
			{
				status: "failed",
				finishedAt: new Date().toISOString(),
				lastRepair: `Sure run failed to start or continue: ${message}`,
			},
			"agent_error",
			{ message },
		);
		const onError = await active.hooks.run("on_error", {
			run: active.record,
			skill: skillPackage.manifest,
			cwd: ctx.cwd,
			packageDir: skillPackage.packageDir,
			runDir: active.record.runDir,
			args,
			event: { reason: "agent_error", message },
		});
		applyStatePatch(pi, ctx, active, onError.state_patch, "on_error_state");
		ctx.ui.setStatus(STATUS_KEY, undefined);
		pi.appendEntry("sure.run", active.record);
		clearActiveRun(pi, active, setActiveRun);
		throw error;
	}
}

/** A run the agent may pick back up: same project, not stopped on purpose, checkpoint says so. */
function isResumable(record: SureRunRecord, state: SureDisplayState | undefined, cwd: string): boolean {
	if (record.cwd !== cwd) {
		return false;
	}
	// "cancelled" is a deliberate interrupt, and the other terminal statuses mean
	// the skill itself decided the run was over.
	if (record.status !== "running" && record.status !== "failed") {
		return false;
	}
	return state?.checkpoint?.resumable === true;
}

async function resumeRun(
	pi: ExtensionAPI,
	ctx: ExtensionCommandContext,
	setActiveRun: (run: ActiveRun | undefined) => void,
	runId: string | undefined,
): Promise<void> {
	if (!ctx.isIdle()) {
		ctx.ui.notify("Cannot resume a Sure run while the agent is busy.", "warning");
		return;
	}
	if (!ctx.isProjectTrusted()) {
		ctx.ui.notify("Cannot resume a Sure run until this project is trusted.", "error");
		return;
	}

	const runManager = new SureRunManager(ctx.cwd);
	const candidates = runId ? [runId] : runManager.listRunIds().reverse();
	let resumed: { record: SureRunRecord; state: SureDisplayState | undefined } | undefined;
	for (const candidate of candidates) {
		const record = runManager.readRun(candidate);
		if (!record) {
			continue;
		}
		const state = runManager.readState(record);
		if (isResumable(record, state, ctx.cwd)) {
			resumed = { record, state };
			break;
		}
	}
	if (!resumed) {
		ctx.ui.notify(
			runId
				? `Sure run ${runId} cannot be resumed: no resumable checkpoint for this project.`
				: "No resumable Sure run in this project.",
			"warning",
		);
		return;
	}

	const discovered = discoverSureSkillPackages(ctx.cwd);
	for (const diagnostic of discovered.diagnostics) {
		pi.appendEntry("sure.diagnostic", diagnostic);
	}
	const skillPackage = discovered.packages.find((pkg) => pkg.manifest.command === resumed.record.command);
	if (!skillPackage) {
		ctx.ui.notify(`No Sure skill package found for /${resumed.record.command}.`, "error");
		return;
	}

	const active: ActiveRun = {
		record: resumed.record,
		skillPackage,
		hooks: new SureHookRunner(skillPackage),
		previousActiveTools: pi.getActiveTools(),
	};
	setActiveRun(active);
	// pre_start is deliberately not re-run: it resolves inputs and resets the
	// checkpoint to the first unit, which is the progress a resume is protecting.
	active.record = runManager.updateRun(active.record, { status: "running", finishedAt: undefined }, "resumed");
	pi.setActiveTools(activeToolNames(active));
	setRunStatusText(ctx, active.record, resumed.state);
	pi.appendEntry("sure.run", active.record);
	try {
		await pi.sendUserMessage(buildResumePrompt(skillPackage, active.record, resumed.state));
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		active.record = runManager.updateRun(
			active.record,
			{
				status: "failed",
				finishedAt: new Date().toISOString(),
				lastRepair: `Sure run failed to resume: ${message}`,
			},
			"agent_error",
			{ message },
		);
		ctx.ui.setStatus(STATUS_KEY, undefined);
		pi.appendEntry("sure.run", active.record);
		clearActiveRun(pi, active, setActiveRun);
		throw error;
	}
}

function clearActiveRun(
	pi: ExtensionAPI,
	active: ActiveRun | undefined,
	setActiveRun: (run: ActiveRun | undefined) => void,
) {
	if (!active) {
		return;
	}
	pi.setActiveTools(
		pi.getActiveTools().filter((toolName) => toolName !== FINISH_TOOL_NAME && toolName !== UPDATE_STATE_TOOL_NAME),
	);
	setActiveRun(undefined);
}

/**
 * After a successful /sure_init, switch the current session onto the model it just configured
 * instead of leaving the user on whatever model they started with. Returns the success line for
 * the caller to print rather than printing it here: the TUI rewrites the same status line on
 * every consecutive info notify, so notifying here would wipe out everything init reported.
 * Falls back to a warning of its own if the model can't be resolved or has no configured auth
 * yet — init itself already succeeded, so this degrades gracefully rather than as an error.
 */
export async function switchToInitializedModel(
	pi: ExtensionAPI,
	ctx: ExtensionCommandContext,
	manifest: SureInitManifest,
): Promise<string | undefined> {
	const { defaultProvider, defaultModel } = manifest;
	ctx.modelRegistry.refresh();
	const model = ctx.modelRegistry.find(defaultProvider, defaultModel);
	const switched = model ? await pi.setModel(model) : false;
	if (!switched) {
		ctx.ui.notify(
			`Default model set to ${defaultProvider}/${defaultModel}. ` +
				`Run "/model ${defaultProvider}/${defaultModel}" to switch once credentials are configured.`,
			"warning",
		);
		return undefined;
	}
	// The level init measured went into the manifest and the global settings but never into
	// the session, which is why the session used to sit on medium after an init that said high.
	const level = manifest.defaultThinkingLevel;
	if (level && level !== "off") {
		pi.setThinkingLevel(level);
	}
	return `Switched to ${defaultProvider}/${defaultModel}.`;
}

export function createSureExtension(): ExtensionFactory {
	return (pi: ExtensionAPI) => {
		let activeRun: ActiveRun | undefined;

		const setActiveRun = (run: ActiveRun | undefined) => {
			activeRun = run;
		};

		pi.registerTool(
			defineTool({
				name: UPDATE_STATE_TOOL_NAME,
				label: "Sure State",
				description:
					"Update the active Sure run display state for TUI/WebUI status, diagnostics, artifacts, and next actions.",
				promptSnippet: "Update the active Sure run display state",
				promptGuidelines: [
					`Use ${UPDATE_STATE_TOOL_NAME} during active Sure skill runs to report phase, progress, counters, diagnostics, artifacts, or next actions.`,
					"Use skill-defined phase ids and counter names; Harness only validates the common state shape.",
				],
				activeByDefault: false,
				executionMode: "sequential",
				parameters: updateStateToolParams,
				async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
					const active = activeRun;
					if (!active) {
						return {
							content: toolText("No active Sure run. Start a Sure slash command before updating state."),
							details: { accepted: false } satisfies SureUpdateStateDetails,
							isError: true,
						};
					}
					if (hasCheckpointPatch(params)) {
						const repair = `${UPDATE_STATE_TOOL_NAME} cannot update checkpoints; checkpoints are controlled by Sure hooks.`;
						return {
							content: toolText(repair),
							details: {
								runId: active.record.runId,
								accepted: false,
								repair,
							} satisfies SureUpdateStateDetails,
							isError: true,
						};
					}
					const normalized = normalizeSureDisplayStatePatch(params);
					if (!normalized.ok || !normalized.state) {
						return {
							content: toolText(normalized.message ?? "Invalid Sure state update."),
							details: {
								runId: active.record.runId,
								accepted: false,
								repair: normalized.message,
							} satisfies SureUpdateStateDetails,
							isError: true,
						};
					}
					const runManager = new SureRunManager(active.record.cwd);
					const policyRepair = validateAgentStateUpdatePolicy(runManager, active, normalized.state);
					if (policyRepair) {
						return {
							content: toolText(policyRepair),
							details: {
								runId: active.record.runId,
								accepted: false,
								repair: policyRepair,
							} satisfies SureUpdateStateDetails,
							isError: true,
						};
					}
					const result = applyStatePatch(pi, ctx, active, params, "state_update");
					if (!result.ok) {
						return {
							content: toolText(result.repair ?? "Invalid Sure state update."),
							details: {
								runId: active.record.runId,
								accepted: false,
								repair: result.repair,
							} satisfies SureUpdateStateDetails,
							isError: true,
						};
					}
					return {
						content: toolText(`Updated Sure run ${active.record.runId} state.`),
						details: {
							runId: active.record.runId,
							accepted: true,
							state: result.state,
						} satisfies SureUpdateStateDetails,
					};
				},
			}),
		);

		pi.registerTool(
			defineTool({
				name: FINISH_TOOL_NAME,
				label: "Sure Finish",
				description: "Finish the active Sure scientific run after validating its artifact manifest and gates.",
				promptSnippet: "Finish an active Sure skill run with artifact validation",
				promptGuidelines: [
					`Use ${FINISH_TOOL_NAME} as the final standalone action for active Sure skill runs.`,
					"If it returns repair instructions, update artifacts and call it again.",
				],
				activeByDefault: false,
				executionMode: "sequential",
				parameters: finishToolParams,
				async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
					const active = activeRun;
					if (!active) {
						return {
							content: toolText("No active Sure run. Start a Sure slash command before calling sure_finish."),
							details: { accepted: false } satisfies SureFinishDetails,
						};
					}

					const finish = params as SureFinishParams;
					const runManager = new SureRunManager(active.record.cwd);
					const manifestPath = runManager.resolveRunPath(active.record, finish.manifest_path);
					if (!manifestPath || !existsSync(manifestPath)) {
						const repair = `Create the final manifest at ${finish.manifest_path} inside the project or run workspace, then call ${FINISH_TOOL_NAME} again.`;
						active.record = runManager.updateRun(active.record, { lastRepair: repair }, "finish_repair", {
							repair,
						});
						return {
							content: toolText(repair),
							details: { runId: active.record.runId, accepted: false, repair } satisfies SureFinishDetails,
						};
					}

					let manifest: unknown;
					try {
						manifest = JSON.parse(readFileSync(manifestPath, "utf-8"));
					} catch (error) {
						const repair = `Final manifest must be valid JSON: ${error instanceof Error ? error.message : String(error)}`;
						active.record = runManager.updateRun(active.record, { lastRepair: repair }, "finish_repair", {
							repair,
						});
						return {
							content: toolText(repair),
							details: { runId: active.record.runId, accepted: false, repair } satisfies SureFinishDetails,
						};
					}

					const envelopeError = validateManifestEnvelope(
						manifest,
						active.record,
						finish,
						runManager,
						active.skillPackage,
					);
					if (envelopeError) {
						const repair = `${envelopeError} Repair the manifest and call ${FINISH_TOOL_NAME} again.`;
						active.record = runManager.updateRun(active.record, { lastRepair: repair }, "finish_repair", {
							repair,
						});
						return {
							content: toolText(repair),
							details: { runId: active.record.runId, accepted: false, repair } satisfies SureFinishDetails,
						};
					}

					const gate = await active.hooks.run("pre_finish", {
						run: active.record,
						skill: active.skillPackage.manifest,
						cwd: ctx.cwd,
						packageDir: active.skillPackage.packageDir,
						runDir: active.record.runDir,
						args: active.record.args,
						event: { finish, manifest, manifestPath },
					});
					applyStatePatch(pi, ctx, active, gate.state_patch, "pre_finish_state");
					if (!gate.ok) {
						const repair = gate.repair ?? gate.message ?? "Repair the Sure artifacts and call sure_finish again.";
						active.record = runManager.updateRun(active.record, { lastRepair: repair }, "finish_repair", gate);
						return {
							content: toolText(repair),
							details: {
								runId: active.record.runId,
								accepted: false,
								repair,
								diagnostics: gate.diagnostics,
							} satisfies SureFinishDetails,
						};
					}

					active.record = runManager.updateRun(
						active.record,
						{
							status: finish.status,
							manifestPath,
							summary: finish.summary,
							errorSummary: finish.error_summary,
							artifacts: finish.artifacts,
							finishedAt: new Date().toISOString(),
							lastRepair: undefined,
						},
						"finished",
						{ finish, manifestPath },
					);
					pi.appendEntry("sure.run", active.record);
					const postFinish = await active.hooks.run("post_finish", {
						run: active.record,
						skill: active.skillPackage.manifest,
						cwd: ctx.cwd,
						packageDir: active.skillPackage.packageDir,
						runDir: active.record.runDir,
						args: active.record.args,
						event: { finish, manifest, manifestPath },
					});
					applyStatePatch(pi, ctx, active, postFinish.state_patch, "post_finish_state");
					if (!postFinish.ok) {
						pi.appendEntry("sure.diagnostic", {
							type: "warning",
							message: postFinish.message ?? postFinish.repair ?? "Sure post-finish hook failed.",
							path: active.skillPackage.manifestPath,
						});
					}
					ctx.ui.setStatus(STATUS_KEY, undefined);
					clearActiveRun(pi, active, setActiveRun);
					return {
						content: toolText(`Sure run ${active.record.runId} finished with status ${finish.status}.`),
						details: {
							runId: active.record.runId,
							status: finish.status,
							accepted: true,
						} satisfies SureFinishDetails,
						terminate: true,
					};
				},
			}),
		);

		pi.registerCommand("sure_init", {
			description: "Initialize SURE: select agent, configure auth, and validate environment",
			handler: async (args, ctx) => {
				if (!ctx.isProjectTrusted()) {
					ctx.ui.notify("Cannot initialize SURE until this project is trusted. Run /trust first.", "error");
					return;
				}
				const result = await runSureInit({ ctx, args: args.trim() });
				if (!result.success) {
					ctx.ui.notify(result.message, "error");
					return;
				}
				const switchedLine = result.manifest ? await switchToInitializedModel(pi, ctx, result.manifest) : undefined;
				// One info notify at the very end. Two in a row overwrite each other in the TUI,
				// which is how the probe, round-trip and table lines used to vanish on success.
				ctx.ui.notify([result.message, switchedLine].filter(Boolean).join("\n"), "info");
			},
		});

		pi.registerCommand("sure_resume", {
			description: "Resume a Sure run whose turn ended without sure_finish",
			handler: async (args, ctx) => {
				if (activeRun) {
					ctx.ui.notify(
						`Sure run ${activeRun.record.runId} is still active. Finish or abort it before resuming another run.`,
						"warning",
					);
					return;
				}
				await resumeRun(pi, ctx, setActiveRun, args.trim() || undefined);
			},
		});

		for (const command of SURE_COMMANDS) {
			pi.registerCommand(command, {
				description: `Run Sure skill /${command}`,
				handler: async (args, ctx) => {
					if (!ctx.isProjectTrusted()) {
						ctx.ui.notify("Cannot start a Sure run until this project is trusted.", "error");
						return;
					}
					if (activeRun) {
						ctx.ui.notify(
							`Sure run ${activeRun.record.runId} is still active. Finish or abort it before starting another run.`,
							"warning",
						);
						return;
					}
					const discovered = discoverSureSkillPackages(ctx.cwd);
					for (const diagnostic of discovered.diagnostics) {
						pi.appendEntry("sure.diagnostic", diagnostic);
					}
					const skillPackage = discovered.packages.find((pkg) => pkg.manifest.command === command);
					if (!skillPackage) {
						const diagnostics =
							discovered.diagnostics.length > 0 ? `\n${formatDiagnostics(discovered.diagnostics)}` : "";
						ctx.ui.notify(`No Sure skill package found for /${command}.${diagnostics}`, "error");
						return;
					}
					await startRun(pi, ctx, skillPackage, args.trim(), setActiveRun);
				},
			});
		}

		pi.on("tool_call", async (event, ctx) => {
			const active = activeRun;
			if (!active || event.toolName === FINISH_TOOL_NAME || event.toolName === UPDATE_STATE_TOOL_NAME) {
				return undefined;
			}
			const runManager = new SureRunManager(active.record.cwd);
			active.record = runManager.updateRun(active.record, { staleSince: undefined }, "tool_call", {
				toolName: event.toolName,
				toolCallId: event.toolCallId,
				input: event.input,
			});
			const gate = await active.hooks.run("pre_tool_call", {
				run: active.record,
				skill: active.skillPackage.manifest,
				cwd: ctx.cwd,
				packageDir: active.skillPackage.packageDir,
				runDir: active.record.runDir,
				args: active.record.args,
				event,
			});
			applyStatePatch(pi, ctx, active, gate.state_patch, "pre_tool_call_state");
			if (!gate.ok) {
				const repair = gate.repair ?? gate.message ?? "Repair the tool call and retry.";
				active.record = runManager.updateRun(active.record, { lastRepair: repair }, "tool_call_repair", gate);
				return { block: true, reason: repair };
			}
			return undefined;
		});

		pi.on("tool_result", async (event, ctx) => {
			const active = activeRun;
			if (event.toolName === FINISH_TOOL_NAME || event.toolName === UPDATE_STATE_TOOL_NAME) {
				if (isRecord(event.details) && event.details.accepted === false) {
					return { isError: true };
				}
				return undefined;
			}
			if (!active) {
				return undefined;
			}
			const runManager = new SureRunManager(active.record.cwd);
			active.record = runManager.updateRun(active.record, {}, "tool_result", {
				toolName: event.toolName,
				toolCallId: event.toolCallId,
				isError: event.isError,
			});
			const gate = await active.hooks.run("post_tool_result", {
				run: active.record,
				skill: active.skillPackage.manifest,
				cwd: ctx.cwd,
				packageDir: active.skillPackage.packageDir,
				runDir: active.record.runDir,
				args: active.record.args,
				event,
			});
			applyStatePatch(pi, ctx, active, gate.state_patch, "post_tool_result_state");
			if (!gate.ok) {
				const repair = gate.repair ?? gate.message ?? "Repair the tool result issue and retry.";
				active.record = runManager.updateRun(active.record, { lastRepair: repair }, "tool_result_repair", gate);
				return {
					content: toolText(repair),
					details: { repair, diagnostics: gate.diagnostics },
					isError: true,
				};
			}
			return undefined;
		});

		pi.on("agent_end", async (event, ctx) => {
			const active = activeRun;
			if (!active) {
				return;
			}
			if (active.record.status !== "running") {
				return;
			}
			if (event.willRetry) {
				// The session restarts this same turn, so the run is not abandoned yet.
				return;
			}
			const runManager = new SureRunManager(active.record.cwd);
			const lastAssistant = [...event.messages].reverse().find((message) => message.role === "assistant");
			// A provider that gave up leaves its reason only on the assistant message.
			// Recording the reminder over it makes the run read as agent negligence.
			const providerError =
				lastAssistant !== undefined && "stopReason" in lastAssistant && lastAssistant.stopReason === "error"
					? lastAssistant.errorMessage
					: undefined;
			// Without the state snapshot the event cannot tell "every unit done, only
			// sure_finish missing" from "stalled on the third unit" after the fact.
			const stalledState = runManager.readState(active.record);
			active.record = runManager.updateRun(
				active.record,
				{
					lastRepair:
						providerError ??
						`The Sure run is still active. Call ${FINISH_TOOL_NAME} after producing the required manifest.`,
					// "running" on its own is a lie once the agent has stopped. Stamp the
					// first stall so a reader can age the record without a live session.
					staleSince: active.record.staleSince ?? new Date().toISOString(),
				},
				"finish_missing",
				{
					reason: providerError ? "provider_ended_turn" : "agent_turn_ended_without_finish",
					nudges: active.finishNudges ?? 0,
					phase: stalledState?.phase,
					counters: stalledState?.counters,
					checkpoint: stalledState?.checkpoint,
				},
			);
			const headless = ctx.mode === "print" || ctx.mode === "json";
			// Headless gets a continuation prompt below. An interactive operator gets
			// only this line, and until it named the command there was nothing to act
			// on: one run sat at "running" for 58 hours with every unit already done.
			ctx.ui.notify(
				headless
					? `Sure run ${active.record.runId} is still waiting for ${FINISH_TOOL_NAME}.`
					: `Sure run ${active.record.runId} is still waiting for ${FINISH_TOOL_NAME}. Run /sure_resume to continue it.`,
				"warning",
			);
			const aborted =
				lastAssistant !== undefined && "stopReason" in lastAssistant && lastAssistant.stopReason === "aborted";
			if (aborted) {
				// A deliberate interrupt keeps its cancel semantics on shutdown.
				return;
			}
			active.finishMissing = true;
			if (headless && (active.finishNudges ?? 0) < MAX_FINISH_NUDGES) {
				active.finishNudges = (active.finishNudges ?? 0) + 1;
				await pi.sendUserMessage(buildFinishNudge(active.record), { deliverAs: "followUp" });
			}
		});

		pi.on("session_shutdown", async (_event, ctx) => {
			const active = activeRun;
			if (!active) {
				return;
			}
			const runManager = new SureRunManager(active.record.cwd);
			// A run abandoned by the agent (turn ended, no sure_finish) failed;
			// "cancelled" is reserved for runs interrupted mid-work.
			active.record = runManager.setStatus(
				active.record,
				active.finishMissing ? "failed" : "cancelled",
				"session_shutdown",
			);
			pi.appendEntry("sure.run", active.record);
			const onError = await active.hooks.run("on_error", {
				run: active.record,
				skill: active.skillPackage.manifest,
				cwd: ctx.cwd,
				packageDir: active.skillPackage.packageDir,
				runDir: active.record.runDir,
				args: active.record.args,
				event: { reason: "session_shutdown" },
			});
			applyStatePatch(pi, ctx, active, onError.state_patch, "on_error_state");
			ctx.ui.setStatus(STATUS_KEY, undefined);
			clearActiveRun(pi, active, setActiveRun);
		});

		pi.registerCommand("sure", {
			description: "Show Sure skill discovery status",
			handler: async (_args, ctx) => {
				const discovered = discoverSureSkillPackages(ctx.cwd);
				const commandList = discovered.packages.map((pkg) => `/${pkg.manifest.command}`).join(", ");
				const diagnostics =
					discovered.diagnostics.length > 0 ? `\n${formatDiagnostics(discovered.diagnostics)}` : "";
				ctx.ui.notify(
					commandList ? `Sure skills: ${commandList}${diagnostics}` : `No Sure skills found.${diagnostics}`,
				);
			},
		});
	};
}

export const sureExtension = createSureExtension();
