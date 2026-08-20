import { describe, expect, it, vi } from "vitest";
import {
	classifyProbeResponse,
	levelAccepted,
	probeEffortSupport,
	probeModelCapability,
	probeProtocol,
} from "../../src/core/sure/init-capability-probe.ts";

const TARGET = { baseUrl: "https://gw.example.com/v1", apiKey: "sk-test", modelId: "gpt-5.6-sol" };

/** Route a fake fetch by URL suffix. Values are [status, body]. */
function routedFetch(routes: Record<string, [number, unknown]>) {
	return vi.fn(async (url: string | URL, _init?: RequestInit) => {
		const href = typeof url === "string" ? url : url.href;
		const hit = Object.entries(routes).find(([suffix]) => href.endsWith(suffix));
		if (!hit) throw new Error(`unexpected probe URL ${href}`);
		const [status, body] = hit[1];
		return new Response(typeof body === "string" ? body : JSON.stringify(body), { status });
	});
}

const PROTOCOL_NOT_SUPPORTED = [
	400,
	{ error: { code: "protocol_not_supported", message: "不支持 chat completions 协议" } },
] as [number, unknown];
const NO_CHANNEL = [503, { error: { code: "model_not_found", message: "无可用渠道" } }] as [number, unknown];

describe("classifyProbeResponse", () => {
	it("maps the signatures measured against the company gateway on 2026-08-17", () => {
		expect(classifyProbeResponse(200, "{}")).toBe("ok");
		expect(classifyProbeResponse(401, '{"error":{"message":"Invalid token"}}')).toBe("bad-key");
		expect(classifyProbeResponse(403, "客户端存在异常")).toBe("client-rejected");
		expect(classifyProbeResponse(400, '{"error":{"code":"protocol_not_supported"}}')).toBe("wrong-protocol");
		expect(classifyProbeResponse(503, '{"error":{"code":"model_not_found"}}')).toBe("no-channel");
		expect(classifyProbeResponse(500, "boom")).toBe("unknown");
	});

	it("lets 401 win over a body marker", () => {
		expect(classifyProbeResponse(401, '{"error":{"code":"model_not_found"}}')).toBe("bad-key");
	});
});

