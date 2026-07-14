import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import * as _bundledPiAgentCore from "@earendil-works/pi-agent-core";
import * as _bundledPiAiCompat from "@earendil-works/pi-ai/compat";
import * as _bundledPiAiOauth from "@earendil-works/pi-ai/oauth";
import * as _bundledPiTui from "@earendil-works/pi-tui";
import * as _bundledTypebox from "typebox";
import * as _bundledTypeboxCompile from "typebox/compile";
import * as _bundledTypeboxValue from "typebox/value";
import * as _bundledPiCodingAgent from "../../index.ts";
import * as _bundledPiCodingAgentHooks from "./hook-types.ts";

const require = createRequire(import.meta.url);

let aliases: Record<string, string> | undefined;

export function getSureHookAliases(): Record<string, string> {
	if (aliases) {
		return aliases;
	}

	const currentDir = dirname(fileURLToPath(import.meta.url));
	const packageIndex = [resolve(currentDir, "../..", "index.js"), resolve(currentDir, "../..", "index.ts")].find(
		existsSync,
	);
	const packageHooks = [resolve(currentDir, "hook-types.js"), resolve(currentDir, "hook-types.ts")].find(existsSync);
	const packagesRoot = resolve(currentDir, "../../../../");
	const resolveWorkspaceOrImport = (workspaceRelativePaths: string[], specifier: string): string => {
		for (const workspaceRelativePath of workspaceRelativePaths) {
			const workspacePath = join(packagesRoot, workspaceRelativePath);
			if (existsSync(workspacePath)) {
				return workspacePath;
			}
		}
		return fileURLToPath(import.meta.resolve(specifier));
	};

	const piCodingAgentEntry = packageIndex ?? fileURLToPath(import.meta.resolve("@earendil-works/pi-coding-agent"));
	const piCodingAgentHooksEntry =
		packageHooks ?? fileURLToPath(import.meta.resolve("@earendil-works/pi-coding-agent/hooks"));
	const piAgentCoreEntry = resolveWorkspaceOrImport(
		["agent/dist/index.js", "agent/src/index.ts"],
		"@earendil-works/pi-agent-core",
	);
	const piTuiEntry = resolveWorkspaceOrImport(["tui/dist/index.js", "tui/src/index.ts"], "@earendil-works/pi-tui");
	const piAiCompatEntry = resolveWorkspaceOrImport(
		["ai/dist/compat.js", "ai/src/compat.ts"],
		"@earendil-works/pi-ai/compat",
	);
	const piAiOauthEntry = resolveWorkspaceOrImport(
		["ai/dist/oauth.js", "ai/src/oauth.ts"],
		"@earendil-works/pi-ai/oauth",
	);

	aliases = {
		"@earendil-works/pi-agent-core": piAgentCoreEntry,
		"@earendil-works/pi-tui": piTuiEntry,
		"@earendil-works/pi-ai": piAiCompatEntry,
		"@earendil-works/pi-ai/compat": piAiCompatEntry,
		"@earendil-works/pi-ai/oauth": piAiOauthEntry,
		"@earendil-works/pi-coding-agent": piCodingAgentEntry,
		"@earendil-works/pi-coding-agent/hooks": piCodingAgentHooksEntry,
		"@mariozechner/pi-agent-core": piAgentCoreEntry,
		"@mariozechner/pi-tui": piTuiEntry,
		"@mariozechner/pi-ai": piAiCompatEntry,
		"@mariozechner/pi-ai/compat": piAiCompatEntry,
		"@mariozechner/pi-ai/oauth": piAiOauthEntry,
		"@mariozechner/pi-coding-agent": piCodingAgentEntry,
		"@mariozechner/pi-coding-agent/hooks": piCodingAgentHooksEntry,
		typebox: require.resolve("typebox"),
		"typebox/compile": require.resolve("typebox/compile"),
		"typebox/value": require.resolve("typebox/value"),
		"@sinclair/typebox": require.resolve("typebox"),
		"@sinclair/typebox/compile": require.resolve("typebox/compile"),
		"@sinclair/typebox/value": require.resolve("typebox/value"),
	};

	return aliases;
}

export function getSureHookVirtualModules(): Record<string, unknown> {
	return {
		typebox: _bundledTypebox,
		"typebox/compile": _bundledTypeboxCompile,
		"typebox/value": _bundledTypeboxValue,
		"@sinclair/typebox": _bundledTypebox,
		"@sinclair/typebox/compile": _bundledTypeboxCompile,
		"@sinclair/typebox/value": _bundledTypeboxValue,
		"@earendil-works/pi-agent-core": _bundledPiAgentCore,
		"@earendil-works/pi-tui": _bundledPiTui,
		"@earendil-works/pi-ai": _bundledPiAiCompat,
		"@earendil-works/pi-ai/compat": _bundledPiAiCompat,
		"@earendil-works/pi-ai/oauth": _bundledPiAiOauth,
		"@earendil-works/pi-coding-agent": _bundledPiCodingAgent,
		"@earendil-works/pi-coding-agent/hooks": _bundledPiCodingAgentHooks,
		"@mariozechner/pi-agent-core": _bundledPiAgentCore,
		"@mariozechner/pi-tui": _bundledPiTui,
		"@mariozechner/pi-ai": _bundledPiAiCompat,
		"@mariozechner/pi-ai/compat": _bundledPiAiCompat,
		"@mariozechner/pi-ai/oauth": _bundledPiAiOauth,
		"@mariozechner/pi-coding-agent": _bundledPiCodingAgent,
		"@mariozechner/pi-coding-agent/hooks": _bundledPiCodingAgentHooks,
	};
}
