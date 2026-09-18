import type { AuthEvent, AuthInteraction, AuthPrompt } from "@earendil-works/pi-ai";
import type { ExtensionCommandContext } from "../extensions/types.ts";

/** Same message pi uses, so the existing error mapping keeps matching. */
export class LoginCancelled extends Error {
	constructor() {
		super("Login cancelled");
		this.name = "LoginCancelled";
	}
}

/**
 * Adapt ctx.ui to pi's AuthInteraction. `presetSecret` answers the FIRST `secret`
 * prompt (that is --api-key or the key SURE already asked for); any further
 * question needs the UI.
 */
export function uiAuthInteraction(
	ctx: Pick<ExtensionCommandContext, "hasUI" | "ui">,
	label: string,
	options: { presetSecret?: string; loginHint?: string } = {},
): AuthInteraction {
	let preset = options.presetSecret;
	let authUrl: string | undefined;
	return {
		async prompt(prompt: AuthPrompt): Promise<string> {
			if (prompt.type === "secret" && preset !== undefined) {
				const key = preset;
				preset = undefined; // one shot
				return key;
			}
			if (!ctx.hasUI) {
				throw new Error(
					`${label} needs an answer to "${prompt.message}", which requires an interactive session. ` +
						`Run /sure_init interactively${options.loginHint ? `, or ${options.loginHint}` : ""}.`,
				);
			}
			if (prompt.type === "select") {
				const picked = await ctx.ui.select(
					prompt.message,
					prompt.options.map((o) => o.label),
					{ signal: prompt.signal },
				);
				const id = prompt.options.find((o) => o.label === picked)?.id;
				if (id === undefined) throw new LoginCancelled(); // dismissed, or a label we cannot map
				return id;
			}
			// Keep the auth URL on screen in the paste dialog: pi's manual_code message
			// carries no URL, and notify() is transient.
			const title = prompt.type === "manual_code" && authUrl ? `Open ${authUrl}\n${prompt.message}` : prompt.message;
			const value = await ctx.ui.input(title, prompt.placeholder, { signal: prompt.signal });
			if (value === undefined) throw new LoginCancelled(); // dismissed or prompt.signal aborted
			if (prompt.type !== "secret") return value; // a blank text answer is legal (github-copilot enterprise domain)
			const key = value.trim();
			if (key === "") throw new LoginCancelled();
			return key;
		},
		notify(event: AuthEvent): void {
			if (event.type === "auth_url") {
				authUrl = event.url;
				const lines = [`Open this URL in your browser to authenticate ${label}:`, event.url];
				if (event.instructions) lines.push(event.instructions);
				ctx.ui.notify(lines.join("\n"), "info");
			} else if (event.type === "device_code") {
				ctx.ui.notify(`Device code for ${label}: ${event.userCode}\nVisit: ${event.verificationUri}`, "info");
			} else if (event.type === "info") {
				ctx.ui.notify([event.message, ...(event.links ?? []).map((l) => l.url)].join("\n"), "info");
			} else {
				ctx.ui.notify(event.message, "info");
			}
		},
	};
}
