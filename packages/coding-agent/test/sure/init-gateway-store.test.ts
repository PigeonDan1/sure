import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
	listGatewayProviders,
	mergeProviderCompat,
	readGatewayModels,
	upsertProviderModel,
	writeGatewayProvider,
} from "../../src/core/sure/init-gateway-store.ts";

let tempDir: string;
let modelsPath: string;

beforeEach(() => {
	tempDir = join(tmpdir(), `pi-sure-gw-${Date.now()}-${Math.random().toString(36).slice(2)}`);
	mkdirSync(tempDir, { recursive: true });
	modelsPath = join(tempDir, "agent", "models.json");
});

afterEach(() => {
	if (existsSync(tempDir)) rmSync(tempDir, { recursive: true, force: true });
});

function writeModelsFile(value: unknown): void {
	mkdirSync(join(tempDir, "agent"), { recursive: true });
	writeFileSync(modelsPath, `${JSON.stringify(value, null, 2)}\n`, "utf-8");
}

describe("listGatewayProviders", () => {
	it("returns an empty list when the file is missing or broken", () => {
		expect(listGatewayProviders(modelsPath)).toEqual([]);
		mkdirSync(join(tempDir, "agent"), { recursive: true });
		writeFileSync(modelsPath, "{ not json", "utf-8");
		expect(listGatewayProviders(modelsPath)).toEqual([]);
	});

	it("lists only non-built-in providers that have a baseUrl", () => {
		writeModelsFile({
			providers: {
				openai: { modelOverrides: { "gpt-5.5": { contextWindow: 400000 } } },
				relay: { baseUrl: "https://gw.example.com/v1", apiKey: "sk-1", models: [{ id: "a" }, { id: "b" }] },
				broken: { models: [{ id: "c" }] },
			},
		});
		expect(listGatewayProviders(modelsPath)).toEqual([
			{ name: "relay", baseUrl: "https://gw.example.com/v1", modelCount: 2, hasApiKey: true },
		]);
	});

	it("tolerates comment-bearing models.json when reading", () => {
		mkdirSync(join(tempDir, "agent"), { recursive: true });
		writeFileSync(
			modelsPath,
			`{\n\t// gateway config\n\t"providers": { "relay": { "baseUrl": "https://gw.example.com/v1", "models": [{ "id": "a" }] } }\n}\n`,
			"utf-8",
		);
		expect(listGatewayProviders(modelsPath)).toEqual([
			{ name: "relay", baseUrl: "https://gw.example.com/v1", modelCount: 1, hasApiKey: false },
		]);
	});
});

describe("readGatewayModels", () => {
	it("returns the recorded models with names, and [] when absent", () => {
		writeModelsFile({
			providers: { relay: { baseUrl: "https://gw.example.com/v1", models: [{ id: "a", name: "A" }, { id: "b" }] } },
		});
		expect(readGatewayModels("relay", modelsPath)).toEqual([{ id: "a", name: "A" }, { id: "b" }]);
		expect(readGatewayModels("ghost", modelsPath)).toEqual([]);
	});
});

