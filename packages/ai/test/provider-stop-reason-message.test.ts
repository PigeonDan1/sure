// A provider that gives up names its reason in the terminal event it sends, and
// the stream throws right after mapping that reason. Whatever the mapping drops
// is gone by then: Gemini's `finishMessage`, which is the only place the rule
// that fired is named, and the Responses status behind a `failed` / `cancelled`
// response, which otherwise reaches the user as an unexplained error.

import type { ResponseStreamEvent } from "openai/resources/responses/responses.js";
import { describe, expect, it, vi } from "vitest";

const googleMock = vi.hoisted(() => ({
	finishReason: "SAFETY" as string,
	finishMessage: undefined as string | undefined,
}));

vi.mock("@google/genai", async (importOriginal) => {
	const actual = await importOriginal<Record<string, unknown>>();
	class FakeGoogleGenAI {
		models = {
			generateContentStream: async () =>
				(async function* () {
					yield {
						candidates: [
							{
								finishReason: googleMock.finishReason,
								finishMessage: googleMock.finishMessage,
								content: { parts: [] },
							},
						],
					};
				})(),
		};
	}
	return { ...actual, GoogleGenAI: FakeGoogleGenAI };
});

import { stream as streamGoogleGenerativeAi } from "../src/api/google-generative-ai.ts";
import { stream as streamGoogleVertex } from "../src/api/google-vertex.ts";
import { processResponsesStream } from "../src/api/openai-responses-shared.ts";
import { getModel } from "../src/compat.ts";
import type { AssistantMessage, Context, Model } from "../src/types.ts";
import { AssistantMessageEventStream } from "../src/utils/event-stream.ts";

const context: Context = {
	messages: [{ role: "user", content: "hello", timestamp: Date.now() }],
};

function createResponsesModel(): Model<"openai-responses"> {
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

function createResponsesOutput(model: Model<"openai-responses">): AssistantMessage {
	return {
		role: "assistant",
		content: [],
		api: model.api,
		provider: model.provider,
		model: model.id,
		usage: {
			input: 0,
			output: 0,
			cacheRead: 0,
			cacheWrite: 0,
			totalTokens: 0,
			cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
		},
		stopReason: "pending",
		timestamp: Date.now(),
	};
}

async function* createTerminalEvents(status: "cancelled" | "failed"): AsyncIterable<ResponseStreamEvent> {
	yield {
		type: "response.completed",
		sequence_number: 0,
		response: { id: "resp_terminal", status },
	} as unknown as ResponseStreamEvent;
}

describe("provider stop reason messages", () => {
	it("keeps the message Gemini attached to the finish reason", async () => {
		googleMock.finishReason = "PROHIBITED_CONTENT";
		googleMock.finishMessage = "blocked by policy";

		const message = await streamGoogleGenerativeAi(getModel("google", "gemini-2.5-flash"), context, {
			apiKey: "test-api-key",
		}).result();

		expect(message.stopReason).toBe("error");
		expect(message.rawStopReason).toBe("PROHIBITED_CONTENT (blocked by policy)");
		expect(message.errorMessage).toBe("Provider stopped with: PROHIBITED_CONTENT (blocked by policy)");
	});

	it("keeps the finish message on the Vertex path too", async () => {
		googleMock.finishReason = "MALFORMED_FUNCTION_CALL";
		googleMock.finishMessage = "unparseable function call";

		const message = await streamGoogleVertex(getModel("google-vertex", "gemini-3-flash-preview"), context, {
			project: "test-project",
			location: "us-central1",
		}).result();

		expect(message.stopReason).toBe("error");
		expect(message.rawStopReason).toBe("MALFORMED_FUNCTION_CALL (unparseable function call)");
		expect(message.errorMessage).toBe("Provider stopped with: MALFORMED_FUNCTION_CALL (unparseable function call)");
	});

	it("leaves the raw finish reason alone when Gemini attaches no message", async () => {
		googleMock.finishReason = "SAFETY";
		googleMock.finishMessage = undefined;

		const message = await streamGoogleGenerativeAi(getModel("google", "gemini-2.5-flash"), context, {
			apiKey: "test-api-key",
		}).result();

		expect(message.rawStopReason).toBe("SAFETY");
	});

	it.each(["cancelled", "failed"] as const)("names a Responses status of %s", async (status) => {
		const model = createResponsesModel();
		const output = createResponsesOutput(model);

		await processResponsesStream(createTerminalEvents(status), output, new AssistantMessageEventStream(), model);

		expect(output.stopReason).toBe("error");
		expect(output.errorMessage).toBe(`Response ${status}`);
	});
});
