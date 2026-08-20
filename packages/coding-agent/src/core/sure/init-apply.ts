import type { Api, Model, ModelThinkingLevel } from "@earendil-works/pi-ai";
import { streamSimple } from "@earendil-works/pi-ai/compat";
import type { CapabilityProbeError, ProbeApi, ProbeStep } from "./init-capability-probe.ts";
import { probeModelCapability } from "./init-capability-probe.ts";
import { upsertProviderModel } from "./init-gateway-store.ts";

const LEVEL_ORDER: readonly ModelThinkingLevel[] = ["off", "minimal", "low", "medium", "high", "xhigh"];
const BASE_LEVEL: ModelThinkingLevel = "medium";

/**
 * The level a run picks when nobody said which one. Takes medium and clamps it to what
 * upstream confirmed, searching upward first like pi's own clampThinkingLevel does. Never
 * lands on off, because silently disabling reasoning is not a default anyone asked for.
 */
export function defaultThinkingLevel(supported: readonly ModelThinkingLevel[]): ModelThinkingLevel | undefined {
	const available = LEVEL_ORDER.filter((level) => level !== "off" && supported.includes(level));
	if (available.length === 0) return undefined;
	const base = LEVEL_ORDER.indexOf(BASE_LEVEL);
	for (let i = base; i < LEVEL_ORDER.length; i++) {
		if (available.includes(LEVEL_ORDER[i])) return LEVEL_ORDER[i];
	}
	for (let i = base - 1; i >= 0; i--) {
		if (available.includes(LEVEL_ORDER[i])) return LEVEL_ORDER[i];
	}
	return undefined;
}

/** Turn a probe failure into one screen line that says what to do next. */
export function probeFailureMessage(error: CapabilityProbeError, modelId: string): string {
	switch (error.kind) {
		case "no-channel":
			return `网关上 ${modelId} 现在没有可用渠道(${error.detail})。换一个模型,或者找网关管理员开渠道。`;
		case "bad-key":
			return `网关拒绝了这把 key(${error.detail})。换一把 key 重跑 /sure_init。`;
		case "client-rejected":
			return `网关拒绝这个客户端,${modelId} 走不通(${error.detail})。这是网关侧的客户端检测,换一个模型,或者找网关管理员。`;
		case "flaky":
			return `${modelId} 在网关上时通时不通(${error.detail})。这种状态跑评测会半路失败:换一个模型,或稍后再试。`;
		case "unreachable":
			return `连不上网关(${error.detail})。检查网络和 base URL。`;
		default:
			return `${modelId} 三个协议都不通:${error.detail}`;
	}
}

/** The slice of the command context this module needs. ExtensionCommandContext satisfies it. */
export interface ApplyContext {
	hasUI: boolean;
	ui: {
		select(title: string, choices: string[]): Promise<string | undefined>;
		notify(message: string, type?: "info" | "warning" | "error"): void;
	};
	modelRegistry: { refresh(): void };
}

export interface ApplyProbedModelInput {
	ctx: ApplyContext;
	providerName: string;
	baseUrl: string;
	apiKey?: string;
	modelId: string;
	modelName?: string;
	modelsJsonPath: string;
	/** Value of --effort, if any. */
	requestedEffort?: string;
	/** Override for tests. */
	probe?: typeof probeModelCapability;
}

export interface ApplyProbedModelOutcome {
	ok: boolean;
	message?: string;
	api?: ProbeApi;
	thinkingLevel?: ModelThinkingLevel;
	supportedLevels?: ModelThinkingLevel[];
	steps?: ProbeStep[];
	effortNote?: string;
	/** Lines worth showing on screen. */
	notes: string[];
}

function protocolNote(steps: ProbeStep[], api: ProbeApi): string {
	const tried = steps
		.map((step) => (step.verdict === "ok" ? `${step.api} 通` : `${step.api} ${step.status} ${step.verdict}`))
		.join(";");
	return `协议:${tried}。采用 ${api}。`;
}

function levelLabel(level: ModelThinkingLevel): string {
	return level === "off" ? "off(不推理)" : level;
}

/**
 * Probe one model, record what came back in models.json, and settle its default effort.
 * A failed probe writes nothing of its own, so no half-measured annotation survives — the
 * gateway's model list and key may already be on disk from the listing step before this.
 * This never writes an API key: gateway providers get theirs from writeGatewayProvider
 * beforehand, built-in providers keep theirs in auth storage.
 */
