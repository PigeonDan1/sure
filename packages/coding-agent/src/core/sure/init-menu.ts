import { getProviders } from "@earendil-works/pi-ai/compat";
import type { GatewayProviderSummary } from "./init-gateway-store.ts";
import type { SureInitProviderOption } from "./init-types.ts";

/** Option id reserved for the "add a new gateway" flow. */
export const NEW_GATEWAY_OPTION_ID = "custom";

/** One selectable row in the init provider menu. */
export type InitMenuEntry =
	| { kind: "builtin"; option: SureInitProviderOption }
	| { kind: "gateway"; gateway: GatewayProviderSummary }
	| { kind: "new-gateway" };

/** Assembled menu plus any gateway names dropped for colliding with built-ins. */
export interface InitMenu {
	entries: InitMenuEntry[];
	skippedGateways: string[];
}

/** Assemble the three-segment menu: built-in options, models.json gateways, new-gateway entry. */
export function buildInitMenu(options: SureInitProviderOption[], gateways: GatewayProviderSummary[]): InitMenu {
	const entries: InitMenuEntry[] = options.map((option) => ({ kind: "builtin", option }));
	const skippedGateways: string[] = [];
	for (const gateway of gateways) {
		if (isReservedProviderName(gateway.name, options)) {
			skippedGateways.push(gateway.name);
			continue;
		}
		entries.push({ kind: "gateway", gateway });
	}
	entries.push({ kind: "new-gateway" });
	return { entries, skippedGateways };
}

/** Selector label for a menu entry. */
export function menuEntryLabel(entry: InitMenuEntry): string {
	switch (entry.kind) {
		case "builtin":
			return `${entry.option.name}: ${entry.option.description} → ${entry.option.provider}`;
		case "gateway":
			return `${entry.gateway.name} (custom): ${entry.gateway.baseUrl}, ${entry.gateway.modelCount} models`;
		default:
			return "Custom provider: add an OpenAI-compatible gateway";
	}
}

/** Resolve a --option value against the menu: built-in id, gateway name, or the custom id. */
export function findMenuEntry(menu: InitMenu, optionId: string): InitMenuEntry | undefined {
	if (optionId === NEW_GATEWAY_OPTION_ID) {
		return menu.entries.find((entry) => entry.kind === "new-gateway");
	}
	return menu.entries.find(
		(entry) =>
			(entry.kind === "builtin" && entry.option.id === optionId) ||
			(entry.kind === "gateway" && entry.gateway.name === optionId),
	);
}

/** True when a gateway name collides with the custom id, an option id/provider, or any built-in provider. */
export function isReservedProviderName(name: string, options: SureInitProviderOption[]): boolean {
	if (name === NEW_GATEWAY_OPTION_ID) return true;
	if (options.some((option) => option.id === name || option.provider === name)) return true;
	return (getProviders() as readonly string[]).includes(name);
}
