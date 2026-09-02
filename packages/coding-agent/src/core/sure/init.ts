import { execSync } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { KnownProvider, ModelThinkingLevel, OAuthLoginCallbacks, OAuthProviderId } from "@earendil-works/pi-ai";
import { getModels } from "@earendil-works/pi-ai/compat";
import { getModelsPath } from "../../config.ts";
import type { ExtensionCommandContext } from "../extensions/types.ts";
import type { ModelRegistry } from "../model-registry.ts";
import { SettingsManager } from "../settings-manager.ts";
import { applyProbedModel, compatRetreatFor, probeWholeGateway, verifyModelRoundTrip } from "./init-apply.ts";
import type { probeModelCapability } from "./init-capability-probe.ts";
import type { GatewayProviderSummary } from "./init-gateway-store.ts";
import {
	listGatewayProviders,
	mergeProviderCompat,
	readGatewayModels,
	writeGatewayProvider,
} from "./init-gateway-store.ts";
import type { InitMenuEntry } from "./init-menu.ts";
import { buildInitMenu, findMenuEntry, isReservedProviderName, menuEntryLabel } from "./init-menu.ts";
import type { ListedModel, ModelListing } from "./init-model-listing.ts";
import { describeListingSource, fetchOpenAICompatibleModels, listBuiltInProviderModels } from "./init-model-listing.ts";
import type {
	ProbeStep,
	SureInitArgs,
	SureInitManifest,
	SureInitProviderOption,
	SureInitResult,
} from "./init-types.ts";
import { discoverSureSkillPackages } from "./manifest.ts";

export const SURE_INIT_VERSION = 3;

/** Supported agent/provider options for /sure_init. */
export const SURE_INIT_PROVIDER_OPTIONS: SureInitProviderOption[] = [
	{
		id: "codex",
		name: "OpenAI Codex",
		provider: "openai-codex",
		defaultModel: "gpt-5.5",
		authType: "oauth",
		description: "ChatGPT Plus/Pro subscription coding agent",
	},
	{
		id: "kimi-code",
		name: "Kimi Code",
		provider: "kimi-coding",
		defaultModel: "kimi-for-coding",
		authType: "api_key",
		description: "Kimi dedicated coding endpoint",
	},
	{
		id: "copilot",
		name: "GitHub Copilot",
		provider: "github-copilot",
		defaultModel: "claude-opus-4.6",
		authType: "oauth",
		description: "GitHub Copilot subscription (Claude / GPT)",
	},
	{
		id: "claude",
		name: "Anthropic Claude",
		provider: "anthropic",
		defaultModel: "claude-opus-4-8",
		authType: "api_key",
		description: "Standard Anthropic API",
	},
	{
		id: "openai",
		name: "OpenAI GPT",
		provider: "openai",
		// Measured 2026-08-17: gpt-5.6-luna answers 403 through a relay (anti-forwarding
		// check), so it cannot be the default. Sol answers on the responses protocol.
		defaultModel: "gpt-5.6-sol",
		authType: "api_key",
		description: "Standard OpenAI API",
	},
	{
		id: "azure-openai",
		name: "Azure OpenAI (Responses)",
		provider: "azure-openai-responses",
		defaultModel: "gpt-5.5",
		authType: "api_key",
		description: "Azure OpenAI Responses API",
	},
];

/** True when models.json has to carry this model's protocol because pi-ai's catalog does not. */
export function needsCapabilityProbe(provider: string, modelId: string): boolean {
	try {
		return !getModels(provider as KnownProvider).some((model) => model.id === modelId);
	} catch {
		return true;
	}
}

