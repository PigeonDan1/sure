import { tokenizeCommandArgs } from "../../utils/command-args.ts";
import { discoverSureSkillPackages, SURE_COMMANDS } from "./manifest.ts";

export interface SureAgentRequest {
	schema: "sure.agent.request.v1";
	invocation_id: string;
	command: string;
	arguments: string;
	output_dir?: string;
}

export function parseSureAgentRequest(raw: string): SureAgentRequest {
	const value: unknown = JSON.parse(raw);
	if (typeof value !== "object" || value === null || Array.isArray(value)) {
		throw new Error("SURE agent request must be an object.");
	}
	const request = value as Record<string, unknown>;
	if (
		request.schema !== "sure.agent.request.v1" ||
		typeof request.invocation_id !== "string" ||
		!/^[a-f0-9]{32}$/.test(request.invocation_id) ||
		typeof request.command !== "string" ||
		typeof request.arguments !== "string" ||
		(request.output_dir !== undefined && (typeof request.output_dir !== "string" || !request.output_dir))
	) {
		throw new Error("Invalid SURE agent request schema, invocation ID, command, arguments or output directory.");
	}
	const command = request.command.replace(/^\//, "");
	if (!["sure_init", "sure_resume", ...SURE_COMMANDS].includes(command)) {
		throw new Error(`Unknown SURE command: ${command}.`);
	}
	const tokens = tokenizeCommandArgs(request.arguments).map((token) => token.value);
	for (const token of tokens) {
		const key = token.split("=", 1)[0].replace(/^--?/, "");
		if (["api-key", "api_key"].includes(key)) {
			throw new Error("Configure provider credentials through native login or environment variables.");
		}
		if (key === "output_dir") {
			throw new Error("Pass output_dir as a structured request field, not inside arguments.");
		}
	}
	if (["sure_init", "sure_resume"].includes(command) && request.output_dir !== undefined) {
		throw new Error("init/resume use native manifest locations; omit output_dir.");
	}
	if (command === "sure_resume" && !/^[A-Za-z0-9_-]+$/.test(request.arguments.trim())) {
		throw new Error("resume requires exactly one run ID.");
	}
	return {
		schema: "sure.agent.request.v1",
		invocation_id: request.invocation_id,
		command,
		arguments: request.arguments,
		...(typeof request.output_dir === "string" ? { output_dir: request.output_dir } : {}),
	};
}

export function inspectSureAgentRequest(cwd: string, request?: SureAgentRequest) {
	const discovered = discoverSureSkillPackages(cwd);
	const commands = [
		{ command: "/sure_init", kind: "builtin" },
		{ command: "/sure_resume", kind: "builtin" },
		...discovered.packages.map((skill) => ({
			command: `/${skill.manifest.command}`,
			kind: "workflow",
			description: skill.manifest.description,
			manual: skill.promptPath,
			manifest: skill.manifestPath,
			source: skill.source,
		})),
	];
	if (request && !commands.some((entry) => entry.command === `/${request.command}`)) {
		throw new Error(`No valid native SURE package for /${request.command}. Check discovery diagnostics.`);
	}
	return { schema: "sure.agent.discovery.v1", cwd, commands, diagnostics: discovered.diagnostics };
}
