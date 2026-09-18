import type { Api, Model } from "@earendil-works/pi-ai/compat";
import { setKeybindings, type TUI } from "@earendil-works/pi-tui";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import { KeybindingsManager } from "../../src/core/keybindings.ts";
import type { ModelRuntime } from "../../src/core/model-runtime.ts";
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

/** A model the selector can list. Only the fields the component reads carry meaning here. */
function testModel(provider: string, id: string): Model<Api> {
	return {
		id,
		name: id,
		api: "openai-completions" as Api,
		provider,
		baseUrl: `https://${provider}.test/v1`,
		reasoning: false,
		input: ["text"],
		cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
		contextWindow: 100000,
		maxTokens: 8000,
	};
}

/**
 * The slice of ModelRuntime the selector actually reads while it builds its list. refresh()
 * resolves empty so the background refreshModels() takes its success path instead of its
 * catch branch; scope selection is settled before it runs either way.
 */
function createFakeRuntime(models: readonly Model<Api>[]): ModelRuntime {
	return {
		getAvailableSnapshot: () => models,
		getModel: (provider: string, id: string) =>
			models.find((model) => model.provider === provider && model.id === id),
		getError: () => undefined,
		refresh: async () => ({ aborted: false, errors: new Map<string, Error>() }),
	} as unknown as ModelRuntime;
}

function renderLines(selector: ModelSelectorComponent): string[] {
	return stripAnsi(selector.render(120).join("\n")).split("\n");
}

function findScopeLine(lines: string[]): string | undefined {
	// Text.render() pads lines to the full render width, so trim trailing padding.
	return lines.find((line) => line.startsWith("Scope:"))?.trimEnd();
}

describe("ModelSelectorComponent default scope", () => {
	const a1 = testModel("provider-a", "a1");
	const a2 = testModel("provider-a", "a2");
	const b1 = testModel("provider-b", "b1");
	let runtime: ModelRuntime;

	beforeAll(() => {
		initTheme("dark");
	});

	beforeEach(() => {
		// Keybindings are a global singleton; reset between tests for isolation.
		setKeybindings(new KeybindingsManager());
		runtime = createFakeRuntime([a1, a2, b1]);
	});

	it("defaults to showing only the current provider's models", async () => {
		const selector = new ModelSelectorComponent(
			createFakeTui(),
			a1,
			runtime,
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
			runtime,
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
		// A model whose provider the runtime knows nothing about.
		const orphanModel = testModel("orphan-provider", "orphan-1");

		const selector = new ModelSelectorComponent(
			createFakeTui(),
			orphanModel,
			runtime,
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
		const selector = new ModelSelectorComponent(
			createFakeTui(),
			a1,
			runtime,
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
		const selector = new ModelSelectorComponent(
			createFakeTui(),
			a1,
			runtime,
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
		const selector = new ModelSelectorComponent(
			createFakeTui(),
			a1,
			runtime,
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