describe("writeGatewayProvider", () => {
	it("creates the file and directory from scratch", () => {
		writeGatewayProvider(
			{ name: "relay", baseUrl: "https://gw.example.com/v1", apiKey: "sk-1", models: [{ id: "a", name: "A" }] },
			modelsPath,
		);
		const parsed = JSON.parse(readFileSync(modelsPath, "utf-8"));
		expect(parsed.providers.relay).toEqual({
			baseUrl: "https://gw.example.com/v1",
			api: "openai-completions",
			apiKey: "sk-1",
			models: [{ id: "a", name: "A" }],
		});
	});

	it("replaces the target provider's model list wholesale and keeps everything else", () => {
		writeModelsFile({
			note: "user data",
			providers: {
				other: { baseUrl: "https://other.example.com/v1", models: [{ id: "keep" }] },
				relay: {
					baseUrl: "https://old.example.com/v1",
					api: "anthropic-messages",
					headers: { "x-custom": "1" },
					models: [{ id: "stale" }],
				},
			},
		});
		writeGatewayProvider(
			{ name: "relay", baseUrl: "https://gw.example.com/v1", models: [{ id: "fresh" }] },
			modelsPath,
		);
		const parsed = JSON.parse(readFileSync(modelsPath, "utf-8"));
		expect(parsed.note).toBe("user data");
		expect(parsed.providers.other.models).toEqual([{ id: "keep" }]);
		expect(parsed.providers.relay.baseUrl).toBe("https://gw.example.com/v1");
		expect(parsed.providers.relay.api).toBe("anthropic-messages");
		expect(parsed.providers.relay.headers).toEqual({ "x-custom": "1" });
		expect(parsed.providers.relay.models).toEqual([{ id: "fresh" }]);
		expect(parsed.providers.relay.apiKey).toBeUndefined();
	});

	it("refuses to rewrite a comment-bearing file", () => {
		mkdirSync(join(tempDir, "agent"), { recursive: true });
		writeFileSync(modelsPath, `{\n\t// hands off\n\t"providers": {}\n}\n`, "utf-8");
		expect(() =>
			writeGatewayProvider(
				{ name: "relay", baseUrl: "https://gw.example.com/v1", models: [{ id: "a" }] },
				modelsPath,
			),
		).toThrow(/comments/);
	});

	it("rewrites a file whose only JSONC feature is a trailing comma", () => {
		mkdirSync(join(tempDir, "agent"), { recursive: true });
		writeFileSync(
			modelsPath,
			`{\n\t"providers": { "relay": { "baseUrl": "https://gw.example.com/v1", "models": [{ "id": "a" },] } }\n}\n`,
			"utf-8",
		);
		writeGatewayProvider({ name: "relay", baseUrl: "https://gw.example.com/v1", models: [{ id: "b" }] }, modelsPath);
		const parsed = JSON.parse(readFileSync(modelsPath, "utf-8"));
		expect(parsed.providers.relay.models).toEqual([{ id: "b" }]);
	});

	it("refuses files with block comments", () => {
		mkdirSync(join(tempDir, "agent"), { recursive: true });
		writeFileSync(modelsPath, `{\n\t/* hands off */\n\t"providers": {}\n}\n`, "utf-8");
		expect(() =>
			writeGatewayProvider(
				{ name: "relay", baseUrl: "https://gw.example.com/v1", models: [{ id: "a" }] },
				modelsPath,
			),
		).toThrow(/comments/);
	});

	it("does not mistake // inside a string value for a comment", () => {
		mkdirSync(join(tempDir, "agent"), { recursive: true });
		writeFileSync(
			modelsPath,
			`{\n\t"providers": { "relay": { "baseUrl": "https://gw.example.com/v1", "models": [{ "id": "a" }] } }\n}\n`,
			"utf-8",
		);
		writeGatewayProvider({ name: "relay", baseUrl: "https://gw.example.com/v2", models: [{ id: "b" }] }, modelsPath);
		const parsed = JSON.parse(readFileSync(modelsPath, "utf-8"));
		expect(parsed.providers.relay.baseUrl).toBe("https://gw.example.com/v2");
	});

	it("throws with the file path on invalid JSON", () => {
		mkdirSync(join(tempDir, "agent"), { recursive: true });
		writeFileSync(modelsPath, "{ not json", "utf-8");
		expect(() =>
			writeGatewayProvider(
				{ name: "relay", baseUrl: "https://gw.example.com/v1", models: [{ id: "a" }] },
				modelsPath,
			),
		).toThrow();
	});
});

describe("mergeProviderCompat", () => {
	it("records the setting at the provider, leaving the models and other fields alone", () => {
		writeModelsFile({
			note: "keep",
			providers: {
				apifusion: {
					baseUrl: "https://gw.example.com/v1",
					apiKey: "sk-1",
					models: [{ id: "gpt-5.6-sol", api: "openai-completions" }],
				},
			},
		});
		mergeProviderCompat("apifusion", { supportsDeveloperRole: false }, modelsPath);
		const parsed = JSON.parse(readFileSync(modelsPath, "utf-8"));
		// Provider level, not model level: what a relay refuses, it refuses for every model on it.
		expect(parsed.providers.apifusion.compat).toEqual({ supportsDeveloperRole: false });
		expect(parsed.providers.apifusion.apiKey).toBe("sk-1");
		expect(parsed.providers.apifusion.models).toEqual([{ id: "gpt-5.6-sol", api: "openai-completions" }]);
		expect(parsed.note).toBe("keep");
	});

	it("keeps settings already recorded", () => {
		writeModelsFile({
			providers: {
				apifusion: {
					baseUrl: "https://gw.example.com/v1",
					compat: { supportsDeveloperRole: false, thinkingFormat: "deepseek" },
					models: [],
				},
			},
		});
		mergeProviderCompat("apifusion", { supportsStore: false }, modelsPath);
		const parsed = JSON.parse(readFileSync(modelsPath, "utf-8"));
		// Each round trip settles one thing. Replacing the object would undo the previous one
		// and the negotiation would never converge.
		expect(parsed.providers.apifusion.compat).toEqual({
			supportsDeveloperRole: false,
			thinkingFormat: "deepseek",
			supportsStore: false,
		});
	});

	it("survives a later model refresh", () => {
		writeModelsFile({
			providers: { apifusion: { baseUrl: "https://gw.example.com/v1", models: [] } },
		});
		mergeProviderCompat("apifusion", { supportsDeveloperRole: false }, modelsPath);
		upsertProviderModel("apifusion", "https://gw.example.com/v1", { id: "gpt-5.6-sol" }, modelsPath);
		writeGatewayProvider(
			{ name: "apifusion", baseUrl: "https://gw.example.com/v1", models: [{ id: "gpt-5.6-sol" }] },
			modelsPath,
		);
		const parsed = JSON.parse(readFileSync(modelsPath, "utf-8"));
		expect(parsed.providers.apifusion.compat).toEqual({ supportsDeveloperRole: false });
	});
});

