import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { AuthStorage } from "../../../src/core/auth-storage.ts";
import { createModelRegistry } from "../../model-runtime-test-utils.ts";
import { createHarness } from "../harness.ts";

describe("regression #5661: uppercase models.json header values", () => {
	const cleanups: Array<() => void> = [];

	afterEach(() => {
		while (cleanups.length > 0) {
			cleanups.pop()?.();
		}
	});

	it("keeps uppercase header strings as literals even when they match env var names", async () => {
		const harness = await createHarness({ withConfiguredAuth: false });
		cleanups.push(harness.cleanup);

		const envKeys = ["CUSTOM_API_KEY", "BEARER"];
		const savedEnv: Record<string, string | undefined> = {};
		for (const key of envKeys) {
			savedEnv[key] = process.env[key];
			process.env[key] = `env-${key}`;
		}
		cleanups.push(() => {
			for (const key of envKeys) {
				if (savedEnv[key] === undefined) {
					delete process.env[key];
				} else {
					process.env[key] = savedEnv[key];
				}
			}
		});

		const modelsPath = join(harness.tempDir, "models.json");
		writeFileSync(
			modelsPath,
			`${JSON.stringify(
				{
					providers: {
						"my-provider": {
							baseUrl: "https://example.com/v1",
							apiKey: "CUSTOM_API_KEY",
							api: "openai-completions",
							headers: { Authorization: "BEARER" },
							models: [{ id: "my-model" }],
						},
					},
				},
				null,
				2,
			)}\n`,
			"utf-8",
		);

		const migrated = JSON.parse(readFileSync(modelsPath, "utf-8")) as {
			providers: Record<string, { apiKey?: string; headers?: Record<string, string> }>;
		};
		expect(migrated.providers["my-provider"]?.apiKey).toBe("CUSTOM_API_KEY");
		expect(migrated.providers["my-provider"]?.headers?.Authorization).toBe("BEARER");

		const registry = await createModelRegistry(AuthStorage.create(join(harness.tempDir, "auth.json")), modelsPath);
		const model = registry.find("my-provider", "my-model");
		expect(model).toBeDefined();
		expect(await registry.getApiKeyAndHeaders(model!)).toMatchObject({
			ok: true,
			apiKey: "CUSTOM_API_KEY",
			headers: { Authorization: "BEARER" },
		});
	});
});
