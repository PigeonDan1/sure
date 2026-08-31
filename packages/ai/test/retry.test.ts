import { describe, expect, it } from "vitest";
import { fauxAssistantMessage } from "../src/providers/faux.ts";
import { createAssistantMessageDiagnostic, PROVIDER_MIDSTREAM_ERROR } from "../src/utils/diagnostics.ts";
import { isRetryableAssistantError } from "../src/utils/retry.ts";

const openAIExplicitRetryMessage =
	"An error occurred while processing your request. You can retry your request, or contact us through our help center at help.openai.com if the error persists. Please include the request ID req_******** in your message.";
const bedrockExplicitRetryMessage =
	'{"message":"The system encountered an unexpected error during processing. Try your request again."}';
// Arrives over SSE after a 200, so the message carries no status code.
const azureGenerationFailureMessage =
	"The model produced invalid content. Consider modifying your prompt if you are seeing this error persistently. For more information, please see https://aka.ms/model-error Please include the request ID ******** in your message.";

describe("provider retry classification", () => {
	it("matches explicit provider retry guidance", () => {
		expect(
			isRetryableAssistantError(
				fauxAssistantMessage("", { stopReason: "error", errorMessage: openAIExplicitRetryMessage }),
			),
		).toBe(true);
		expect(
			isRetryableAssistantError(
				fauxAssistantMessage("", { stopReason: "error", errorMessage: bedrockExplicitRetryMessage }),
			),
		).toBe(true);
	});

	it("matches statusless provider generation failures", () => {
		expect(
			isRetryableAssistantError(
				fauxAssistantMessage("", { stopReason: "error", errorMessage: azureGenerationFailureMessage }),
			),
		).toBe(true);
	});

	it("retries a failure the provider reported after the response had started", () => {
		// Wording no pattern matches: the signal is that the provider had already
		// returned 200 and begun the response before it gave up.
		const message = fauxAssistantMessage("", {
			stopReason: "error",
			errorMessage: "Upstream generation aborted by policy engine 7.",
		});
		expect(isRetryableAssistantError(message)).toBe(false);

		message.diagnostics = [
			createAssistantMessageDiagnostic(PROVIDER_MIDSTREAM_ERROR, new Error("Upstream generation aborted")),
		];
		expect(isRetryableAssistantError(message)).toBe(true);
	});

	it("keeps an account limit non-retryable even mid-stream", () => {
		const message = fauxAssistantMessage("", {
			stopReason: "error",
			errorMessage: "insufficient_quota: your account is out of credit",
		});
		message.diagnostics = [createAssistantMessageDiagnostic(PROVIDER_MIDSTREAM_ERROR, new Error("out of credit"))];

		expect(isRetryableAssistantError(message)).toBe(false);
	});

	it("keeps provider limit errors non-retryable", () => {
		expect(
			isRetryableAssistantError(
				fauxAssistantMessage("", { stopReason: "error", errorMessage: "429 quota exceeded" }),
			),
		).toBe(false);
	});

	it("classifies assistant error messages", () => {
		expect(
			isRetryableAssistantError(fauxAssistantMessage("", { stopReason: "error", errorMessage: "overloaded_error" })),
		).toBe(true);
		expect(isRetryableAssistantError(fauxAssistantMessage("not an error"))).toBe(false);
	});
});
