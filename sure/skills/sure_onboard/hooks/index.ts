import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { delimiter, dirname, isAbsolute, join, relative, resolve } from "node:path";
import type { SureHookContext, SureHookResult } from "@earendil-works/pi-coding-agent/hooks";
import { resolveHarnessPython } from "../../../runtime/harness/resolve.ts";
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
import { FIRST_UNIT, findUnit, LAST_UNIT, TOTAL_UNITS, type Unit } from "./state-machine.ts";
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

export function countersFor(completed: CheckpointData, gateBlocks?: number) {
	const ledgerBlocks = Object.values(completed.retries ?? {}).reduce((sum, n) => sum + (n ?? 0), 0);
	return {
		completed_units: completed.completedUnits.length,
		total_units: TOTAL_UNITS,
		gate_blocks: Math.max(ledgerBlocks, gateBlocks ?? 0),
	};
}

// Resolve the model artifacts dir. sure_onboard binds model entity products to
// the repo-level sure/models/<model_name>/ directory (product layout decision).
const TASK_TYPES = [
	"asr",
	"s2tt",
	"sd",
	"ser",
	"tts",
	"vc",
	"kws",
	"slu",
	"gr",
	"speech_understanding",
	"sa-asr",
	"sa_asr",
];
const DEPLOYMENT_TYPES = ["local", "api"];
const PACKAGE_PROFILES = ["none", "docker-local", "docker-registry"];

interface ResolvedOnboardArgs {
	args: Record<string, string>;
	modelInputPath?: string;
	error?: string;
}

type OnboardDiagnostic = { severity: "warning" | "info"; message: string; repair: string };

