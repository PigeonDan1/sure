import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthStorage } from "../../src/core/auth-storage.ts";
import type { ExtensionCommandContext } from "../../src/core/extensions/types.ts";
import { ModelRegistry } from "../../src/core/model-registry.ts";
import { SettingsManager } from "../../src/core/settings-manager.ts";
import { runSureInit } from "../../src/core/sure/init.ts";

let tempDir: string;
let modelsPath: string;
let server: ReturnType<typeof createServer>;
let baseUrl: string;

beforeEach(async () => {
	tempDir = join(tmpdir(), `pi-sure-e2e-${Date.now()}-${Math.random().toString(36).slice(2)}`);
	mkdirSync(tempDir, { recursive: true });
	modelsPath = join(tempDir, "models.json");
	server = createServer((req, res) => {
		if (req.url === "/v1/models" && req.headers.authorization === "Bearer sk-e2e") {
			res.setHeader("content-type", "application/json");
			res.end(JSON.stringify({ data: [{ id: "alpha", display_name: "Alpha" }, { id: "beta" }] }));
			return;
		}
		res.statusCode = 401;
		res.end("unauthorized");
	});
	await new Promise<void>((resolve) => {
		server.listen(0, "127.0.0.1", resolve);
	});
	baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}/v1`;
});

afterEach(async () => {
	await new Promise<void>((resolve, reject) => {
		server.close((error) => (error ? reject(error) : resolve()));
	});
	if (existsSync(tempDir)) rmSync(tempDir, { recursive: true, force: true });
});

function makeContext(hasUI: boolean) {
	const authStorage = AuthStorage.inMemory();
	const modelRegistry = ModelRegistry.create(authStorage, modelsPath);
	const settingsManager = SettingsManager.inMemory();
	const ui = { select: vi.fn(), input: vi.fn(), confirm: vi.fn(), notify: vi.fn() };
	const ctx = {
		ui,
		hasUI,
		cwd: tempDir,
		modelRegistry,
		isProjectTrusted: () => true,
	} as unknown as ExtensionCommandContext;
	return { ctx, settingsManager, ui };
}

describe("init gateway E2E over real HTTP", () => {
	it("creates a gateway one-shot: fetches live models, writes models.json, sets defaults, writes the manifest", async () => {
		const { ctx, settingsManager } = makeContext(false);
		const result = await runSureInit({
			ctx,
			args: `--option custom --name e2egw --base-url ${baseUrl} --api-key sk-e2e --model alpha`,
			settingsManager,
			modelsJsonPath: modelsPath,
		});
		expect(result.success).toBe(true);
		const written = JSON.parse(readFileSync(modelsPath, "utf-8"));
		expect(written.providers.e2egw.baseUrl).toBe(baseUrl);
		expect(written.providers.e2egw.apiKey).toBe("sk-e2e");
		expect(written.providers.e2egw.models).toEqual([{ id: "alpha", name: "Alpha" }, { id: "beta" }]);
		expect(settingsManager.getDefaultProvider()).toBe("e2egw");
		expect(settingsManager.getDefaultModel()).toBe("alpha");
		const manifest = JSON.parse(readFileSync(join(tempDir, ".sure", "init.json"), "utf-8"));
		expect(manifest.defaultProvider).toBe("e2egw");
		expect(manifest.defaultModel).toBe("alpha");
	});

	it("surfaces the HTTP status when the key is wrong", async () => {
		const { ctx, settingsManager } = makeContext(false);
		const result = await runSureInit({
			ctx,
			args: `--option custom --name e2egw --base-url ${baseUrl} --api-key sk-wrong --model alpha`,
			settingsManager,
			modelsJsonPath: modelsPath,
		});
		expect(result.success).toBe(false);
		expect(result.message).toContain("401");
		expect(existsSync(modelsPath)).toBe(false);
	});

	it("refreshes an existing gateway interactively and replaces its stale model list", async () => {
		writeFileSync(
			modelsPath,
			`${JSON.stringify(
				{
					providers: {
						e2egw: { baseUrl, api: "openai-completions", apiKey: "sk-e2e", models: [{ id: "stale" }] },
					},
				},
				null,
				2,
			)}\n`,
			"utf-8",
		);
		const { ctx, settingsManager, ui } = makeContext(true);
		ui.select.mockResolvedValueOnce(`e2egw (custom): ${baseUrl}, 1 models`).mockResolvedValueOnce("beta");
		const result = await runSureInit({ ctx, settingsManager, modelsJsonPath: modelsPath });
		expect(result.success).toBe(true);
		expect(result.message).toContain("live");
		expect(settingsManager.getDefaultModel()).toBe("beta");
		const written = JSON.parse(readFileSync(modelsPath, "utf-8"));
		expect(written.providers.e2egw.models).toEqual([{ id: "alpha", name: "Alpha" }, { id: "beta" }]);
	});
});
