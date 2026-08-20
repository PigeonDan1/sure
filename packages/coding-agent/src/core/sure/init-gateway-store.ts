import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { getProviders } from "@earendil-works/pi-ai/compat";
import { getModelsPath } from "../../config.ts";
import { stripJsonComments } from "../../utils/json.ts";
import type { ListedModel } from "./init-model-listing.ts";

interface ModelsFileProvider {
	baseUrl?: unknown;
	apiKey?: unknown;
	api?: unknown;
	models?: unknown;
	[key: string]: unknown;
}

interface ModelsFile {
	providers?: Record<string, ModelsFileProvider>;
	[key: string]: unknown;
}

/** A models.json provider that looks like an OpenAI-compatible gateway. */
export interface GatewayProviderSummary {
	name: string;
	baseUrl: string;
	modelCount: number;
	hasApiKey: boolean;
}

/** Everything needed to (re)write one gateway provider entry. */
export interface GatewayWriteInput {
	name: string;
	baseUrl: string;
	apiKey?: string;
	models: ListedModel[];
}

function stripBom(raw: string): string {
	return raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw;
}

/** True when the raw text contains `//` or `/*` comment syntax outside of string literals. */
function containsJsonComments(raw: string): boolean {
	const withoutStrings = raw.replace(/"(?:\\.|[^"\\])*"/g, "");
	return withoutStrings.includes("//") || withoutStrings.includes("/*");
}

function parseModelsFile(raw: string, path: string): ModelsFile {
	let parsed: unknown;
	try {
		parsed = JSON.parse(stripJsonComments(stripBom(raw)));
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		throw new Error(`failed to parse models.json: ${message}. File: ${path}`);
	}
	if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
		throw new Error(`models.json root must be an object. File: ${path}`);
	}
	return parsed as ModelsFile;
}

function readModelsFile(path: string): { raw: string | undefined; file: ModelsFile } {
	if (!existsSync(path)) {
		return { raw: undefined, file: { providers: {} } };
	}
	const raw = readFileSync(path, "utf-8");
	return { raw, file: parseModelsFile(raw, path) };
}

function modelRecord(model: ListedModel): Record<string, unknown> {
	return Object.fromEntries(Object.entries(model).filter(([, value]) => value !== undefined));
}

function writeModelsFile(file: ModelsFile, path: string): void {
	mkdirSync(dirname(path), { recursive: true });
	const tmpPath = `${path}.sure-init-tmp`;
	writeFileSync(tmpPath, `${JSON.stringify(file, null, 2)}\n`, "utf-8");
	renameSync(tmpPath, path);
}

function readEditableModelsFile(path: string): ModelsFile {
	if (!existsSync(path)) {
		return { providers: {} };
	}
	const raw = readFileSync(path, "utf-8");
	if (containsJsonComments(stripBom(raw))) {
		throw new Error(
			`models.json contains comments; refusing to rewrite it — add the provider manually. File: ${path}`,
		);
	}
	return parseModelsFile(raw, path);
}

/** Enumerate models.json providers that are not built-in and carry a baseUrl. Unreadable file → empty list. */
export function listGatewayProviders(modelsJsonPath: string = getModelsPath()): GatewayProviderSummary[] {
	let file: ModelsFile;
	try {
		file = readModelsFile(modelsJsonPath).file;
	} catch {
		return [];
	}
	const builtIns = new Set<string>(getProviders());
	const summaries: GatewayProviderSummary[] = [];
	for (const [name, provider] of Object.entries(file.providers ?? {})) {
		if (builtIns.has(name)) continue;
		if (typeof provider?.baseUrl !== "string" || provider.baseUrl.length === 0) continue;
		const models = Array.isArray(provider.models) ? provider.models : [];
		summaries.push({
			name,
			baseUrl: provider.baseUrl,
			modelCount: models.length,
			hasApiKey: typeof provider.apiKey === "string" && provider.apiKey.length > 0,
		});
	}
	return summaries.sort((a, b) => a.name.localeCompare(b.name));
}