/** Parse /sure_init command arguments. */
export function parseInitArgs(raw: string): SureInitArgs {
	const args = raw.trim().split(/\s+/).filter(Boolean);
	const result: SureInitArgs = {};
	for (let i = 0; i < args.length; i++) {
		const arg = args[i];
		if (arg === "--option" || arg === "--provider") {
			result.optionId = args[++i];
		} else if (arg === "--api-key") {
			result.apiKey = args[++i];
		} else if (arg === "--model") {
			result.model = args[++i];
		} else if (arg === "--name") {
			result.gatewayName = args[++i];
		} else if (arg === "--base-url") {
			result.gatewayBaseUrl = args[++i];
		} else if (arg === "--effort") {
			result.effort = args[++i];
		} else if (arg === "--probe-all") {
			result.probeAll = true;
		}
	}
	return result;
}

async function runOAuthLogin(
	option: SureInitProviderOption,
	modelRegistry: ModelRegistry,
	ctx: ExtensionCommandContext,
): Promise<{ ok: boolean; message?: string }> {
	if (!ctx.hasUI) {
		return {
			ok: false,
			message: `Please run /login ${option.provider} to authenticate with ${option.name}, then run /sure_init again.`,
		};
	}

	const providerInfo = modelRegistry.authStorage.getOAuthProviders().find((p) => p.id === option.provider);
	if (!providerInfo) {
		return {
			ok: false,
			message: `OAuth provider ${option.name} is not registered. Run /login ${option.provider} manually, then run /sure_init again.`,
		};
	}

	let manualCodePromise: Promise<string> | undefined;
	let authUrl: string | undefined;

	const callbacks: OAuthLoginCallbacks = {
		onAuth: (info) => {
			authUrl = info.url;
			const lines = [`Open this URL in your browser to authenticate ${option.name}:`, info.url];
			if (info.instructions) {
				lines.push(info.instructions);
			}
			ctx.ui.notify(lines.join("\n"), "info");
		},
		onDeviceCode: (info) => {
			ctx.ui.notify(`Device code for ${option.name}: ${info.userCode}\nVisit: ${info.verificationUri}`, "info");
		},
		onPrompt: async (prompt) => {
			const value = await ctx.ui.input(prompt.message, prompt.placeholder);
			if (value === undefined) {
				throw new Error("Login cancelled");
			}
			return value;
		},
		onSelect: async (prompt) => {
			const labels = prompt.options.map((o) => o.label);
			const selected = await ctx.ui.select(prompt.message, labels);
			if (selected === undefined) {
				return undefined;
			}
			return prompt.options.find((o) => o.label === selected)?.id;
		},
		onProgress: (message) => {
			ctx.ui.notify(message, "info");
		},
		onManualCodeInput: () => {
			if (!manualCodePromise) {
				manualCodePromise = new Promise<string>((resolve, reject) => {
					const prompt = authUrl
						? `Open ${authUrl}\nPaste the redirect URL here when done:`
						: "Paste the redirect URL here when done:";
					ctx.ui.input(prompt).then((value) => {
						if (value) {
							resolve(value);
						} else {
							reject(new Error("Login cancelled"));
						}
					});
				});
			}
			return manualCodePromise;
		},
	};

	try {
		await modelRegistry.authStorage.login(option.provider as OAuthProviderId, callbacks);
		modelRegistry.refresh();
		return { ok: true };
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		if (message === "Login cancelled") {
			return { ok: false, message: `OAuth login for ${option.name} was cancelled.` };
		}
		return { ok: false, message: `OAuth login failed for ${option.name}: ${message}` };
	}
}

async function ensureAuth(
	option: SureInitProviderOption,
	modelRegistry: ModelRegistry,
	ctx: ExtensionCommandContext,
	args: SureInitArgs,
): Promise<{ ok: boolean; message?: string }> {
	const hasConfiguredAuth = modelRegistry.hasConfiguredAuth({
		provider: option.provider,
		id: option.defaultModel,
	} as any);
	if (!args.apiKey?.trim() && hasConfiguredAuth) {
		return { ok: true };
	}

	if (option.authType === "oauth") {
		return runOAuthLogin(option, modelRegistry, ctx);
	}

	let apiKey = args.apiKey?.trim();
	if (!apiKey) {
		if (!ctx.hasUI) {
			return {
				ok: false,
				message: `No API key configured for ${option.name}. Run /sure_init --option ${option.id} --api-key <key>.`,
			};
		}
		apiKey = await ctx.ui.input(`Enter your ${option.name} API key:`);
	}

	if (!apiKey?.trim()) {
		return { ok: false, message: "API key is required." };
	}

	modelRegistry.authStorage.set(option.provider, { type: "api_key", key: apiKey.trim() });
	return { ok: true };
}

