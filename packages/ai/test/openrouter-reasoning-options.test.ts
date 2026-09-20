import { describe, expect, it } from "vitest";
import { streamSimple } from "../src/api/openai-completions.ts";
import type { Context, Model, ThinkingLevelMap } from "../src/types.ts";

const context: Context = {
	messages: [{ role: "user", content: "Hello", timestamp: 0 }],
};

function openRouterModel(thinkingLevelMap?: ThinkingLevelMap): Model<"openai-completions"> {
	return {
		id: "stealth/ox-alpha",
		name: "Ox Alpha",
		api: "openai-completions",
		provider: "openrouter",
		baseUrl: "https://example.invalid/v1",
		reasoning: true,
		thinkingLevelMap,
		input: ["text"],
		cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
		contextWindow: 128000,
		maxTokens: 4096,
		compat: { thinkingFormat: "openrouter" },
	};
}

async function capturePayload(model: Model<"openai-completions">, reasoning?: "low") {
	let payload: { reasoning?: { effort?: string } } | undefined;
	await streamSimple(model, context, {
		apiKey: "test",
		reasoning,
		onPayload: (request) => {
			payload = request as { reasoning?: { effort?: string } };
			throw new Error("payload captured");
		},
	}).result();
	if (!payload) throw new Error("OpenRouter payload was not captured");
	return payload;
}

describe("OpenRouter mandatory reasoning payloads", () => {
	// What getOpenRouterThinkingLevelMap({ mandatory: true, supported_efforts: ["max", "high", "low"] })
	// used to produce: mandatory reasoning cannot be turned off, and only the three listed efforts are selectable.
	const mandatoryMap: ThinkingLevelMap = {
		off: null,
		minimal: null,
		low: "low",
		medium: null,
		high: "high",
		xhigh: null,
		max: "max",
	};

	it("omits reasoning when a background call does not request it", async () => {
		expect(await capturePayload(openRouterModel(mandatoryMap))).not.toHaveProperty("reasoning");
	});

	it("still sends an explicitly selected supported effort", async () => {
		expect(await capturePayload(openRouterModel(mandatoryMap), "low")).toMatchObject({
			reasoning: { effort: "low" },
		});
	});

	it("continues to explicitly disable reasoning for optional models", async () => {
		expect(await capturePayload(openRouterModel())).toMatchObject({
			reasoning: { effort: "none" },
		});
	});
});
