import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
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

	it("requires model_framework separately from framework", () => {
		const result = preStart({
			args:
				"dockerfile=/abs/Dockerfile model=/abs/model inference_entrypoint=/abs/infer.py " +
				"framework=pytorch model_name=organization__model",
		} as SureHookContext);

		expect(result.ok).toBe(false);
		expect(result.repair).toContain("model_framework");
	});

	it("rejects the old combined framework value", () => {
		const result = preStart({
			args:
				"dockerfile=/abs/Dockerfile model=/abs/model inference_entrypoint=/abs/infer.py " +
				"framework=pytorch_transformers model_framework=transformers model_name=organization__model",
		} as SureHookContext);

		expect(result.ok).toBe(false);
		expect(result.repair).toContain("framework must be pytorch");
	});

	it("rejects VC execution for a CPU Docker input", () => {
		const root = mkdtempSync(join(tmpdir(), "sure-trans-cpu-vc-"));
		const model = join(root, "model");
		const dockerfile = join(root, "Dockerfile");
		const entrypoint = join(root, "infer.py");
		mkdirSync(model);
		writeFileSync(dockerfile, "FROM scratch\n");
		writeFileSync(entrypoint, "print('ok')\n");
		try {
			const result = preStart({
				args:
					`dockerfile=${dockerfile} model=${model} inference_entrypoint=${entrypoint} ` +
					"framework=pytorch model_framework=transformers model_name=organization__model " +
					"device=cpu execution=vc",
			} as SureHookContext);
			expect(result.ok).toBe(false);
			expect(result.repair).toContain("execution=vc requires device=auto or cuda");
		} finally {
			rmSync(root, { recursive: true, force: true });
		}
	});
});