function checkPythonEnvironment(): { ok: boolean; details: string[] } {
	const details: string[] = [];
	try {
		const version = execSync("python3 --version", { encoding: "utf-8", stdio: ["pipe", "pipe", "ignore"] }).trim();
		details.push(version);
	} catch {
		return { ok: false, details: ["python3 not found"] };
	}
	return { ok: true, details };
}

function writeInitManifest(cwd: string, manifest: SureInitManifest): string {
	const dir = join(cwd, ".sure");
	if (!existsSync(dir)) {
		mkdirSync(dir, { recursive: true });
	}
	const path = join(dir, "init.json");
	writeFileSync(path, `${JSON.stringify(manifest, null, 2)}\n`, "utf-8");
	return path;
}

const NON_INTERACTIVE_MODEL_ERROR = "Non-interactive /sure_init requires --model <model-id>.";

/** Probe verdicts as they read on the 跳过 line of a --probe-all summary. */
const SKIP_REASON_LABELS: Record<string, string> = {
	"no-channel": "没渠道",
	"client-rejected": "拒绝客户端",
	"no-protocol": "协议不通",
	flaky: "时通时不通",
	unreachable: "连不上",
	"bad-key": "key 无效",
};

async function pickModelId(
	ctx: ExtensionCommandContext,
	listing: ModelListing,
	providerLabel: string,
	preferredModelIds: string[] = [],
): Promise<string | undefined> {
	if (listing.source === "manual" && listing.models.length === 1) {
		return listing.models[0].id;
	}
	ctx.ui.notify(describeListingSource(listing), listing.source === "live" ? "info" : "warning");
	const models =
		preferredModelIds.length > 0
			? [...listing.models].sort((a, b) => {
					const aIndex = preferredModelIds.indexOf(a.id);
					const bIndex = preferredModelIds.indexOf(b.id);
					const aRank = aIndex < 0 ? preferredModelIds.length : aIndex;
					const bRank = bIndex < 0 ? preferredModelIds.length : bIndex;
					return aRank - bRank;
				})
			: listing.models;
	const choices = models.map((model) => (model.name ? `${model.id} — ${model.name}` : model.id));
	const selected = await ctx.ui.select(`Select the model for ${providerLabel}:`, choices);
	if (selected === undefined) return undefined;
	return models[choices.indexOf(selected)]?.id;
}

interface GatewayOutcome {
	ok: boolean;
	message?: string;
	providerName?: string;
	modelId?: string;
	listing?: ModelListing;
	wroteModelsJson?: boolean;
	baseUrl?: string;
	/** The key this run settled on, so the probe uses it even before the registry sees it. */
	apiKey?: string;
}

