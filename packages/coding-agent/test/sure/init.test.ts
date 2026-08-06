import { existsSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Model } from "@earendil-works/pi-ai";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthStorage } from "../../src/core/auth-storage.ts";
import type { ExtensionCommandContext } from "../../src/core/extensions/types.ts";
import { ModelRegistry } from "../../src/core/model-registry.ts";
import { SettingsManager } from "../../src/core/settings-manager.ts";
import { parseInitArgs, runSureInit, SURE_INIT_PROVIDER_OPTIONS } from "../../src/core/sure/init.ts";
import type { SureInitManifest } from "../../src/core/sure/init-types.ts";

vi.mock("../../src/core/sure/manifest.ts", () => ({
	discoverSureSkillPackages: vi.fn(() => ({
		packages: [
			{
				manifest: { name: "sure_feed", command: "sure_feed" },
				packageDir: "/fake/sure_feed",
				promptPath: "/fake/sure_feed/SKILL.md",
				prompt: "",
				source: "repository" as const,
				sourceRoot: "/fake",
			},
		],
		diagnostics: [],
	})),
}));

function createMockContext(options?: {
	trusted?: boolean;
	hasUI?: boolean;
	selectedIndex?: number;
	apiKey?: string;
	configuredAuth?: boolean;
	cwd?: string;
}): ExtensionCommandContext {
	const cwd = options?.cwd ?? join(tmpdir(), `pi-sure-init-${Date.now()}`);
	const authStorage = AuthStorage.inMemory();
	if (options?.configuredAuth) {
		authStorage.set("kimi-coding", { type: "api_key", key: "fake-key" });
	}
	const modelRegistry = ModelRegistry.inMemory(authStorage);

	return {
		cwd,
		ui: {
			select: vi.fn(async () => {
				if (options?.selectedIndex === undefined) return undefined;
				const labels = SURE_INIT_PROVIDER_OPTIONS.map(
					(option) => `${option.name}: ${option.description} → ${option.provider}/${option.defaultModel}`,
				);
				return labels[options.selectedIndex];
			}),
			confirm: vi.fn(async () => true),
			input: vi.fn(async () => options?.apiKey ?? ""),
			notify: vi.fn(),
			onTerminalInput: vi.fn(() => () => {}),
			setStatus: vi.fn(),
			setWorkingMessage: vi.fn(),
			setWorkingVisible: vi.fn(),
			setWorkingIndicator: vi.fn(),
			setHiddenThinkingLabel: vi.fn(),
			setWidget: vi.fn(),
			setFooter: vi.fn(),
			setHeader: vi.fn(),
			setTitle: vi.fn(),
			custom: vi.fn(),
			pasteToEditor: vi.fn(),
			setEditorText: vi.fn(),
			getEditorText: vi.fn(() => ""),
			editor: vi.fn(async () => undefined),
			addAutocompleteProvider: vi.fn(),
			setEditorComponent: vi.fn(),
			getEditorComponent: vi.fn(() => undefined),
			theme: { name: "light" } as any,
			getAllThemes: vi.fn(() => []),
			getTheme: vi.fn(() => undefined),
			setTheme: vi.fn(() => ({ success: true })),
			getToolsExpanded: vi.fn(() => false),
			setToolsExpanded: vi.fn(),
		},
		mode: "tui" as const,
		hasUI: options?.hasUI ?? true,
		sessionManager: {} as any,
		modelRegistry,
		model: { provider: "kimi-coding", id: "kimi-for-coding" } as Model<any>,
		isIdle: vi.fn(() => true),
		isProjectTrusted: vi.fn(() => options?.trusted ?? true),
		signal: undefined,
		abort: vi.fn(),
		hasPendingMessages: vi.fn(() => false),
		shutdown: vi.fn(),
		getContextUsage: vi.fn(),
		compact: vi.fn(),
		getSystemPrompt: vi.fn(),
		getSystemPromptOptions: vi.fn(),
		waitForIdle: vi.fn(),
		newSession: vi.fn(async () => ({ cancelled: false })),
		fork: vi.fn(async () => ({ cancelled: false })),
		navigateTree: vi.fn(async () => ({ cancelled: false })),
		switchSession: vi.fn(async () => ({ cancelled: false })),
		reload: vi.fn(),
	};
}

describe("parseInitArgs", () => {
	it("parses --option", () => {
		expect(parseInitArgs("--option kimi-code")).toEqual({ optionId: "kimi-code" });
	});

	it("parses --api-key", () => {
		expect(parseInitArgs("--option kimi-code --api-key sk-xxx")).toEqual({
			optionId: "kimi-code",
			apiKey: "sk-xxx",
		});
	});

	it("returns empty object for empty args", () => {
		expect(parseInitArgs("")).toEqual({});
	});
});

