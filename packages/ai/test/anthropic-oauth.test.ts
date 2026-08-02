import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthEvent, AuthPrompt } from "../src/auth/types.ts";
import { anthropicOAuth, loginAnthropic, refreshAnthropicToken } from "../src/utils/oauth/anthropic.ts";
import { CALLBACK_TIMEOUT_MS } from "../src/utils/oauth/types.ts";

function jsonResponse(body: unknown, status: number = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: {
			"Content-Type": "application/json",
		},
	});
}

function getUrl(input: unknown): string {
	if (typeof input === "string") {
		return input;
	}
	if (input instanceof URL) {
		return input.toString();
	}
	if (input instanceof Request) {
		return input.url;
	}
	throw new Error(`Unsupported fetch input: ${String(input)}`);
}

function getJsonBody(init?: RequestInit): Record<string, string> {
	if (typeof init?.body !== "string") {
		throw new Error(`Expected string request body, got ${typeof init?.body}`);
	}
	return JSON.parse(init.body) as Record<string, string>;
}

describe.sequential("Anthropic OAuth", () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it("keeps the localhost redirect_uri for manual callback login", async () => {
		let authUrl = "";
		const fetchMock = vi.fn(async (input: unknown, init?: RequestInit): Promise<Response> => {
			expect(getUrl(input)).toBe("https://platform.claude.com/v1/oauth/token");
			expect(init?.method).toBe("POST");
			const body = getJsonBody(init);
			expect(body.grant_type).toBe("authorization_code");
			expect(body.code).toBe("manual-code");
			expect(body.redirect_uri).toBe("http://localhost:53692/callback");
			return jsonResponse({
				access_token: "access-token",
				refresh_token: "refresh-token",
				expires_in: 3600,
			});
		});
		vi.stubGlobal("fetch", fetchMock);

		const credentials = await loginAnthropic({
			onAuth: (info) => {
				authUrl = info.url;
			},
			onPrompt: async () => "",
			onManualCodeInput: async () => {
				const url = new URL(authUrl);
				const state = url.searchParams.get("state");
				const redirectUri = url.searchParams.get("redirect_uri");
				if (!state || !redirectUri) {
					throw new Error("Missing OAuth state or redirect_uri in auth URL");
				}
				return `${redirectUri}?code=manual-code&state=${state}`;
			},
		});

		expect(credentials.access).toBe("access-token");
		expect(credentials.refresh).toBe("refresh-token");
		expect(fetchMock).toHaveBeenCalledOnce();
	});

	it("omits scope from refresh token requests", async () => {
		const fetchMock = vi.fn(async (input: unknown, init?: RequestInit): Promise<Response> => {
			expect(getUrl(input)).toBe("https://platform.claude.com/v1/oauth/token");
			expect(init?.method).toBe("POST");
			const body = getJsonBody(init);
			expect(body.grant_type).toBe("refresh_token");
			expect(body.client_id).toBeTruthy();
			expect(body.refresh_token).toBe("refresh-token");
			expect(body).not.toHaveProperty("scope");
			return jsonResponse({
				access_token: "new-access-token",
				refresh_token: "new-refresh-token",
				expires_in: 3600,
			});
		});
		vi.stubGlobal("fetch", fetchMock);

		const credentials = await refreshAnthropicToken("refresh-token");

		expect(credentials.access).toBe("new-access-token");
		expect(credentials.refresh).toBe("new-refresh-token");
		expect(fetchMock).toHaveBeenCalledOnce();
	});

	it(
		"times out and releases the port when neither a callback nor manual code arrives",
		{ timeout: 2000 },
		async () => {
			// Bug: with no onManualCodeInput supplied, loginAnthropic awaits the
			// callback server forever. That holds the fixed callback port
			// (53692) open indefinitely if the browser callback never arrives.
			vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });

			// onAuth only fires once the callback server is listening and its
			// timeout timer is already scheduled, so awaiting it here (before
			// advancing the fake clock) avoids racing the real socket-listen
			// I/O against the fake-timer registration.
			let resolveAuthCalled: () => void;
			const authCalled = new Promise<void>((resolve) => {
				resolveAuthCalled = resolve;
			});

			const loginPromise = loginAnthropic({
				onAuth: () => resolveAuthCalled(),
				onPrompt: async () => {
					throw new Error("onPrompt should not run before the callback timeout fires");
				},
			});
			const rejection = loginPromise.then(
				() => new Error("expected loginAnthropic to time out and reject"),
				(error: unknown) => error,
			);

			await authCalled;
			await vi.advanceTimersByTimeAsync(CALLBACK_TIMEOUT_MS);

			const result = await rejection;
			expect(result).toBeInstanceOf(Error);
			expect((result as Error).message).toMatch(/timed out/i);

			vi.useRealTimers();

			// The port must be released: a second callback server should be
			// able to bind to the same fixed port right after the timeout.
			const fetchMock = vi.fn(async () =>
				jsonResponse({ access_token: "access-token", refresh_token: "refresh-token", expires_in: 3600 }),
			);
			vi.stubGlobal("fetch", fetchMock);

			let authUrl = "";
			const credentials = await loginAnthropic({
				onAuth: (info) => {
					authUrl = info.url;
				},
				onPrompt: async () => "",
				onManualCodeInput: async () => {
					const url = new URL(authUrl);
					const state = url.searchParams.get("state");
					const redirectUri = url.searchParams.get("redirect_uri");
					if (!state || !redirectUri) {
						throw new Error("Missing OAuth state or redirect_uri in auth URL");
					}
					return `${redirectUri}?code=manual-code&state=${state}`;
				},
			});
			expect(credentials.access).toBe("access-token");
		},
	);

	it("anthropicOAuth.login resolves through the manual_code prompt and aborts it after settling", async () => {
		const fetchMock = vi.fn(async (input: unknown): Promise<Response> => {
			const url = typeof input === "string" ? input : String(input);
			if (url.includes("/oauth/token")) {
				return jsonResponse({ access_token: "access", refresh_token: "refresh", expires_in: 3600 });
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal("fetch", fetchMock);

		const events: AuthEvent[] = [];
		const prompts: AuthPrompt[] = [];
		let manualSignal: AbortSignal | undefined;

		const credential = await anthropicOAuth.login({
			notify: (event) => events.push(event),
			prompt: async (prompt) => {
				prompts.push(prompt);
				if (prompt.type === "manual_code") {
					manualSignal = prompt.signal;
					return "the-code";
				}
				throw new Error(`Unexpected prompt: ${prompt.type}`);
			},
		});

		expect(credential.type).toBe("oauth");
		expect(credential.access).toBe("access");
		expect(events.some((e) => e.type === "auth_url")).toBe(true);
		expect(prompts.some((p) => p.type === "manual_code")).toBe(true);
		// the prompt's signal is aborted once login settles, so UIs can dismiss it
		expect(manualSignal?.aborted).toBe(true);
	});
});
