import type { ModelRegistry } from "../model-registry.ts";
import type { SureInitProviderOption } from "./init-types.ts";

/** One selectable model as reported by a provider. */
export interface ListedModel {
	id: string;
	name?: string;
}

/** Where a model list came from. */
export type ModelListingSource = "live" | "cached" | "builtin" | "manual";

/** A model list plus its provenance, ready for the picker. */
export interface ModelListing {
	source: ModelListingSource;
	models: ListedModel[];
	error?: string;
}

const MODEL_LIST_TIMEOUT_MS = 10_000;
const ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models";
const ANTHROPIC_VERSION = "2023-06-01";

/** Build the OpenAI-compatible model-list URL for a base URL that may or may not carry a version segment. */
export function openAICompatibleModelsUrl(baseUrl: string): string {
	const trimmed = baseUrl.replace(/\/+$/, "");
	return /\/v\d+$/.test(trimmed) ? `${trimmed}/models` : `${trimmed}/v1/models`;
}

function parseModelList(payload: unknown): ListedModel[] {
	const data = (payload as { data?: unknown })?.data;
	if (!Array.isArray(data)) {
		throw new Error("model list response has no data array");
	}
	const models: ListedModel[] = [];
	for (const entry of data) {
		if (typeof entry !== "object" || entry === null) continue;
		const id = (entry as { id?: unknown }).id;
		if (typeof id !== "string" || id.length === 0) continue;
		const displayName = (entry as { display_name?: unknown }).display_name;
		models.push(typeof displayName === "string" && displayName.length > 0 ? { id, name: displayName } : { id });
	}
	if (models.length === 0) {
		throw new Error("model list response contained no model ids");
	}
	return models;
}

async function requestModelList(url: string, headers: Record<string, string>): Promise<ListedModel[]> {
	const response = await fetch(url, {
		headers: { accept: "application/json", ...headers },
		signal: AbortSignal.timeout(MODEL_LIST_TIMEOUT_MS),
	});
	if (!response.ok) {
		throw new Error(`model list request failed: HTTP ${response.status}`);
	}
	return parseModelList(await response.json());
}

/** Ask an OpenAI-compatible endpoint for its models. Throws on any failure. */
export async function fetchOpenAICompatibleModels(baseUrl: string, apiKey: string | undefined): Promise<ListedModel[]> {
	const headers: Record<string, string> = {};
	if (apiKey) {
		headers.Authorization = `Bearer ${apiKey}`;
	}
	return requestModelList(openAICompatibleModelsUrl(baseUrl), headers);
}

/** Ask the Anthropic API for its models. Throws on any failure. */
export async function fetchAnthropicModels(apiKey: string): Promise<ListedModel[]> {
	return requestModelList(ANTHROPIC_MODELS_URL, { "x-api-key": apiKey, "anthropic-version": ANTHROPIC_VERSION });
}

function builtInListing(option: SureInitProviderOption, modelRegistry: ModelRegistry, error?: string): ModelListing {
	const models = modelRegistry
		.getAll()
		.filter((model) => model.provider === option.provider)
		.map((model) => (model.name && model.name !== model.id ? { id: model.id, name: model.name } : { id: model.id }));
	return error ? { source: "builtin", models, error } : { source: "builtin", models };
}

/**
 * Ask a built-in provider for its live model list, falling back to the registry catalog.
 * Copilot's registry list is already the account's live list (pi-ai filters it to the
 * availableModelIds fetched from Copilot's /models endpoint at OAuth login/refresh).
 * Codex (ChatGPT subscription backend) exposes no listing endpoint, so it stays built-in.
 */
export async function listBuiltInProviderModels(
	option: SureInitProviderOption,
	modelRegistry: ModelRegistry,
): Promise<ModelListing> {
	try {
		switch (option.provider) {
			case "openai":
			case "kimi-coding": {
				const apiKey = await modelRegistry.getApiKeyForProvider(option.provider);
				if (!apiKey) {
					return builtInListing(option, modelRegistry, "no API key available for a live model query");
				}
				const baseUrl = modelRegistry.getAll().find((model) => model.provider === option.provider)?.baseUrl;
				if (!baseUrl) {
					return builtInListing(option, modelRegistry, "no base URL known for a live model query");
				}
				return { source: "live", models: await fetchOpenAICompatibleModels(baseUrl, apiKey) };
			}
			case "anthropic": {
				const apiKey = await modelRegistry.getApiKeyForProvider("anthropic");
				if (!apiKey) {
					return builtInListing(option, modelRegistry, "no API key available for a live model query");
				}
				return { source: "live", models: await fetchAnthropicModels(apiKey) };
			}
			case "github-copilot": {
				const listing = builtInListing(option, modelRegistry);
				return { source: "live", models: listing.models };
			}
			default:
				return builtInListing(option, modelRegistry);
		}
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		return builtInListing(option, modelRegistry, message);
	}
}

/** Human-readable one-liner about where a model list came from. */
export function describeListingSource(listing: ModelListing): string {
	switch (listing.source) {
		case "live":
			return "Model list: live from the provider.";
		case "cached":
			return `Model list: cached from models.json${listing.error ? ` (live query failed: ${listing.error})` : ""} — not confirmed with the provider.`;
		case "manual":
			return "Model list: entered manually — not confirmed with the provider.";
		default:
			return `Model list: built-in catalog${listing.error ? ` (live query failed: ${listing.error})` : ""}.`;
	}
}