async function initExistingGateway(
	ctx: ExtensionCommandContext,
	gateway: GatewayProviderSummary,
	args: SureInitArgs,
	modelsJsonPath: string,
): Promise<GatewayOutcome> {
	// A cached hit used to short-circuit here. It cannot any more: the protocol and the
	// effort table have to come from a live probe, and the probe needs a key.
	const storedKey = await ctx.modelRegistry.getApiKeyForProvider(gateway.name);
	let typedKey: string | undefined;
	if (!args.apiKey?.trim() && !storedKey && ctx.hasUI) {
		typedKey = (await ctx.ui.input(`Enter the API key for ${gateway.name}:`))?.trim();
	}
	// An explicit --api-key outranks what is on disk. Every failure message tells the user to
	// rerun /sure_init with another key, and that only works if the new one replaces the old.
	const apiKey = args.apiKey?.trim() || storedKey || typedKey;
	if (!apiKey) {
		return {
			ok: false,
			message: `No API key stored for ${gateway.name}, and the capability probe needs one. Pass --api-key <key>.`,
		};
	}
	const keyIsNew = apiKey !== storedKey;
	if (args.model) {
		// ModelRegistry only resolves custom-provider models from models.json, so an id missing
		// from the list would fall back to another model at startup. Append it and persist.
		const cached = readGatewayModels(gateway.name, modelsJsonPath);
		const models = cached.some((model) => model.id === args.model) ? cached : [...cached, { id: args.model }];
		writeGatewayProvider(
			{ name: gateway.name, baseUrl: gateway.baseUrl, apiKey: keyIsNew ? apiKey : undefined, models },
			modelsJsonPath,
		);
		return {
			ok: true,
			providerName: gateway.name,
			modelId: args.model,
			baseUrl: gateway.baseUrl,
			wroteModelsJson: true,
			apiKey,
		};
	}
	let listing: ModelListing;
	try {
		const models = await fetchOpenAICompatibleModels(gateway.baseUrl, apiKey);
		listing = { source: "live", models };
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		const cached = readGatewayModels(gateway.name, modelsJsonPath);
		if (cached.length === 0) {
			return {
				ok: false,
				message: `Live model query for ${gateway.name} failed (${message}) and no cached model list exists.`,
			};
		}
		listing = { source: "cached", models: cached, error: message };
	}

	const modelId = await pickModelId(ctx, listing, gateway.name);
	if (!modelId) return { ok: false, message: "No model selected." };

	let wroteModelsJson = false;
	if (listing.source === "live") {
		// Write failures (e.g. a comment-bearing models.json) must propagate as a hard failure,
		// not be swallowed as if the live query itself had failed.
		writeGatewayProvider(
			{
				name: gateway.name,
				baseUrl: gateway.baseUrl,
				apiKey: keyIsNew ? apiKey : undefined,
				models: listing.models,
			},
			modelsJsonPath,
		);
		wroteModelsJson = true;
	} else if (keyIsNew) {
		// A cached fallback used to drop a freshly typed key on the floor. The store merges by
		// id, so writing the same cached list back keeps every annotation and adds the key.
		writeGatewayProvider(
			{ name: gateway.name, baseUrl: gateway.baseUrl, apiKey, models: listing.models },
			modelsJsonPath,
		);
		wroteModelsJson = true;
	}
	return {
		ok: true,
		providerName: gateway.name,
		modelId,
		listing,
		wroteModelsJson,
		baseUrl: gateway.baseUrl,
		apiKey,
	};
}

