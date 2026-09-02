import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Model, ModelThinkingLevel } from "@earendil-works/pi-ai";
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

/** What the probe reports for a reasoning model on the responses protocol. */
const SOL_PROBE_OUTCOME = {
	ok: true as const,
	result: {
		api: "openai-responses" as const,
		reasoning: true,
		thinkingLevelMap: { off: "none", minimal: null, low: null, medium: null, high: "high", xhigh: "xhigh" },
		supportedLevels: ["xhigh", "high", "off"] as ModelThinkingLevel[],
		steps: [
			{ api: "openai-completions" as const, status: 400, verdict: "wrong-protocol" as const, detail: "" },
			{ api: "openai-responses" as const, status: 200, verdict: "ok" as const, detail: "" },
		],
		effortNote: "effort 上游确认:xhigh、high",
	},
};

/** What the probe reports for a plain model that does not reason. */
const PLAIN_PROBE_OUTCOME = {
	ok: true as const,
	result: {
		api: "openai-completions" as const,
		reasoning: false,
		supportedLevels: [] as ModelThinkingLevel[],
		steps: [{ api: "openai-completions" as const, status: 200, verdict: "ok" as const, detail: "" }],
		effortNote: "effort:上游不接受任何档位,这个模型不做推理",
	},
};

/** A stand-in probe that reports a reasoning model on the responses protocol. */
const solProbe = () => vi.fn(async () => SOL_PROBE_OUTCOME);

/**
 * A stand-in for the real round trip. These tests fake the transport, so the one live
 * request /sure_init sends at the end has nothing real to answer it; the E2E suite covers
 * that request against an actual HTTP server.
 */
const okVerify = () => vi.fn(async () => ({ ok: true }));

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

	it("parses the effort and probe-all flags", () => {
		const args = parseInitArgs("--option apifusion --model gpt-5.6-sol --effort high --probe-all");
		expect(args).toMatchObject({
			optionId: "apifusion",
			model: "gpt-5.6-sol",
			effort: "high",
			probeAll: true,
		});
	});

	it("leaves the new flags undefined when they are absent", () => {
		const args = parseInitArgs("--option codex --model gpt-5.5");
		expect(args.effort).toBeUndefined();
		expect(args.probeAll).toBeUndefined();
	});
});

