import type { ModelThinkingLevel, ThinkingLevelMap } from "@earendil-works/pi-ai";
import { openAICompatibleUrl } from "./init-model-listing.ts";
import type { ProbeApi, ProbeStep, ProbeVerdict } from "./init-types.ts";

export type { ProbeApi, ProbeStep, ProbeVerdict };

/**
 * Order matters. Measured against the company gateway on 2026-08-17: relays that answer
 * both protocols serve a thin shim on /responses (chatcmpl-shaped body, reasoning null),
 * while the gpt family refuses chat/completions outright and falls through to /responses.
 * Trying completions first landed correctly on all 8 models measured.
 */
export const PROBE_APIS: readonly ProbeApi[] = ["openai-completions", "openai-responses", "anthropic-messages"];

export interface ProbeTarget {
	baseUrl: string;
	apiKey?: string;
	modelId: string;
}

export interface ProbeOptions {
	fetch?: typeof globalThis.fetch;
	timeoutMs?: number;
	/** Pause between two attempts at the same request. Tests pass 0. */
	retryDelayMs?: number;
}

export interface ProtocolProbeOutcome {
	api?: ProbeApi;
	verdict: ProbeVerdict | "no-protocol";
	steps: ProbeStep[];
	detail: string;
}

const PROBE_TIMEOUT_MS = 20_000;
const DETAIL_LIMIT = 240;
/** How much upstream text one effort level contributes to a failure detail. */
const SNIPPET_LIMIT = 60;

/** Verdicts that no other protocol can fix. */
const FATAL_VERDICTS = new Set<ProbeVerdict>(["no-channel", "bad-key", "client-rejected", "unreachable"]);

/**
 * Classify one probe response. The marker strings were measured against the company
 * gateway on 2026-08-17: 400 with protocol_not_supported means the model speaks a
 * different protocol, 503 with model_not_found means no channel is wired up at all.
 */
export function classifyProbeResponse(status: number, body: string): ProbeVerdict {
	if (status >= 200 && status < 300) return "ok";
	if (status === 401) return "bad-key";
	if (status === 403) return "client-rejected";
	if (body.includes("protocol_not_supported")) return "wrong-protocol";
	if (body.includes("model_not_found")) return "no-channel";
	return "unknown";
}

interface ProbeRequest {
	url: string;
	body: unknown;
	headers: Record<string, string>;
}

function protocolRequest(api: ProbeApi, target: ProbeTarget): ProbeRequest {
	switch (api) {
		case "openai-completions":
			return {
				url: openAICompatibleUrl(target.baseUrl, "chat/completions"),
				body: { model: target.modelId, messages: [{ role: "user", content: "ok" }], max_tokens: 16 },
				headers: {},
			};
		case "openai-responses":
			return {
				url: openAICompatibleUrl(target.baseUrl, "responses"),
				body: { model: target.modelId, input: "ok", max_output_tokens: 16 },
				headers: {},
			};
		default:
			return {
				url: openAICompatibleUrl(target.baseUrl, "messages"),
				body: { model: target.modelId, max_tokens: 16, messages: [{ role: "user", content: "ok" }] },
				headers: {
					"anthropic-version": "2023-06-01",
					...(target.apiKey ? { "x-api-key": target.apiKey } : {}),
				},
			};
	}
}

export interface RawProbe {
	status: number | "network";
	body: string;
}

/** POST one probe request. Never throws: a transport failure comes back as status "network". */
export async function postProbe(request: ProbeRequest, target: ProbeTarget, options?: ProbeOptions): Promise<RawProbe> {
	const doFetch = options?.fetch ?? globalThis.fetch;
	try {
		const response = await doFetch(request.url, {
			method: "POST",
			headers: {
				"content-type": "application/json",
				accept: "application/json",
				...(target.apiKey ? { Authorization: `Bearer ${target.apiKey}` } : {}),
				...request.headers,
			},
			body: JSON.stringify(request.body),
			signal: AbortSignal.timeout(options?.timeoutMs ?? PROBE_TIMEOUT_MS),
		});
		return { status: response.status, body: await response.text() };
	} catch (error) {
		return { status: "network", body: error instanceof Error ? error.message : String(error) };
	}
}

