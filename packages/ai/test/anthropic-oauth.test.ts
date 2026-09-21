import { createServer } from "node:http";
import { afterEach, describe, expect, it, vi } from "vitest";
import { anthropicOAuth } from "../src/auth/oauth/anthropic.ts";
import type { AuthEvent, AuthPrompt } from "../src/auth/types.ts";

const neverAbortedSignal = new AbortController().signal;

/** Binds the fixed callback port for a moment; throws EADDRINUSE if a stale login still holds it. */
async function bindCallbackPort(port: number): Promise<void> {
	const probe = createServer();
	await new Promise<void>((resolve, reject) => {
		probe.once("error", reject);
		probe.listen(port, "127.0.0.1", resolve);
	});
	await new Promise<void>((resolve) => probe.close(() => resolve()));
}

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
		vi.useRealTimers();
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

		const credentials = await anthropicOAuth.login({
			signal: neverAbortedSignal,
			notify: (event) => {
				if (event.type === "auth_url") authUrl = event.url;
			},
			prompt: async (prompt) => {
				if (prompt.type !== "manual_code") throw new Error(`Unexpected prompt: ${prompt.type}`);
				const url = new URL(authUrl);
				const state = url.searchParams.get("state");
				const redirectUri = url.searchParams.get("redirect_uri");
				if (!state || !redirectUri) throw new Error("Missing OAuth state or redirect_uri in auth URL");
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

		const credentials = await anthropicOAuth.refresh(
			{
				type: "oauth",
				access: "old-access-token",
				refresh: "refresh-token",
				expires: 0,
			},
			neverAbortedSignal,
		);

		expect(credentials.access).toBe("new-access-token");
		expect(credentials.refresh).toBe("new-refresh-token");
		expect(fetchMock).toHaveBeenCalledOnce();
	});

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
			signal: neverAbortedSignal,
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

	it("times out and releases the callback port when neither a callback nor a manual code arrives", async () => {
		// Without the timeout the login waits on the callback server forever,
		// holding the fixed port 53692 open.
		vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });

		// auth_url is only notified once the callback server listens and its timer
		// is armed, so waiting for it keeps the real listen I/O off the fake clock.
		let resolveListening: () => void = () => {};
		const listening = new Promise<void>((resolve) => {
			resolveListening = resolve;
		});
		let manualSignal: AbortSignal | undefined;

		const login = anthropicOAuth.login({
			signal: neverAbortedSignal,
			notify: (event) => {
				if (event.type === "auth_url") resolveListening();
			},
			prompt: (prompt) => {
				manualSignal = prompt.signal;
				return new Promise<string>(() => {});
			},
		});
		const rejection = login.then(
			() => new Error("expected the Anthropic login to time out"),
			(error: unknown) => error,
		);

		await listening;
		await vi.advanceTimersByTimeAsync(5 * 60 * 1000);

		const result = await rejection;
		expect(result).toBeInstanceOf(Error);
		expect((result as Error).message).toBe("Anthropic OAuth login timed out");
		// the pending manual_code prompt is dismissed rather than left hanging
		expect(manualSignal?.aborted).toBe(true);

		vi.useRealTimers();
		await bindCallbackPort(53692);
	});

	it("rejects instead of hanging when the caller aborts while the manual_code prompt is pending", async () => {
		const controller = new AbortController();
		let resolveListening: () => void = () => {};
		const listening = new Promise<void>((resolve) => {
			resolveListening = resolve;
		});

		const login = anthropicOAuth.login({
			signal: controller.signal,
			notify: (event) => {
				if (event.type === "auth_url") resolveListening();
			},
			// A prompt that only goes away when its own signal says so, which is
			// what a UI that never sees the caller's signal looks like.
			prompt: (prompt) =>
				new Promise<string>((_resolve, reject) => {
					const dismiss = () => reject(new Error("prompt dismissed"));
					if (prompt.signal?.aborted) dismiss();
					else prompt.signal?.addEventListener("abort", dismiss, { once: true });
				}),
		});
		const settled = login.then(
			() => "resolved",
			(error: unknown) => (error instanceof Error ? error.message : String(error)),
		);

		await listening;
		controller.abort();

		const outcome = await Promise.race([
			settled,
			new Promise<string>((resolve) => setTimeout(() => resolve("still pending"), 2000)),
		]);
		expect(outcome).toBe("prompt dismissed");
		await bindCallbackPort(53692);
	});
});
