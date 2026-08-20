import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
	applyProbedModel,
	defaultThinkingLevel,
	probeFailureMessage,
	probeWholeGateway,
	verifyModelRoundTrip,
} from "../../src/core/sure/init-apply.ts";

describe("defaultThinkingLevel", () => {
	it("clamps medium up to the lowest supported level above it", () => {
		expect(defaultThinkingLevel(["xhigh", "high", "off"])).toBe("high");
	});

	it("returns medium when everything is supported", () => {
		expect(defaultThinkingLevel(["xhigh", "high", "medium", "low", "minimal", "off"])).toBe("medium");
	});

	it("clamps down when nothing above medium is supported", () => {
		expect(defaultThinkingLevel(["low", "minimal"])).toBe("low");
	});

	it("returns undefined when the model only supports off", () => {
		expect(defaultThinkingLevel(["off"])).toBeUndefined();
		expect(defaultThinkingLevel([])).toBeUndefined();
	});

	it("never auto-selects off", () => {
		expect(defaultThinkingLevel(["off", "xhigh"])).toBe("xhigh");
	});
});

describe("probeFailureMessage", () => {
	it("tells the user what to do for each verdict", () => {
		expect(
			probeFailureMessage({ kind: "no-channel", detail: "无可用渠道", steps: [] }, "gpt-5.6-sol-openai-compact"),
		).toContain("没有可用渠道");
		expect(probeFailureMessage({ kind: "bad-key", detail: "Invalid token", steps: [] }, "gpt-5.6-sol")).toContain(
			"换一把 key",
		);
		// 2026-08-18: sol is hit by the same client check, so the message must not send anyone there.
		const clientRejected = probeFailureMessage(
			{ kind: "client-rejected", detail: "客户端异常", steps: [] },
			"gpt-5.6-luna",
		);
		expect(clientRejected).toContain("客户端");
		expect(clientRejected).not.toContain("gpt-5.6-sol");
		expect(
			probeFailureMessage({ kind: "flaky", detail: "effort 探测 6 档里 3 档被拒", steps: [] }, "gpt-5.6-sol"),
		).toContain("时通时不通");
		expect(probeFailureMessage({ kind: "no-protocol", detail: "三条都不通", steps: [] }, "x")).toContain("协议");
		expect(probeFailureMessage({ kind: "unreachable", detail: "ENOTFOUND", steps: [] }, "x")).toContain("连不上");
	});
});

let tempDir: string;

beforeEach(() => {
	tempDir = join(tmpdir(), `pi-sure-apply-${process.pid}-${Math.random().toString(36).slice(2)}`);
	mkdirSync(tempDir, { recursive: true });
});

afterEach(() => {
	if (existsSync(tempDir)) rmSync(tempDir, { recursive: true, force: true });
});

function makeApplyContext(options?: { hasUI?: boolean; pick?: string }) {
	const select = vi.fn(
		async (_title: string, choices: string[]): Promise<string | undefined> => options?.pick ?? choices[0],
	);
	const notify = vi.fn();
	const refresh = vi.fn();
	return {
		ctx: { hasUI: options?.hasUI ?? true, ui: { select, notify }, modelRegistry: { refresh } },
		select,
		notify,
		refresh,
	};
}

const SOL_RESULT = {
	api: "openai-responses" as const,
	reasoning: true,
	thinkingLevelMap: { off: "none", minimal: null, low: null, medium: null, high: "high", xhigh: "xhigh" },
	supportedLevels: ["xhigh", "high", "off"] as const,
	steps: [
		{ api: "openai-completions" as const, status: 400, verdict: "wrong-protocol" as const, detail: "" },
		{ api: "openai-responses" as const, status: 200, verdict: "ok" as const, detail: "" },
	],
	effortNote: "effort 上游确认:xhigh、high",
};

function fakeProbe(outcome: unknown) {
	return vi.fn(
		async () => outcome,
	) as unknown as typeof import("../../src/core/sure/init-capability-probe.ts").probeModelCapability;
}

