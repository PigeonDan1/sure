import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

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