async function initNewGateway(
	ctx: ExtensionCommandContext,
	args: SureInitArgs,
	modelsJsonPath: string,
): Promise<GatewayOutcome> {
	if (!ctx.hasUI) {
		const missing: string[] = [];
		if (!args.gatewayName) missing.push("--name");
		if (!args.gatewayBaseUrl) missing.push("--base-url");
		if (!args.apiKey) missing.push("--api-key");
		if (!args.model) missing.push("--model");
		if (missing.length > 0 || !args.gatewayName || !args.gatewayBaseUrl || !args.apiKey || !args.model) {
			return { ok: false, message: `Non-interactive gateway setup is missing: ${missing.join(", ")}.` };
		}
		const name = args.gatewayName.trim();
		if (isReservedProviderName(name, SURE_INIT_PROVIDER_OPTIONS)) {
			return { ok: false, message: `Provider name "${name}" is reserved (built-in provider or menu id).` };
		}
		let models: ListedModel[];
		try {
			models = await fetchOpenAICompatibleModels(args.gatewayBaseUrl, args.apiKey);
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			return { ok: false, message: `Model query against ${args.gatewayBaseUrl} failed: ${message}` };
		}
		if (!models.some((model) => model.id === args.model)) {
			models = [...models, { id: args.model }];
		}
		writeGatewayProvider({ name, baseUrl: args.gatewayBaseUrl, apiKey: args.apiKey, models }, modelsJsonPath);
		return {
			ok: true,
			providerName: name,
			modelId: args.model,
			listing: { source: "live", models },
			wroteModelsJson: true,
			baseUrl: args.gatewayBaseUrl,
			apiKey: args.apiKey,
		};
	}

	let name: string | undefined;
	while (true) {
		name = (await ctx.ui.input("Name for the new provider (e.g. myrelay):"))?.trim();
		if (!name) return { ok: false, message: "Gateway setup cancelled." };
		if (isReservedProviderName(name, SURE_INIT_PROVIDER_OPTIONS)) {
			ctx.ui.notify(`"${name}" is reserved (built-in provider or menu id). Pick another name.`, "error");
			continue;
		}
		const existing = listGatewayProviders(modelsJsonPath).find((gateway) => gateway.name === name);
		if (existing) {
			const update = await ctx.ui.confirm(
				"Provider already exists",
				`"${name}" already points at ${existing.baseUrl}. Update its URL, key, and model list?`,
			);
			if (!update) continue;
		}
		break;
	}
	const baseUrl = (
		await ctx.ui.input("Gateway base URL (OpenAI-compatible, e.g. https://gw.example.com/v1):")
	)?.trim();
	if (!baseUrl) return { ok: false, message: "Gateway setup cancelled." };
	const apiKey = (await ctx.ui.input(`API key for ${name}:`))?.trim();
	if (!apiKey) return { ok: false, message: "Gateway setup cancelled." };

	let listing: ModelListing | undefined;
	while (!listing) {
		try {
			listing = { source: "live", models: await fetchOpenAICompatibleModels(baseUrl, apiKey) };
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			const action = await ctx.ui.select(`Model query failed: ${message}`, [
				"Retry",
				"Enter a model id manually",
				"Cancel",
			]);
			if (action === "Retry") continue;
			if (action === "Enter a model id manually") {
				const manualId = (await ctx.ui.input("Model id:"))?.trim();
				if (!manualId) return { ok: false, message: "Gateway setup cancelled." };
				listing = { source: "manual", models: [{ id: manualId }] };
			} else {
				return { ok: false, message: "Gateway setup cancelled." };
			}
		}
	}
	const modelId = await pickModelId(ctx, listing, name);
	if (!modelId) return { ok: false, message: "No model selected." };
	writeGatewayProvider({ name, baseUrl, apiKey, models: listing.models }, modelsJsonPath);
	return { ok: true, providerName: name, modelId, listing, wroteModelsJson: true, baseUrl, apiKey };
}

export interface RunSureInitOptions {
	ctx: ExtensionCommandContext;
	args?: string;
	settingsManager?: SettingsManager;
	/** Override for tests; defaults to the agent-dir models.json. */
	modelsJsonPath?: string;
	/** Override for tests; defaults to the real capability probe. */
	probe?: typeof probeModelCapability;
	/** Override for tests; defaults to the real round-trip check. */
	verify?: typeof verifyModelRoundTrip;
}