describe("applyProbedModel", () => {
	it("writes the probed protocol and level map, then asks for the effort", async () => {
		const modelsPath = join(tempDir, "models.json");
		const { ctx, select, refresh } = makeApplyContext({ pick: "high" });
		const probe = fakeProbe({ ok: true, result: { ...SOL_RESULT, supportedLevels: ["xhigh", "high", "off"] } });
		const outcome = await applyProbedModel({
			ctx,
			providerName: "apifusion",
			baseUrl: "https://gw.example.com/v1",
			apiKey: "sk-1",
			modelId: "gpt-5.6-sol",
			modelName: "GPT-5.6 Sol",
			modelsJsonPath: modelsPath,
			probe,
		});

		expect(outcome.ok).toBe(true);
		expect(outcome.api).toBe("openai-responses");
		expect(outcome.thinkingLevel).toBe("high");
		expect(select).toHaveBeenCalledTimes(1);
		expect(refresh).toHaveBeenCalledTimes(1);
		expect(probe).toHaveBeenCalledWith(
			{ baseUrl: "https://gw.example.com/v1", apiKey: "sk-1", modelId: "gpt-5.6-sol" },
			undefined,
		);
		const written = JSON.parse(readFileSync(modelsPath, "utf-8"));
		expect(written.providers.apifusion.models[0]).toMatchObject({
			id: "gpt-5.6-sol",
			name: "GPT-5.6 Sol",
			api: "openai-responses",
			reasoning: true,
			contextWindow: 256000,
			maxTokens: 64000,
		});
		expect(written.providers.apifusion.models[0].thinkingLevelMap.minimal).toBeNull();
	});

	it("does not ask when there is no UI and clamps medium instead", async () => {
		const modelsPath = join(tempDir, "models.json");
		const { ctx, select } = makeApplyContext({ hasUI: false });
		const outcome = await applyProbedModel({
			ctx,
			providerName: "apifusion",
			baseUrl: "https://gw.example.com/v1",
			modelId: "gpt-5.6-sol",
			modelsJsonPath: modelsPath,
			probe: fakeProbe({ ok: true, result: SOL_RESULT }),
		});
		expect(outcome.thinkingLevel).toBe("high");
		expect(select).not.toHaveBeenCalled();
	});

	it("clamps to the default when the picker returns nothing", async () => {
		const modelsPath = join(tempDir, "models.json");
		const { ctx, select } = makeApplyContext();
		// Esc in the TUI, and print/json mode where hasUI is true but every dialog resolves
		// undefined. Neither means "run without a level".
		select.mockResolvedValueOnce(undefined);
		const outcome = await applyProbedModel({
			ctx,
			providerName: "apifusion",
			baseUrl: "https://gw.example.com/v1",
			modelId: "gpt-5.6-sol",
			modelsJsonPath: modelsPath,
			probe: fakeProbe({ ok: true, result: SOL_RESULT }),
		});
		expect(select).toHaveBeenCalledTimes(1);
		expect(outcome.thinkingLevel).toBe("high");
	});

	it("honours an explicit effort", async () => {
		const modelsPath = join(tempDir, "models.json");
		const { ctx } = makeApplyContext({ hasUI: false });
		const outcome = await applyProbedModel({
			ctx,
			providerName: "apifusion",
			baseUrl: "https://gw.example.com/v1",
			modelId: "gpt-5.6-sol",
			modelsJsonPath: modelsPath,
			requestedEffort: "xhigh",
			probe: fakeProbe({ ok: true, result: SOL_RESULT }),
		});
		expect(outcome.thinkingLevel).toBe("xhigh");
	});

	it("rejects an effort upstream did not confirm and lists the ones it did", async () => {
		const modelsPath = join(tempDir, "models.json");
		const { ctx, refresh } = makeApplyContext({ hasUI: false });
		const outcome = await applyProbedModel({
			ctx,
			providerName: "apifusion",
			baseUrl: "https://gw.example.com/v1",
			modelId: "gpt-5.6-sol",
			modelsJsonPath: modelsPath,
			requestedEffort: "medium",
			probe: fakeProbe({ ok: true, result: SOL_RESULT }),
		});
		expect(outcome.ok).toBe(false);
		expect(outcome.message).toContain("xhigh");
		expect(existsSync(modelsPath)).toBe(false);
		expect(refresh).not.toHaveBeenCalled();
	});

	it("explains that --effort cannot apply when no level was ever confirmed", async () => {
		const modelsPath = join(tempDir, "models.json");
		const { ctx, refresh } = makeApplyContext({ hasUI: false });
		const outcome = await applyProbedModel({
			ctx,
			providerName: "relay",
			baseUrl: "https://relay.example.com/v1",
			modelId: "claude-opus-4-8",
			modelsJsonPath: modelsPath,
			requestedEffort: "high",
			probe: fakeProbe({
				ok: true,
				result: {
					api: "anthropic-messages",
					supportedLevels: [],
					steps: [
						{ api: "openai-completions", status: 400, verdict: "wrong-protocol", detail: "" },
						{ api: "openai-responses", status: 400, verdict: "wrong-protocol", detail: "" },
						{ api: "anthropic-messages", status: 200, verdict: "ok", detail: "" },
					],
					effortNote: "effort 未探:anthropic-messages 用 thinking budget,不是 effort 字符串",
				},
			}),
		});
		expect(outcome.ok).toBe(false);
		expect(outcome.message).toContain("用不上");
		expect(outcome.message).toContain("thinking budget");
		expect(outcome.message).not.toContain("上游没确认");
		expect(refresh).not.toHaveBeenCalled();
		expect(existsSync(modelsPath)).toBe(false);
	});

	it("skips the effort question for a model that cannot reason", async () => {
		const modelsPath = join(tempDir, "models.json");
		const { ctx, select } = makeApplyContext();
		const outcome = await applyProbedModel({
			ctx,
			providerName: "apifusion",
			baseUrl: "https://gw.example.com/v1",
			modelId: "glm-5.1",
			modelsJsonPath: modelsPath,
			probe: fakeProbe({
				ok: true,
				result: {
					api: "openai-completions",
					reasoning: false,
					supportedLevels: [],
					steps: [{ api: "openai-completions", status: 200, verdict: "ok", detail: "" }],
					effortNote: "effort:上游不接受任何档位,这个模型不做推理",
				},
			}),
		});
		expect(outcome.ok).toBe(true);
		expect(outcome.thinkingLevel).toBeUndefined();
		expect(select).not.toHaveBeenCalled();
		const written = JSON.parse(readFileSync(modelsPath, "utf-8"));
		expect(written.providers.apifusion.models[0].thinkingLevelMap).toBeUndefined();
	});

	it("does not ask when off is the only level upstream accepts", async () => {
		const modelsPath = join(tempDir, "models.json");
		const { ctx, select } = makeApplyContext();
		const outcome = await applyProbedModel({
			ctx,
			providerName: "apifusion",
			baseUrl: "https://gw.example.com/v1",
			modelId: "quiet-model",
			modelsJsonPath: modelsPath,
			probe: fakeProbe({
				ok: true,
				result: {
					api: "openai-completions",
					reasoning: false,
					supportedLevels: ["off"],
					steps: [{ api: "openai-completions", status: 200, verdict: "ok", detail: "" }],
					effortNote: "effort:上游不接受任何档位,这个模型不做推理",
				},
			}),
		});
		expect(outcome.ok).toBe(true);
		expect(outcome.thinkingLevel).toBeUndefined();
		expect(select).not.toHaveBeenCalled();
	});

	it("keeps an explicitly chosen off", async () => {
		const modelsPath = join(tempDir, "models.json");
		const { ctx } = makeApplyContext({ pick: "off(不推理)" });
		const outcome = await applyProbedModel({
			ctx,
			providerName: "apifusion",
			baseUrl: "https://gw.example.com/v1",
			modelId: "gpt-5.6-sol",
			modelsJsonPath: modelsPath,
			probe: fakeProbe({ ok: true, result: SOL_RESULT }),
		});
		expect(outcome.thinkingLevel).toBe("off");
	});

	it("fails without writing anything when the probe fails", async () => {
		const modelsPath = join(tempDir, "models.json");
		const { ctx, refresh } = makeApplyContext();
		const outcome = await applyProbedModel({
			ctx,
			providerName: "apifusion",
			baseUrl: "https://gw.example.com/v1",
			modelId: "gpt-5.6-luna",
			modelsJsonPath: modelsPath,
			probe: fakeProbe({ ok: false, error: { kind: "client-rejected", detail: "客户端异常", steps: [] } }),
		});
		expect(outcome.ok).toBe(false);
		expect(outcome.message).toContain("gpt-5.6-luna");
		expect(outcome.message).toContain("客户端");
		expect(existsSync(modelsPath)).toBe(false);
		expect(refresh).not.toHaveBeenCalled();
	});

	it("reports a write failure instead of throwing", async () => {
		const modelsPath = join(tempDir, "models.json");
		const original = '// hand-edited\n{ "providers": {} }\n';
		writeFileSync(modelsPath, original, "utf-8");
		const { ctx, select, refresh } = makeApplyContext({ pick: "high" });
		const outcome = await applyProbedModel({
			ctx,
			providerName: "apifusion",
			baseUrl: "https://gw.example.com/v1",
			modelId: "gpt-5.6-sol",
			modelsJsonPath: modelsPath,
			probe: fakeProbe({ ok: true, result: SOL_RESULT }),
		});
		expect(outcome.ok).toBe(false);
		expect(outcome.message).toContain("comments");
		expect(outcome.supportedLevels).toEqual(["xhigh", "high", "off"]);
		expect(select).not.toHaveBeenCalled();
		expect(refresh).not.toHaveBeenCalled();
		expect(readFileSync(modelsPath, "utf-8")).toBe(original);
	});
});

