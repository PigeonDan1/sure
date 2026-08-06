import { execSync } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { OAuthLoginCallbacks, OAuthProviderId } from "@earendil-works/pi-ai";
import { getModelsPath } from "../../config.ts";
import type { ExtensionCommandContext } from "../extensions/types.ts";
import type { ModelRegistry } from "../model-registry.ts";
import { SettingsManager } from "../settings-manager.ts";
import type { GatewayProviderSummary } from "./init-gateway-store.ts";
import { listGatewayProviders, readGatewayModels, writeGatewayProvider } from "./init-gateway-store.ts";
import type { InitMenuEntry } from "./init-menu.ts";
import { buildInitMenu, findMenuEntry, isReservedProviderName, menuEntryLabel } from "./init-menu.ts";
import type { ListedModel, ModelListing } from "./init-model-listing.ts";
import { describeListingSource, fetchOpenAICompatibleModels, listBuiltInProviderModels } from "./init-model-listing.ts";
import type { SureInitArgs, SureInitManifest, SureInitProviderOption, SureInitResult } from "./init-types.ts";
import { discoverSureSkillPackages } from "./manifest.ts";

export const SURE_INIT_VERSION = 1;

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
		defaultModel: "gpt-5.5",
		authType: "api_key",
		description: "Standard OpenAI API",
	},
];

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
	if (modelRegistry.hasConfiguredAuth({ provider: option.provider, id: option.defaultModel } as any)) {
		return { ok: true };
	}

	if (option.authType === "oauth") {
		return runOAuthLogin(option, modelRegistry, ctx);
	}

	let apiKey = args.apiKey;
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

async function pickModelId(
	ctx: ExtensionCommandContext,
	listing: ModelListing,
	providerLabel: string,
): Promise<string | undefined> {
	if (listing.source === "manual" && listing.models.length === 1) {
		return listing.models[0].id;
	}
	ctx.ui.notify(describeListingSource(listing), listing.source === "live" ? "info" : "warning");
	const choices = listing.models.map((model) => (model.name ? `${model.id} — ${model.name}` : model.id));
	const selected = await ctx.ui.select(`Select the model for ${providerLabel}:`, choices);
	if (selected === undefined) return undefined;
	return listing.models[choices.indexOf(selected)]?.id;
}

interface GatewayOutcome {
	ok: boolean;
	message?: string;
	providerName?: string;
	modelId?: string;
	listing?: ModelListing;
	wroteModelsJson?: boolean;
}

async function initExistingGateway(
	ctx: ExtensionCommandContext,
	gateway: GatewayProviderSummary,
	args: SureInitArgs,
	modelsJsonPath: string,
): Promise<GatewayOutcome> {
	if (args.model) {
		const cached = readGatewayModels(gateway.name, modelsJsonPath);
		if (cached.some((model) => model.id === args.model)) {
			return { ok: true, providerName: gateway.name, modelId: args.model };
		}
		// The id isn't in the gateway's model list yet; ModelRegistry only resolves custom-provider
		// models from models.json, so a silent match failure would fall back to another model at
		// startup. Append it and persist, mirroring the non-interactive new-gateway path below.
		const models = [...cached, { id: args.model }];
		writeGatewayProvider({ name: gateway.name, baseUrl: gateway.baseUrl, apiKey: undefined, models }, modelsJsonPath);
		return { ok: true, providerName: gateway.name, modelId: args.model, wroteModelsJson: true };
	}
	const storedKey = await ctx.modelRegistry.getApiKeyForProvider(gateway.name);
	let newKey: string | undefined;
	if (!storedKey) {
		newKey = args.apiKey?.trim();
		if (!newKey && ctx.hasUI) {
			newKey = (await ctx.ui.input(`Enter the API key for ${gateway.name}:`))?.trim();
		}
		if (!newKey) {
			return { ok: false, message: `No API key for ${gateway.name}. Pass --api-key <key> or run interactively.` };
		}
	}
	const apiKey = storedKey ?? newKey;
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
			{ name: gateway.name, baseUrl: gateway.baseUrl, apiKey: newKey, models: listing.models },
			modelsJsonPath,
		);
		wroteModelsJson = true;
	}
	return { ok: true, providerName: gateway.name, modelId, listing, wroteModelsJson };
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
	return { ok: true, providerName: name, modelId, listing, wroteModelsJson: true };
}

export interface RunSureInitOptions {
	ctx: ExtensionCommandContext;
	args?: string;
	settingsManager?: SettingsManager;
	/** Override for tests; defaults to the agent-dir models.json. */
	modelsJsonPath?: string;
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
			modelId = await pickModelId(ctx, listing, option.name);
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
		listing = outcome.listing;
		refreshRegistry = outcome.wroteModelsJson === true;
	}

	if (refreshRegistry) {
		ctx.modelRegistry.refresh();
	}

	const settings = options.settingsManager ?? SettingsManager.create(ctx.cwd);
	settings.setDefaultModelAndProvider(providerKey, modelId);

	const discovered = discoverSureSkillPackages(ctx.cwd);
	const availableSkills = discovered.packages.map((pkg) => `/${pkg.manifest.command}`);

	const python = checkPythonEnvironment();

	const manifest: SureInitManifest = {
		initializedAt: new Date().toISOString(),
		defaultProvider: providerKey,
		defaultModel: modelId,
		trusted: true,
		pythonOk: python.ok,
		availableSkills,
		version: SURE_INIT_VERSION,
	};

	const manifestPath = writeInitManifest(ctx.cwd, manifest);

	const modelCommand = `/model ${providerKey}/${modelId}`;
	const lines = [
		`SURE initialized for ${providerLabel} (${providerKey}/${modelId}).`,
		`Manifest written to ${manifestPath}.`,
	];
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
	lines.push(`Run "${modelCommand}" to switch to the recommended model.`);

	return {
		success: true,
		manifest,
		message: lines.join("\n"),
		nextAction: modelCommand,
	};
}
