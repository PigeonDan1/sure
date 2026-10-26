import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { AssistantMessage, Usage } from "@earendil-works/pi-ai";
import type { SureRunRecord } from "./types.ts";

export const TOKEN_USAGE_FILE = "token_usage.json";

interface TokenUsageCost {
	input: number;
	output: number;
	cache_read: number;
	cache_write: number;
	total: number;
}

interface TokenUsageTotals {
	requests: number;
	input_tokens: number;
	output_tokens: number;
	cache_read_tokens: number;
	cache_write_tokens: number;
	cache_write_1h_tokens: number;
	reasoning_tokens: number;
	total_tokens: number;
	cost: number;
	cost_breakdown: TokenUsageCost;
}

interface TokenUsageModelTotals extends TokenUsageTotals {
	provider: string;
	model: string;
	api: string;
}

export interface SureTokenUsage extends TokenUsageTotals {
	schema: "sure.token_usage.v1";
	run_id: string;
	command: string;
	status: SureRunRecord["status"];
	started_at: string;
	updated_at: string;
	finished_at?: string;
	by_model: Record<string, TokenUsageModelTotals>;
}

function zeroCost(): TokenUsageCost {
	return { input: 0, output: 0, cache_read: 0, cache_write: 0, total: 0 };
}

function zeroTotals(): TokenUsageTotals {
	return {
		requests: 0,
		input_tokens: 0,
		output_tokens: 0,
		cache_read_tokens: 0,
		cache_write_tokens: 0,
		cache_write_1h_tokens: 0,
		reasoning_tokens: 0,
		total_tokens: 0,
		cost: 0,
		cost_breakdown: zeroCost(),
	};
}

function roundCost(value: number): number {
	return Math.round(value * 1_000_000_000_000) / 1_000_000_000_000;
}

function createTokenUsage(record: SureRunRecord): SureTokenUsage {
	return {
		schema: "sure.token_usage.v1",
		run_id: record.runId,
		command: `/${record.command.replace(/^\//, "")}`,
		status: record.status,
		started_at: record.startedAt,
		updated_at: record.updatedAt,
		...(record.finishedAt ? { finished_at: record.finishedAt } : {}),
		...zeroTotals(),
		by_model: {},
	};
}

function usagePath(record: SureRunRecord): string {
	return join(record.runDir, "artifacts", TOKEN_USAGE_FILE);
}

function writeJson(path: string, value: unknown): void {
	const temporaryPath = `${path}.sure-token-usage-tmp`;
	writeFileSync(temporaryPath, `${JSON.stringify(value, null, 2)}\n`, "utf-8");
	renameSync(temporaryPath, path);
}

function readTokenUsage(record: SureRunRecord): SureTokenUsage {
	const path = usagePath(record);
	if (!existsSync(path)) {
		return createTokenUsage(record);
	}
	return JSON.parse(readFileSync(path, "utf-8")) as SureTokenUsage;
}

function addCost(target: TokenUsageCost, source: Usage["cost"]): void {
	target.input = roundCost(target.input + source.input);
	target.output = roundCost(target.output + source.output);
	target.cache_read = roundCost(target.cache_read + source.cacheRead);
	target.cache_write = roundCost(target.cache_write + source.cacheWrite);
	target.total = roundCost(target.total + source.total);
}

function addUsage(target: TokenUsageTotals, usage: Usage): void {
	target.requests += 1;
	target.input_tokens += usage.input;
	target.output_tokens += usage.output;
	target.cache_read_tokens += usage.cacheRead;
	target.cache_write_tokens += usage.cacheWrite;
	target.cache_write_1h_tokens += usage.cacheWrite1h ?? 0;
	target.reasoning_tokens += usage.reasoning ?? 0;
	target.total_tokens += usage.totalTokens;
	target.cost = roundCost(target.cost + usage.cost.total);
	addCost(target.cost_breakdown, usage.cost);
}

function syncMetadata(tokenUsage: SureTokenUsage, record: SureRunRecord): void {
	tokenUsage.command = `/${record.command.replace(/^\//, "")}`;
	tokenUsage.status = record.status;
	tokenUsage.started_at = record.startedAt;
	tokenUsage.updated_at = record.updatedAt;
	if (record.finishedAt) {
		tokenUsage.finished_at = record.finishedAt;
	} else {
		delete tokenUsage.finished_at;
	}
}

export function updateTokenUsageMetadata(record: SureRunRecord): void {
	const path = usagePath(record);
	mkdirSync(join(record.runDir, "artifacts"), { recursive: true });
	const tokenUsage = readTokenUsage(record);
	syncMetadata(tokenUsage, record);
	writeJson(path, tokenUsage);
}

export function recordAssistantUsage(record: SureRunRecord, message: AssistantMessage): SureTokenUsage {
	const path = usagePath(record);
	mkdirSync(join(record.runDir, "artifacts"), { recursive: true });
	const tokenUsage = readTokenUsage(record);
	addUsage(tokenUsage, message.usage);
	const modelKey = `${message.provider}/${message.model}`;
	const modelTotals = tokenUsage.by_model[modelKey] ?? {
		provider: message.provider,
		model: message.model,
		api: message.api,
		...zeroTotals(),
	};
	addUsage(modelTotals, message.usage);
	tokenUsage.by_model[modelKey] = modelTotals;
	syncMetadata(tokenUsage, record);
	writeJson(path, tokenUsage);
	return tokenUsage;
}