describe("probeProtocol", () => {
	it("falls through completions to responses for the gpt family", async () => {
		const fetchMock = routedFetch({
			"/chat/completions": PROTOCOL_NOT_SUPPORTED,
			"/responses": [200, { id: "resp_1", object: "response" }],
		});
		const outcome = await probeProtocol(TARGET, { fetch: fetchMock as unknown as typeof fetch });
		expect(outcome.verdict).toBe("ok");
		expect(outcome.api).toBe("openai-responses");
		expect(outcome.steps.map((step) => step.api)).toEqual(["openai-completions", "openai-responses"]);
		expect(fetchMock).toHaveBeenCalledTimes(2);
	});

	it("stops at completions when it already works", async () => {
		const fetchMock = routedFetch({ "/chat/completions": [200, { id: "chatcmpl-1" }] });
		const outcome = await probeProtocol(
			{ ...TARGET, modelId: "deepseek-chat" },
			{
				fetch: fetchMock as unknown as typeof fetch,
			},
		);
		expect(outcome.api).toBe("openai-completions");
		expect(outcome.steps).toHaveLength(1);
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it("reports a rejected client without trying the remaining protocols", async () => {
		const fetchMock = routedFetch({
			"/chat/completions": PROTOCOL_NOT_SUPPORTED,
			"/responses": [403, { error: { message: "请使用标准 Codex 客户端请求" } }],
		});
		const outcome = await probeProtocol(
			{ ...TARGET, modelId: "gpt-5.6-luna" },
			{
				fetch: fetchMock as unknown as typeof fetch,
				retryDelayMs: 0,
			},
		);
		expect(outcome.verdict).toBe("client-rejected");
		expect(outcome.api).toBeUndefined();
		expect(outcome.steps).toHaveLength(2);
		// completions answers once and decides; responses is asked three times before its 403 counts.
		expect(fetchMock).toHaveBeenCalledTimes(4);
	});

	it("retries a 403 on the protocol probe", async () => {
		let completionsCalls = 0;
		const fetchMock = vi.fn(async (url: string | URL) => {
			const href = typeof url === "string" ? url : url.href;
			if (!href.endsWith("/chat/completions")) throw new Error(`unexpected probe URL ${href}`);
			completionsCalls++;
			if (completionsCalls === 1) return new Response("我们检测到您的客户端存在异常", { status: 403 });
			return Response.json({ id: "chatcmpl-1" });
		});
		const outcome = await probeProtocol(TARGET, { fetch: fetchMock as unknown as typeof fetch, retryDelayMs: 0 });
		expect(outcome.verdict).toBe("ok");
		expect(outcome.api).toBe("openai-completions");
		expect(completionsCalls).toBe(2);
		expect(fetchMock).toHaveBeenCalledTimes(2);
	});

	it("gives up after three 403s", async () => {
		let completionsCalls = 0;
		let responsesCalls = 0;
		const fetchMock = vi.fn(async (url: string | URL) => {
			const href = typeof url === "string" ? url : url.href;
			if (href.endsWith("/chat/completions")) completionsCalls++;
			else responsesCalls++;
			return new Response("我们检测到您的客户端存在异常", { status: 403 });
		});
		const outcome = await probeProtocol(TARGET, { fetch: fetchMock as unknown as typeof fetch, retryDelayMs: 0 });
		expect(outcome.verdict).toBe("client-rejected");
		expect(completionsCalls).toBe(3);
		expect(responsesCalls).toBe(0);
		expect(outcome.steps).toHaveLength(1);
	});

	it("reports a missing channel on the first answer", async () => {
		const fetchMock = routedFetch({ "/chat/completions": NO_CHANNEL });
		const outcome = await probeProtocol(
			{ ...TARGET, modelId: "gpt-5.6-sol-openai-compact" },
			{
				fetch: fetchMock as unknown as typeof fetch,
			},
		);
		expect(outcome.verdict).toBe("no-channel");
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it("reports a dead key on the first answer", async () => {
		const fetchMock = routedFetch({ "/chat/completions": [401, { error: { message: "Invalid token" } }] });
		const outcome = await probeProtocol(TARGET, { fetch: fetchMock as unknown as typeof fetch });
		expect(outcome.verdict).toBe("bad-key");
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it("reports no-protocol after all three refuse", async () => {
		const fetchMock = routedFetch({
			"/chat/completions": PROTOCOL_NOT_SUPPORTED,
			"/responses": PROTOCOL_NOT_SUPPORTED,
			"/messages": PROTOCOL_NOT_SUPPORTED,
		});
		const outcome = await probeProtocol(TARGET, { fetch: fetchMock as unknown as typeof fetch });
		expect(outcome.verdict).toBe("no-protocol");
		expect(outcome.steps).toHaveLength(3);
	});

	it("reports unreachable when fetch throws", async () => {
		const fetchMock = vi.fn(async () => {
			throw new Error("getaddrinfo ENOTFOUND gw.example.com");
		});
		const outcome = await probeProtocol(TARGET, { fetch: fetchMock as unknown as typeof fetch });
		expect(outcome.verdict).toBe("unreachable");
		expect(outcome.steps[0].status).toBe("network");
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it("sends the anthropic version header on the third attempt", async () => {
		const fetchMock = routedFetch({
			"/chat/completions": PROTOCOL_NOT_SUPPORTED,
			"/responses": PROTOCOL_NOT_SUPPORTED,
			"/messages": [200, { id: "msg_1", type: "message" }],
		});
		const outcome = await probeProtocol(TARGET, { fetch: fetchMock as unknown as typeof fetch });
		expect(outcome.api).toBe("anthropic-messages");
		const lastCall = fetchMock.mock.calls[2];
		expect((lastCall[1] as RequestInit & { headers: Record<string, string> }).headers["anthropic-version"]).toBe(
			"2023-06-01",
		);
	});
});

describe("levelAccepted", () => {
	it("trusts the echoed effort on the responses protocol", () => {
		expect(levelAccepted("openai-responses", "high", JSON.stringify({ reasoning: { effort: "high" } }))).toBe(true);
		expect(levelAccepted("openai-responses", "xhigh", JSON.stringify({ reasoning: { effort: "xhigh" } }))).toBe(true);
		expect(levelAccepted("openai-responses", "off", JSON.stringify({ reasoning: { effort: "none" } }))).toBe(true);
	});

	it("rejects a level upstream rewrote (measured: sol turns minimal into none)", () => {
		expect(levelAccepted("openai-responses", "minimal", JSON.stringify({ reasoning: { effort: "none" } }))).toBe(
			false,
		);
	});

	it("rejects a model that reports no reasoning config at all (measured: glm-5.1)", () => {
		expect(levelAccepted("openai-responses", "high", JSON.stringify({ reasoning: null }))).toBe(false);
	});

	it("uses reasoning-token usage on the completions protocol", () => {
		const withTokens = JSON.stringify({ usage: { completion_tokens_details: { reasoning_tokens: 27 } } });
		const withoutTokens = JSON.stringify({ usage: { completion_tokens_details: { reasoning_tokens: 0 } } });
		expect(levelAccepted("openai-completions", "high", withTokens)).toBe(true);
		expect(levelAccepted("openai-completions", "high", withoutTokens)).toBe(false);
		expect(levelAccepted("openai-completions", "off", withoutTokens)).toBe(true);
		expect(levelAccepted("openai-completions", "off", withTokens)).toBe(false);
	});

	it("accepts reasoning text when no token counter is present", () => {
		const body = JSON.stringify({ choices: [{ message: { reasoning_content: "let me think" } }] });
		expect(levelAccepted("openai-completions", "high", body)).toBe(true);
	});

	it("rejects an unparseable body", () => {
		expect(levelAccepted("openai-responses", "high", "not json")).toBe(false);
	});
});

describe("probeEffortSupport", () => {
	it("reproduces the measured sol map: only off, high and xhigh", async () => {
		const fetchMock = vi.fn(async (_url: string | URL, init?: RequestInit) => {
			const sent = JSON.parse(String(init?.body)) as { reasoning?: { effort?: string } };
			const requested = sent.reasoning?.effort;
			const echoed = requested === "high" || requested === "xhigh" ? requested : "none";
			return Response.json({ id: "resp_1", reasoning: { effort: echoed } });
		});
		const outcome = await probeEffortSupport(TARGET, "openai-responses", {
			fetch: fetchMock as unknown as typeof fetch,
		});
		expect(outcome.reasoning).toBe(true);
		expect(outcome.supportedLevels).toEqual(["xhigh", "high", "off"]);
		expect(outcome.thinkingLevelMap).toEqual({
			off: "none",
			minimal: null,
			low: null,
			medium: null,
			high: "high",
			xhigh: "xhigh",
		});
		expect(fetchMock).toHaveBeenCalledTimes(6);
		expect(outcome.effortNote).toContain("xhigh");
	});

	it("marks a non-reasoning model as reasoning false", async () => {
		const fetchMock = vi.fn(async () => Response.json({ id: "chatcmpl-1", reasoning: null }));
		const outcome = await probeEffortSupport(TARGET, "openai-responses", {
			fetch: fetchMock as unknown as typeof fetch,
		});
		expect(outcome.reasoning).toBe(false);
		expect(outcome.supportedLevels).toEqual([]);
	});

	it("reads reasoning tokens on the completions protocol", async () => {
		const fetchMock = vi.fn(async (_url: string | URL, init?: RequestInit) => {
			const sent = JSON.parse(String(init?.body)) as { reasoning_effort?: string };
			const thinking = sent.reasoning_effort === "minimal" || sent.reasoning_effort === "high";
			return Response.json({
				id: "chatcmpl-1",
				usage: { completion_tokens_details: { reasoning_tokens: thinking ? 27 : 0 } },
			});
		});
		const outcome = await probeEffortSupport({ ...TARGET, modelId: "deepseek-chat" }, "openai-completions", {
			fetch: fetchMock as unknown as typeof fetch,
		});
		expect(outcome.reasoning).toBe(true);
		expect(outcome.supportedLevels).toEqual(["high", "minimal", "off"]);
	});

	it("treats a 5xx that never recovers as flaky, not as unsupported", async () => {
		const fetchMock = vi.fn(async () => new Response("boom", { status: 500 }));
		const outcome = await probeEffortSupport(TARGET, "openai-responses", {
			fetch: fetchMock as unknown as typeof fetch,
			retryDelayMs: 0,
		});
		expect(outcome.reasoning).toBe(false);
		expect(outcome.failure?.kind).toBe("flaky");
		expect(outcome.thinkingLevelMap).toEqual({});
	});

	it("retries a 403 on one level and accepts it when the retry answers", async () => {
		let highCalls = 0;
		const fetchMock = vi.fn(async (_url: string | URL, init?: RequestInit) => {
			const effort = (JSON.parse(String(init?.body)) as { reasoning?: { effort?: string } }).reasoning?.effort;
			if (effort === "high") {
				highCalls++;
				if (highCalls === 1) return new Response("我们检测到您的客户端存在异常", { status: 403 });
			}
			return Response.json({ id: "resp_1", reasoning: { effort } });
		});
		const outcome = await probeEffortSupport(TARGET, "openai-responses", {
			fetch: fetchMock as unknown as typeof fetch,
			retryDelayMs: 0,
		});
		expect(outcome.supportedLevels).toContain("high");
		expect(outcome.failure).toBeUndefined();
		expect(fetchMock).toHaveBeenCalledTimes(7);
	});

	it("gives up on a level after two retries and reports the model as flaky", async () => {
		let highCalls = 0;
		const fetchMock = vi.fn(async (_url: string | URL, init?: RequestInit) => {
			const effort = (JSON.parse(String(init?.body)) as { reasoning?: { effort?: string } }).reasoning?.effort;
			if (effort === "high") {
				highCalls++;
				return new Response("我们检测到您的客户端存在异常", { status: 403 });
			}
			return Response.json({ id: "resp_1", reasoning: { effort } });
		});
		const outcome = await probeEffortSupport(TARGET, "openai-responses", {
			fetch: fetchMock as unknown as typeof fetch,
			retryDelayMs: 0,
		});
		expect(outcome.failure?.kind).toBe("flaky");
		expect(outcome.failure?.detail).toContain("high→403");
		expect(outcome.supportedLevels).toEqual([]);
		expect(outcome.thinkingLevelMap).toEqual({});
		expect(highCalls).toBe(3);
	});

	it("treats 400 as unsupported without retrying", async () => {
		let xhighCalls = 0;
		const fetchMock = vi.fn(async (_url: string | URL, init?: RequestInit) => {
			const effort = (JSON.parse(String(init?.body)) as { reasoning?: { effort?: string } }).reasoning?.effort;
			if (effort === "xhigh") {
				xhighCalls++;
				return Response.json({ error: { message: "unsupported effort" } }, { status: 400 });
			}
			return Response.json({ id: "resp_1", reasoning: { effort } });
		});
		const outcome = await probeEffortSupport(TARGET, "openai-responses", {
			fetch: fetchMock as unknown as typeof fetch,
			retryDelayMs: 0,
		});
		expect(outcome.supportedLevels).not.toContain("xhigh");
		expect(outcome.failure).toBeUndefined();
		expect(xhighCalls).toBe(1);
	});

	it("never sends more than three effort probes at once", async () => {
		let inFlight = 0;
		let peak = 0;
		const fetchMock = vi.fn(async (_url: string | URL, init?: RequestInit) => {
			inFlight++;
			peak = Math.max(peak, inFlight);
			await new Promise((resolve) => setTimeout(resolve, 5));
			inFlight--;
			const effort = (JSON.parse(String(init?.body)) as { reasoning?: { effort?: string } }).reasoning?.effort;
			return Response.json({ id: "resp_1", reasoning: { effort } });
		});
		await probeEffortSupport(TARGET, "openai-responses", {
			fetch: fetchMock as unknown as typeof fetch,
			retryDelayMs: 0,
		});
		expect(fetchMock).toHaveBeenCalledTimes(6);
		expect(peak).toBeLessThanOrEqual(3);
	});

	it("stops on 401 with bad-key", async () => {
		const fetchMock = vi.fn(async (_url: string | URL, init?: RequestInit) => {
			const effort = (JSON.parse(String(init?.body)) as { reasoning?: { effort?: string } }).reasoning?.effort;
			if (effort === "low") return Response.json({ error: { message: "Invalid token" } }, { status: 401 });
			return Response.json({ id: "resp_1", reasoning: { effort } });
		});
		const outcome = await probeEffortSupport(TARGET, "openai-responses", {
			fetch: fetchMock as unknown as typeof fetch,
			retryDelayMs: 0,
		});
		expect(outcome.failure?.kind).toBe("bad-key");
		expect(outcome.supportedLevels).toEqual([]);
	});
});

describe("probeModelCapability", () => {
	it("returns protocol and effort together for a reasoning relay model", async () => {
		const fetchMock = vi.fn(async (url: string | URL, init?: RequestInit) => {
			const href = typeof url === "string" ? url : url.href;
			if (href.endsWith("/chat/completions")) {
				return Response.json({ error: { code: "protocol_not_supported" } }, { status: 400 });
			}
			const sent = JSON.parse(String(init?.body)) as { reasoning?: { effort?: string } };
			const requested = sent.reasoning?.effort;
			if (requested === undefined) return Response.json({ id: "resp_probe", object: "response" });
			const echoed = requested === "high" || requested === "xhigh" ? requested : "none";
			return Response.json({ id: "resp_1", reasoning: { effort: echoed } });
		});
		const outcome = await probeModelCapability(TARGET, { fetch: fetchMock as unknown as typeof fetch });
		expect(outcome.ok).toBe(true);
		if (!outcome.ok) return;
		expect(outcome.result.api).toBe("openai-responses");
		expect(outcome.result.reasoning).toBe(true);
		expect(outcome.result.supportedLevels).toEqual(["xhigh", "high", "off"]);
		expect(outcome.result.thinkingLevelMap?.minimal).toBeNull();
		expect(outcome.result.steps.map((step) => step.verdict)).toEqual(["wrong-protocol", "ok"]);
	});

	it("skips effort probing on the anthropic protocol and says so", async () => {
		const fetchMock = vi.fn(async (url: string | URL) => {
			const href = typeof url === "string" ? url : url.href;
			if (href.endsWith("/messages")) return Response.json({ id: "msg_1", type: "message" });
			return Response.json({ error: { code: "protocol_not_supported" } }, { status: 400 });
		});
		const outcome = await probeModelCapability(TARGET, { fetch: fetchMock as unknown as typeof fetch });
		expect(outcome.ok).toBe(true);
		if (!outcome.ok) return;
		expect(outcome.result.api).toBe("anthropic-messages");
		expect(outcome.result.reasoning).toBeUndefined();
		expect(outcome.result.thinkingLevelMap).toBeUndefined();
		expect(outcome.result.effortNote).toContain("未探");
		expect(fetchMock).toHaveBeenCalledTimes(3);
	});

	it("drops a non-reasoning model's level map entirely", async () => {
		const fetchMock = vi.fn(async () => Response.json({ id: "chatcmpl-1", usage: {} }));
		const outcome = await probeModelCapability(
			{ ...TARGET, modelId: "glm-5.1" },
			{
				fetch: fetchMock as unknown as typeof fetch,
			},
		);
		expect(outcome.ok).toBe(true);
		if (!outcome.ok) return;
		expect(outcome.result.api).toBe("openai-completions");
		expect(outcome.result.reasoning).toBe(false);
		expect(outcome.result.thinkingLevelMap).toBeUndefined();
	});

	it("returns ok:false when the effort probe is flaky, keeping the protocol steps", async () => {
		const fetchMock = vi.fn(async (url: string | URL, init?: RequestInit) => {
			const href = typeof url === "string" ? url : url.href;
			if (!href.endsWith("/chat/completions")) throw new Error(`unexpected probe URL ${href}`);
			const sent = JSON.parse(String(init?.body)) as { reasoning_effort?: string };
			if (sent.reasoning_effort === undefined) return Response.json({ id: "chatcmpl-probe" });
			if (sent.reasoning_effort === "high") {
				return new Response("我们检测到您的客户端存在异常", { status: 403 });
			}
			return Response.json({
				id: "chatcmpl-1",
				usage: { completion_tokens_details: { reasoning_tokens: 0 } },
			});
		});
		const outcome = await probeModelCapability(TARGET, {
			fetch: fetchMock as unknown as typeof fetch,
			retryDelayMs: 0,
		});
		expect(outcome.ok).toBe(false);
		if (outcome.ok) return;
		expect(outcome.error.kind).toBe("flaky");
		expect(outcome.error.steps.length).toBeGreaterThanOrEqual(1);
	});

	it("passes protocol failures through as typed errors", async () => {
		const fetchMock = vi.fn(async () => Response.json({ error: { message: "Invalid token" } }, { status: 401 }));
		const outcome = await probeModelCapability(TARGET, { fetch: fetchMock as unknown as typeof fetch });
		expect(outcome.ok).toBe(false);
		if (outcome.ok) return;
		expect(outcome.error.kind).toBe("bad-key");
		expect(outcome.error.steps).toHaveLength(1);
	});
});
