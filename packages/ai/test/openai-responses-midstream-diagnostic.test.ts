// The mid-stream diagnostic is what tells a failure the provider accepted from
// one that never reached it, and only the former is retried on the strength of
// that alone. A request that fails before the response starts must not carry it.

import { describe, expect, it, vi } from "vitest";
import { stream as streamOpenAIResponses } from "../src/api/openai-responses.ts";
import type { Context, Model } from "../src/types.ts";
import { PROVIDER_MIDSTREAM_ERROR } from "../src/utils/diagnostics.ts";

vi.mock("openai", () => {
	// Reproduce the openai SDK APIError shape for a gateway rejection.
	class FakeAPIError extends Error {
		status: number;
		constructor(status: number) {
			super(`${status} status code (no body)`);
			this.name = "PermissionDeniedError";
			this.status = status;
		}
	}

	class FakeOpenAI {
		responses = {
			create: () => {
				const promise = Promise.resolve(undefined) as unknown as { withResponse: () => Promise<never> };
				promise.withResponse = async () => {
					throw new FakeAPIError(403);
				};
				return promise;
			},
		};
	}

	return { default: FakeOpenAI };
});

function createModel(): Model<"openai-responses"> {
	return {
		id: "gpt-5-mini",
		name: "GPT-5 Mini",
		api: "openai-responses",
		provider: "openai",
		baseUrl: "https://api.openai.com/v1",
		reasoning: true,
		input: ["text"],
		cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
		contextWindow: 400000,
		maxTokens: 128000,
	};
}

describe("mid-stream provider diagnostic", () => {
	it("is absent when the request was rejected before the response started", async () => {
		const context: Context = {
			systemPrompt: "",
			messages: [{ role: "user", content: [{ type: "text", text: "hi" }], timestamp: 0 }],
			tools: [],
		};
		const stream = streamOpenAIResponses(createModel(), context, { apiKey: "test" });

		for await (const _event of stream) {
			// The result carries the failure; the events are not under test here.
		}
		const result = await stream.result();

		expect(result.stopReason).toBe("error");
		expect(result.diagnostics?.map((diagnostic) => diagnostic.type) ?? []).not.toContain(PROVIDER_MIDSTREAM_ERROR);
	});
});
