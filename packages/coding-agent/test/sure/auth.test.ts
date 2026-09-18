import type { AuthPrompt } from "@earendil-works/pi-ai";
import { describe, expect, it, vi } from "vitest";
import { LoginCancelled, uiAuthInteraction } from "../../src/core/sure/auth.ts";

function makeCtx(hasUI = true) {
	const ui = {
		input: vi.fn(async (_title: string, _placeholder?: string) => undefined as string | undefined),
		select: vi.fn(async (_title: string, _options: string[]) => undefined as string | undefined),
		notify: vi.fn((_message: string, _type?: "info" | "warning" | "error") => {}),
	};
	return { ctx: { hasUI, ui } as any, ui };
}

const selectPrompt: AuthPrompt = {
	type: "select",
	message: "Pick an account",
	options: [
		{ id: "personal", label: "Personal" },
		{ id: "work", label: "Work" },
	],
};

describe("uiAuthInteraction", () => {
	it("maps the selected label back to the option id", async () => {
		const { ctx, ui } = makeCtx();
		ui.select.mockResolvedValueOnce("Work");
		const interaction = uiAuthInteraction(ctx, "OpenAI Codex");

		await expect(interaction.prompt(selectPrompt)).resolves.toBe("work");
		expect(ui.select).toHaveBeenCalledWith("Pick an account", ["Personal", "Work"], { signal: undefined });
	});

	it("treats a dismissed selector as a cancellation", async () => {
		const { ctx, ui } = makeCtx();
		ui.select.mockResolvedValueOnce(undefined);
		const interaction = uiAuthInteraction(ctx, "OpenAI Codex");

		await expect(interaction.prompt(selectPrompt)).rejects.toBeInstanceOf(LoginCancelled);
	});

	it("answers the first secret prompt from presetSecret without touching the UI", async () => {
		const { ctx, ui } = makeCtx();
		const interaction = uiAuthInteraction(ctx, "Kimi Code", { presetSecret: "sk-preset" });

		await expect(interaction.prompt({ type: "secret", message: "Kimi API key" })).resolves.toBe("sk-preset");
		expect(ui.input).not.toHaveBeenCalled();
	});

	it("fails the second question with the question text and the login hint when there is no UI", async () => {
		const { ctx } = makeCtx(false);
		const interaction = uiAuthInteraction(ctx, "Kimi Code", {
			presetSecret: "sk-preset",
			loginHint: "/login kimi-coding",
		});

		await expect(interaction.prompt({ type: "secret", message: "Kimi API key" })).resolves.toBe("sk-preset");
		await expect(interaction.prompt({ type: "secret", message: "Second secret" })).rejects.toThrow(
			/Second secret[\s\S]*\/login kimi-coding/,
		);
	});

	it("trims a secret typed into the dialog", async () => {
		const { ctx, ui } = makeCtx();
		ui.input.mockResolvedValueOnce(" k ");
		const interaction = uiAuthInteraction(ctx, "Kimi Code");

		await expect(interaction.prompt({ type: "secret", message: "Kimi API key" })).resolves.toBe("k");
	});

	it("treats a blank secret as a cancellation", async () => {
		const { ctx, ui } = makeCtx();
		ui.input.mockResolvedValueOnce("  ");
		const interaction = uiAuthInteraction(ctx, "Kimi Code");

		await expect(interaction.prompt({ type: "secret", message: "Kimi API key" })).rejects.toBeInstanceOf(
			LoginCancelled,
		);
	});

	it("passes an empty text answer through", async () => {
		const { ctx, ui } = makeCtx();
		ui.input.mockResolvedValueOnce("");
		const interaction = uiAuthInteraction(ctx, "GitHub Copilot");

		await expect(interaction.prompt({ type: "text", message: "Enterprise domain" })).resolves.toBe("");
	});

	it("keeps the auth URL in the manual_code dialog title", async () => {
		const { ctx, ui } = makeCtx();
		ui.input.mockResolvedValueOnce("code-123");
		const interaction = uiAuthInteraction(ctx, "OpenAI Codex");

		interaction.notify({ type: "auth_url", url: "https://auth.example.com/go" });
		await expect(
			interaction.prompt({ type: "manual_code", message: "Paste the redirect URL here when done:" }),
		).resolves.toBe("code-123");
		expect(ui.input.mock.calls[0][0]).toBe(
			"Open https://auth.example.com/go\nPaste the redirect URL here when done:",
		);
	});

	it("renders every auth event kind", () => {
		const { ctx, ui } = makeCtx();
		const interaction = uiAuthInteraction(ctx, "OpenAI Codex");

		interaction.notify({ type: "auth_url", url: "https://auth.example.com/go", instructions: "Sign in" });
		interaction.notify({ type: "device_code", userCode: "ABCD", verificationUri: "https://device.example.com" });
		interaction.notify({ type: "info", message: "Almost there", links: [{ url: "https://docs.example.com" }] });
		interaction.notify({ type: "progress", message: "Exchanging tokens" });

		expect(ui.notify.mock.calls.map((call) => call[0])).toEqual([
			"Open this URL in your browser to authenticate OpenAI Codex:\nhttps://auth.example.com/go\nSign in",
			"Device code for OpenAI Codex: ABCD\nVisit: https://device.example.com",
			"Almost there\nhttps://docs.example.com",
			"Exchanging tokens",
		]);
		expect(ui.notify.mock.calls.every((call) => call[1] === "info")).toBe(true);
	});
});