function summarize(body: string): string {
	return body.replace(/\s+/g, " ").trim().slice(0, DETAIL_LIMIT);
}

/** Extra attempts one request gets before its answer counts as final. */
const PROBE_RETRIES = 2;
const PROBE_RETRY_DELAY_MS = 800;

/** A status that says nothing about the request itself, so it is worth asking again. */
function worthRetrying(status: number | "network"): boolean {
	if (status === "network") return true;
	return status === 403 || status === 429 || (status >= 500 && status < 600);
}

/**
 * POST one probe request, asking again while the caller says the answer carries no verdict.
 * Measured 2026-08-18: the relay answers 403 on a random half of the requests for the whole
 * gpt family, so one refusal is no evidence about the model.
 */
async function postWithRetry(
	request: ProbeRequest,
	target: ProbeTarget,
	options: ProbeOptions | undefined,
	retryable: (raw: RawProbe) => boolean,
): Promise<RawProbe> {
	const delayMs = options?.retryDelayMs ?? PROBE_RETRY_DELAY_MS;
	let raw = await postProbe(request, target, options);
	for (let attempt = 0; attempt < PROBE_RETRIES && retryable(raw); attempt++) {
		await new Promise((resolve) => setTimeout(resolve, delayMs));
		raw = await postProbe(request, target, options);
	}
	return raw;
}

/** Verdicts that answer the protocol question on their own, however the status reads. */
const DECISIVE_VERDICTS = new Set<ProbeVerdict>(["ok", "bad-key", "no-channel", "wrong-protocol"]);

/**
 * A protocol answer worth asking again. "network" is left out on purpose: it already means
 * the host is unreachable, not that this protocol is in doubt. 400 protocol_not_supported,
 * 401 and 503 model_not_found are decisive answers about the model, so they stand as given.
 */
function protocolWorthRetrying(raw: RawProbe): boolean {
	if (raw.status === "network") return false;
	if (!worthRetrying(raw.status)) return false;
	return !DECISIVE_VERDICTS.has(classifyProbeResponse(raw.status, raw.body));
}

/** Try each protocol in order and stop at the first one the model answers. */
export async function probeProtocol(target: ProbeTarget, options?: ProbeOptions): Promise<ProtocolProbeOutcome> {
	const steps: ProbeStep[] = [];
	for (const api of PROBE_APIS) {
		const raw = await postWithRetry(protocolRequest(api, target), target, options, protocolWorthRetrying);
		if (raw.status === "network") {
			steps.push({ api, status: "network", verdict: "unreachable", detail: summarize(raw.body) });
			return { verdict: "unreachable", steps, detail: summarize(raw.body) };
		}
		const verdict = classifyProbeResponse(raw.status, raw.body);
		const detail = verdict === "ok" ? "" : summarize(raw.body);
		steps.push({ api, status: raw.status, verdict, detail });
		if (verdict === "ok") {
			return { api, verdict: "ok", steps, detail: "" };
		}
		if (FATAL_VERDICTS.has(verdict)) {
			return { verdict, steps, detail };
		}
	}
	const detail = steps.map((step) => `${step.api}: HTTP ${step.status} ${step.detail}`).join("; ");
	return { verdict: "no-protocol", steps, detail };
}

/** Every level /sure_init asks upstream about, lowest to highest. */
export const PROBE_LEVELS: readonly ModelThinkingLevel[] = ["off", "minimal", "low", "medium", "high", "xhigh"];

/** Wire value sent for each internal level. "off" maps to the OpenAI "none" effort. */
const UPSTREAM_EFFORT: Record<ModelThinkingLevel, string> = {
	off: "none",
	minimal: "minimal",
	low: "low",
	medium: "medium",
	high: "high",
	xhigh: "xhigh",
};

export type EffortApi = "openai-completions" | "openai-responses";

export interface EffortProbeOutcome {
	supportedLevels: ModelThinkingLevel[];
	thinkingLevelMap: ThinkingLevelMap;
	reasoning: boolean;
	effortNote: string;
	/** Set when the probe could not decide every level. Nothing in the outcome is then usable. */
	failure?: CapabilityProbeError;
}

