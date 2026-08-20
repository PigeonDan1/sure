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

/** Everything the fake gateway was asked, in arrival order. Reset per test. */
interface RecordedRequest {
	method: string;
	path: string;
	body: Record<string, unknown> | undefined;
}
let requests: RecordedRequest[] = [];

function isStreamed(request: RecordedRequest): boolean {
	return request.method === "POST" && request.path === "/v1/chat/completions" && request.body?.stream === true;
}

/** One SSE chat completion that says ok and stops. */
const STREAMED_OK = [
	JSON.stringify({
		id: "chatcmpl-e2e",
		object: "chat.completion.chunk",
		choices: [{ index: 0, delta: { role: "assistant", content: "ok" }, finish_reason: null }],
	}),
	JSON.stringify({
		id: "chatcmpl-e2e",
		object: "chat.completion.chunk",
		choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
		usage: { prompt_tokens: 1, completion_tokens: 1 },
	}),
];

beforeEach(async () => {
	tempDir = join(tmpdir(), `pi-sure-e2e-${Date.now()}-${Math.random().toString(36).slice(2)}`);
	mkdirSync(tempDir, { recursive: true });
	modelsPath = join(tempDir, "models.json");
	requests = [];
	server = createServer((req, res) => {
		const chunks: Buffer[] = [];
		req.on("data", (chunk: Buffer) => chunks.push(chunk));
		req.on("end", () => {
			const body = Buffer.concat(chunks).toString("utf-8");
			let parsed: Record<string, unknown> | undefined;
			try {
				parsed = body.length > 0 ? (JSON.parse(body) as Record<string, unknown>) : undefined;
			} catch {
				parsed = undefined;
			}
			requests.push({ method: req.method ?? "", path: req.url ?? "", body: parsed });
			if (req.headers.authorization !== "Bearer sk-e2e") {
				res.statusCode = 401;
				res.end("unauthorized");
				return;
			}
			if (req.url === "/v1/models") {
				res.setHeader("content-type", "application/json");
				res.end(JSON.stringify({ data: [{ id: "alpha", display_name: "Alpha" }, { id: "beta" }] }));
				return;
			}
			if (req.method === "POST" && req.url === "/v1/chat/completions") {
				// The closing round trip goes through pi-ai itself, which always streams.
				if (parsed?.stream === true) {
					res.setHeader("content-type", "text/event-stream");
					for (const event of STREAMED_OK) {
						res.write(`data: ${event}\n\n`);
					}
					res.end("data: [DONE]\n\n");
					return;
				}
				// The capability probe. Answering chat/completions with no reasoning tokens makes
				// /sure_init record openai-completions and no usable effort level.
				res.setHeader("content-type", "application/json");
				res.end(
					JSON.stringify({
						id: "chatcmpl-e2e",
						choices: [{ index: 0, message: { role: "assistant", content: "ok" }, finish_reason: "stop" }],
						usage: { prompt_tokens: 1, completion_tokens: 1, completion_tokens_details: { reasoning_tokens: 0 } },
					}),
				);
				return;
			}
			res.statusCode = 404;
			res.end("not found");
		});
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
		expect(written.providers.e2egw.models.map((model: { id: string }) => model.id)).toEqual(["alpha", "beta"]);
		expect(written.providers.e2egw.models[0]).toMatchObject({
			id: "alpha",
			name: "Alpha",
			api: "openai-completions",
			reasoning: false,
		});
		expect(written.providers.e2egw.models[1]).toEqual({ id: "beta" });
		expect(settingsManager.getDefaultProvider()).toBe("e2egw");
		expect(settingsManager.getDefaultModel()).toBe("alpha");
		const manifest = JSON.parse(readFileSync(join(tempDir, ".sure", "init.json"), "utf-8"));
		expect(manifest.defaultProvider).toBe("e2egw");
		expect(manifest.defaultModel).toBe("alpha");
		expect(manifest.defaultApi).toBe("openai-completions");
		// The probe builds its requests by hand; the round trip must go out afterwards through
		// pi-ai itself, or nothing has proven the written configuration works.
		const probeIndex = requests.findIndex(
			(request) => request.method === "POST" && request.path === "/v1/chat/completions" && !isStreamed(request),
		);
		const roundTripIndex = requests.findIndex(isStreamed);
		expect(probeIndex).toBeGreaterThanOrEqual(0);
		expect(roundTripIndex).toBeGreaterThan(probeIndex);
		expect(JSON.stringify(requests[roundTripIndex].body?.messages)).toContain("say ok");
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
		expect(written.providers.e2egw.models.map((model: { id: string }) => model.id)).toEqual(["alpha", "beta"]);
		expect(written.providers.e2egw.models[0]).toEqual({ id: "alpha", name: "Alpha" });
		expect(written.providers.e2egw.models[1]).toMatchObject({ id: "beta", api: "openai-completions" });
	});
});