/** The models currently recorded for a gateway provider — the "cached" list. Never throws. */
export function readGatewayModels(name: string, modelsJsonPath: string = getModelsPath()): ListedModel[] {
	let file: ModelsFile;
	try {
		file = readModelsFile(modelsJsonPath).file;
	} catch {
		return [];
	}
	const models = file.providers?.[name]?.models;
	if (!Array.isArray(models)) return [];
	const listed: ListedModel[] = [];
	for (const entry of models) {
		if (typeof entry !== "object" || entry === null) continue;
		const id = (entry as { id?: unknown }).id;
		if (typeof id !== "string" || id.length === 0) continue;
		const displayName = (entry as { name?: unknown }).name;
		listed.push(typeof displayName === "string" && displayName.length > 0 ? { id, name: displayName } : { id });
	}
	return listed;
}

/**
 * Defaults written for a model /sure_init just configured. The gateway cannot be asked
 * for either number: measured 2026-08-17, it accepted 606584 input tokens and an absurd
 * max_output_tokens without complaint. contextWindow is therefore a local compaction
 * threshold, not an upstream fact. The registry would otherwise fall back to 128000 and
 * 16384, and 16384 truncates reasoning models because reasoning counts as output.
 */
export const NEW_MODEL_DEFAULTS = { contextWindow: 256_000, maxTokens: 64_000 } as const;

function modelId(entry: unknown): string | undefined {
	if (typeof entry !== "object" || entry === null) return undefined;
	const id = (entry as { id?: unknown }).id;
	return typeof id === "string" && id.length > 0 ? id : undefined;
}

/**
 * Replace one gateway provider entry (create if absent). Membership comes from the given
 * list, but a model that survives keeps every field it already had, so refreshing the
 * list no longer throws away the protocol and effort annotations. Refuses to rewrite a
 * comment-bearing file, since a rewrite would silently destroy the comments.
 */
export function writeGatewayProvider(input: GatewayWriteInput, modelsJsonPath: string = getModelsPath()): void {
	const file = readEditableModelsFile(modelsJsonPath);
	const providers = file.providers ?? {};
	const existing = providers[input.name] ?? {};
	const priorById = new Map<string, Record<string, unknown>>();
	for (const entry of Array.isArray(existing.models) ? existing.models : []) {
		const id = modelId(entry);
		if (id) priorById.set(id, entry as Record<string, unknown>);
	}
	providers[input.name] = {
		...existing,
		baseUrl: input.baseUrl,
		api: typeof existing.api === "string" && existing.api.length > 0 ? existing.api : "openai-completions",
		...(input.apiKey !== undefined ? { apiKey: input.apiKey } : {}),
		models: input.models.map((model) => ({ ...(priorById.get(model.id) ?? {}), ...modelRecord(model) })),
	};
	file.providers = providers;
	writeModelsFile(file, modelsJsonPath);
}

/**
 * Add or refresh one model's annotation without touching the provider's other models.
 * Missing context window and output cap get NEW_MODEL_DEFAULTS; values already on disk win.
 */
export function upsertProviderModel(
	providerName: string,
	baseUrl: string,
	model: ListedModel,
	modelsJsonPath: string = getModelsPath(),
): void {
	const file = readEditableModelsFile(modelsJsonPath);
	const providers = file.providers ?? {};
	const existingProvider = providers[providerName] ?? {};
	const existingModels = Array.isArray(existingProvider.models) ? existingProvider.models : [];
	const modelIndex = existingModels.findIndex((entry) => modelId(entry) === model.id);
	const prior = modelIndex >= 0 ? (existingModels[modelIndex] as Record<string, unknown>) : {};
	const merged: Record<string, unknown> = { ...prior, ...modelRecord(model) };
	if (merged.contextWindow === undefined) merged.contextWindow = NEW_MODEL_DEFAULTS.contextWindow;
	if (merged.maxTokens === undefined) merged.maxTokens = NEW_MODEL_DEFAULTS.maxTokens;
	const nextModels = [...existingModels];
	if (modelIndex >= 0) {
		nextModels[modelIndex] = merged;
	} else {
		nextModels.push(merged);
	}
	providers[providerName] = { ...existingProvider, baseUrl, models: nextModels };
	file.providers = providers;
	writeModelsFile(file, modelsJsonPath);
}