export async function applyProbedModel(input: ApplyProbedModelInput): Promise<ApplyProbedModelOutcome> {
	const probe = input.probe ?? probeModelCapability;
	const notes: string[] = [];
	const outcome = await probe({ baseUrl: input.baseUrl, apiKey: input.apiKey, modelId: input.modelId }, undefined);
	if (!outcome.ok) {
		return {
			ok: false,
			message: probeFailureMessage(outcome.error, input.modelId),
			steps: outcome.error.steps,
			notes,
		};
	}
	const result = outcome.result;
	notes.push(protocolNote(result.steps, result.api));
	notes.push(result.effortNote);

	const supported = [...result.supportedLevels];
	if (input.requestedEffort !== undefined) {
		if (!supported.includes(input.requestedEffort as ModelThinkingLevel)) {
			const message =
				supported.length === 0
					? `--effort ${input.requestedEffort} 用不上:${input.modelId} 没有任何上游确认的 effort 档位(${result.effortNote})。`
					: `--effort ${input.requestedEffort} 上游没确认。${input.modelId} 上游确认的档位:${supported.join("、")}。`;
			return {
				ok: false,
				message,
				steps: result.steps,
				supportedLevels: supported,
				notes,
			};
		}
	}

	try {
		upsertProviderModel(
			input.providerName,
			input.baseUrl,
			{
				id: input.modelId,
				...(input.modelName ? { name: input.modelName } : {}),
				api: result.api,
				...(result.reasoning !== undefined ? { reasoning: result.reasoning } : {}),
				...(result.thinkingLevelMap ? { thinkingLevelMap: result.thinkingLevelMap } : {}),
			},
			input.modelsJsonPath,
		);
		input.ctx.modelRegistry.refresh();
	} catch (error) {
		return {
			ok: false,
			message: error instanceof Error ? error.message : String(error),
			steps: result.steps,
			supportedLevels: supported,
			notes,
		};
	}

	// Asking is only worth it when there is a real level to choose. A model that upstream
	// only accepts "none" for has nothing to offer, so no question and no stored state.
	// Nobody picking is not an answer either: Esc in the TUI and print/json mode (hasUI is
	// true there, but the no-op dialog resolves undefined) both land on the same clamp the
	// headless path uses, instead of leaving the session with no level at all.
	const choosable = supported.filter((level) => level !== "off");
	let thinkingLevel: ModelThinkingLevel | undefined;
	if (input.requestedEffort !== undefined) {
		thinkingLevel = input.requestedEffort as ModelThinkingLevel;
	} else if (choosable.length > 0 && input.ctx.hasUI) {
		const choices = supported.map(levelLabel);
		const picked = await input.ctx.ui.select(`选默认 effort(${input.modelId}):`, choices);
		const chosen = picked === undefined ? undefined : supported[choices.indexOf(picked)];
		thinkingLevel = chosen ?? defaultThinkingLevel(supported);
	} else {
		thinkingLevel = defaultThinkingLevel(supported);
	}

	return {
		ok: true,
		api: result.api,
		...(thinkingLevel ? { thinkingLevel } : {}),
		supportedLevels: supported,
		steps: result.steps,
		effortNote: result.effortNote,
		notes,
	};
}

export interface VerifyRoundTripInput {
	registry: {
		find(provider: string, modelId: string): Model<Api> | undefined;
		getApiKeyAndHeaders(model: Model<Api>): Promise<{
			ok: boolean;
			apiKey?: string;
			headers?: Record<string, string>;
			env?: Record<string, string>;
			error?: string;
		}>;
	};
	provider: string;
	modelId: string;
	thinkingLevel?: ModelThinkingLevel;
	/** Override for tests. */
	streamFn?: typeof streamSimple;
}

/** Deadline for the closing round trip. A relay that never answers must not hang /sure_init. */
const VERIFY_TIMEOUT_MS = 60_000;

/**
 * Send one real request down pi-ai's own path. The capability probe builds its requests
 * by hand, so a probe that passed does not prove the configuration pi-ai will actually
 * use works. This is the check that does.
 */
