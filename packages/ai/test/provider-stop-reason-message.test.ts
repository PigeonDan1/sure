// A provider that gives up names its reason in the finish reason it sends.
// Every provider maps that reason onto our `StopReason`, and several distinct
// reasons collapse onto "error" — Gemini alone folds fourteen of them, safety
// blocks and malformed function calls included. Nothing records which one
// arrived, so the throw that follows the stream loop has nothing to carry and
// the user reads the literal "An unknown error occurred".
//
// These tests pin the reason to the resulting `errorMessage`.

import { FinishReason } from "@google/genai";
import { describe, expect, it, vi } from "vitest";
import { stream as streamBedrock } from "../src/api/bedrock-converse-stream.ts";
import { stream as streamGoogle } from "../src/api/google-generative-ai.ts";
import { stream as streamGoogleVertex } from "../src/api/google-vertex.ts";
import { getModel } from "../src/compat.ts";
import type { Context, Model } from "../src/types.ts";

const googleChunks: unknown[] = [];
const bedrockItems: unknown[] = [];

vi.mock("@aws-sdk/client-bedrock-runtime", async (importOriginal) => {
	const actual = await importOriginal<Record<string, unknown>>();
	class BedrockRuntimeClient {
		middlewareStack = { add: () => undefined };
		async send() {
			return {
				$metadata: {},
				stream: (async function* () {
					for (const item of bedrockItems) {
						yield item;
					}
				})(),
			};
		}
	}
	return { ...actual, BedrockRuntimeClient };
});

vi.mock("@google/genai", async (importOriginal) => {
	const actual = await importOriginal<Record<string, unknown>>();
	class FakeGoogleGenAI {
		models = {
			generateContentStream: async () =>
				(async function* () {
					for (const chunk of googleChunks) {
						yield chunk;
					}
				})(),
		};
	}
	return { ...actual, GoogleGenAI: FakeGoogleGenAI };
});

function googleModel(): Model<"google-generative-ai"> {
	return {
		id: "gemini-3-pro",
		name: "Gemini 3 Pro",
		api: "google-generative-ai",
		provider: "google",
		baseUrl: "https://generativelanguage.googleapis.com",
		reasoning: true,
		input: ["text"],
		cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
		contextWindow: 1000000,
		maxTokens: 64000,
	};
}

function vertexModel(): Model<"google-vertex"> {
	return {
		id: "gemini-3-pro",
		name: "Gemini 3 Pro",
		api: "google-vertex",
		provider: "google-vertex",
		baseUrl: "",
		reasoning: true,
		input: ["text"],
		cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
		contextWindow: 1000000,
		maxTokens: 64000,
	};
}

function textContext(): Context {
	return {
		systemPrompt: "",
		messages: [{ role: "user", content: [{ type: "text", text: "hi" }], timestamp: 0 }],
		tools: [],
	};
}

async function drain(assistantStream: ReturnType<typeof streamGoogle>) {
	for await (const _event of assistantStream) {
		// The result carries the failure; the events are not under test here.
	}
	return assistantStream.result();
}

describe("provider stop reason messages", () => {
	it("names the Gemini finish reason that stopped the generation", async () => {
		googleChunks.length = 0;
		googleChunks.push({ candidates: [{ finishReason: FinishReason.SAFETY, content: { parts: [] } }] });

		const result = await drain(streamGoogle(googleModel(), textContext(), { apiKey: "test" }));

		expect(result.stopReason).toBe("error");
		expect(result.errorMessage).toContain("SAFETY");
	});

	it("keeps the message Gemini attached to the finish reason", async () => {
		googleChunks.length = 0;
		googleChunks.push({
			candidates: [
				{
					finishReason: FinishReason.PROHIBITED_CONTENT,
					finishMessage: "blocked by policy",
					content: { parts: [] },
				},
			],
		});

		const result = await drain(streamGoogle(googleModel(), textContext(), { apiKey: "test" }));

		expect(result.errorMessage).toContain("PROHIBITED_CONTENT");
		expect(result.errorMessage).toContain("blocked by policy");
	});

	it("names the finish reason on the Vertex path too", async () => {
		googleChunks.length = 0;
		googleChunks.push({
			candidates: [{ finishReason: FinishReason.MALFORMED_FUNCTION_CALL, content: { parts: [] } }],
		});

		const result = await drain(
			streamGoogleVertex(vertexModel(), textContext(), {
				apiKey: "test",
				project: "test-project",
				location: "us-central1",
			}),
		);

		expect(result.stopReason).toBe("error");
		expect(result.errorMessage).toContain("MALFORMED_FUNCTION_CALL");
	});

	it("names the Bedrock stop reason it does not map", async () => {
		bedrockItems.length = 0;
		bedrockItems.push(
			{ messageStart: { role: "assistant" } },
			{ messageStop: { stopReason: "guardrail_intervened" } },
		);

		const result = await streamBedrock(getModel("amazon-bedrock", "us.anthropic.claude-opus-4-8"), textContext(), {
			cacheRetention: "none",
			region: "us-east-1",
		}).result();

		expect(result.stopReason).toBe("error");
		expect(result.errorMessage).toContain("guardrail_intervened");
	});
});