function modelDirFor(ctx: SureHookContext, args: Record<string, string>): string | undefined {
	const modelName = typeof args.model_name === "string" ? args.model_name : undefined;
	const modelId = typeof args.model_id === "string" ? args.model_id : undefined;
	const existing = typeof args.existing_model_dir === "string" ? args.existing_model_dir : undefined;
	if (existing) {
		return existing;
	}
	if (modelName || modelId) {
		return join(ctx.cwd, "sure", "models", slugifyModelName(modelName ?? modelId ?? "model"));
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
		if (!token.startsWith("-") && looksLikeModelInputPath(token) && !out.model_input_path) {
			out.model_input_path = token;
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

function unboundedFilesystemSearchTarget(command: string): string | undefined {
	const pattern = /\bfind\s+(['"]?)(\/[^\s'"]*)\1(?=\s|$)/g;
	for (const match of command.matchAll(pattern)) {
		if (isBlockedSearchRoot(match[2])) {
			return match[2];
		}
	}
	return undefined;
}

function isBlockedSearchRoot(target: string): boolean {
	const normalizedTarget = normalizeRoot(target);
	return blockedSearchRoots().some((root) => {
		if (root === "/") {
			return normalizedTarget === "/";
		}
		return normalizedTarget === root || normalizedTarget.startsWith(`${root}/`);
	});
}

function blockedSearchRoots(): string[] {
	const configured = (process.env.SURE_ONBOARD_BLOCKED_SEARCH_ROOTS ?? "")
		.split(delimiter)
		.map((root) => normalizeRoot(root))
		.filter((root) => root.startsWith("/"));
	return Array.from(new Set(["/", "/mnt", ...configured]));
}

function normalizeRoot(root: string): string {
	const trimmed = root.trim();
	if (!trimmed || trimmed === "/") {
		return "/";
	}
	return trimmed.replace(/\/+$/, "");
}

function discoverHeavyOperation(command: string): string | undefined {
	const lower = command.toLowerCase();
	if (/\bsnapshot_download\s*\(/.test(command) || /\bhuggingface-cli\s+download\b/.test(lower)) {
		return "checkpoint download";
	}
	if (
		/\bhf_hub_download\s*\(/.test(command) &&
		/(safetensors|pytorch_model|model-[0-9].*of.*[0-9]|checkpoint|\.bin)/.test(lower)
	) {
		return "checkpoint file download";
	}
	if (/\b(curl|wget)\b/.test(lower) && /(safetensors|pytorch_model|model-[0-9].*of.*[0-9]|\.bin)/.test(lower)) {
		return "checkpoint file download";
	}
	for (const match of command.matchAll(/\bsleep\s+([0-9]+)/g)) {
		const seconds = Number.parseInt(match[1], 10);
		if (Number.isFinite(seconds) && seconds >= 60) {
			return `long sleep (${seconds}s)`;
		}
	}
	return undefined;
}

function discoverRuntimeProbe(command: string): string | undefined {
	const lower = command.toLowerCase();
	if (!/\bpython(?:3(?:\.\d+)?)?\b/.test(lower) || !/(^|\s)-c\s+/.test(lower)) {
		return undefined;
	}
	const runtimeImportPattern =
		/\b(?:import|from)\s+(torch|torchaudio|torchvision|transformers|accelerate|librosa|soundfile|funasr|modelscope|speechbrain|whisper|openai_whisper|nemo|espnet|kaldi|sherpa_onnx|qwen_omni)\b/;
	if (runtimeImportPattern.test(lower)) {
		return "runtime dependency import probe";
	}
	if (
		/\b(from_pretrained|automodel|autoprocessor|cuda\.is_available|torch\.__version__|transformers\.__version__)\b/.test(
			lower,
		)
	) {
		return "runtime dependency import probe";
	}
	return undefined;
}

function boundedDiscoverRepair(ctx: SureHookContext, target: string): string {
	const resolved = readArtifact(ctx, "model_input_resolved.json");
	const modelDir = isRecord(resolved) && typeof resolved.model_dir === "string" ? resolved.model_dir : undefined;
	const source = isRecord(resolved) && isRecord(resolved.source) ? resolved.source : {};
	const handoffArtifactsDir =
		typeof source.handoff_artifacts_dir === "string" ? source.handoff_artifacts_dir : undefined;
	const boundedPaths = [modelDir, handoffArtifactsDir].filter((path): path is string => Boolean(path));
	const pathHint =
		boundedPaths.length > 0
			? `Use bounded paths instead: ${boundedPaths.join(", ")}.`
			: "Read model_input_resolved.json first, then derive a bounded path from model_dir, handoff artifacts, or weights.local_path.";
	return `Blocked unbounded discover search rooted at "${target}". ${pathHint} Do not search filesystem roots during /sure_onboard discover.`;
}

function heavyDiscoverRepair(operation: string): string {
	return `Blocked ${operation} during discover. Discover may inspect MODEL_INPUT, handoff artifacts, existing model_dir, README/config metadata, and short network probes only. Produce repo_summary.json now; full checkpoint transfer, resumable downloads, HF mirror/Xet diagnostics, and long waits belong to the fetch_weights unit.`;
}

function runtimeProbeDiscoverRepair(operation: string): string {
	return `Blocked ${operation} during discover. Discover must not validate model runtime dependencies with the current shell or base Python. Record dependency evidence from README/config/requirements in repo_summary.json, choose the backend in plan, create the model-local environment in build_env, then run import/load/infer checks with that environment in validate_import/load/infer.`;
}

function isPathUnder(child: string, parent: string): boolean {
	const rel = relative(resolve(parent), resolve(child));
	return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
}

function normalizedCommand(command: string): string {
	return command.replace(/\\/g, "/");
}

function shellSingleQuote(value: string): string {
	return `'${value.replace(/'/g, `'\\''`)}'`;
}

function commandMentionsCdTo(command: string, absolutePath: string, cwd: string): boolean {
	const normalized = normalizedCommand(command);
	const abs = normalizedCommand(resolve(absolutePath));
	const rel = normalizedCommand(relative(cwd, abs));
	const cdTargets = [abs, rel].filter((entry) => entry && entry !== ".");
	return cdTargets.some((target) => {
		const quotedSingle = shellSingleQuote(target);
		const quotedDouble = `"${target.replace(/"/g, '\\"')}"`;
		return (
			normalized.includes(`cd ${target}`) ||
			normalized.includes(`cd ${quotedSingle}`) ||
			normalized.includes(`cd ${quotedDouble}`)
		);
	});
}

function commandUsesSkillScriptPath(ctx: SureHookContext, command: string, invokedScript: string): boolean {
	const normalized = normalizedCommand(command);
	const packageDir = normalizedCommand(resolve(ctx.packageDir));
	const relPackageDir = normalizedCommand(relative(ctx.cwd, packageDir));
	return (
		normalized.includes(`${packageDir}/${invokedScript}`) ||
		(relPackageDir !== "" && relPackageDir !== "." && normalized.includes(`${relPackageDir}/${invokedScript}`))
	);
}

function cwdIsSkillPackage(ctx: SureHookContext): boolean {
	return resolve(ctx.cwd) === resolve(ctx.packageDir);
}

function modelDirFromResolvedArtifact(ctx: SureHookContext): string | undefined {
	const resolved = readArtifact(ctx, "model_input_resolved.json");
	if (isRecord(resolved) && typeof resolved.model_dir === "string") {
		return resolved.model_dir;
	}
	return undefined;
}

function skillScriptRepair(ctx: SureHookContext, currentUnit: Unit, invokedScript: string): string {
	const command = `cd ${shellSingleQuote(ctx.packageDir)} && "$HARNESS_PYTHON_BIN" ${invokedScript} --run-dir ${shellSingleQuote(ctx.runDir)} --produces ${shellSingleQuote(artifactPath(ctx, currentUnit.produces))}`;
	return `Harness gate/helper scripts must run from the skill package with harness Python, not the model runtime. Use this shape when a manual diagnostic is needed: ${command}. The hook will also run the current gate script automatically after ${currentUnit.produces} is produced.`;
}

function relativeModelRuntimeRepair(ctx: SureHookContext): string {
	const modelDir = modelDirFromResolvedArtifact(ctx);
	const modelHint = modelDir
		? `cd ${shellSingleQuote(modelDir)} && .venv/bin/python ...`
		: "cd sure/models/<model_name> && .venv/bin/python ...";
	return `Relative .venv/bin/python is model-local runtime Python. Run it from the model directory (${modelHint}) or use an absolute interpreter path. Do not run model-local .venv commands from the repo root.`;
}

function validateInferFixturePrerequisite(ctx: SureHookContext): GateResult | undefined {
	const manifest = readArtifact(ctx, "fixture_manifest.json");
	if (!isRecord(manifest)) {
		return {
			ok: false,
			reason: "fixture manifest missing",
			repair:
				"VALIDATE_INFER requires a prepared model-local fixture. Run the prepare_fixture unit first and produce fixture_manifest.json with sure/models/<model>/fixture/<task>/<fixture>/gt.jsonl.",
		};
	}
	const modelDir = typeof manifest.model_dir === "string" ? manifest.model_dir : undefined;
	const stagedDir = typeof manifest.staged_dir === "string" ? manifest.staged_dir : undefined;
	const gtJsonl = typeof manifest.gt_jsonl === "string" ? manifest.gt_jsonl : undefined;
	const sampleCount = typeof manifest.sample_count === "number" ? manifest.sample_count : undefined;
	if (!modelDir || !stagedDir || !gtJsonl || sampleCount === undefined) {
		return {
			ok: false,
			reason: "fixture manifest incomplete",
			repair:
				"fixture_manifest.json must declare model_dir, staged_dir, gt_jsonl, and sample_count before validate_infer can run.",
		};
	}
	if (!existsSync(stagedDir) || !existsSync(gtJsonl)) {
		return {
			ok: false,
			reason: "staged fixture missing",
			repair: `Prepared fixture paths do not exist. staged_dir=${stagedDir}, gt_jsonl=${gtJsonl}. Re-run scripts/prepare_fixture.py from the prepare_fixture unit.`,
		};
	}
	if (sampleCount < 1 || sampleCount > 5) {
		return {
			ok: false,
			reason: "invalid fixture sample count",
			repair: `fixture_manifest.json sample_count must be between 1 and 5 for local validation (got ${sampleCount}).`,
		};
	}
	const fixtureRoot = join(modelDir, "fixture");
	if (!isPathUnder(stagedDir, fixtureRoot) || !isPathUnder(gtJsonl, fixtureRoot)) {
		return {
			ok: false,
			reason: "fixture outside model dir",
			repair: `Prepared fixture must live under ${fixtureRoot}; got staged_dir=${stagedDir}, gt_jsonl=${gtJsonl}.`,
		};
	}
	return { ok: true };
}

function looksLikeModelInputPath(token: string): boolean {
	return (
		token.endsWith(".yaml") ||
		token.endsWith(".yml") ||
		token.includes("model_input.yaml") ||
		token.includes("model_input.yml")
	);
}

function slugifyModelName(value: string): string {
	return (
		value
			.trim()
			.replace(/\/+/g, "__")
			.replace(/[^A-Za-z0-9_.-]+/g, "_")
			.replace(/^[._-]+|[._-]+$/g, "") || "model"
	);
}

function handoffNameCandidates(raw: string): string[] {
	const candidates = new Set<string>();
	const trimmed = raw.trim();
	if (!trimmed) {
		return [];
	}
	candidates.add(trimmed);
	try {
		const url = new URL(trimmed);
		const parts = url.pathname.split("/").filter(Boolean);
		const host = url.hostname.replace(/^www\./, "");
		if ((host === "huggingface.co" || host === "hf-mirror.com" || host === "github.com") && parts.length >= 2) {
			candidates.add(`${parts[0]}__${parts[1]}`);
		}
		if (host === "modelscope.cn" && parts.length >= 3 && parts[0] === "models") {
			candidates.add(`${parts[1]}__${parts[2]}`);
		}
	} catch {
		// Not a URL; treat it as an id/folder name.
	}
	candidates.add(slugifyModelName(trimmed));
	return [...candidates];
}

function cleanScalar(raw: string): string | undefined {
	const trimmed = raw.trim();
	if (!trimmed || trimmed === "null" || trimmed === "~") {
		return undefined;
	}
	if ((trimmed.startsWith('"') && trimmed.endsWith('"')) || (trimmed.startsWith("'") && trimmed.endsWith("'"))) {
		return trimmed.slice(1, -1);
	}
	return trimmed.replace(/\s+#.*$/, "");
}

function parseSimpleYamlScalars(text: string): Record<string, string> {
	const values: Record<string, string> = {};
	const stack: Array<{ indent: number; key: string }> = [];
	for (const rawLine of text.split(/\r?\n/)) {
		if (!rawLine.trim() || rawLine.trimStart().startsWith("#") || rawLine.trimStart().startsWith("- ")) {
			continue;
		}
		const match = rawLine.match(/^(\s*)([A-Za-z0-9_-]+):(?:\s*(.*))?$/);
		if (!match) {
			continue;
		}
		const indent = match[1].length;
		const key = match[2];
		const rawValue = match[3] ?? "";
		while (stack.length > 0 && stack[stack.length - 1].indent >= indent) {
			stack.pop();
		}
		const path = [...stack.map((entry) => entry.key), key].join(".");
		const scalar = cleanScalar(rawValue);
		if (scalar !== undefined) {
			values[path] = scalar;
		} else if (!rawValue.trim()) {
			stack.push({ indent, key });
		}
	}
	return values;
}

function resolveExistingPath(ctx: SureHookContext, rawPath: string): string | undefined {
	const candidates = isAbsolute(rawPath) ? [rawPath] : [resolve(ctx.cwd, rawPath), resolve(ctx.runDir, rawPath)];
	return candidates.find((candidate) => existsSync(candidate));
}

function resolveHandoffModelInputPath(ctx: SureHookContext, rawModel: string): { path?: string; error?: string } {
	for (const name of handoffNameCandidates(rawModel)) {
		const candidate = resolveExistingPath(ctx, join("sure", "handoffs", name, "model_input.yaml"));
		if (candidate) {
			return { path: candidate };
		}
	}
	return {
		error: `handoff "${rawModel}" does not exist. Expected sure/handoffs/<model_name>/model_input.yaml; run /sure_feed first or pass model_input_path explicitly.`,
	};
}

function resolveOnboardArgs(ctx: SureHookContext): ResolvedOnboardArgs {
	const args = parseArgs(ctx.args);
	let rawModelInputPath = args.model_input_path ?? args.model_input;
	if (!rawModelInputPath && args.model) {
		const handoff = resolveHandoffModelInputPath(ctx, args.model);
		if (handoff.error) {
			return { args, error: handoff.error };
		}
		if (!handoff.path) {
			return { args, error: `handoff "${args.model}" did not resolve to a model_input.yaml path.` };
		}
		rawModelInputPath = handoff.path;
	}
	if (!rawModelInputPath) {
		return { args };
	}
	const path = resolveExistingPath(ctx, rawModelInputPath);
	if (!path) {
		return {
			args,
			error: `model_input_path "${rawModelInputPath}" does not exist. Pass an absolute path or a path relative to the current working directory.`,
		};
	}
	try {
		const values = parseSimpleYamlScalars(readFileSync(path, "utf-8"));
		const merged = { ...args };
		merged.model_input_path = path;
		if (!merged.model_id && values.model_id) {
			merged.model_id = values.model_id;
		}
		if (!merged.model_name && values.model_name) {
			merged.model_name = values.model_name;
		}
		if (!merged.repo && values["repo.url"]) {
			merged.repo = values["repo.url"];
		}
		if (!merged.task_type && values.task_type) {
			merged.task_type = values.task_type;
		}
		if (!merged.deployment_type && values.deployment_type) {
			merged.deployment_type = values.deployment_type;
		}
		if (!merged.preferred_backend && values["environment_hint.preferred_backend"]) {
			merged.preferred_backend = values["environment_hint.preferred_backend"];
		}
		if (!merged.python_version && values["environment_hint.python_version"]) {
			merged.python_version = values["environment_hint.python_version"];
		}
		if (!merged.weights_source && values["weights.source"]) {
			merged.weights_source = values["weights.source"];
		}
		if (!merged.package_profile && !merged.package) {
			merged.package_profile = merged.deployment_type === "api" ? "none" : "docker-registry";
		}
		if (!merged.model_name && merged.model_id) {
			merged.model_name = slugifyModelName(merged.model_id);
		}
		return { args: merged, modelInputPath: path };
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		return { args, error: `Failed to read model_input_path "${path}": ${message}` };
	}
}

export function preStart(ctx: SureHookContext): SureHookResult {
	const resolved = resolveOnboardArgs(ctx);
	if (resolved.error) {
		return failure(resolved.error, "Invalid model_input_path.");
	}
	const args = resolved.args;
	if (!args.package_profile && args.package) {
		args.package_profile = args.package;
	}
	if (!args.package_profile) {
		args.package_profile = args.deployment_type === "api" ? "none" : "docker-registry";
	}
	if (!args.model_name && args.model_id) {
		args.model_name = slugifyModelName(args.model_id);
	}
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
			`Missing required /sure_onboard parameters: ${missing.join(", ")}. Usage: /sure_onboard model=<handoff_name> OR /sure_onboard model_input_path=sure/handoffs/<handoff_name>/model_input.yaml OR /sure_onboard model_id=<owner/model> model_name=<owner__model> repo=<url|path> task_type=<${TASK_TYPES.join("|")}> deployment_type=<local|api> [preferred_backend=uv|pip|conda|pixi|docker|api] [python_version=...] [weights_source=...] [package=docker-registry] [force_repair=true] [existing_model_dir=...] [max_retries=3]`,
			"Missing required parameters.",
		);
	}

	// Early enum validation — fail fast on a bad task_type/deployment_type
	// instead of letting it surface much later at the classify unit.
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
	if (!PACKAGE_PROFILES.includes(args.package_profile)) {
		return failure(
			`package "${args.package_profile}" is not one of ${JSON.stringify(PACKAGE_PROFILES)}. Local models default to package=docker-registry; package=none selects an explicitly approved local Python runtime.`,
			"Invalid package profile.",
		);
	}
	if (args.deployment_type === "local" && args.package_profile === "none") {
		try {
			const policy = requireSitePolicy({ repositoryRoot: ctx.cwd }).policy;
			if (!policy.execution.surfaces.includes("local")) {
				return failure(
					"package=none requires local in execution.surfaces because Python runtimes cannot run on VC.",
					"Local execution is disabled by site policy.",
				);
			}
			if (!policy.execution.local_runtimes.includes("python")) {
				return failure(
					"package=none requires python in execution.local_runtimes. Enable it in the active site policy or use package=docker-registry.",
					"Local Python runtime is disabled by site policy.",
				);
			}
		} catch (error) {
			return failure(
				error instanceof Error ? error.message : String(error),
				"Local Python site policy is unavailable.",
			);
		}
	}
	const runtime = resolveHarnessPython(ctx.packageDir);
	if (!runtime.ok || !runtime.contract) {
		return failure(
			runtime.error ?? "Bootstrap the locked common Harness Runtime and retry /sure_onboard.",
			"HARNESS_RUNTIME_NOT_READY",
		);
	}

	// Ensure the global model root exists without claiming the concrete model
	// directory. Later units may create it, or replace an empty slot with a
	// symlink to an already validated local deployment.
	const modelDir = modelDirFor(ctx, args);
	if (modelDir) {
		try {
			mkdirSync(dirname(modelDir), { recursive: true });
		} catch {
			// Non-fatal — the wrapper/save_artifacts units will surface real errors.
		}
	}

	const scriptsDir = join(ctx.packageDir, "scripts");
	const backendPresent = existsSync(scriptsDir) && existsSync(join(scriptsDir, "check_verdict.py"));
	const checkpoint = readCheckpoint(ctx);
	const diagnostics: OnboardDiagnostic[] = backendPresent
		? []
		: [
				{
					severity: "warning" as const,
					message: "scripts/ backend is not fully bundled.",
					repair: "Bundle the deterministic Python backend into scripts/ before a real onboard.",
				},
			];
	if (resolved.modelInputPath) {
		diagnostics.push({
			severity: "info" as const,
			message: `Loaded MODEL_INPUT from ${resolved.modelInputPath}.`,
			repair:
				"Continue with discover/classify using the normalized MODEL_INPUT values unless an explicit argument overrides them.",
		});
	}
	return {
		ok: true,
		state_patch: {
			phase: phaseFor(findUnit(checkpoint.data.currentUnit) ?? FIRST_UNIT, "running"),
			message: `SURE model-tool skill loaded for model "${args.model_id}" as "${args.model_name}"${resolved.modelInputPath ? " from MODEL_INPUT" : ""}; package=${args.package_profile}; Harness Runtime ${runtime.contract.runtime_id} (→ ${modelDir ?? "(no dir)"}).`,
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
	const toolName =
		typeof event.toolName === "string" ? event.toolName : typeof toolCall.name === "string" ? toolCall.name : "";
	if (toolName !== "bash") {
		return { ok: true };
	}
	const input = isRecord(event.input) ? event.input : isRecord(toolCall.input) ? toolCall.input : {};
	const command = typeof input.command === "string" ? input.command : "";
	const checkpoint = readCheckpoint(ctx);
	const currentUnit = findUnit(checkpoint.data.currentUnit);
	if (/(^|[;&|]\s*)\.venv\/bin\/python\b/.test(command)) {
		const modelDir = modelDirFromResolvedArtifact(ctx);
		if (!modelDir || !commandMentionsCdTo(command, modelDir, ctx.cwd)) {
			const repair = relativeModelRuntimeRepair(ctx);
			return {
				ok: false,
				repair,
				state_patch: {
					phase: phaseFor(currentUnit ?? FIRST_UNIT, "blocked"),
					message: "Blocked model-local runtime command from the wrong working directory.",
					counters: countersFor(checkpoint.data, 1),
					diagnostics: [
						{
							severity: "error",
							message: "Relative .venv/bin/python was invoked without cd-ing into the model directory.",
							repair,
						},
					],
				},
			};
		}
	}
	if (currentUnit?.id === "discover") {
		const target = unboundedFilesystemSearchTarget(command);
		if (target) {
			const repair = boundedDiscoverRepair(ctx, target);
			return {
				ok: false,
				repair,
				state_patch: {
					phase: phaseFor(currentUnit, "blocked"),
					message: "Blocked unbounded discover search.",
					counters: countersFor(checkpoint.data, 1),
					diagnostics: [
						{
							severity: "error",
							message: `Unbounded filesystem search is not allowed during discover: find ${target}`,
							repair,
						},
					],
				},
			};
		}
		const heavyOperation = discoverHeavyOperation(command);
		if (heavyOperation) {
			const repair = heavyDiscoverRepair(heavyOperation);
			return {
				ok: false,
				repair,
				state_patch: {
					phase: phaseFor(currentUnit, "blocked"),
					message: `Blocked heavy discover operation: ${heavyOperation}.`,
					counters: countersFor(checkpoint.data, 1),
					diagnostics: [
						{
							severity: "error",
							message: `Heavy operation is not allowed during discover: ${heavyOperation}`,
							repair,
						},
					],
				},
			};
		}
		const runtimeProbe = discoverRuntimeProbe(command);
		if (runtimeProbe) {
			const repair = runtimeProbeDiscoverRepair(runtimeProbe);
			return {
				ok: false,
				repair,
				state_patch: {
					phase: phaseFor(currentUnit, "blocked"),
					message: `Blocked discover runtime probe: ${runtimeProbe}.`,
					counters: countersFor(checkpoint.data, 1),
					diagnostics: [
						{
							severity: "error",
							message: `Runtime dependency probe is not allowed during discover: ${runtimeProbe}`,
							repair,
						},
					],
				},
			};
		}
	}
	const invokedScripts = invokedSkillScripts(command);
	if (invokedScripts.length === 0) {
		return { ok: true };
	}
	if (!currentUnit) {
		return { ok: true };
	}
	const allowed = new Set<string>();
	if (currentUnit.gateScript) {
		allowed.add(`scripts/${currentUnit.gateScript}`);
	}
	for (const helperScript of currentUnit.helperScripts ?? []) {
		allowed.add(`scripts/${helperScript}`);
	}
	const disallowed = invokedScripts.find((script) => !allowed.has(script));
	const invokedScript = disallowed ?? invokedScripts[0];
	if (!disallowed) {
		if (command.includes(".venv/bin/python")) {
			const repair = skillScriptRepair(ctx, currentUnit, invokedScript);
			return {
				ok: false,
				repair,
				state_patch: {
					phase: phaseFor(currentUnit, "blocked"),
					message: `Blocked model runtime Python for harness script: ${invokedScript}`,
					counters: countersFor(checkpoint.data, 1),
					diagnostics: [
						{
							severity: "error",
							message: `Harness script ${invokedScript} was invoked with model-local .venv Python.`,
							repair,
						},
					],
				},
			};
		}
		if (
			!cwdIsSkillPackage(ctx) &&
			!commandUsesSkillScriptPath(ctx, command, invokedScript) &&
			!commandMentionsCdTo(command, ctx.packageDir, ctx.cwd)
		) {
			const repair = skillScriptRepair(ctx, currentUnit, invokedScript);
			return {
				ok: false,
				repair,
				state_patch: {
					phase: phaseFor(currentUnit, "blocked"),
					message: `Blocked harness script from wrong working directory: ${invokedScript}`,
					counters: countersFor(checkpoint.data, 1),
					diagnostics: [
						{
							severity: "error",
							message: `Harness script ${invokedScript} must run from the skill package or via its package path.`,
							repair,
						},
					],
				},
			};
		}
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
	if (unit.id === "validate_infer") {
		const fixtureReady = validateInferFixturePrerequisite(ctx);
		if (fixtureReady && !fixtureReady.ok) {
			return fixtureReady;
		}
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
	const unchangedFailure = unchangedFailedArtifact(ctx, currentUnit, checkpoint);
	if (unchangedFailure) {
		return unchangedFailure;
	}
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
	// Honor the user's max_retries parameter (default 3). Read from ctx.args so a
	// run started with /sure_onboard ... max_retries=5 actually gets 5 attempts.
	const args = parseArgs(ctx.args);
	const maxRetries = args.max_retries ? Number.parseInt(args.max_retries, 10) : undefined;
	const effectiveMax = Number.isFinite(maxRetries) && (maxRetries ?? 0) > 0 ? maxRetries : undefined;
	if (retryExhausted(unit, next.data, effectiveMax)) {
		return failure(
			`${repair} Blocked because: ${reason}. After ${attempts} consecutive blocked attempts, /sure_onboard still cannot produce a valid artifact for unit "${unit.id}". If the blocking reason above points at the model_input_path or repo link rather than the artifact you wrote, ask the user to confirm access permissions and whether the referenced documentation covers install, load, inference, and artifacts.`,
			`Gate "${unit.id}" exhausted ${attempts} blocked attempts: ${reason}`,
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
	const terminalArtifact = readArtifact(ctx, LAST_UNIT.produces);
	if (!terminalArtifact) {
		return failure(
			`Produce ${LAST_UNIT.produces} under the run artifacts directory before calling sure_finish.`,
			"Missing finalized bundle artifact.",
			countersFor(checkpoint.data, 0),
		);
	}
	const producesResult = validateProduces(ctx, LAST_UNIT, terminalArtifact);
	if (!producesResult.ok) {
		return failure(
			producesResult.repair ?? "Final verdict invalid.",
			`Terminal unit "${LAST_UNIT.id}" produces invalid: ${producesResult.reason ?? ""}`,
			countersFor(checkpoint.data, 1),
		);
	}
	const finishStatus = isRecord(ctx.event) && isRecord(ctx.event.finish) ? ctx.event.finish.status : undefined;
	if (finishStatus !== "success") {
		const packageGate = readArtifact(ctx, "package_gate.json");
		const incompleteError = incompleteDeploymentError(terminalArtifact, packageGate, finishStatus);
		if (incompleteError) {
			return failure(
				incompleteError,
				"Invalid non-success Onboard terminal evidence.",
				countersFor(checkpoint.data, 1),
			);
		}
		return {
			ok: true,
			state_patch: {
				phase: {
					id: "finish",
					label: "SURE model-tool stopped before registry delivery",
					status: finishStatus as "failed" | "incomplete",
					progress: 1,
				},
				message: "Local model and container evidence retained; no Eval-ready deployment was claimed.",
				counters: countersFor(checkpoint.data, 1),
				artifacts: [
					{
						type: "deployment_ready",
						name: "Blocked deployment marker",
						path: `.sure/runs/${ctx.run.runId}/artifacts/${LAST_UNIT.produces}`,
						// The marker itself says blocked; the display only knows the four
						// statuses in ARTIFACT_STATUSES, and an unknown one drops the patch.
						status: "incomplete",
						summary: "Container validation evidence is present, but registry delivery is incomplete.",
					},
				],
			},
		};
	}
	// Final backstop: re-run the finalized-bundle gate so mutations after the
	// normal state transition cannot bypass deployment readiness checks.
	const gateResult = LAST_UNIT.gateScript ? runGateScript(ctx, LAST_UNIT) : { ok: true };
	if (gateResult && !gateResult.ok) {
		return failure(
			gateResult.repair ?? "Final gate failed.",
			`SURE onboard terminal gate "${LAST_UNIT.id}" rejected the finish.`,
			countersFor(checkpoint.data, 1),
		);
	}
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
	const completedUnits = checkpoint.data.completedUnits.includes(LAST_UNIT.id)
		? checkpoint.data.completedUnits
		: [...checkpoint.data.completedUnits, LAST_UNIT.id];
	return {
		ok: true,
		state_patch: {
			phase: { id: LAST_UNIT.id, label: LAST_UNIT.label, status: "success", progress: 1 },
			message: "SURE model-tool run validated.",
			counters: countersFor(
				{
					currentUnit: LAST_UNIT.id,
					completedUnits,
					retries: checkpoint.data.retries,
					failedArtifactDigests: checkpoint.data.failedArtifactDigests,
				},
				0,
			),
			artifacts: [
				{
					type: "verdict",
					name: "SURE verdict",
					path: `.sure/runs/${ctx.run.runId}/artifacts/verdict.json`,
					status: "ready",
					summary: "Validated SURE model-tool verdict.",
				},
				{
					type: "deployment_ready",
					name: "SURE deployment binding",
					path: `.sure/runs/${ctx.run.runId}/artifacts/${LAST_UNIT.produces}`,
					status: "ready",
					summary: "Finalized model bundle with immutable deployment binding.",
				},
			],
		},
	};
}

export function incompleteDeploymentError(
	deployment: unknown,
	packageGate: unknown,
	finishStatus: unknown,
): string | undefined {
	if (finishStatus !== "incomplete" && finishStatus !== "failed") {
		return "non-success Onboard finish must declare status=incomplete or status=failed";
	}
	if (!isRecord(deployment) || (deployment.status !== "blocked" && deployment.status !== "local_only")) {
		return "non-success Onboard requires deployment_ready.status=blocked or local_only";
	}
	if (!isRecord(packageGate) || !isRecord(packageGate.readiness)) {
		return "non-success Onboard requires structured package_gate readiness evidence";
	}
	if (packageGate.status !== "passed" && packageGate.status !== "blocked" && packageGate.status !== "failed") {
		return "non-success Onboard package_gate.status must be passed, blocked, or failed";
	}
	if (packageGate.bundle_ready !== false && packageGate.readiness.bundle_ready !== false) {
		return "non-success Onboard must prove bundle_ready=false";
	}
	if (packageGate.readiness.registry_ready !== false) {
		return "non-success Onboard must prove registry_ready=false";
	}
	const policy = deployment.execution_policy;
	if (!isRecord(policy) || policy.container_only !== false) {
		return "blocked deployment marker must not claim container_only Eval readiness";
	}
	return undefined;
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
