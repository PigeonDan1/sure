import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Model } from "@earendil-works/pi-ai";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthStorage } from "../../src/core/auth-storage.ts";
import type { ExtensionCommandContext } from "../../src/core/extensions/types.ts";
import { ModelRegistry } from "../../src/core/model-registry.ts";
import { SettingsManager } from "../../src/core/settings-manager.ts";
import { parseInitArgs, runSureInit, SURE_INIT_PROVIDER_OPTIONS } from "../../src/core/sure/init.ts";
import { menuEntryLabel } from "../../src/core/sure/init-menu.ts";
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

/** Build a fake command context. Returns the ui dialog mocks separately for per-test overrides. */
function makeContext(options?: {
	trusted?: boolean;
	hasUI?: boolean;
	selectedIndex?: number;
	apiKey?: string;
	configuredAuth?: boolean;
	cwd?: string;
	modelsJsonPath?: string;
}) {
	const cwd = options?.cwd ?? join(tmpdir(), `pi-sure-init-${Date.now()}`);
	const authStorage = AuthStorage.inMemory();
	if (options?.configuredAuth) {
		authStorage.set("kimi-coding", { type: "api_key", key: "fake-key" });
	}
	const modelRegistry = options?.modelsJsonPath
		? ModelRegistry.create(authStorage, options.modelsJsonPath)
		: ModelRegistry.inMemory(authStorage);
	const settingsManager = SettingsManager.inMemory();

	const select = vi.fn(async (_title: string, _choices: string[]) => undefined as string | undefined);
	if (options?.selectedIndex !== undefined) {
		const option = SURE_INIT_PROVIDER_OPTIONS[options.selectedIndex];
		select.mockResolvedValueOnce(menuEntryLabel({ kind: "builtin", option }));
	}

	const ui = {
		select,
		confirm: vi.fn(async (_title: string, _message: string) => true),
		input: vi.fn(async (_title: string, _placeholder?: string) => options?.apiKey ?? ""),
		notify: vi.fn((_message: string, _type?: "info" | "warning" | "error") => {}),
	};

	const ctx: ExtensionCommandContext = {
		cwd,
		ui: {
			...ui,
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

	return { ctx, settingsManager, ui };
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

	it("parses model and gateway flags", () => {
		const args = parseInitArgs(
			"--option custom --name relay --base-url https://gw.example.com/v1 --api-key sk-1 --model alpha",
		);
		expect(args).toEqual({
			optionId: "custom",
			gatewayName: "relay",
			gatewayBaseUrl: "https://gw.example.com/v1",
			apiKey: "sk-1",
			model: "alpha",
		});
	});

	it("keeps the legacy flags working", () => {
		expect(parseInitArgs("--provider codex --api-key k")).toEqual({ optionId: "codex", apiKey: "k" });
	});
});

describe("runSureInit", () => {
	let tempDir: string;

	beforeEach(() => {
		tempDir = join(tmpdir(), `pi-sure-init-${Date.now()}-${Math.random().toString(36).slice(2)}`);
		mkdirSync(tempDir, { recursive: true });
	});

	afterEach(() => {
		if (existsSync(tempDir)) {
			rmSync(tempDir, { recursive: true, force: true });
		}
		vi.unstubAllGlobals();
	});

	it("fails when project is not trusted", async () => {
		const { ctx } = makeContext({ trusted: false, cwd: tempDir });
		const result = await runSureInit({ ctx, modelsJsonPath: join(tempDir, "models.json") });
		expect(result.success).toBe(false);
		expect(result.message).toContain("not trusted");
		expect(result.nextAction).toBe("/trust");
	});

	it("fails when no option is selected in non-UI mode", async () => {
		const { ctx } = makeContext({ hasUI: false, cwd: tempDir });
		const result = await runSureInit({ ctx, modelsJsonPath: join(tempDir, "models.json") });
		expect(result.success).toBe(false);
		expect(result.message).toContain("No agent selected");
	});

	it("configures API key provider, sets default model, and writes manifest", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn(async () => Response.json({ data: [{ id: "kimi-for-coding" }] })),
		);
		const { ctx, settingsManager, ui } = makeContext({
			selectedIndex: 1, // kimi-code
			apiKey: "test-kimi-api-key",
			cwd: tempDir,
		});
		ui.select.mockResolvedValueOnce("kimi-for-coding");
		const result = await runSureInit({ ctx, settingsManager, modelsJsonPath: join(tempDir, "models.json") });

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

		expect(settingsManager.getGlobalSettings().defaultProvider).toBe("kimi-coding");
		expect(settingsManager.getGlobalSettings().defaultModel).toBe("kimi-for-coding");
	});

	it("uses pre-provided API key from args", async () => {
		const { ctx, settingsManager } = makeContext({ cwd: tempDir });
		const result = await runSureInit({
			ctx,
			args: "--option kimi-code --api-key sk-arg --model kimi-for-coding",
			settingsManager,
			modelsJsonPath: join(tempDir, "models.json"),
		});

		expect(result.success).toBe(true);
		expect(ctx.ui.select).not.toHaveBeenCalled();
		expect(ctx.ui.input).not.toHaveBeenCalled();

		const stored = ctx.modelRegistry.authStorage.get("kimi-coding");
		expect(stored?.type).toBe("api_key");
		if (stored?.type === "api_key") {
			expect(stored.key).toBe("sk-arg");
		}
		expect(settingsManager.getGlobalSettings().defaultProvider).toBe("kimi-coding");
		expect(settingsManager.getGlobalSettings().defaultModel).toBe("kimi-for-coding");
	});

	it("leaves the model switch to the caller instead of telling the user to run /model manually", async () => {
		const { ctx, settingsManager } = makeContext({ cwd: tempDir });
		const result = await runSureInit({
			ctx,
			args: "--option kimi-code --api-key sk-arg --model kimi-for-coding",
			settingsManager,
			modelsJsonPath: join(tempDir, "models.json"),
		});

		expect(result.success).toBe(true);
		expect(result.message).not.toContain('Run "/model');
		expect(result.nextAction).toBe("/model kimi-coding/kimi-for-coding");
	});

	it("runs OAuth login flow when provider is available", async () => {
		const { ctx, settingsManager, ui } = makeContext({
			selectedIndex: 0, // codex (oauth)
			cwd: tempDir,
		});

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

		ui.select.mockResolvedValueOnce("gpt-5.5 — GPT-5.5");

		const result = await runSureInit({ ctx, settingsManager, modelsJsonPath: join(tempDir, "models.json") });

		expect(result.success).toBe(true);
		expect(ctx.modelRegistry.authStorage.login).toHaveBeenCalledWith("openai-codex", expect.any(Object));
		expect(refreshSpy).toHaveBeenCalled();
		expect(settingsManager.getGlobalSettings().defaultProvider).toBe("openai-codex");
		expect(settingsManager.getGlobalSettings().defaultModel).toBe("gpt-5.5");
	});

	it("falls back to /login message when OAuth provider is not registered", async () => {
		const { ctx, settingsManager } = makeContext({
			selectedIndex: 0, // codex (oauth)
			cwd: tempDir,
		});
		vi.spyOn(ctx.modelRegistry.authStorage, "getOAuthProviders").mockReturnValue([]);

		const result = await runSureInit({ ctx, settingsManager, modelsJsonPath: join(tempDir, "models.json") });

		expect(result.success).toBe(false);
		expect(result.message).toContain("/login");
	});

	it("falls back to /login message in non-UI mode for OAuth provider", async () => {
		const { ctx, settingsManager } = makeContext({
			hasUI: false,
			cwd: tempDir,
		});
		const result = await runSureInit({
			ctx,
			args: "--option codex --model gpt-5.5",
			settingsManager,
			modelsJsonPath: join(tempDir, "models.json"),
		});

		expect(result.success).toBe(false);
		expect(result.message).toContain("/login");
	});

	it("reports OAuth login cancellation", async () => {
		const { ctx, settingsManager } = makeContext({
			selectedIndex: 0, // codex (oauth)
			cwd: tempDir,
		});
		vi.spyOn(ctx.modelRegistry.authStorage, "getOAuthProviders").mockReturnValue([
			{ id: "openai-codex", name: "OpenAI Codex" },
		] as any);
		vi.spyOn(ctx.modelRegistry.authStorage, "login").mockRejectedValue(new Error("Login cancelled"));

		const result = await runSureInit({ ctx, settingsManager, modelsJsonPath: join(tempDir, "models.json") });

		expect(result.success).toBe(false);
		expect(result.message).toContain("cancelled");
	});

	it("skips auth setup when already configured", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn(async () => Response.json({ data: [{ id: "kimi-for-coding" }] })),
		);
		const { ctx, settingsManager, ui } = makeContext({
			selectedIndex: 1, // kimi-code
			configuredAuth: true,
			cwd: tempDir,
		});
		ui.select.mockResolvedValueOnce("kimi-for-coding");
		const result = await runSureInit({ ctx, settingsManager, modelsJsonPath: join(tempDir, "models.json") });

		expect(result.success).toBe(true);
		expect(ctx.ui.input).not.toHaveBeenCalled();
		expect(settingsManager.getGlobalSettings().defaultProvider).toBe("kimi-coding");
		expect(settingsManager.getGlobalSettings().defaultModel).toBe("kimi-for-coding");
	});

	describe("runSureInit new flow", () => {
		it("fails non-interactively without --model", async () => {
			const { ctx, settingsManager } = makeContext({ hasUI: false, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option claude --api-key sk-1",
				settingsManager,
				modelsJsonPath: join(tempDir, "models.json"),
			});
			expect(result.success).toBe(false);
			expect(result.message).toContain("--model");
		});

		it("uses --model verbatim for a built-in provider without any network call", async () => {
			const fetchMock = vi.fn();
			vi.stubGlobal("fetch", fetchMock);
			const { ctx, settingsManager } = makeContext({ hasUI: false, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option claude --api-key sk-1 --model claude-pinned",
				settingsManager,
				modelsJsonPath: join(tempDir, "models.json"),
			});
			expect(result.success).toBe(true);
			expect(result.manifest?.defaultProvider).toBe("anthropic");
			expect(result.manifest?.defaultModel).toBe("claude-pinned");
			expect(settingsManager.getDefaultModel()).toBe("claude-pinned");
			expect(fetchMock).not.toHaveBeenCalled();
		});

		it("lists live models for a built-in provider and lets the user pick", async () => {
			vi.stubGlobal(
				"fetch",
				vi.fn(async () => Response.json({ data: [{ id: "claude-live" }] })),
			);
			const { ctx, settingsManager, ui } = makeContext({ hasUI: true, cwd: tempDir });
			ui.select
				.mockResolvedValueOnce("Anthropic Claude: Standard Anthropic API → anthropic")
				.mockResolvedValueOnce("claude-live");
			ui.input.mockResolvedValueOnce("sk-1");
			const result = await runSureInit({ ctx, settingsManager, modelsJsonPath: join(tempDir, "models.json") });
			expect(result.success).toBe(true);
			expect(result.manifest?.defaultModel).toBe("claude-live");
			expect(result.message).toContain("live");
		});

		it("falls back to the built-in catalog when the live query fails", async () => {
			vi.stubGlobal(
				"fetch",
				vi.fn(async () => new Response("down", { status: 503 })),
			);
			const { ctx, settingsManager, ui } = makeContext({ hasUI: true, cwd: tempDir });
			ui.select
				.mockResolvedValueOnce("Anthropic Claude: Standard Anthropic API → anthropic")
				.mockImplementationOnce(async (_title: string, choices: string[]) => choices[0]);
			ui.input.mockResolvedValueOnce("sk-1");
			const result = await runSureInit({ ctx, settingsManager, modelsJsonPath: join(tempDir, "models.json") });
			expect(result.success).toBe(true);
			expect(result.message).toContain("built-in catalog");
		});

		it("refreshes an existing gateway from a live query and rewrites its model list", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeFileSync(
				modelsPath,
				`${JSON.stringify(
					{
						providers: {
							relay: {
								baseUrl: "https://gw.example.com/v1",
								api: "openai-completions",
								apiKey: "sk-relay",
								models: [{ id: "stale" }],
							},
						},
					},
					null,
					2,
				)}\n`,
				"utf-8",
			);
			vi.stubGlobal(
				"fetch",
				vi.fn(async () => Response.json({ data: [{ id: "g1" }, { id: "g2" }] })),
			);
			const { ctx, settingsManager, ui } = makeContext({ hasUI: true, modelsJsonPath: modelsPath, cwd: tempDir });
			ui.select
				.mockResolvedValueOnce("relay (custom): https://gw.example.com/v1, 1 models")
				.mockResolvedValueOnce("g2");
			const result = await runSureInit({ ctx, settingsManager, modelsJsonPath: modelsPath });
			expect(result.success).toBe(true);
			expect(result.manifest?.defaultProvider).toBe("relay");
			expect(result.manifest?.defaultModel).toBe("g2");
			const rewritten = JSON.parse(readFileSync(modelsPath, "utf-8"));
			expect(rewritten.providers.relay.models).toEqual([{ id: "g1" }, { id: "g2" }]);
		});

		it("offers the cached list when the gateway live query fails and does not rewrite the file", async () => {
			const modelsPath = join(tempDir, "models.json");
			const original = `${JSON.stringify(
				{
					providers: {
						relay: {
							baseUrl: "https://gw.example.com/v1",
							api: "openai-completions",
							apiKey: "sk-relay",
							models: [{ id: "cached-1" }],
						},
					},
				},
				null,
				2,
			)}\n`;
			writeFileSync(modelsPath, original, "utf-8");
			vi.stubGlobal(
				"fetch",
				vi.fn(async () => new Response("down", { status: 502 })),
			);
			const { ctx, settingsManager, ui } = makeContext({ hasUI: true, modelsJsonPath: modelsPath, cwd: tempDir });
			ui.select
				.mockResolvedValueOnce("relay (custom): https://gw.example.com/v1, 1 models")
				.mockResolvedValueOnce("cached-1");
			const result = await runSureInit({ ctx, settingsManager, modelsJsonPath: modelsPath });
			expect(result.success).toBe(true);
			expect(result.message).toContain("cached");
			expect(readFileSync(modelsPath, "utf-8")).toBe(original);
		});

		it("leaves models.json untouched and skips refresh when the user cancels the model picker", async () => {
			const modelsPath = join(tempDir, "models.json");
			const original = `${JSON.stringify(
				{
					providers: {
						relay: {
							baseUrl: "https://gw.example.com/v1",
							api: "openai-completions",
							apiKey: "sk-relay",
							models: [{ id: "stale" }],
						},
					},
				},
				null,
				2,
			)}\n`;
			writeFileSync(modelsPath, original, "utf-8");
			vi.stubGlobal(
				"fetch",
				vi.fn(async () => Response.json({ data: [{ id: "g1" }, { id: "g2" }] })),
			);
			const { ctx, settingsManager, ui } = makeContext({ hasUI: true, modelsJsonPath: modelsPath, cwd: tempDir });
			const refreshSpy = vi.spyOn(ctx.modelRegistry, "refresh");
			ui.select
				.mockResolvedValueOnce("relay (custom): https://gw.example.com/v1, 1 models")
				.mockResolvedValueOnce(undefined);
			const result = await runSureInit({ ctx, settingsManager, modelsJsonPath: modelsPath });
			expect(result.success).toBe(false);
			expect(readFileSync(modelsPath, "utf-8")).toBe(original);
			expect(refreshSpy).not.toHaveBeenCalled();
		});

		it("propagates a writeGatewayProvider failure instead of silently falling back to the cached list", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeFileSync(
				modelsPath,
				`{\n\t// keep my comments\n\t"providers": {\n\t\t"relay": {\n\t\t\t"baseUrl": "https://gw.example.com/v1",\n\t\t\t"api": "openai-completions",\n\t\t\t"apiKey": "sk-relay",\n\t\t\t"models": [{ "id": "cached-1" }]\n\t\t}\n\t}\n}\n`,
				"utf-8",
			);
			vi.stubGlobal(
				"fetch",
				vi.fn(async () => Response.json({ data: [{ id: "g1" }] })),
			);
			const { ctx, settingsManager, ui } = makeContext({ hasUI: true, modelsJsonPath: modelsPath, cwd: tempDir });
			ui.select
				.mockResolvedValueOnce("relay (custom): https://gw.example.com/v1, 1 models")
				.mockImplementationOnce(async (_title: string, choices: string[]) => choices[0]);
			const result = await runSureInit({ ctx, settingsManager, modelsJsonPath: modelsPath });
			expect(result.success).toBe(false);
			expect(result.message).toMatch(/comments/);
		});

		it("accepts --model verbatim for an existing gateway without fetching", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeFileSync(
				modelsPath,
				`${JSON.stringify(
					{
						providers: {
							relay: {
								baseUrl: "https://gw.example.com/v1",
								api: "openai-completions",
								apiKey: "sk-relay",
								models: [{ id: "cached-1" }],
							},
						},
					},
					null,
					2,
				)}\n`,
				"utf-8",
			);
			const fetchMock = vi.fn();
			vi.stubGlobal("fetch", fetchMock);
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option relay --model g9",
				settingsManager,
				modelsJsonPath: modelsPath,
			});
			expect(result.success).toBe(true);
			expect(result.manifest?.defaultModel).toBe("g9");
			expect(fetchMock).not.toHaveBeenCalled();
		});

		it("appends a --model missing from an existing gateway's cached list and preserves the stored key", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeFileSync(
				modelsPath,
				`${JSON.stringify(
					{
						providers: {
							relay: {
								baseUrl: "https://gw.example.com/v1",
								api: "openai-completions",
								apiKey: "sk-relay",
								models: [{ id: "cached-1" }],
							},
						},
					},
					null,
					2,
				)}\n`,
				"utf-8",
			);
			const fetchMock = vi.fn();
			vi.stubGlobal("fetch", fetchMock);
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const refreshSpy = vi.spyOn(ctx.modelRegistry, "refresh");
			const result = await runSureInit({
				ctx,
				args: "--option relay --model new-id",
				settingsManager,
				modelsJsonPath: modelsPath,
			});
			expect(result.success).toBe(true);
			expect(result.manifest?.defaultModel).toBe("new-id");
			expect(fetchMock).not.toHaveBeenCalled();
			const written = JSON.parse(readFileSync(modelsPath, "utf-8"));
			expect(written.providers.relay.models).toEqual([{ id: "cached-1" }, { id: "new-id" }]);
			expect(written.providers.relay.apiKey).toBe("sk-relay");
			expect(refreshSpy).toHaveBeenCalled();
		});

		it("does not rewrite models.json when --model for an existing gateway is already cached", async () => {
			const modelsPath = join(tempDir, "models.json");
			const original = `${JSON.stringify(
				{
					providers: {
						relay: {
							baseUrl: "https://gw.example.com/v1",
							api: "openai-completions",
							apiKey: "sk-relay",
							models: [{ id: "cached-1" }],
						},
					},
				},
				null,
				2,
			)}\n`;
			writeFileSync(modelsPath, original, "utf-8");
			const fetchMock = vi.fn();
			vi.stubGlobal("fetch", fetchMock);
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const refreshSpy = vi.spyOn(ctx.modelRegistry, "refresh");
			const result = await runSureInit({
				ctx,
				args: "--option relay --model cached-1",
				settingsManager,
				modelsJsonPath: modelsPath,
			});
			expect(result.success).toBe(true);
			expect(result.manifest?.defaultModel).toBe("cached-1");
			expect(fetchMock).not.toHaveBeenCalled();
			expect(readFileSync(modelsPath, "utf-8")).toBe(original);
			expect(refreshSpy).not.toHaveBeenCalled();
		});

		it("lists every missing flag for a non-interactive gateway creation", async () => {
			const { ctx, settingsManager } = makeContext({ hasUI: false, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option custom --name relay",
				settingsManager,
				modelsJsonPath: join(tempDir, "models.json"),
			});
			expect(result.success).toBe(false);
			expect(result.message).toContain("--base-url");
			expect(result.message).toContain("--api-key");
			expect(result.message).toContain("--model");
		});

		it("rejects reserved names for a new gateway", async () => {
			const { ctx, settingsManager } = makeContext({ hasUI: false, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option custom --name openai --base-url https://gw.example.com/v1 --api-key sk-1 --model m",
				settingsManager,
				modelsJsonPath: join(tempDir, "models.json"),
			});
			expect(result.success).toBe(false);
			expect(result.message).toContain("reserved");
		});

		it("creates a gateway one-shot, appending a --model missing from the live list", async () => {
			const modelsPath = join(tempDir, "models.json");
			vi.stubGlobal(
				"fetch",
				vi.fn(async () => Response.json({ data: [{ id: "a" }] })),
			);
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option custom --name relay --base-url https://gw.example.com/v1 --api-key sk-1 --model b",
				settingsManager,
				modelsJsonPath: modelsPath,
			});
			expect(result.success).toBe(true);
			expect(result.manifest?.defaultProvider).toBe("relay");
			const written = JSON.parse(readFileSync(modelsPath, "utf-8"));
			expect(written.providers.relay.models).toEqual([{ id: "a" }, { id: "b" }]);
			expect(written.providers.relay.apiKey).toBe("sk-1");
		});

		it("reports a friendly error when models.json cannot be rewritten", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeFileSync(modelsPath, `{\n\t// keep my comments\n\t"providers": {}\n}\n`, "utf-8");
			vi.stubGlobal(
				"fetch",
				vi.fn(async () => Response.json({ data: [{ id: "a" }] })),
			);
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option custom --name relay --base-url https://gw.example.com/v1 --api-key sk-1 --model a",
				settingsManager,
				modelsJsonPath: modelsPath,
			});
			expect(result.success).toBe(false);
			expect(result.message).toContain("comments");
		});

		it("walks the interactive gateway flow with a manual model id after a failed fetch", async () => {
			const modelsPath = join(tempDir, "models.json");
			vi.stubGlobal(
				"fetch",
				vi.fn(async () => new Response("down", { status: 500 })),
			);
			const { ctx, settingsManager, ui } = makeContext({ hasUI: true, modelsJsonPath: modelsPath, cwd: tempDir });
			ui.select
				.mockResolvedValueOnce("Custom provider: add an OpenAI-compatible gateway")
				.mockResolvedValueOnce("Enter a model id manually");
			ui.input
				.mockResolvedValueOnce("relay")
				.mockResolvedValueOnce("https://gw.example.com/v1")
				.mockResolvedValueOnce("sk-1")
				.mockResolvedValueOnce("m1");
			const result = await runSureInit({ ctx, settingsManager, modelsJsonPath: modelsPath });
			expect(result.success).toBe(true);
			expect(result.manifest?.defaultModel).toBe("m1");
			expect(result.message).toContain("manually");
			const written = JSON.parse(readFileSync(modelsPath, "utf-8"));
			expect(written.providers.relay.models).toEqual([{ id: "m1" }]);
		});
	});
});
