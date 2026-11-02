// Native discovery and request validation for external SURE skill callers.
import { readFileSync } from "node:fs";
import {
	inspectSureAgentRequest,
	parseSureAgentRequest,
} from "../packages/coding-agent/src/core/sure/agent-invocation.ts";

try {
	const raw = readFileSync(process.argv[2] ?? 0, "utf8").trim();
	const request = raw ? parseSureAgentRequest(raw) : undefined;
	const discovery = inspectSureAgentRequest(process.cwd(), request);
	process.stdout.write(`${JSON.stringify({ status: "success", discovery, request })}\n`);
} catch (error) {
	process.stdout.write(
		`${JSON.stringify({ status: "blocked", error: error instanceof Error ? error.message : String(error) })}\n`,
	);
	process.exitCode = 1;
}
