import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { SureHookContext, SureHookResult } from "@earendil-works/pi-coding-agent/hooks";

export function preStart(ctx: SureHookContext): SureHookResult {
	const args = parseArgs(ctx.args);
	if (!args.scholar_name) {
		return {
			ok: false,
			repair:
				'Missing required argument: scholar_name. Provide a scholar name, e.g. /scholar_profile scholar_name="Yoshua Bengio"',
		};
	}

	return {
		ok: true,
		message: `[preStart] Scholar: ${args.scholar_name}, Run dir: ${ctx.runDir}`,
		state_patch: {
			phase: { id: "init", label: "Initializing pipeline", status: "running" },
			counters: { completed_stages: 0, total_stages: 5 },
		},
	};
}

export function preToolCall(): SureHookResult | undefined {
	return undefined;
}

export function postToolResult(): SureHookResult | undefined {
	return undefined;
}

export function preFinish(ctx: SureHookContext): SureHookResult {
	const manifestPath = join(ctx.runDir, "manifest.json");
	if (!existsSync(manifestPath)) {
		return {
			ok: false,
			repair: "manifest.json not found in run directory. Write it before calling sure_finish.",
			state_patch: {
				phase: { id: "validate", label: "Validating artifacts", status: "blocked" },
			},
		};
	}

	try {
		const manifest = JSON.parse(readFileSync(manifestPath, "utf-8")) as unknown;
		if (!isRecord(manifest)) {
			return { ok: false, repair: "manifest.json must be a JSON object. Fix the manifest and retry sure_finish." };
		}
		const required = [
			"schema_version",
			"run_id",
			"skill_name",
			"status",
			"created_at",
			"inputs",
			"outputs",
			"validation",
		];
		const missing = required.filter((key) => !(key in manifest));
		if (missing.length > 0) {
			return {
				ok: false,
				repair: `manifest.json missing required fields: ${missing.join(", ")}. Fix the manifest and retry sure_finish.`,
			};
		}

		const promptPath = join(ctx.runDir, "system_prompt.md");
		if (!existsSync(promptPath)) {
			return { ok: false, repair: "system_prompt.md not found. Run the full pipeline before finishing." };
		}
		const content = readFileSync(promptPath, "utf-8");
		const wordCount = content.split(/\s+/).filter(Boolean).length;
		if (wordCount < 500) {
			return {
				ok: false,
				repair: `system_prompt.md has only ${wordCount} words (minimum 500). Generate a more complete prompt.`,
				state_patch: {
					phase: { id: "validate", label: "Validating prompt quality", status: "blocked" },
					diagnostics: [{ severity: "warning", code: "LOW_WORD_COUNT", message: `${wordCount} words` }],
				},
			};
		}
	} catch (error: unknown) {
		const message = error instanceof Error ? error.message : String(error);
		return { ok: false, repair: `Failed to validate manifest: ${message}` };
	}

	return {
		ok: true,
		message: "[preFinish] All artifacts validated.",
		state_patch: {
			phase: { id: "validate", label: "Artifacts validated", status: "success" },
			counters: { completed_stages: 5, total_stages: 5 },
		},
	};
}

export function postFinish(): SureHookResult {
	return { ok: true, message: "[postFinish] Pipeline complete." };
}

export function onError(ctx: SureHookContext, error?: Error): SureHookResult {
	const args = parseArgs(ctx.args);
	const message = error?.message ?? "Unknown error";
	const manifest = {
		schema_version: "1",
		run_id: ctx.run.runId,
		skill_name: "scholar_profile",
		status: "failed",
		created_at: new Date().toISOString(),
		inputs: args,
		outputs: {},
		validation: {},
		error: { message },
	};

	try {
		writeFileSync(join(ctx.runDir, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf-8");
	} catch {
		// Best effort only: original agent error remains the primary failure.
	}

	return {
		ok: false,
		message: `[onError] Pipeline failed: ${message}`,
		state_patch: {
			phase: { id: "error", label: "Pipeline failed", status: "failed" },
			diagnostics: [{ severity: "error", code: "PIPELINE_FAILED", message }],
		},
	};
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseArgs(argsStr: string): Record<string, string> {
	const result: Record<string, string> = {};
	if (!argsStr) {
		return result;
	}
	const re = /(\w+)\s*=\s*(?:"([^"]*)"|(\S+))/g;
	let match = re.exec(argsStr);
	while (match !== null) {
		result[match[1]] = match[2] ?? match[3] ?? "";
		match = re.exec(argsStr);
	}
	return result;
}