function fakeStream(events: Array<{ type: string }>, result: { stopReason: string; errorMessage?: string }) {
	return vi.fn(() => {
		const iterable = {
			async *[Symbol.asyncIterator]() {
				for (const event of events) yield event;
			},
			result: async () => result,
		};
		return iterable;
	});
}

describe("verifyModelRoundTrip", () => {
	const registry = {
		find: vi.fn(() => ({ id: "gpt-5.6-sol", provider: "apifusion", api: "openai-responses" })),
		getApiKeyAndHeaders: vi.fn(async () => ({ ok: true, apiKey: "sk-1", headers: {}, env: undefined })),
	};

	it("passes when the stream finishes normally", async () => {
		const streamFn = fakeStream([{ type: "start" }, { type: "text_delta" }], { stopReason: "stop" });
		const outcome = await verifyModelRoundTrip({
			registry: registry as never,
			provider: "apifusion",
			modelId: "gpt-5.6-sol",
			thinkingLevel: "high",
			streamFn: streamFn as never,
		});
		expect(outcome.ok).toBe(true);
		// The whole point of this check is that it goes out over pi-ai's own path: one user
		// message, the probed level, and a deadline so a silent gateway cannot hang init.
		expect(streamFn).toHaveBeenCalledWith(
			expect.anything(),
			expect.objectContaining({ messages: [{ role: "user", content: "say ok" }] }),
			expect.objectContaining({ maxTokens: 32, reasoning: "high", signal: expect.any(AbortSignal) }),
		);
	});

	it("omits reasoning for off", async () => {
		const streamFn = fakeStream([], { stopReason: "stop" });
		await verifyModelRoundTrip({
			registry: registry as never,
			provider: "apifusion",
			modelId: "gpt-5.6-sol",
			thinkingLevel: "off",
			streamFn: streamFn as never,
		});
		const call = streamFn.mock.calls[0] as unknown[];
		expect(call[2]).not.toHaveProperty("reasoning");
	});

	it("fails when credentials cannot be resolved", async () => {
		const outcome = await verifyModelRoundTrip({
			registry: {
				...registry,
				getApiKeyAndHeaders: vi.fn(async () => ({ ok: false, error: "no key" })),
			} as never,
			provider: "apifusion",
			modelId: "gpt-5.6-sol",
			streamFn: fakeStream([], { stopReason: "stop" }) as never,
		});
		expect(outcome.ok).toBe(false);
		expect(outcome.detail).toBe("no key");
	});

	it("fails with the upstream error text", async () => {
		const outcome = await verifyModelRoundTrip({
			registry: registry as never,
			provider: "apifusion",
			modelId: "gpt-5.6-sol",
			streamFn: fakeStream([], { stopReason: "error", errorMessage: "HTTP 400 protocol_not_supported" }) as never,
		});
		expect(outcome.ok).toBe(false);
		expect(outcome.detail).toContain("protocol_not_supported");
	});

	it("fails when the model cannot be resolved after the write", async () => {
		const outcome = await verifyModelRoundTrip({
			registry: { ...registry, find: vi.fn(() => undefined) } as never,
			provider: "apifusion",
			modelId: "gpt-5.6-sol",
			streamFn: fakeStream([], { stopReason: "stop" }) as never,
		});
		expect(outcome.ok).toBe(false);
		expect(outcome.detail).toContain("models.json");
	});
});