function effortRequest(api: EffortApi, target: ProbeTarget, level: ModelThinkingLevel): ProbeRequest {
	const effort = UPSTREAM_EFFORT[level];
	if (api === "openai-responses") {
		return {
			url: openAICompatibleUrl(target.baseUrl, "responses"),
			body: { model: target.modelId, input: "ok", max_output_tokens: 16, reasoning: { effort } },
			headers: {},
		};
	}
	return {
		url: openAICompatibleUrl(target.baseUrl, "chat/completions"),
		body: {
			model: target.modelId,
			messages: [{ role: "user", content: "ok" }],
			max_tokens: 16,
			reasoning_effort: effort,
		},
		headers: {},
	};
}

/**
 * Decide whether upstream honored the level we asked for. Measured 2026-08-17: the
 * responses protocol echoes the effort it actually applied (sol rewrites minimal to
 * none), while chat/completions reports reasoning work only through token usage.
 */
export function levelAccepted(api: EffortApi, level: ModelThinkingLevel, body: string): boolean {
	let parsed: unknown;
	try {
		parsed = JSON.parse(body);
	} catch {
		return false;
	}
	if (api === "openai-responses") {
		const reasoning = (parsed as { reasoning?: { effort?: unknown } | null })?.reasoning;
		return !!reasoning && reasoning.effort === UPSTREAM_EFFORT[level];
	}
	const usage = (parsed as { usage?: { completion_tokens_details?: { reasoning_tokens?: unknown } } })?.usage;
	const rawTokens = usage?.completion_tokens_details?.reasoning_tokens;
	const tokens = typeof rawTokens === "number" ? rawTokens : 0;
	const message = (parsed as { choices?: Array<{ message?: Record<string, unknown> }> })?.choices?.[0]?.message ?? {};
	const text = ["reasoning_content", "reasoning", "reasoning_text"]
		.map((field) => (typeof message[field] === "string" ? (message[field] as string) : ""))
		.find((value) => value.length > 0);
	const thought = tokens > 0 || !!text;
	return level === "off" ? !thought : thought;
}

/** Levels probed together. Six at once made the relay's client check fire on 2026-08-18. */
const EFFORT_BATCH_SIZE = 3;

/** A level upstream answered with something other than a verdict. */
interface AnsweredLevel {
	level: ModelThinkingLevel;
	state: "undecided" | "bad-key";
	status: number | "network";
	body: string;
}

type LevelOutcome = { level: ModelThinkingLevel; state: "decided"; accepted: boolean } | AnsweredLevel;

function statusLabel(status: number | "network"): string {
	return status === "network" ? "网络超时" : String(status);
}

/**
 * Ask upstream about one level, retrying the statuses that carry no verdict. A timeout
 * counts as one of those here: unlike the protocol probe, the host is already known to be
 * reachable, so a single silent request says nothing about the level.
 */
async function probeOneLevel(
	target: ProbeTarget,
	api: EffortApi,
	level: ModelThinkingLevel,
	options?: ProbeOptions,
): Promise<LevelOutcome> {
	const raw = await postWithRetry(effortRequest(api, target, level), target, options, (attempt) =>
		worthRetrying(attempt.status),
	);
	if (raw.status === 200) return { level, state: "decided", accepted: levelAccepted(api, level, raw.body) };
	if (raw.status === 400) return { level, state: "decided", accepted: false };
	if (raw.status === 401) return { level, state: "bad-key", status: 401, body: raw.body };
	return { level, state: "undecided", status: raw.status, body: raw.body };
}

function effortFailure(kind: CapabilityProbeError["kind"], detail: string): EffortProbeOutcome {
	return {
		supportedLevels: [],
		thinkingLevelMap: {},
		reasoning: false,
		effortNote: "",
		failure: { kind, detail, steps: [] },
	};
}

/**
 * Ask upstream about every level and keep only the ones it honored. A level the relay
 * never answered is not "unsupported": recording it as such would write a wrong
 * thinkingLevelMap that survives the session, so the whole probe fails instead.
 */
