import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
	listGatewayProviders,
	readGatewayModels,
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