describe("upsertProviderModel", () => {
	it("preserves provider fields and other models while refreshing one model profile", () => {
		writeModelsFile({
			note: "keep",
			providers: {
				openai: {
					baseUrl: "https://old.example.com/v1",
					headers: { "x-route": "sure" },
					models: [
						{ id: "keep", api: "openai-completions" },
						{ id: "gpt-5.6-luna", name: "old", contextWindow: 1, custom: "preserve" },
					],
				},
			},
		});
		upsertProviderModel(
			"openai",
			"https://gw.example.com/v1",
			{
				id: "gpt-5.6-luna",
				name: "GPT-5.6 Luna",
				api: "openai-responses",
				reasoning: true,
				contextWindow: 272_000,
			},
			modelsPath,
		);
		const parsed = JSON.parse(readFileSync(modelsPath, "utf-8"));
		expect(parsed.note).toBe("keep");
		expect(parsed.providers.openai.headers).toEqual({ "x-route": "sure" });
		expect(parsed.providers.openai.models[0]).toEqual({ id: "keep", api: "openai-completions" });
		expect(parsed.providers.openai.models[1]).toMatchObject({
			id: "gpt-5.6-luna",
			name: "GPT-5.6 Luna",
			api: "openai-responses",
			reasoning: true,
			contextWindow: 272_000,
			custom: "preserve",
		});
	});
});

describe("writeGatewayProvider merge behaviour", () => {
	it("keeps existing per-model annotations when the live list is refreshed", () => {
		const path = join(tempDir, "models.json");
		writeFileSync(
			path,
			`${JSON.stringify(
				{
					providers: {
						apifusion: {
							baseUrl: "https://gw.example.com/v1",
							api: "openai-completions",
							models: [
								{
									id: "gpt-5.6-sol",
									api: "openai-responses",
									reasoning: true,
									thinkingLevelMap: { off: "none", high: "high", xhigh: "xhigh" },
									contextWindow: 400000,
								},
								{ id: "retired-model" },
							],
						},
					},
				},
				null,
				2,
			)}\n`,
			"utf-8",
		);

		writeGatewayProvider(
			{
				name: "apifusion",
				baseUrl: "https://gw.example.com/v1",
				models: [{ id: "gpt-5.6-sol", name: "GPT-5.6 Sol" }, { id: "deepseek-chat" }],
			},
			path,
		);

		const written = JSON.parse(readFileSync(path, "utf-8"));
		const models = written.providers.apifusion.models as Array<Record<string, unknown>>;
		expect(models.map((model) => model.id)).toEqual(["gpt-5.6-sol", "deepseek-chat"]);
		expect(models[0]).toMatchObject({
			id: "gpt-5.6-sol",
			name: "GPT-5.6 Sol",
			api: "openai-responses",
			reasoning: true,
			contextWindow: 400000,
		});
		expect(models[0].thinkingLevelMap).toEqual({ off: "none", high: "high", xhigh: "xhigh" });
		expect(models[1]).toEqual({ id: "deepseek-chat" });
	});
});

describe("upsertProviderModel defaults", () => {
	it("fills the context window and output cap only when they are missing", () => {
		const path = join(tempDir, "models.json");
		writeFileSync(
			path,
			`${JSON.stringify(
				{
					providers: {
						apifusion: { baseUrl: "https://gw.example.com/v1", models: [{ id: "kept", contextWindow: 400000 }] },
					},
				},
				null,
				2,
			)}\n`,
			"utf-8",
		);

		upsertProviderModel("apifusion", "https://gw.example.com/v1", { id: "fresh", api: "openai-responses" }, path);
		upsertProviderModel("apifusion", "https://gw.example.com/v1", { id: "kept", api: "openai-responses" }, path);

		const models = JSON.parse(readFileSync(path, "utf-8")).providers.apifusion.models as Array<
			Record<string, unknown>
		>;
		const fresh = models.find((model) => model.id === "fresh");
		const kept = models.find((model) => model.id === "kept");
		expect(fresh).toMatchObject({ contextWindow: 256000, maxTokens: 64000 });
		expect(kept).toMatchObject({ contextWindow: 400000, maxTokens: 64000 });
	});
});