/** Main entry point for /sure_init. */
export async function runSureInit(options: RunSureInitOptions): Promise<SureInitResult> {
	const { ctx } = options;
	const args = parseInitArgs(options.args ?? "");
	const modelsJsonPath = options.modelsJsonPath ?? getModelsPath();

	if (!ctx.isProjectTrusted()) {
		return {
			success: false,
			message: "Project is not trusted. Run /trust first, then /sure_init.",
			nextAction: "/trust",
		};
	}

	const menu = buildInitMenu(SURE_INIT_PROVIDER_OPTIONS, listGatewayProviders(modelsJsonPath));
	for (const skipped of menu.skippedGateways) {
		ctx.ui.notify(
			`models.json provider "${skipped}" collides with a built-in name and is not selectable here.`,
			"warning",
		);
	}

	let entry: InitMenuEntry | undefined;
	if (args.optionId) {
		entry = findMenuEntry(menu, args.optionId);
		if (!entry) {
			const builtInIds = SURE_INIT_PROVIDER_OPTIONS.map((option) => option.id).join(", ");
			return {
				success: false,
				message: `Unknown option "${args.optionId}". Use a built-in id (${builtInIds}), a models.json provider name, or "custom".`,
			};
		}
	} else if (ctx.hasUI) {
		const labels = menu.entries.map((menuEntry) => menuEntryLabel(menuEntry));
		const selected = await ctx.ui.select("Select the agent to use for SURE:", labels);
		if (selected === undefined) {
			return { success: false, message: "No agent selected." };
		}
		entry = menu.entries[labels.indexOf(selected)];
	}
	if (!entry) {
		return {
			success: false,
			message: "No agent selected. Run /sure_init in an interactive UI, or use --option <id>.",
		};
	}

	let providerKey: string;
	let providerLabel: string;
	let modelId: string | undefined;
	let listing: ModelListing | undefined;
	let refreshRegistry = false;
	let gatewayBaseUrl: string | undefined;
	let gatewayApiKey: string | undefined;

	if (entry.kind === "builtin") {
		const option = entry.option;
		if (!args.model && !ctx.hasUI) {
			return { success: false, message: NON_INTERACTIVE_MODEL_ERROR };
		}
		const authResult = await ensureAuth(option, ctx.modelRegistry, ctx, args);
		if (!authResult.ok) {
			return { success: false, message: authResult.message ?? `Authentication failed for ${option.name}.` };
		}
		if (args.model) {
			modelId = args.model;
		} else {
			listing = await listBuiltInProviderModels(option, ctx.modelRegistry);
			if (listing.models.length === 0) {
				return { success: false, message: `No models known for ${option.name}.` };
			}
			modelId = await pickModelId(ctx, listing, option.name, [option.defaultModel]);
			if (!modelId) {
				return { success: false, message: "No model selected." };
			}
		}
		providerKey = option.provider;
		providerLabel = option.name;
	} else if (entry.kind === "gateway") {
		if (!args.model && !ctx.hasUI) {
			return { success: false, message: NON_INTERACTIVE_MODEL_ERROR };
		}
		let outcome: GatewayOutcome;
		try {
			outcome = await initExistingGateway(ctx, entry.gateway, args, modelsJsonPath);
		} catch (error) {
			return { success: false, message: error instanceof Error ? error.message : String(error) };
		}
		if (!outcome.ok || !outcome.providerName || !outcome.modelId) {
			return { success: false, message: outcome.message ?? "Gateway initialization failed." };
		}
		providerKey = outcome.providerName;
		providerLabel = outcome.providerName;
		modelId = outcome.modelId;
		gatewayBaseUrl = outcome.baseUrl;
		gatewayApiKey = outcome.apiKey;
		listing = outcome.listing;
		refreshRegistry = outcome.wroteModelsJson === true;
	} else {
		let outcome: GatewayOutcome;
		try {
			outcome = await initNewGateway(ctx, args, modelsJsonPath);
		} catch (error) {
			return { success: false, message: error instanceof Error ? error.message : String(error) };
		}
		if (!outcome.ok || !outcome.providerName || !outcome.modelId) {
			return { success: false, message: outcome.message ?? "Gateway setup failed." };
		}
		providerKey = outcome.providerName;
		providerLabel = outcome.providerName;
		modelId = outcome.modelId;
		gatewayBaseUrl = outcome.baseUrl;
		gatewayApiKey = outcome.apiKey;
		listing = outcome.listing;
		refreshRegistry = outcome.wroteModelsJson === true;
	}

	if (refreshRegistry) {
		ctx.modelRegistry.refresh();
	}

	let probedApi: string | undefined;
	let thinkingLevel: ModelThinkingLevel | undefined;
	let supportedLevels: ModelThinkingLevel[] | undefined;
	let probeSteps: ProbeStep[] | undefined;
	let effortNote: string | undefined;
	let probeNotes: string[] = [];

	if (needsCapabilityProbe(providerKey, modelId)) {
		const baseUrl =
			entry.kind === "gateway"
				? entry.gateway.baseUrl
				: (ctx.modelRegistry.getAll().find((model) => model.provider === providerKey)?.baseUrl ?? gatewayBaseUrl);
		if (!baseUrl) {
			return { success: false, message: `不知道 ${providerKey}/${modelId} 的 base URL,探不了。` };
		}
		const apiKey = gatewayApiKey ?? (await ctx.modelRegistry.getApiKeyForProvider(providerKey)) ?? args.apiKey;
		// The probe takes 8 to 30 seconds. Say what is happening; the final message replaces this.
		ctx.ui.notify(`探测 ${providerKey}/${modelId} …`, "info");
		const applied = await applyProbedModel({
			ctx,
			providerName: providerKey,
			baseUrl,
			apiKey,
			modelId,
			modelName: listing?.models.find((model) => model.id === modelId)?.name,
			modelsJsonPath,
			requestedEffort: args.effort,
			probe: options.probe,
		});
		if (!applied.ok) {
			return { success: false, message: applied.message ?? `Capability probe failed for ${modelId}.` };
		}
		probeNotes = applied.notes;
		probedApi = applied.api;
		thinkingLevel = applied.thinkingLevel;
		supportedLevels = applied.supportedLevels;
		probeSteps = applied.steps;
		effortNote = applied.effortNote;
	} else if (args.effort) {
		return {
			success: false,
			message: `--effort 只对探测过的模型有效;${providerKey}/${modelId} 在内置目录里,不用探。`,
		};
	}

	// Nothing below may point a session at this model until the real round trip proves it
	// answers: on 2026-08-18 the relay let the probe through and then refused the first
	// real request, which used to leave a persisted default nobody could use.
	if (probedApi) {
		const verify = options.verify ?? verifyModelRoundTrip;
		const tried = new Set<string>();
		const settled: string[] = [];
		let verified: { ok: boolean; detail?: string };
		for (;;) {
			try {
				verified = await verify({
					registry: ctx.modelRegistry,
					provider: providerKey,
					modelId,
					thinkingLevel,
				});
			} catch (error) {
				return {
					success: false,
					message: `${providerKey}/${modelId} 真发一句话时出错:${error instanceof Error ? error.message : String(error)}`,
				};
			}
			if (verified.ok) break;
			// A relay that names what it refused is saying how it wants to be talked to. Record
			// that against the provider and ask again. A failure that names nothing — a dead key,
			// a missing channel, a bad day — ends it, because guessing there writes a wrong
			// setting nobody goes looking for again. Each setting is tried once, so this stops.
			const retreat = compatRetreatFor(verified.detail ?? "", tried);
			if (!retreat) break;
			tried.add(retreat.field);
			mergeProviderCompat(providerKey, { [retreat.field]: retreat.value }, modelsJsonPath);
			ctx.modelRegistry.refresh();
			settled.push(retreat.why);
		}
		const settledNotes = settled.map((why) => `兼容位已记进 models.json:${why}`);
		if (!verified.ok) {
			return {
				success: false,
				message: [
					`${providerKey}/${modelId} 的探测结果:`,
					...probeNotes,
					...settledNotes,
					`模型标注已写进 models.json,但真发一句话没通过:${verified.detail}。默认模型没有切换。`,
				].join("\n"),
			};
		}
		probeNotes = [...probeNotes, ...settledNotes];
	}

	const probeAllLines: string[] = [];
	if (args.probeAll) {
		if (entry.kind === "builtin") {
			return { success: false, message: "--probe-all 只对中转站有效,内置目录里的模型不需要探测。" };
		}
		const baseUrl = entry.kind === "gateway" ? entry.gateway.baseUrl : gatewayBaseUrl;
		if (!baseUrl) {
			return { success: false, message: `不知道 ${providerKey} 的 base URL,整表探不了。` };
		}
		const apiKey = gatewayApiKey ?? (await ctx.modelRegistry.getApiKeyForProvider(providerKey)) ?? args.apiKey;
		const ids = readGatewayModels(providerKey, modelsJsonPath).map((model) => model.id);
		try {
			const all = await probeWholeGateway({
				providerName: providerKey,
				baseUrl,
				apiKey,
				modelIds: ids.filter((id) => id !== modelId),
				modelsJsonPath,
				probe: options.probe,
			});
			ctx.modelRegistry.refresh();
			if (!all.ok) {
				probeAllLines.push(`整表探测中止:${all.message ?? "整表探测失败。"}`);
			}
			probeAllLines.push(`整表探测:标注 ${all.annotated.length} 个,跳过 ${all.skipped.length} 个`);
			if (all.skipped.length > 0) {
				probeAllLines.push(
					`跳过:${all.skipped
						.map(
							(skip) =>
								`${skip.modelId}:${SKIP_REASON_LABELS[skip.reason] ?? skip.reason}${skip.detail ? `(${skip.detail})` : ""}`,
						)
						.join("、")}`,
				);
			}
		} catch (error) {
			probeAllLines.push(`整表探测出错:${error instanceof Error ? error.message : String(error)}`);
		}
	}

	const settings = options.settingsManager ?? SettingsManager.create(ctx.cwd);
	settings.setDefaultModelAndProvider(providerKey, modelId);
	if (thinkingLevel) {
		settings.setDefaultThinkingLevel(thinkingLevel);
	}

	const discovered = discoverSureSkillPackages(ctx.cwd);
	const availableSkills = discovered.packages.map((pkg) => `/${pkg.manifest.command}`);

	const python = checkPythonEnvironment();

	const manifest: SureInitManifest = {
		initializedAt: new Date().toISOString(),
		defaultProvider: providerKey,
		defaultModel: modelId,
		...(probedApi ? { defaultApi: probedApi } : {}),
		...(thinkingLevel ? { defaultThinkingLevel: thinkingLevel } : {}),
		...(supportedLevels ? { supportedThinkingLevels: supportedLevels } : {}),
		...(probeSteps
			? { capabilityProbe: { probedAt: new Date().toISOString(), steps: probeSteps, effortNote: effortNote ?? "" } }
			: {}),
		trusted: true,
		pythonOk: python.ok,
		availableSkills,
		version: SURE_INIT_VERSION,
	};

	const manifestPath = writeInitManifest(ctx.cwd, manifest);

	const modelCommand = `/model ${providerKey}/${modelId}${thinkingLevel ? `:${thinkingLevel}` : ""}`;
	// The TUI shows only the last notification, so everything worth reading goes here: the
	// notes, the round-trip verdict and the table summary all reach print/json callers too.
	const lines = [`SURE initialized for ${providerLabel} (${providerKey}/${modelId}).`, ...probeNotes];
	if (probedApi) {
		lines.push(`真实通路验证通过:${providerKey}/${modelId}`);
	}
	lines.push(...probeAllLines, `Manifest written to ${manifestPath}.`);
	if (listing) {
		lines.push(describeListingSource(listing));
	}
	if (!python.ok) {
		lines.push("Warning: Python backend check failed. Some SURE skills may not work.");
	}
	if (discovered.diagnostics.length > 0) {
		lines.push("Discovery diagnostics:");
		for (const diagnostic of discovered.diagnostics) {
			lines.push(`  - ${diagnostic.message}`);
		}
	}
	if (availableSkills.length > 0) {
		lines.push(`Available SURE commands: ${availableSkills.join(", ")}`);
	}

	return {
		success: true,
		manifest,
		message: lines.join("\n"),
		nextAction: modelCommand,
	};
}