export async function verifyModelRoundTrip(input: VerifyRoundTripInput): Promise<{ ok: boolean; detail?: string }> {
	const model = input.registry.find(input.provider, input.modelId);
	if (!model) {
		return { ok: false, detail: `${input.provider}/${input.modelId} does not resolve after the models.json write.` };
	}
	const auth = await input.registry.getApiKeyAndHeaders(model);
	if (!auth.ok) {
		return { ok: false, detail: auth.error ?? "no usable credentials" };
	}
	const stream = (input.streamFn ?? streamSimple)(
		model,
		{
			messages: [{ role: "user", content: "say ok" }],
		} as never,
		{
			apiKey: auth.apiKey,
			headers: auth.headers,
			env: auth.env,
			maxTokens: 32,
			...(input.thinkingLevel && input.thinkingLevel !== "off" ? { reasoning: input.thinkingLevel } : {}),
			signal: AbortSignal.timeout(VERIFY_TIMEOUT_MS),
		} as never,
	);
	for await (const _event of stream) {
		// Drain. The verdict comes from result().
	}
	const result = await stream.result();
	if (result.stopReason === "error" || result.stopReason === "aborted") {
		return { ok: false, detail: result.errorMessage ?? result.stopReason };
	}
	return { ok: true };
}

export interface ProbeWholeGatewayInput {
	providerName: string;
	baseUrl: string;
	apiKey?: string;
	modelIds: string[];
	modelsJsonPath: string;
	/**
	 * Models probed at once. Two, because each model is itself 3 effort probes in flight, and
	 * 6 concurrent requests is what made the relay's client check fire on 2026-08-18. A
	 * 19-model table takes roughly two to three minutes at this rate.
	 */
	concurrency?: number;
	probe?: typeof probeModelCapability;
}

/** Verdicts that mean the whole run is pointless, not just this model. */
const RUN_FATAL = new Set(["bad-key", "unreachable"]);

/** How much upstream text a skipped model carries back to the summary line. */
const SKIP_DETAIL_LIMIT = 80;

/**
 * models.json is rewritten whole on every annotation, so concurrent probes must not write
 * at the same time. Every write goes through one promise chain.
 */
let writeChain: Promise<void> = Promise.resolve();

function serializeWrite(work: () => void): Promise<void> {
	writeChain = writeChain.then(work, work);
	return writeChain;
}

/**
 * Probe and annotate every model on a gateway. A model with no channel or a refused
 * client is normal on a shared relay, so it is skipped with a reason rather than
 * failing the run. A dead key or an unreachable host stops everything.
 */
export async function probeWholeGateway(input: ProbeWholeGatewayInput): Promise<{
	ok: boolean;
	message?: string;
	annotated: string[];
	skipped: Array<{ modelId: string; reason: string; detail: string }>;
}> {
	const probe = input.probe ?? probeModelCapability;
	const concurrency = Math.max(1, input.concurrency ?? 2);
	const annotated: string[] = [];
	const skipped: Array<{ modelId: string; reason: string; detail: string }> = [];
	const queue = [...input.modelIds];
	let fatal: { kind: string; detail: string } | undefined;

	async function worker(): Promise<void> {
		while (queue.length > 0 && !fatal) {
			const modelId = queue.shift();
			if (!modelId) return;
			const outcome = await probe({ baseUrl: input.baseUrl, apiKey: input.apiKey, modelId }, undefined);
			if (!outcome.ok) {
				if (RUN_FATAL.has(outcome.error.kind)) {
					fatal = { kind: outcome.error.kind, detail: outcome.error.detail };
					return;
				}
				skipped.push({
					modelId,
					reason: outcome.error.kind,
					detail: outcome.error.detail.slice(0, SKIP_DETAIL_LIMIT),
				});
				continue;
			}
			const result = outcome.result;
			await serializeWrite(() =>
				upsertProviderModel(
					input.providerName,
					input.baseUrl,
					{
						id: modelId,
						api: result.api,
						...(result.reasoning !== undefined ? { reasoning: result.reasoning } : {}),
						...(result.thinkingLevelMap ? { thinkingLevelMap: result.thinkingLevelMap } : {}),
					},
					input.modelsJsonPath,
				),
			);
			annotated.push(modelId);
		}
	}

	await Promise.all(Array.from({ length: Math.min(concurrency, queue.length) }, () => worker()));

	if (fatal) {
		return {
			ok: false,
			message: probeFailureMessage(
				{ kind: fatal.kind as CapabilityProbeError["kind"], detail: fatal.detail, steps: [] },
				"整表探测",
			),
			annotated,
			skipped,
		};
	}
	return { ok: true, annotated, skipped };
}