export async function probeEffortSupport(
	target: ProbeTarget,
	api: EffortApi,
	options?: ProbeOptions,
): Promise<EffortProbeOutcome> {
	const results: LevelOutcome[] = [];
	for (let start = 0; start < PROBE_LEVELS.length; start += EFFORT_BATCH_SIZE) {
		const batch = PROBE_LEVELS.slice(start, start + EFFORT_BATCH_SIZE);
		results.push(...(await Promise.all(batch.map((level) => probeOneLevel(target, api, level, options)))));
		if (results.some((entry) => entry.state === "bad-key")) break;
	}
	const deadKey = results.find((entry): entry is AnsweredLevel => entry.state === "bad-key");
	if (deadKey) {
		return effortFailure("bad-key", summarize(deadKey.body).slice(0, SNIPPET_LIMIT));
	}
	const undecided = results.filter((entry): entry is AnsweredLevel => entry.state === "undecided");
	if (undecided.length > 0) {
		const listed = undecided.map((entry) => `${entry.level}→${statusLabel(entry.status)}`).join("、");
		const snippet = summarize(undecided[undecided.length - 1].body).slice(0, SNIPPET_LIMIT);
		return effortFailure(
			"flaky",
			`effort 探测 ${PROBE_LEVELS.length} 档里 ${undecided.length} 档被拒:${listed}(${snippet})`,
		);
	}
	const supported = new Set(
		results.filter((entry) => entry.state === "decided" && entry.accepted).map((entry) => entry.level),
	);
	const thinkingLevelMap: ThinkingLevelMap = {};
	for (const level of PROBE_LEVELS) {
		thinkingLevelMap[level] = supported.has(level) ? UPSTREAM_EFFORT[level] : null;
	}
	const reasoning = PROBE_LEVELS.some((level) => level !== "off" && supported.has(level));
	const supportedLevels = [...PROBE_LEVELS].reverse().filter((level) => supported.has(level));
	const rejected = PROBE_LEVELS.filter((level) => !supported.has(level));
	const effortNote = reasoning
		? `effort 上游确认:${supportedLevels.join("、")};不支持:${rejected.join("、") || "无"}`
		: "effort:上游不接受任何档位,这个模型不做推理";
	return { supportedLevels, thinkingLevelMap, reasoning, effortNote };
}

export interface CapabilityProbeResult {
	api: ProbeApi;
	/** Omitted when the protocol carries no comparable effort concept. */
	reasoning?: boolean;
	thinkingLevelMap?: ThinkingLevelMap;
	supportedLevels: ModelThinkingLevel[];
	steps: ProbeStep[];
	effortNote: string;
}

export interface CapabilityProbeError {
	/**
	 * `flaky` means the protocol probe passed but the effort probes were rejected or failed
	 * after retries: the model answers only some of the time on this relay.
	 */
	kind: "no-channel" | "bad-key" | "client-rejected" | "no-protocol" | "unreachable" | "flaky";
	detail: string;
	steps: ProbeStep[];
}

export type CapabilityProbeOutcome =
	| { ok: true; result: CapabilityProbeResult }
	| { ok: false; error: CapabilityProbeError };

/**
 * Find out how one model on one endpoint has to be talked to: which protocol it
 * answers, and which reasoning efforts upstream actually honors.
 */
export async function probeModelCapability(
	target: ProbeTarget,
	options?: ProbeOptions,
): Promise<CapabilityProbeOutcome> {
	const protocol = await probeProtocol(target, options);
	if (!protocol.api) {
		const kind = protocol.verdict === "unknown" ? "no-protocol" : protocol.verdict;
		return {
			ok: false,
			error: { kind: kind as CapabilityProbeError["kind"], detail: protocol.detail, steps: protocol.steps },
		};
	}
	if (protocol.api === "anthropic-messages") {
		return {
			ok: true,
			result: {
				api: protocol.api,
				supportedLevels: [],
				steps: protocol.steps,
				effortNote: "effort 未探:anthropic-messages 用 thinking budget,不是 effort 字符串",
			},
		};
	}
	const effort = await probeEffortSupport(target, protocol.api, options);
	if (effort.failure) {
		return { ok: false, error: { ...effort.failure, steps: protocol.steps } };
	}
	return {
		ok: true,
		result: {
			api: protocol.api,
			reasoning: effort.reasoning,
			...(effort.reasoning ? { thinkingLevelMap: effort.thinkingLevelMap } : {}),
			supportedLevels: effort.supportedLevels,
			steps: protocol.steps,
			effortNote: effort.effortNote,
		},
	};
}
