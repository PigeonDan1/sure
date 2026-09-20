// A provider that gives up names its reason in the terminal event it sends, and
// the stream throws right after mapping that reason. Whatever the mapping drops
// is gone by then: the Responses status behind a `failed` / `cancelled`
// response, which otherwise reaches the user as an unexplained error.

import type { ResponseStreamEvent } from "openai/resources/responses/responses.js";
import { describe, expect, it } from "vitest";
import { processResponsesStream } from "../src/api/openai-responses-shared.ts";
import type { AssistantMessage, Model } from "../src/types.ts";
import { AssistantMessageEventStream } from "../src/utils/event-stream.ts";

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
	it.each(["cancelled", "failed"] as const)("names a Responses status of %s", async (status) => {
		const model = createResponsesModel();
		const output = createResponsesOutput(model);

		await processResponsesStream(createTerminalEvents(status), output, new AssistantMessageEventStream(), model);

		expect(output.stopReason).toBe("error");
		expect(output.errorMessage).toBe(`Response ${status}`);
	});
});
