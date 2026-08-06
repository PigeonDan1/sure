import { describe, expect, it } from "vitest";
import { SURE_INIT_PROVIDER_OPTIONS } from "../../src/core/sure/init.ts";
import {
	buildInitMenu,
	findMenuEntry,
	isReservedProviderName,
	menuEntryLabel,
	NEW_GATEWAY_OPTION_ID,
} from "../../src/core/sure/init-menu.ts";

const relay = { name: "relay", baseUrl: "https://gw.example.com/v1", modelCount: 3, hasApiKey: true };
const clash = { name: "openai", baseUrl: "https://evil.example.com/v1", modelCount: 1, hasApiKey: false };

describe("buildInitMenu", () => {
	it("keeps built-ins first, appends gateways, ends with the new-gateway entry", () => {
		const menu = buildInitMenu(SURE_INIT_PROVIDER_OPTIONS, [relay]);
		expect(menu.entries[0]).toEqual({ kind: "builtin", option: SURE_INIT_PROVIDER_OPTIONS[0] });
		expect(menu.entries[SURE_INIT_PROVIDER_OPTIONS.length]).toEqual({ kind: "gateway", gateway: relay });
		expect(menu.entries[menu.entries.length - 1]).toEqual({ kind: "new-gateway" });
		expect(menu.skippedGateways).toEqual([]);
	});

	it("skips gateways whose name collides with built-ins and reports them", () => {
		const menu = buildInitMenu(SURE_INIT_PROVIDER_OPTIONS, [clash, relay]);
		expect(menu.skippedGateways).toEqual(["openai"]);
		expect(menu.entries.filter((entry) => entry.kind === "gateway")).toEqual([{ kind: "gateway", gateway: relay }]);
	});
});

describe("menuEntryLabel", () => {
	it("formats all three kinds", () => {
		expect(menuEntryLabel({ kind: "builtin", option: SURE_INIT_PROVIDER_OPTIONS[0] })).toBe(
			"OpenAI Codex: ChatGPT Plus/Pro subscription coding agent → openai-codex",
		);
		expect(menuEntryLabel({ kind: "gateway", gateway: relay })).toBe(
			"relay (custom): https://gw.example.com/v1, 3 models",
		);
		expect(menuEntryLabel({ kind: "new-gateway" })).toBe("Custom provider: add an OpenAI-compatible gateway");
	});
});

describe("findMenuEntry", () => {
	it("resolves built-in ids, gateway names, and the custom id", () => {
		const menu = buildInitMenu(SURE_INIT_PROVIDER_OPTIONS, [relay]);
		expect(findMenuEntry(menu, "codex")).toEqual({ kind: "builtin", option: SURE_INIT_PROVIDER_OPTIONS[0] });
		expect(findMenuEntry(menu, "relay")).toEqual({ kind: "gateway", gateway: relay });
		expect(findMenuEntry(menu, NEW_GATEWAY_OPTION_ID)).toEqual({ kind: "new-gateway" });
		expect(findMenuEntry(menu, "ghost")).toBeUndefined();
	});
});

describe("isReservedProviderName", () => {
	it("rejects the custom id, option ids, provider keys, and built-in providers", () => {
		expect(isReservedProviderName("custom", SURE_INIT_PROVIDER_OPTIONS)).toBe(true);
		expect(isReservedProviderName("codex", SURE_INIT_PROVIDER_OPTIONS)).toBe(true);
		expect(isReservedProviderName("openai-codex", SURE_INIT_PROVIDER_OPTIONS)).toBe(true);
		expect(isReservedProviderName("github-copilot", SURE_INIT_PROVIDER_OPTIONS)).toBe(true);
		expect(isReservedProviderName("relay", SURE_INIT_PROVIDER_OPTIONS)).toBe(false);
	});
});