describe("probeWholeGateway", () => {
	it("annotates what works and skips what does not", async () => {
		const modelsPath = join(tempDir, "models.json");
		const probe = vi.fn(async (target: { modelId: string }) => {
			if (target.modelId === "dead-model") {
				return { ok: false as const, error: { kind: "no-channel" as const, detail: "无可用渠道", steps: [] } };
			}
			return {
				ok: true as const,
				result: {
					api: "openai-completions" as const,
					reasoning: false,
					supportedLevels: [],
					steps: [{ api: "openai-completions" as const, status: 200, verdict: "ok" as const, detail: "" }],
					effortNote: "effort:不做推理",
				},
			};
		});

		const outcome = await probeWholeGateway({
			providerName: "apifusion",
			baseUrl: "https://gw.example.com/v1",
			apiKey: "sk-1",
			modelIds: ["deepseek-chat", "dead-model", "glm-5.1"],
			modelsJsonPath: modelsPath,
			concurrency: 1,
			probe: probe as never,
		});

		expect(outcome.ok).toBe(true);
		expect(outcome.annotated).toEqual(["deepseek-chat", "glm-5.1"]);
		expect(outcome.skipped[0]).toMatchObject({
			modelId: "dead-model",
			reason: "no-channel",
			detail: expect.stringContaining("无可用渠道"),
		});
		const models = JSON.parse(readFileSync(modelsPath, "utf-8")).providers.apifusion.models as Array<
			Record<string, unknown>
		>;
		expect(models.map((model) => model.id)).toEqual(["deepseek-chat", "glm-5.1"]);
		expect(models[0]).toMatchObject({
			id: "deepseek-chat",
			api: "openai-completions",
			reasoning: false,
			contextWindow: 256000,
			maxTokens: 64000,
		});
	});

	it("stops the other workers on a dead key", async () => {
		const modelsPath = join(tempDir, "models.json");
		const probe = vi.fn(async () => ({
			ok: false as const,
			error: { kind: "bad-key" as const, detail: "Invalid token", steps: [] },
		}));
		const outcome = await probeWholeGateway({
			providerName: "apifusion",
			baseUrl: "https://gw.example.com/v1",
			modelIds: ["a", "b", "c", "d", "e", "f"],
			modelsJsonPath: modelsPath,
			concurrency: 2,
			probe: probe as never,
		});
		expect(outcome.ok).toBe(false);
		expect(probe.mock.calls.length).toBeLessThanOrEqual(2);
	});

	it("keeps every annotation when workers finish out of order", async () => {
		const modelsPath = join(tempDir, "models.json");
		const ids = ["m0", "m1", "m2", "m3", "m4", "m5"];
		const probe = vi.fn(async (target: { modelId: string }) => {
			const index = ids.indexOf(target.modelId);
			await new Promise((resolve) => setTimeout(resolve, (ids.length - index) * 5));
			return {
				ok: true as const,
				result: {
					api: "openai-completions" as const,
					reasoning: false,
					supportedLevels: [],
					steps: [{ api: "openai-completions" as const, status: 200, verdict: "ok" as const, detail: "" }],
					effortNote: "effort:不做推理",
				},
			};
		});
		const outcome = await probeWholeGateway({
			providerName: "apifusion",
			baseUrl: "https://gw.example.com/v1",
			modelIds: ids,
			modelsJsonPath: modelsPath,
			concurrency: 4,
			probe: probe as never,
		});
		expect(outcome.ok).toBe(true);
		const models = JSON.parse(readFileSync(modelsPath, "utf-8")).providers.apifusion.models as Array<
			Record<string, unknown>
		>;
		expect(models.map((model) => model.id).sort()).toEqual(ids);
		expect(models.every((model) => model.api === "openai-completions")).toBe(true);
	});

	it("stops the whole run on a dead key", async () => {
		const modelsPath = join(tempDir, "models.json");
		const probe = vi.fn(async () => ({
			ok: false as const,
			error: { kind: "bad-key" as const, detail: "Invalid token", steps: [] },
		}));
		const outcome = await probeWholeGateway({
			providerName: "apifusion",
			baseUrl: "https://gw.example.com/v1",
			modelIds: ["a", "b", "c"],
			modelsJsonPath: modelsPath,
			concurrency: 1,
			probe: probe as never,
		});
		expect(outcome.ok).toBe(false);
		expect(outcome.message).toContain("key");
		expect(probe).toHaveBeenCalledTimes(1);
	});
});
