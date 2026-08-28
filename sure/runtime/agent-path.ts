import { homedir } from "node:os";
import { delimiter, join } from "node:path";

/** Directory the coding agent keeps its bundled fd and rg in. */
export function agentBinDir(env: NodeJS.ProcessEnv = process.env): string {
	const configured = env.PI_CODING_AGENT_DIR;
	const base = configured
		? configured.startsWith("~")
			? join(homedir(), configured.slice(1))
			: configured
		: join(homedir(), ".pi", "agent");
	return join(base, "bin");
}

/**
 * Park the agent's own bin dir at the end of PATH.
 *
 * The agent puts that dir first so its bundled fd and rg are found, and it
 * leaves PATH untouched once the dir appears anywhere on it. Anything else
 * dropped in there therefore shadows the system binary of the same name for
 * every command the run makes: a docker left in that directory answered every
 * push with "denied" while the system docker pushed fine. Moving the dir to
 * the end keeps fd and rg reachable and stops the shadowing.
 */
export function demoteAgentBinDir(env: NodeJS.ProcessEnv, binDir: string): void {
	const current = env.PATH;
	if (current === undefined) {
		return;
	}
	const kept = current.split(delimiter).filter((entry) => entry && entry !== binDir);
	env.PATH = [...kept, binDir].join(delimiter);
}
