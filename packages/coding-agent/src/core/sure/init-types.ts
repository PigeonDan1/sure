import type { KnownProvider, ModelThinkingLevel } from "@earendil-works/pi-ai";

/** Persistent record written by /sure_init to .sure/init.json. */
export interface SureInitManifest {
	/** ISO timestamp of initialization. */
	initializedAt: string;
	/** Selected provider id (e.g. "openai-codex", "kimi-coding"). */
	defaultProvider: string;
	/** Selected model id (e.g. "gpt-5.5", "kimi-for-coding"). */
	defaultModel: string;
	/** Model reasoning level selected by SURE when the model has a defined profile. */
	defaultThinkingLevel?: ModelThinkingLevel;
	/** Whether the project was trusted at initialization time. */
	trusted: boolean;
	/** Whether the Python backend environment check passed. */
	pythonOk: boolean;
	/** List of available SURE skill commands discovered. */
	availableSkills: string[];
	/** Manifest format version. */
	version: number;
	/** Protocol /sure_init measured for the selected model. */
	defaultApi?: string;
	/** Reasoning levels upstream confirmed for the selected model, highest first. */
	supportedThinkingLevels?: ModelThinkingLevel[];
	/** What the probe did. Each step keeps its verdict plus a short upstream error snippet (detail, at most 240 characters); never a full body, never a credential. */
	capabilityProbe?: { probedAt: string; steps: ProbeStep[]; effortNote: string };
}

/** Auth mechanism for a SURE init provider option. */
export type SureInitAuthType = "oauth" | "api_key";

/** A selectable agent/provider option presented by /sure_init. */
export interface SureInitProviderOption {
	/** Stable option id used for CLI args and manifest. */
	id: string;
	/** Human-readable label shown in the selector. */
	name: string;
	/** Provider key recognized by the model registry. */
	provider: KnownProvider;
	/** Default model id to use when this option is selected. */
	defaultModel: string;
	/** How the user authenticates with this provider. */
	authType: SureInitAuthType;
	/** Short description shown in the selector. */
	description: string;
}

/** Result returned by runSureInit. */
export interface SureInitResult {
	/** Whether initialization completed successfully. */
	success: boolean;
	/** Optional manifest that was written. */
	manifest?: SureInitManifest;
	/** Human-readable summary message. */
	message: string;
	/** Concrete next action for the user, if any. */
	nextAction?: string;
}

/** Parsed /sure_init command arguments. */
export interface SureInitArgs {
	/** Pre-selected option: built-in option id, gateway provider name, or "custom". */
	optionId?: string;
	/** Pre-provided API key (api_key providers and gateways). */
	apiKey?: string;
	/** Pre-selected model id. Required for non-interactive runs. */
	model?: string;
	/** New gateway provider name (used with --option custom). */
	gatewayName?: string;
	/** New gateway base URL (used with --option custom). */
	gatewayBaseUrl?: string;
	/** Pre-selected reasoning effort. Must be one of the levels upstream confirmed. */
	effort?: string;
	/** Probe and annotate every model on the gateway, not just the selected one. */
	probeAll?: boolean;
}

/** Protocols /sure_init knows how to try against a relay. */
export type ProbeApi = "openai-completions" | "openai-responses" | "anthropic-messages";

export type ProbeVerdict =
	| "ok"
	| "wrong-protocol"
	| "no-channel"
	| "bad-key"
	| "client-rejected"
	| "unreachable"
	| "unknown";

/** One probe attempt, kept for the screen and for .sure/init.json. detail is the upstream body collapsed to one line and cut at 240 characters. */
export interface ProbeStep {
	api: ProbeApi;
	status: number | "network";
	verdict: ProbeVerdict;
	detail: string;
}
