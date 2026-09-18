import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthStorage } from "../../src/core/auth-storage.ts";
import { needsCapabilityProbe, SURE_INIT_PROVIDER_OPTIONS } from "../../src/core/sure/init.ts";
import { listBuiltInProviderModels } from "../../src/core/sure/init-model-listing.ts";
import { createInMemoryModelRegistry } from "../model-runtime-test-utils.ts";

afterEach(() => {
	vi.unstubAllGlobals();
});

describe("SURE init provider options", () => {
	it("offers the built-in DeepSeek provider with its catalog default", () => {
		expect(SURE_INIT_PROVIDER_OPTIONS.find((option) => option.id === "deepseek")).toEqual({
			id: "deepseek",
			name: "DeepSeek",
			provider: "deepseek",
			defaultModel: "deepseek-v4-flash",
			authType: "api_key",
			description: "Standard DeepSeek API",
		});
		expect(needsCapabilityProbe("deepseek", "deepseek-v4-flash")).toBe(false);
	});

	it("queries the official DeepSeek model list when auth is configured", async () => {
		const option = SURE_INIT_PROVIDER_OPTIONS.find((entry) => entry.id === "deepseek");
		if (!option) throw new Error("missing DeepSeek option");
		const authStorage = AuthStorage.inMemory({ deepseek: { type: "api_key", key: "sk-live" } });
		const registry = await createInMemoryModelRegistry(authStorage);
		const fetchMock = vi.fn(async () => Response.json({ data: [{ id: "deepseek-v4-flash" }] }));
		vi.stubGlobal("fetch", fetchMock);

		const listing = await listBuiltInProviderModels(option, registry);

		expect(listing).toEqual({ source: "live", models: [{ id: "deepseek-v4-flash" }] });
		expect(fetchMock).toHaveBeenCalledWith(
			"https://api.deepseek.com/v1/models",
			expect.objectContaining({
				headers: expect.objectContaining({ Authorization: "Bearer sk-live" }),
			}),
		);
	});
});
