import type { KnownProvider } from "@earendil-works/pi-ai";

/** Persistent record written by /sure_init to .sure/init.json. */
export interface SureInitManifest {
	/** ISO timestamp of initialization. */
	initializedAt: string;
	/** Selected provider id (e.g. "openai-codex", "kimi-coding"). */
	defaultProvider: string;
	/** Selected model id (e.g. "gpt-5.5", "kimi-for-coding"). */
	defaultModel: string;
	/** Whether the project was trusted at initialization time. */
	trusted: boolean;
	/** Whether the Python backend environment check passed. */
	pythonOk: boolean;
	/** List of available SURE skill commands discovered. */
	availableSkills: string[];
	/** Manifest format version. */
	version: number;
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
	/** Pre-selected option id. */
	optionId?: string;
	/** Pre-provided API key (only valid for api_key providers). */
	apiKey?: string;
}
