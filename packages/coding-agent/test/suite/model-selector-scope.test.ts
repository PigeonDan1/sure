import type { Api, Model } from "@earendil-works/pi-ai/compat";
import { setKeybindings, type TUI } from "@earendil-works/pi-tui";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { AuthStorage } from "../../src/core/auth-storage.ts";
import { KeybindingsManager } from "../../src/core/keybindings.ts";
import { ModelRegistry } from "../../src/core/model-registry.ts";
import { SettingsManager } from "../../src/core/settings-manager.ts";
import { ModelSelectorComponent } from "../../src/modes/interactive/components/model-selector.ts";
import { initTheme } from "../../src/modes/interactive/theme/theme.ts";
import { stripAnsi } from "../../src/utils/ansi.ts";

function createFakeTui(): TUI {
	return {
		requestRender: () => {},
	} as unknown as TUI;
}

async function waitForAsyncRender(): Promise<void> {
	await new Promise((resolve) => setTimeout(resolve, 0));
}

/** Registers a provider with the given model ids using real ModelRegistry auth plumbing. */
function registerTestProvider(registry: ModelRegistry, providerId: string, modelIds: string[]): void {
	registry.registerProvider(providerId, {
		baseUrl: `https://${providerId}.test/v1`,
		apiKey: `${providerId}-key`,
		api: "openai-completions" as Api,
		models: modelIds.map((id) => ({
			id,
			name: id,
			reasoning: false,
			input: ["text"],
			cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
			contextWindow: 100000,
			maxTokens: 8000,
		})),
	});
}

function renderLines(selector: ModelSelectorComponent): string[] {
	return stripAnsi(selector.render(120).join("\n")).split("\n");
}

function findScopeLine(lines: string[]): string | undefined {
	// Text.render() pads lines to the full render width, so trim trailing padding.
	return lines.find((line) => line.startsWith("Scope:"))?.trimEnd();
}

