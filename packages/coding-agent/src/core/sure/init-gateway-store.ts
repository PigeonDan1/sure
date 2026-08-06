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
 * Replace one gateway provider entry (create if absent). The model list is replaced wholesale;
 * every other provider and unknown field is preserved. Refuses to rewrite a comment-bearing
 * file — a rewrite would silently destroy the comments.
 */
export function writeGatewayProvider(input: GatewayWriteInput, modelsJsonPath: string = getModelsPath()): void {
	const { raw, file } = readModelsFile(modelsJsonPath);
	if (raw !== undefined && stripJsonComments(stripBom(raw)) !== stripBom(raw)) {
		throw new Error(
			`models.json contains comments; refusing to rewrite it — add the provider manually. File: ${modelsJsonPath}`,
		);
	}
	const providers = file.providers ?? {};
	const existing = providers[input.name] ?? {};
	providers[input.name] = {
		...existing,
		baseUrl: input.baseUrl,
		api: typeof existing.api === "string" && existing.api.length > 0 ? existing.api : "openai-completions",
		...(input.apiKey !== undefined ? { apiKey: input.apiKey } : {}),
		models: input.models.map((model) => (model.name ? { id: model.id, name: model.name } : { id: model.id })),
	};
	file.providers = providers;
	mkdirSync(dirname(modelsJsonPath), { recursive: true });
	const tmpPath = `${modelsJsonPath}.sure-init-tmp`;
	writeFileSync(tmpPath, `${JSON.stringify(file, null, 2)}\n`, "utf-8");
	renameSync(tmpPath, modelsJsonPath);
}