describe("runSureInit", () => {
	let tempDir: string;
	let previousKimiApiKey: string | undefined;

	beforeEach(() => {
		previousKimiApiKey = process.env.KIMI_API_KEY;
		delete process.env.KIMI_API_KEY;
		tempDir = join(tmpdir(), `pi-sure-init-${Date.now()}-${Math.random().toString(36).slice(2)}`);
		mkdirSync(tempDir, { recursive: true });
	});

	afterEach(() => {
		if (existsSync(tempDir)) {
			rmSync(tempDir, { recursive: true, force: true });
		}
		if (previousKimiApiKey === undefined) {
			delete process.env.KIMI_API_KEY;
		} else {
			process.env.KIMI_API_KEY = previousKimiApiKey;
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
		expect(parsed.version).toBe(3);

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

		it("lists live models for a built-in provider and lets the user pick", async () => {
			vi.stubGlobal(
				"fetch",
				vi.fn(async () => Response.json({ data: [{ id: "claude-live" }] })),
			);
			const modelsPath = join(tempDir, "models.json");
			// Needs the file-backed registry (not ModelRegistry.inMemory) so that ctx.modelRegistry.refresh()
			// after the probe actually re-reads what applyProbedModel just wrote to modelsPath.
			const { ctx, settingsManager, ui } = makeContext({ hasUI: true, modelsJsonPath: modelsPath, cwd: tempDir });
			ui.select
				.mockResolvedValueOnce("Anthropic Claude: Standard Anthropic API → anthropic")
				.mockResolvedValueOnce("claude-live");
			ui.input.mockResolvedValueOnce("sk-1");
			const probe = solProbe();
			const result = await runSureInit({
				ctx,
				settingsManager,
				modelsJsonPath: modelsPath,
				probe,
				verify: okVerify(),
			});
			expect(result.success).toBe(true);
			expect(result.manifest?.defaultModel).toBe("claude-live");
			expect(result.message).toContain("live");
			// Pins what the built-in path hands to the probe: the anthropic catalog's baseUrl,
			// the key ensureAuth just stored via ui.input, and the picked model id.
			expect(probe).toHaveBeenCalledWith(
				{ baseUrl: "https://api.anthropic.com", apiKey: "sk-1", modelId: "claude-live" },
				undefined,
			);
			// The deleted "selects Sol with Luna absent" test used to carry these two for the
			// built-in path: the annotation landed on disk, and the registry sees it after refresh.
			expect(JSON.parse(readFileSync(modelsPath, "utf-8")).providers.anthropic.models[0]).toMatchObject({
				id: "claude-live",
				api: "openai-responses",
			});
			expect(ctx.modelRegistry.find("anthropic", "claude-live")?.api).toBe("openai-responses");
		});

		it("puts the built-in default model first in the picker", async () => {
			vi.stubGlobal(
				"fetch",
				vi.fn(async () => Response.json({ data: [{ id: "gpt-5.6-terra" }, { id: "gpt-5.6-sol" }] })),
			);
			const { ctx, settingsManager, ui } = makeContext({ hasUI: true, cwd: tempDir });
			ui.select
				.mockResolvedValueOnce("OpenAI GPT: Standard OpenAI API → openai")
				.mockImplementationOnce(async (_title: string, choices: string[]) => choices[0]);
			ui.input.mockResolvedValueOnce("sk-1");
			const result = await runSureInit({
				ctx,
				settingsManager,
				modelsJsonPath: join(tempDir, "models.json"),
				probe: solProbe(),
				verify: okVerify(),
			});
			expect(result.success).toBe(true);
			expect(result.manifest?.defaultModel).toBe("gpt-5.6-sol");
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
			const result = await runSureInit({
				ctx,
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: solProbe(),
				verify: okVerify(),
			});
			expect(result.success).toBe(true);
			expect(result.manifest?.defaultProvider).toBe("relay");
			expect(result.manifest?.defaultModel).toBe("g2");
			const rewritten = JSON.parse(readFileSync(modelsPath, "utf-8"));
			expect(rewritten.providers.relay.models.map((model: { id: string }) => model.id)).toEqual(["g1", "g2"]);
			expect(rewritten.providers.relay.models[0]).toEqual({ id: "g1" });
			expect(rewritten.providers.relay.models[1]).toMatchObject({ id: "g2", api: "openai-responses" });
		});

		it("offers the cached list when the gateway live query fails and keeps that list on disk", async () => {
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
			const result = await runSureInit({
				ctx,
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: solProbe(),
				verify: okVerify(),
			});
			expect(result.success).toBe(true);
			expect(result.message).toContain("cached");
			// The failed live query must not replace the cached list; only the probed model gains an annotation.
			const written = JSON.parse(readFileSync(modelsPath, "utf-8"));
			expect(written.providers.relay.models.map((model: { id: string }) => model.id)).toEqual(["cached-1"]);
			expect(written.providers.relay.models[0]).toMatchObject({ id: "cached-1", api: "openai-responses" });
			expect(written.providers.relay.apiKey).toBe("sk-relay");
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
				probe: solProbe(),
				verify: okVerify(),
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
				probe: solProbe(),
				verify: okVerify(),
			});
			expect(result.success).toBe(true);
			expect(result.manifest?.defaultModel).toBe("new-id");
			expect(fetchMock).not.toHaveBeenCalled();
			const written = JSON.parse(readFileSync(modelsPath, "utf-8"));
			expect(written.providers.relay.models.map((model: { id: string }) => model.id)).toEqual([
				"cached-1",
				"new-id",
			]);
			expect(written.providers.relay.models[0]).toEqual({ id: "cached-1" });
			expect(written.providers.relay.models[1]).toMatchObject({ id: "new-id", api: "openai-responses" });
			expect(written.providers.relay.apiKey).toBe("sk-relay");
			expect(refreshSpy).toHaveBeenCalled();
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
				probe: solProbe(),
				verify: okVerify(),
			});
			expect(result.success).toBe(true);
			expect(result.manifest?.defaultProvider).toBe("relay");
			const written = JSON.parse(readFileSync(modelsPath, "utf-8"));
			expect(written.providers.relay.models.map((model: { id: string }) => model.id)).toEqual(["a", "b"]);
			expect(written.providers.relay.models[0]).toEqual({ id: "a" });
			expect(written.providers.relay.models[1]).toMatchObject({ id: "b", api: "openai-responses" });
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
			const result = await runSureInit({
				ctx,
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: solProbe(),
				verify: okVerify(),
			});
			expect(result.success).toBe(true);
			expect(result.manifest?.defaultModel).toBe("m1");
			expect(result.message).toContain("manually");
			const written = JSON.parse(readFileSync(modelsPath, "utf-8"));
			expect(written.providers.relay.models.map((model: { id: string }) => model.id)).toEqual(["m1"]);
			expect(written.providers.relay.models[0]).toMatchObject({ id: "m1", api: "openai-responses" });
		});
	});

	describe("runSureInit with capability probing", () => {
		function writeGatewayFile(modelsPath: string, modelIds: string[] = ["gpt-5.6-sol"]) {
			writeFileSync(
				modelsPath,
				`${JSON.stringify(
					{
						providers: {
							apifusion: {
								baseUrl: "https://gw.example.com/v1",
								api: "openai-completions",
								models: modelIds.map((id) => ({ id })),
							},
						},
					},
					null,
					2,
				)}\n`,
				"utf-8",
			);
		}

		it("asks for the effort and writes the probed protocol for a gateway", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeGatewayFile(modelsPath);
			vi.stubGlobal(
				"fetch",
				vi.fn(async () => Response.json({ data: [{ id: "gpt-5.6-sol", display_name: "GPT-5.6 Sol" }] })),
			);
			const { ctx, settingsManager, ui } = makeContext({ hasUI: true, modelsJsonPath: modelsPath, cwd: tempDir });
			ui.select
				.mockResolvedValueOnce("apifusion (custom): https://gw.example.com/v1, 1 models")
				.mockResolvedValueOnce("gpt-5.6-sol — GPT-5.6 Sol")
				.mockResolvedValueOnce("xhigh");
			ui.input.mockResolvedValueOnce("sk-1");

			const verify = okVerify();
			const result = await runSureInit({
				ctx,
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: solProbe(),
				verify,
			});

			expect(result.success).toBe(true);
			// The level the user picked has to be the level the round trip actually sends.
			expect(verify).toHaveBeenCalledWith(
				expect.objectContaining({ provider: "apifusion", modelId: "gpt-5.6-sol", thinkingLevel: "xhigh" }),
			);
			expect(result.manifest).toMatchObject({
				defaultProvider: "apifusion",
				defaultModel: "gpt-5.6-sol",
				defaultApi: "openai-responses",
				defaultThinkingLevel: "xhigh",
				supportedThinkingLevels: ["xhigh", "high", "off"],
				version: 3,
			});
			expect(result.manifest?.capabilityProbe?.steps).toHaveLength(2);
			expect(settingsManager.getDefaultThinkingLevel()).toBe("xhigh");
			// If the effort question were never asked, applyProbedModel's headless fallback
			// (medium clamped upward within the supported levels) also lands on "high", so the
			// assertions above alone cannot tell "asked and answered xhigh" apart from "never
			// asked". Pin the question itself: the third select call must be the effort prompt
			// with exactly the label list init-apply.ts's levelLabel builds from supportedLevels.
			expect(ui.select).toHaveBeenNthCalledWith(3, expect.stringContaining("effort"), [
				"xhigh",
				"high",
				"off(不推理)",
			]);
			// The TUI only ever shows the last notification, so every probe note has to reach the
			// caller through the result message instead.
			expect(result.message).toContain("openai-responses");
			expect(result.message).toContain("effort 上游确认");
			expect(result.message).toContain("真实通路验证通过");
			expect(ui.notify).not.toHaveBeenCalledWith(expect.stringContaining("effort"), expect.anything());
			const written = JSON.parse(readFileSync(modelsPath, "utf-8"));
			expect(written.providers.apifusion.models[0]).toMatchObject({
				id: "gpt-5.6-sol",
				api: "openai-responses",
				reasoning: true,
			});
		});

		it("fails when the gateway rejects the client", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeGatewayFile(modelsPath);
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option apifusion --api-key sk-1 --model gpt-5.6-luna",
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: vi.fn(async () => ({
					ok: false as const,
					error: { kind: "client-rejected" as const, detail: "客户端异常", steps: [] },
				})),
			});
			expect(result.success).toBe(false);
			expect(result.message).toContain("客户端");
			expect(result.message).toContain("gpt-5.6-luna");
			// A failed probe must leave nothing behind: no default written to settings, no manifest on disk.
			expect(settingsManager.getDefaultModel()).toBeUndefined();
			expect(existsSync(join(tempDir, ".sure", "init.json"))).toBe(false);
		});

		it("does not probe a model the built-in catalog already knows", async () => {
			const modelsPath = join(tempDir, "models.json");
			const probe = vi.fn();
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option claude --api-key sk-1 --model claude-opus-4-8",
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: probe as never,
			});
			expect(result.success).toBe(true);
			expect(probe).not.toHaveBeenCalled();
			expect(result.manifest?.defaultApi).toBeUndefined();
		});

		it("still probes when --model hits the cached list", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeGatewayFile(modelsPath);
			const probe = solProbe();
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option apifusion --api-key sk-1 --model gpt-5.6-sol",
				settingsManager,
				modelsJsonPath: modelsPath,
				probe,
				verify: okVerify(),
			});
			expect(result.success).toBe(true);
			expect(probe).toHaveBeenCalledTimes(1);
			// The cached-list path has to hand the probe the same target as the live one.
			// applyProbedModel passes no probe options, so the second argument is undefined here;
			// expect.anything() would not match it.
			expect(probe).toHaveBeenCalledWith(
				expect.objectContaining({ modelId: "gpt-5.6-sol", apiKey: "sk-1" }),
				undefined,
			);
			expect(result.manifest?.defaultThinkingLevel).toBe("high");
			expect(result.manifest?.defaultApi).toBe("openai-responses");
		});

		it("refuses to run without a key it can probe with", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeGatewayFile(modelsPath);
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option apifusion --model gpt-5.6-sol",
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: solProbe(),
				verify: okVerify(),
			});
			expect(result.success).toBe(false);
			expect(result.message).toContain("--api-key");
		});

		it("hands the typed key to the probe when the live list fails", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeGatewayFile(modelsPath);
			vi.stubGlobal(
				"fetch",
				vi.fn(async () => new Response("down", { status: 502 })),
			);
			const { ctx, settingsManager, ui } = makeContext({ hasUI: true, modelsJsonPath: modelsPath, cwd: tempDir });
			ui.input.mockResolvedValueOnce("sk-typed");
			ui.select
				.mockResolvedValueOnce("apifusion (custom): https://gw.example.com/v1, 1 models")
				.mockResolvedValueOnce("gpt-5.6-sol")
				.mockResolvedValueOnce("high");
			const probe = solProbe();
			const result = await runSureInit({
				ctx,
				settingsManager,
				modelsJsonPath: modelsPath,
				probe,
				verify: okVerify(),
			});
			expect(result.success).toBe(true);
			// The cached fallback used to drop the typed key: it was neither stored nor handed to
			// the probe, so the probe ran unauthenticated and then blamed the key.
			expect(probe).toHaveBeenCalledWith(expect.objectContaining({ apiKey: "sk-typed" }), undefined);
			expect(JSON.parse(readFileSync(modelsPath, "utf-8")).providers.apifusion.apiKey).toBe("sk-typed");
		});

		it("lets --api-key replace a stale stored key", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeFileSync(
				modelsPath,
				`${JSON.stringify(
					{
						providers: {
							apifusion: {
								baseUrl: "https://gw.example.com/v1",
								api: "openai-completions",
								apiKey: "sk-old",
								models: [{ id: "gpt-5.6-sol" }],
							},
						},
					},
					null,
					2,
				)}\n`,
				"utf-8",
			);
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const probe = solProbe();
			const result = await runSureInit({
				ctx,
				args: "--option apifusion --api-key sk-new --model gpt-5.6-sol",
				settingsManager,
				modelsJsonPath: modelsPath,
				probe,
				verify: okVerify(),
			});
			expect(result.success).toBe(true);
			// Every failure message tells the user to rerun /sure_init with another key, which
			// only works if an explicit --api-key outranks the dead one already on disk.
			expect(probe).toHaveBeenCalledWith(expect.objectContaining({ apiKey: "sk-new" }), undefined);
			expect(JSON.parse(readFileSync(modelsPath, "utf-8")).providers.apifusion.apiKey).toBe("sk-new");
		});

		it("sets the clamped level in print mode where the UI is a no-op", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeGatewayFile(modelsPath);
			vi.stubGlobal(
				"fetch",
				vi.fn(async () => Response.json({ data: [{ id: "gpt-5.6-sol" }] })),
			);
			// print/json mode reports hasUI true and answers every dialog with undefined, so the
			// effort question is asked and nobody picks. That must clamp, not leave the level unset.
			const { ctx, settingsManager, ui } = makeContext({ hasUI: true, modelsJsonPath: modelsPath, cwd: tempDir });
			ui.input.mockResolvedValueOnce("sk-1");
			ui.select
				.mockResolvedValueOnce("apifusion (custom): https://gw.example.com/v1, 1 models")
				.mockResolvedValueOnce("gpt-5.6-sol");
			const result = await runSureInit({
				ctx,
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: solProbe(),
				verify: okVerify(),
			});
			expect(result.success).toBe(true);
			expect(ui.select).toHaveBeenCalledTimes(3);
			expect(result.manifest?.defaultThinkingLevel).toBe("high");
			expect(settingsManager.getDefaultThinkingLevel()).toBe("high");
		});

		it("rejects an effort upstream never confirmed", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeGatewayFile(modelsPath);
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option apifusion --api-key sk-1 --model gpt-5.6-sol --effort medium",
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: solProbe(),
				verify: okVerify(),
			});
			expect(result.success).toBe(false);
			expect(result.message).toContain("xhigh");
		});

		it("settles what the relay refused and gets the round trip through", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeGatewayFile(modelsPath);
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const verify = vi
				.fn()
				.mockResolvedValueOnce({
					ok: false,
					detail: "400 messages[0].role: unknown variant `developer`, expected one of `system`, `user`",
				})
				.mockResolvedValueOnce({ ok: true });
			const result = await runSureInit({
				ctx,
				args: "--option apifusion --api-key sk-1 --model gpt-5.6-sol",
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: solProbe(),
				verify,
			});
			expect(result.success).toBe(true);
			expect(verify).toHaveBeenCalledTimes(2);
			// Recorded at the provider so every model on this relay picks it up, and so the next
			// session does not rediscover it. The user is told, because models.json is theirs.
			const parsed = JSON.parse(readFileSync(modelsPath, "utf-8"));
			expect(parsed.providers.apifusion.compat).toMatchObject({ supportsDeveloperRole: false });
			expect(result.message).toContain("developer");
		});

		it("writes no setting for a failure that names none", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeGatewayFile(modelsPath);
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const verify = vi.fn(async () => ({ ok: false, detail: "HTTP 502" }));
			const result = await runSureInit({
				ctx,
				args: "--option apifusion --api-key sk-1 --model gpt-5.6-sol",
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: solProbe(),
				verify,
			});
			expect(result.success).toBe(false);
			// A gateway having a bad day says nothing about how it wants to be talked to. Turning
			// a capability off here would write a wrong setting nobody goes looking for again.
			expect(verify).toHaveBeenCalledTimes(1);
			expect(JSON.parse(readFileSync(modelsPath, "utf-8")).providers.apifusion.compat).toBeUndefined();
		});

		it("stops instead of retrying a setting it already tried", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeGatewayFile(modelsPath);
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const verify = vi.fn(async () => ({
				ok: false,
				detail: "400 messages[0].role: unknown variant `developer`",
			}));
			const result = await runSureInit({
				ctx,
				args: "--option apifusion --api-key sk-1 --model gpt-5.6-sol",
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: solProbe(),
				verify,
			});
			expect(result.success).toBe(false);
			expect(verify).toHaveBeenCalledTimes(2);
			expect(result.message).toContain("默认模型没有切换");
		});

		it("fails init when the real round trip does not come back", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeGatewayFile(modelsPath);
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option apifusion --api-key sk-1 --model gpt-5.6-sol",
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: solProbe(),
				verify: vi.fn(async () => ({ ok: false, detail: "HTTP 502" })),
			});
			expect(result.success).toBe(false);
			expect(result.message).toContain("502");
			// The annotation stays (it is measured), but nothing that points a session at a model
			// that cannot answer may be written.
			expect(settingsManager.getDefaultModel()).toBeUndefined();
			expect(existsSync(join(tempDir, ".sure", "init.json"))).toBe(false);
			expect(result.message).toContain("默认模型没有切换");
		});

		it("reports a thrown round trip as a failure", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeGatewayFile(modelsPath);
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option apifusion --api-key sk-1 --model gpt-5.6-sol",
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: solProbe(),
				verify: vi.fn(async () => {
					throw new Error("boom");
				}),
			});
			expect(result.success).toBe(false);
			expect(result.message).toContain("boom");
			expect(settingsManager.getDefaultModel()).toBeUndefined();
		});

		it("--probe-all annotates the rest of the table without re-probing the chosen model", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeGatewayFile(modelsPath, ["gpt-5.6-sol", "deepseek-chat", "glm-5.1"]);
			const probed: string[] = [];
			const probe = vi.fn(async (target: { modelId: string }) => {
				probed.push(target.modelId);
				return target.modelId === "gpt-5.6-sol" ? SOL_PROBE_OUTCOME : PLAIN_PROBE_OUTCOME;
			});
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option apifusion --api-key sk-1 --model gpt-5.6-sol --probe-all",
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: probe as never,
				verify: okVerify(),
			});
			expect(result.success).toBe(true);
			expect(probe).toHaveBeenCalledTimes(3);
			expect(probed.filter((id) => id === "gpt-5.6-sol")).toEqual(["gpt-5.6-sol"]);
			expect([...probed].sort()).toEqual(["deepseek-chat", "glm-5.1", "gpt-5.6-sol"]);
			expect(result.message).toContain("标注 2 个");
			const written = JSON.parse(readFileSync(modelsPath, "utf-8"));
			expect(written.providers.apifusion.models.map((model: { api?: string }) => model.api)).toEqual([
				"openai-responses",
				"openai-completions",
				"openai-completions",
			]);
		});

		it("--probe-all does not fail init when the table run aborts", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeGatewayFile(modelsPath, ["gpt-5.6-sol", "deepseek-chat", "glm-5.1"]);
			const probe = vi.fn(async (target: { modelId: string }) =>
				target.modelId === "gpt-5.6-sol"
					? SOL_PROBE_OUTCOME
					: { ok: false as const, error: { kind: "bad-key" as const, detail: "Invalid token", steps: [] } },
			);
			const { ctx, settingsManager } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option apifusion --api-key sk-1 --model gpt-5.6-sol --probe-all",
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: probe as never,
				verify: okVerify(),
			});
			// The chosen model is already probed, written and verified; the table is a bonus.
			expect(result.success).toBe(true);
			expect(result.message).toContain("整表探测中止");
			expect(settingsManager.getDefaultModel()).toBe("gpt-5.6-sol");
		});

		it("--probe-all is refused for a built-in provider", async () => {
			const { ctx, settingsManager } = makeContext({ hasUI: false, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option claude --api-key sk-1 --model claude-opus-4-8 --probe-all",
				settingsManager,
				modelsJsonPath: join(tempDir, "models.json"),
				probe: vi.fn() as never,
			});
			expect(result.success).toBe(false);
			expect(result.message).toContain("只对中转站有效");
		});

		it("--probe-all is refused for a built-in provider even when the model was probed", async () => {
			vi.stubGlobal(
				"fetch",
				vi.fn(async () => Response.json({ data: [{ id: "claude-live" }] })),
			);
			const modelsPath = join(tempDir, "models.json");
			const { ctx, settingsManager, ui } = makeContext({ hasUI: true, modelsJsonPath: modelsPath, cwd: tempDir });
			ui.select
				.mockResolvedValueOnce("Anthropic Claude: Standard Anthropic API → anthropic")
				.mockResolvedValueOnce("claude-live");
			ui.input.mockResolvedValueOnce("sk-1");
			const result = await runSureInit({
				ctx,
				args: "--probe-all",
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: solProbe(),
				verify: okVerify(),
			});
			expect(result.success).toBe(false);
			expect(result.message).toContain("只对中转站有效");
		});

		it("refuses --effort for a model the built-in catalog already knows", async () => {
			const { ctx, settingsManager } = makeContext({ hasUI: false, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option claude --api-key sk-1 --model claude-opus-4-8 --effort high",
				settingsManager,
				modelsJsonPath: join(tempDir, "models.json"),
				probe: vi.fn() as never,
			});
			expect(result.success).toBe(false);
			expect(result.message).toContain("--effort 只对探测过的模型有效");
		});

		it("passes --effort through to the probe layer and records it", async () => {
			const modelsPath = join(tempDir, "models.json");
			writeGatewayFile(modelsPath);
			const { ctx, settingsManager, ui } = makeContext({ hasUI: false, modelsJsonPath: modelsPath, cwd: tempDir });
			const result = await runSureInit({
				ctx,
				args: "--option apifusion --api-key sk-1 --model gpt-5.6-sol --effort xhigh",
				settingsManager,
				modelsJsonPath: modelsPath,
				probe: solProbe(),
				verify: okVerify(),
			});
			expect(result.success).toBe(true);
			expect(result.manifest?.defaultThinkingLevel).toBe("xhigh");
			expect(settingsManager.getDefaultThinkingLevel()).toBe("xhigh");
			expect(ui.select).not.toHaveBeenCalled();
		});
	});
});