describe("ModelSelectorComponent default scope", () => {
	let registry: ModelRegistry;
	const settingsManager = SettingsManager.inMemory();

	beforeAll(() => {
		initTheme("dark");
	});

	beforeEach(() => {
		// Keybindings are a global singleton; reset between tests for isolation.
		setKeybindings(new KeybindingsManager());
		registry = ModelRegistry.inMemory(AuthStorage.inMemory());
		registerTestProvider(registry, "provider-a", ["a1", "a2"]);
		registerTestProvider(registry, "provider-b", ["b1"]);
	});

	afterEach(() => {
		registry.refresh();
	});

	it("defaults to showing only the current provider's models", async () => {
		const currentModel = registry.find("provider-a", "a1") as Model<Api>;
		const selector = new ModelSelectorComponent(
			createFakeTui(),
			currentModel,
			settingsManager,
			registry,
			[],
			() => {},
			() => {},
		);

		await waitForAsyncRender();

		const lines = renderLines(selector);
		expect(lines.some((line) => line.includes("a1"))).toBe(true);
		expect(lines.some((line) => line.includes("a2"))).toBe(true);
		expect(lines.some((line) => line.includes("b1"))).toBe(false);

		// Both "provider" and "all" are reachable; "scoped" isn't offered (none configured).
		expect(findScopeLine(lines)).toBe("Scope: provider | all");
	});

	it("falls back to all-provider scope when there is no current model", async () => {
		const selector = new ModelSelectorComponent(
			createFakeTui(),
			undefined,
			settingsManager,
			registry,
			[],
			() => {},
			() => {},
		);

		await waitForAsyncRender();

		const lines = renderLines(selector);
		expect(lines.some((line) => line.includes("a1"))).toBe(true);
		expect(lines.some((line) => line.includes("a2"))).toBe(true);
		expect(lines.some((line) => line.includes("b1"))).toBe(true);

		// "provider" isn't offered: there is no current model to filter by.
		expect(findScopeLine(lines)).toBe("Scope: all");
	});

	it("falls back to all-provider scope when the current provider contributes zero available models", async () => {
		// A model whose provider isn't registered in this registry at all.
		const orphanModel: Model<Api> = {
			id: "orphan-1",
			name: "Orphan",
			api: "openai-completions" as Api,
			provider: "orphan-provider",
			baseUrl: "https://orphan.test/v1",
			reasoning: false,
			input: ["text"],
			cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
			contextWindow: 100000,
			maxTokens: 8000,
		};

		const selector = new ModelSelectorComponent(
			createFakeTui(),
			orphanModel,
			settingsManager,
			registry,
			[],
			() => {},
			() => {},
		);

		await waitForAsyncRender();

		const lines = renderLines(selector);
		expect(lines.some((line) => line.includes("a1"))).toBe(true);
		expect(lines.some((line) => line.includes("b1"))).toBe(true);
		expect(findScopeLine(lines)).toBe("Scope: all");
	});

	it("keeps the scoped default when scoped models are configured, and Tab cycles provider -> all -> scoped", async () => {
		const a1 = registry.find("provider-a", "a1") as Model<Api>;
		const a2 = registry.find("provider-a", "a2") as Model<Api>;
		const b1 = registry.find("provider-b", "b1") as Model<Api>;

		const selector = new ModelSelectorComponent(
			createFakeTui(),
			a1,
			settingsManager,
			registry,
			[{ model: a2 }, { model: b1 }],
			() => {},
			() => {},
		);

		await waitForAsyncRender();

		// Default stays "scoped" (today's behavior for this feature's users).
		let lines = renderLines(selector);
		expect(findScopeLine(lines)).toBe("Scope: provider | all | scoped");
		expect(lines.some((line) => line.includes("a2"))).toBe(true);
		expect(lines.some((line) => line.includes("b1"))).toBe(true);
		expect(lines.some((line) => line.includes("a1"))).toBe(false);

		// Tab -> provider: only the current model's provider.
		selector.handleInput("\t");
		lines = renderLines(selector);
		expect(lines.some((line) => line.includes("a1"))).toBe(true);
		expect(lines.some((line) => line.includes("a2"))).toBe(true);
		expect(lines.some((line) => line.includes("b1"))).toBe(false);

		// Tab -> all: every configured model.
		selector.handleInput("\t");
		lines = renderLines(selector);
		expect(lines.some((line) => line.includes("a1"))).toBe(true);
		expect(lines.some((line) => line.includes("a2"))).toBe(true);
		expect(lines.some((line) => line.includes("b1"))).toBe(true);

		// Tab -> back to scoped.
		selector.handleInput("\t");
		lines = renderLines(selector);
		expect(lines.some((line) => line.includes("a2"))).toBe(true);
		expect(lines.some((line) => line.includes("b1"))).toBe(true);
		expect(lines.some((line) => line.includes("a1"))).toBe(false);
	});

	it("Tab cycles provider -> all -> provider when no scoped models are configured", async () => {
		const a1 = registry.find("provider-a", "a1") as Model<Api>;

		const selector = new ModelSelectorComponent(
			createFakeTui(),
			a1,
			settingsManager,
			registry,
			[],
			() => {},
			() => {},
		);

		await waitForAsyncRender();

		let lines = renderLines(selector);
		expect(lines.some((line) => line.includes("b1"))).toBe(false);

		selector.handleInput("\t");
		lines = renderLines(selector);
		expect(lines.some((line) => line.includes("b1"))).toBe(true);

		selector.handleInput("\t");
		lines = renderLines(selector);
		expect(lines.some((line) => line.includes("b1"))).toBe(false);
		expect(lines.some((line) => line.includes("a1"))).toBe(true);
	});

	it("defaults to all-provider scope when opened pre-filled from a failed exact match, even with scoped models configured", async () => {
		const a1 = registry.find("provider-a", "a1") as Model<Api>;
		const b1 = registry.find("provider-b", "b1") as Model<Api>;

		const selector = new ModelSelectorComponent(
			createFakeTui(),
			a1,
			settingsManager,
			registry,
			[{ model: a1 }],
			() => {},
			() => {},
			"b1",
		);

		await waitForAsyncRender();

		const lines = renderLines(selector);
		// The search box itself echoes the pre-filled term, so check the results list via its
		// provider badge rather than the raw id (which would also match the search box text).
		// "b1" only turns up in the results when scope is "all" (the "scoped" default only contains a1).
		expect(lines.some((line) => line.includes(`[${b1.provider}]`))).toBe(true);
	});
});
