import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthStorage } from "../../src/core/auth-storage.ts";
import { ModelRegistry } from "../../src/core/model-registry.ts";
import { SURE_INIT_PROVIDER_OPTIONS } from "../../src/core/sure/init.ts";
import {
	describeListingSource,
	fetchAnthropicModels,
	fetchOpenAICompatibleModels,
	listBuiltInProviderModels,
	openAICompatibleModelsUrl,
	openAICompatibleUrl,
} from "../../src/core/sure/init-model-listing.ts";

function optionById(id: string) {
	const option = SURE_INIT_PROVIDER_OPTIONS.find((entry) => entry.id === id);
	if (!option) throw new Error(`missing option ${id}`);
	return option;
}

afterEach(() => {
	vi.unstubAllGlobals();
});

describe("openAICompatibleUrl", () => {
	it("appends the path when the base already ends with a version segment", () => {
		expect(openAICompatibleUrl("https://api.openai.com/v1", "models")).toBe("https://api.openai.com/v1/models");
		expect(openAICompatibleUrl("http://127.0.0.1:9999/v1/", "chat/completions")).toBe(
			"http://127.0.0.1:9999/v1/chat/completions",
		);
	});

	it("inserts /v1 when the base has no version segment", () => {
		expect(openAICompatibleUrl("https://gw.example.com", "responses")).toBe("https://gw.example.com/v1/responses");
	});

	it("tolerates a leading slash on the path", () => {
		expect(openAICompatibleUrl("https://gw.example.com/v1", "/messages")).toBe("https://gw.example.com/v1/messages");
	});
});

describe("openAICompatibleModelsUrl", () => {
	it("still resolves the model list URL", () => {
		expect(openAICompatibleModelsUrl("https://gw.example.com")).toBe("https://gw.example.com/v1/models");
	});
});

describe("fetchOpenAICompatibleModels", () => {
	it("returns ids and display names from the data array", async () => {
		const fetchMock = vi.fn(async () =>
			Response.json({ data: [{ id: "alpha", display_name: "Alpha" }, { id: "beta" }] }),
		);
		vi.stubGlobal("fetch", fetchMock);
		const models = await fetchOpenAICompatibleModels("https://gw.example.com/v1", "sk-test");
		expect(models).toEqual([{ id: "alpha", name: "Alpha" }, { id: "beta" }]);
		expect(fetchMock).toHaveBeenCalledWith(
			"https://gw.example.com/v1/models",
			expect.objectContaining({
				headers: expect.objectContaining({ Authorization: "Bearer sk-test" }),
			}),
		);
	});

	it("returns bare ids without inventing runtime metadata", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn(async () => Response.json({ data: [{ id: "gpt-5.6-sol" }, { id: "gpt-5.6-luna" }] })),
		);
		const models = await fetchOpenAICompatibleModels("https://gw.example.com/v1", "sk-test");
		expect(models).toEqual([{ id: "gpt-5.6-sol" }, { id: "gpt-5.6-luna" }]);
	});

	it("throws on non-2xx responses", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn(async () => new Response("denied", { status: 401 })),
		);
		await expect(fetchOpenAICompatibleModels("https://gw.example.com/v1", "bad")).rejects.toThrow("HTTP 401");
	});

	it("throws when the payload has no usable ids", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn(async () => Response.json({ data: [] })),
		);
		await expect(fetchOpenAICompatibleModels("https://gw.example.com/v1", "key")).rejects.toThrow("no model ids");
	});
});

describe("fetchAnthropicModels", () => {
	it("sends x-api-key and anthropic-version headers", async () => {
		const fetchMock = vi.fn(async () => Response.json({ data: [{ id: "claude-x", display_name: "Claude X" }] }));
		vi.stubGlobal("fetch", fetchMock);
		const models = await fetchAnthropicModels("sk-ant");
		expect(models).toEqual([{ id: "claude-x", name: "Claude X" }]);
		expect(fetchMock).toHaveBeenCalledWith(
			"https://api.anthropic.com/v1/models",
			expect.objectContaining({
				headers: expect.objectContaining({ "x-api-key": "sk-ant", "anthropic-version": "2023-06-01" }),
			}),
		);
	});
});

describe("listBuiltInProviderModels", () => {
	it("goes live for openai when a key is configured", async () => {
		const authStorage = AuthStorage.inMemory({ openai: { type: "api_key", key: "sk-live" } });
		const registry = ModelRegistry.inMemory(authStorage);
		const fetchMock = vi.fn(async () => Response.json({ data: [{ id: "gpt-fresh" }] }));
		vi.stubGlobal("fetch", fetchMock);
		const listing = await listBuiltInProviderModels(optionById("openai"), registry);
		expect(listing.source).toBe("live");
		expect(listing.models).toEqual([{ id: "gpt-fresh" }]);
		expect(fetchMock).toHaveBeenCalledWith("https://api.openai.com/v1/models", expect.anything());
	});

	it("falls back to the built-in catalog with an error note when the live query fails", async () => {
		const authStorage = AuthStorage.inMemory({ openai: { type: "api_key", key: "sk-live" } });
		const registry = ModelRegistry.inMemory(authStorage);
		vi.stubGlobal(
			"fetch",
			vi.fn(async () => new Response("boom", { status: 500 })),
		);
		const listing = await listBuiltInProviderModels(optionById("openai"), registry);
		expect(listing.source).toBe("builtin");
		expect(listing.error).toContain("HTTP 500");
		expect(listing.models.length).toBeGreaterThan(0);
		expect(listing.models.every((model) => typeof model.id === "string")).toBe(true);
	});

	it("uses the built-in catalog for codex without touching the network", async () => {
		const registry = ModelRegistry.inMemory(AuthStorage.inMemory());
		const fetchMock = vi.fn();
		vi.stubGlobal("fetch", fetchMock);
		const listing = await listBuiltInProviderModels(optionById("codex"), registry);
		expect(listing.source).toBe("builtin");
		expect(listing.models.length).toBeGreaterThan(0);
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it("labels copilot's registry list as live (account-filtered at login)", async () => {
		const registry = ModelRegistry.inMemory(AuthStorage.inMemory());
		const fetchMock = vi.fn();
		vi.stubGlobal("fetch", fetchMock);
		const listing = await listBuiltInProviderModels(optionById("copilot"), registry);
		expect(listing.source).toBe("live");
		expect(fetchMock).not.toHaveBeenCalled();
	});
});

describe("describeListingSource", () => {
	it("mentions the failure when a fallback happened", () => {
		expect(describeListingSource({ source: "builtin", models: [], error: "HTTP 500" })).toContain("HTTP 500");
		expect(describeListingSource({ source: "live", models: [] })).toContain("live");
	});
});
