import { delimiter, join } from "node:path";
import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import { describe, expect, it } from "vitest";
import { agentBinDir, demoteAgentBinDir } from "../../../../sure/runtime/agent-path.ts";
import { preStart } from "../../../../sure/skills/sure_trans/hooks/index.ts";

describe("demoteAgentBinDir", () => {
	const binDir = join("/home/user", ".pi", "agent", "bin");

	it("moves the agent bin dir last so system binaries win", () => {
		const env = { PATH: [binDir, "/usr/local/bin", "/usr/bin"].join(delimiter) };
		demoteAgentBinDir(env, binDir);
		expect(env.PATH).toBe(["/usr/local/bin", "/usr/bin", binDir].join(delimiter));
	});

	it("keeps the dir on PATH so the bundled fd and rg stay reachable", () => {
		const env = { PATH: [binDir, "/usr/bin"].join(delimiter) };
		demoteAgentBinDir(env, binDir);
		expect(env.PATH?.split(delimiter)).toContain(binDir);
	});

	it("appends the dir when PATH does not carry it yet", () => {
		// getShellEnv() prepends the dir only when PATH does not already hold it,
		// so parking it at the end is what stops that prepend from happening.
		const env = { PATH: "/usr/bin" };
		demoteAgentBinDir(env, binDir);
		expect(env.PATH).toBe(["/usr/bin", binDir].join(delimiter));
	});

	it("is idempotent", () => {
		const env = { PATH: ["/usr/bin", binDir].join(delimiter) };
		demoteAgentBinDir(env, binDir);
		demoteAgentBinDir(env, binDir);
		expect(env.PATH).toBe(["/usr/bin", binDir].join(delimiter));
	});

	it("collapses every copy of the dir, not just the first", () => {
		const env = { PATH: [binDir, "/usr/bin", binDir].join(delimiter) };
		demoteAgentBinDir(env, binDir);
		expect(env.PATH).toBe(["/usr/bin", binDir].join(delimiter));
	});

	it("leaves an unset PATH alone", () => {
		const env: NodeJS.ProcessEnv = {};
		demoteAgentBinDir(env, binDir);
		expect(env.PATH).toBeUndefined();
	});
});

describe("sure_trans preStart", () => {
	it("parks the agent bin dir last before the run does anything", () => {
		// Seven units drive docker straight from bash rather than through a
		// skill script, so the fix has to land on the environment itself.
		const dir = agentBinDir();
		const previous = process.env.PATH;
		process.env.PATH = [dir, "/usr/bin"].join(delimiter);
		try {
			preStart({ args: "" } as SureHookContext);
			expect(process.env.PATH).toBe(["/usr/bin", dir].join(delimiter));
		} finally {
			process.env.PATH = previous;
		}
	});
});
