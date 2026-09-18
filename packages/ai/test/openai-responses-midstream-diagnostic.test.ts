// The mid-stream diagnostic is what tells a failure the provider accepted from
// one that never reached it, and only the former is retried on the strength of
// that alone. A request that fails before the response starts must not carry it.

import type { ResponseStreamEvent } from "openai/resources/responses/responses.js";
import { describe, expect, it, vi } from "vitest";
import { stream as streamOpenAIResponses } from "../src/api/openai-responses.ts";
import type { Context, Model } from "../src/types.ts";
import { PROVIDER_MIDSTREAM_ERROR } from "../src/utils/diagnostics.ts";
import { isRetryableAssistantError } from "../src/utils/retry.ts";

// Wording no retryable pattern matches: the signal under test is that the
// provider had already returned 200 and begun the response before it gave up.
const MIDSTREAM_FAILURE_MESSAGE = "Upstream generation aborted by policy engine 7.";

const transport = vi.hoisted(() => ({ mode: "reject-before-start" as "reject-before-start" | "break-midstream" }));

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

	async function* createBrokenStream(): AsyncIterable<ResponseStreamEvent> {
		yield {
			type: "response.created",
			sequence_number: 0,
			response: { id: "resp_midstream" },
		} as ResponseStreamEvent;
		throw new Error(MIDSTREAM_FAILURE_MESSAGE);
	}

	class FakeOpenAI {
		responses = {
			create: () => {
				const promise = Promise.resolve(undefined) as unknown as { withResponse: () => Promise<unknown> };
				promise.withResponse = async () => {
					if (transport.mode === "reject-before-start") throw new FakeAPIError(403);
					return { data: createBrokenStream(), response: { status: 200, headers: new Headers() } };
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

async function runStream() {
	const context: Context = {
		systemPrompt: "",
		messages: [{ role: "user", content: [{ type: "text", text: "hi" }], timestamp: 0 }],
		tools: [],
	};
	const stream = streamOpenAIResponses(createModel(), context, { apiKey: "test" });

	for await (const _event of stream) {
		// The result carries the failure; the events are not under test here.
	}
	return await stream.result();
}

describe("mid-stream provider diagnostic", () => {
	it("is absent when the request was rejected before the response started", async () => {
		transport.mode = "reject-before-start";
		const result = await runStream();

		expect(result.stopReason).toBe("error");
		expect(result.diagnostics?.map((diagnostic) => diagnostic.type) ?? []).not.toContain(PROVIDER_MIDSTREAM_ERROR);
	});

	it("is attached when the response had started before the stream broke", async () => {
		transport.mode = "break-midstream";
		const result = await runStream();

		expect(result.stopReason).toBe("error");
		expect(result.errorMessage).toBe(MIDSTREAM_FAILURE_MESSAGE);
		expect(result.diagnostics?.map((diagnostic) => diagnostic.type)).toContain(PROVIDER_MIDSTREAM_ERROR);
		// Unmatched wording, so only the diagnostic can make this retryable.
		expect(isRetryableAssistantError(result)).toBe(true);
	});
});
