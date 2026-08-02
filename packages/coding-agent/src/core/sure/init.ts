import { execSync } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { OAuthLoginCallbacks, OAuthProviderId } from "@earendil-works/pi-ai";
import type { ExtensionCommandContext } from "../extensions/types.ts";
import type { ModelRegistry } from "../model-registry.ts";
import { SettingsManager } from "../settings-manager.ts";
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

const OPTION_BY_ID = new Map(SURE_INIT_PROVIDER_OPTIONS.map((option) => [option.id, option]));

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
		}
	}
	return result;
}

function formatModelReference(option: SureInitProviderOption): string {
	return `${option.provider}/${option.defaultModel}`;
}

function findOptionById(id: string | undefined): SureInitProviderOption | undefined {
	if (!id) return undefined;
	return OPTION_BY_ID.get(id);
}

async function selectOption(
	ctx: ExtensionCommandContext,
	args: SureInitArgs,
): Promise<SureInitProviderOption | undefined> {
	const fromArgs = findOptionById(args.optionId);
	if (fromArgs) return fromArgs;

	if (!ctx.hasUI) {
		return undefined;
	}

	const choices = SURE_INIT_PROVIDER_OPTIONS.map(
		(option) => `${option.name}: ${option.description} → ${formatModelReference(option)}`,
	);
	const selected = await ctx.ui.select("Select the agent to use for SURE:", choices);
	if (selected === undefined) return undefined;
	const index = choices.indexOf(selected);
	return SURE_INIT_PROVIDER_OPTIONS[index];
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

export interface RunSureInitOptions {
	ctx: ExtensionCommandContext;
	args?: string;
	settingsManager?: SettingsManager;
}

/** Main entry point for /sure_init. */
export async function runSureInit(options: RunSureInitOptions): Promise<SureInitResult> {
	const { ctx } = options;
	const args = parseInitArgs(options.args ?? "");

	if (!ctx.isProjectTrusted()) {
		return {
			success: false,
			message: "Project is not trusted. Run /trust first, then /sure_init.",
			nextAction: "/trust",
		};
	}

	const option = await selectOption(ctx, args);
	if (!option) {
		return {
			success: false,
			message: "No agent selected. Run /sure_init in an interactive UI, or use --option <id>.",
		};
	}

	const authResult = await ensureAuth(option, ctx.modelRegistry, ctx, args);
	if (!authResult.ok) {
		return {
			success: false,
			message: authResult.message ?? `Authentication failed for ${option.name}.`,
		};
	}

	const settings = options.settingsManager ?? SettingsManager.create(ctx.cwd);
	settings.setDefaultModelAndProvider(option.provider, option.defaultModel);

	const discovered = discoverSureSkillPackages(ctx.cwd);
	const availableSkills = discovered.packages.map((pkg) => `/${pkg.manifest.command}`);

	const python = checkPythonEnvironment();

	const manifest: SureInitManifest = {
		initializedAt: new Date().toISOString(),
		defaultProvider: option.provider,
		defaultModel: option.defaultModel,
		trusted: true,
		pythonOk: python.ok,
		availableSkills,
		version: SURE_INIT_VERSION,
	};

	const manifestPath = writeInitManifest(ctx.cwd, manifest);

	const modelCommand = `/model ${formatModelReference(option)}`;
	const lines = [
		`SURE initialized for ${option.name} (${formatModelReference(option)}).`,
		`Manifest written to ${manifestPath}.`,
	];

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