describe("runSureInit", () => {
	let tempDir: string;

	beforeEach(() => {
		tempDir = join(tmpdir(), `pi-sure-init-${Date.now()}-${Math.random().toString(36).slice(2)}`);
	});

	afterEach(() => {
		if (existsSync(tempDir)) {
			rmSync(tempDir, { recursive: true, force: true });
		}
	});

	it("fails when project is not trusted", async () => {
		const ctx = createMockContext({ trusted: false, cwd: tempDir });
		const result = await runSureInit({ ctx });
		expect(result.success).toBe(false);
		expect(result.message).toContain("not trusted");
		expect(result.nextAction).toBe("/trust");
	});

	it("fails when no option is selected in non-UI mode", async () => {
		const ctx = createMockContext({ hasUI: false, cwd: tempDir });
		const result = await runSureInit({ ctx });
		expect(result.success).toBe(false);
		expect(result.message).toContain("No agent selected");
	});

	it("configures API key provider, sets default model, and writes manifest", async () => {
		const ctx = createMockContext({
			selectedIndex: 1, // kimi-code
			apiKey: "test-kimi-api-key",
			cwd: tempDir,
		});
		const settings = SettingsManager.inMemory();
		const result = await runSureInit({ ctx, settingsManager: settings });

		expect(result.success).toBe(true);
		expect(result.manifest).toBeDefined();
		expect(result.manifest?.defaultProvider).toBe("kimi-coding");
		expect(result.manifest?.defaultModel).toBe("kimi-for-coding");
		expect(result.manifest?.availableSkills).toContain("/sure_feed");

		const stored = ctx.modelRegistry.authStorage.get("kimi-coding");
		expect(stored?.type).toBe("api_key");
		if (stored?.type === "api_key") {
			expect(stored.key).toBe("test-kimi-api-key");
		}

		const manifestPath = join(tempDir, ".sure", "init.json");
		expect(existsSync(manifestPath)).toBe(true);
		const parsed: SureInitManifest = JSON.parse(readFileSync(manifestPath, "utf-8"));
		expect(parsed.defaultProvider).toBe("kimi-coding");
		expect(parsed.version).toBe(1);

		expect(settings.getGlobalSettings().defaultProvider).toBe("kimi-coding");
		expect(settings.getGlobalSettings().defaultModel).toBe("kimi-for-coding");
	});

	it("uses pre-provided API key from args", async () => {
		const ctx = createMockContext({ cwd: tempDir });
		const settings = SettingsManager.inMemory();
		const result = await runSureInit({ ctx, args: "--option kimi-code --api-key sk-arg", settingsManager: settings });

		expect(result.success).toBe(true);
		expect(ctx.ui.select).not.toHaveBeenCalled();
		expect(ctx.ui.input).not.toHaveBeenCalled();

		const stored = ctx.modelRegistry.authStorage.get("kimi-coding");
		expect(stored?.type).toBe("api_key");
		if (stored?.type === "api_key") {
			expect(stored.key).toBe("sk-arg");
		}
		expect(settings.getGlobalSettings().defaultProvider).toBe("kimi-coding");
		expect(settings.getGlobalSettings().defaultModel).toBe("kimi-for-coding");
	});

	it("runs OAuth login flow when provider is available", async () => {
		const ctx = createMockContext({
			selectedIndex: 0, // codex (oauth)
			cwd: tempDir,
		});
		const settings = SettingsManager.inMemory();

		vi.spyOn(ctx.modelRegistry.authStorage, "getOAuthProviders").mockReturnValue([
			{ id: "openai-codex", name: "OpenAI Codex" },
		] as any);

		vi.spyOn(ctx.modelRegistry.authStorage, "login").mockImplementation(async (providerId) => {
			ctx.modelRegistry.authStorage.set(providerId, {
				type: "oauth",
				refresh: "refresh-token",
				access: "access-token",
				expires: Date.now() + 3600000,
			});
		});

		const refreshSpy = vi.spyOn(ctx.modelRegistry, "refresh");

		const result = await runSureInit({ ctx, settingsManager: settings });

		expect(result.success).toBe(true);
		expect(ctx.modelRegistry.authStorage.login).toHaveBeenCalledWith("openai-codex", expect.any(Object));
		expect(refreshSpy).toHaveBeenCalled();
		expect(settings.getGlobalSettings().defaultProvider).toBe("openai-codex");
		expect(settings.getGlobalSettings().defaultModel).toBe("gpt-5.5");
	});

	it("falls back to /login message when OAuth provider is not registered", async () => {
		const ctx = createMockContext({
			selectedIndex: 0, // codex (oauth)
			cwd: tempDir,
		});
		vi.spyOn(ctx.modelRegistry.authStorage, "getOAuthProviders").mockReturnValue([]);

		const result = await runSureInit({ ctx });

		expect(result.success).toBe(false);
		expect(result.message).toContain("/login");
	});

	it("falls back to /login message in non-UI mode for OAuth provider", async () => {
		const ctx = createMockContext({
			hasUI: false,
			cwd: tempDir,
		});
		const result = await runSureInit({ ctx, args: "--option codex" });

		expect(result.success).toBe(false);
		expect(result.message).toContain("/login");
	});

	it("reports OAuth login cancellation", async () => {
		const ctx = createMockContext({
			selectedIndex: 0, // codex (oauth)
			cwd: tempDir,
		});
		vi.spyOn(ctx.modelRegistry.authStorage, "getOAuthProviders").mockReturnValue([
			{ id: "openai-codex", name: "OpenAI Codex" },
		] as any);
		vi.spyOn(ctx.modelRegistry.authStorage, "login").mockRejectedValue(new Error("Login cancelled"));

		const result = await runSureInit({ ctx });

		expect(result.success).toBe(false);
		expect(result.message).toContain("cancelled");
	});

	it("skips auth setup when already configured", async () => {
		const ctx = createMockContext({
			selectedIndex: 1, // kimi-code
			configuredAuth: true,
			cwd: tempDir,
		});
		const settings = SettingsManager.inMemory();
		const result = await runSureInit({ ctx, settingsManager: settings });

		expect(result.success).toBe(true);
		expect(ctx.ui.input).not.toHaveBeenCalled();
		expect(settings.getGlobalSettings().defaultProvider).toBe("kimi-coding");
		expect(settings.getGlobalSettings().defaultModel).toBe("kimi-for-coding");
	});
});
