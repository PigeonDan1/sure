import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { CheckpointData } from "../../../../sure/skills/sure_trans/hooks/checkpoints.ts";
import { postToolResult } from "../../../../sure/skills/sure_trans/hooks/index.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_trans");

type StatePatchForTest = {
	message?: string;
	checkpoint?: { data: CheckpointData };
};

function statePatch(result: { state_patch?: unknown }): StatePatchForTest {
	return (result.state_patch ?? {}) as StatePatchForTest;
}

function ctxWithManifest(name: string, status: string): SureHookContext {
	const runDir = resolve(__dirname, "tmp-trans-draft", name);
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	const data: CheckpointData = {
		currentUnit: "generate_adapter",
		completedUnits: [],
		retries: {},
		failedArtifactDigests: {},
	};
	writeFileSync(join(runDir, "state.json"), JSON.stringify({ checkpoint: { data } }, null, 2), "utf-8");
	writeFileSync(
		join(runDir, "artifacts", "adapter_manifest.json"),
		JSON.stringify(
			{
				schema: "sure.trans.adapter_manifest.v1",
				status,
				strategy: "python-import",
				model_py: join(runDir, "adapter", "model.py"),
				init_py: join(runDir, "adapter", "__init__.py"),
				validate_py: join(runDir, "adapter", "validate.py"),
				server_py: join(runDir, "adapter", "server.py"),
				config_yaml: join(runDir, "adapter", "config.yaml"),
				model_spec: join(runDir, "adapter", "model.spec.yaml"),
				dockerfile: join(runDir, "adapter", "Dockerfile.sure"),
				io_contract: { primary_field: "text", required_fields: ["text"] },
			},
			null,
			2,
		),
		"utf-8",
	);
	return {
		point: "post_tool_result",
		run: { id: "test-trans-draft", command: "/sure_trans", status: "running" } as never,
		skill: { name: "sure_trans", command: "/sure_trans" } as never,
		cwd: PACKAGE_DIR,
		packageDir: PACKAGE_DIR,
		runDir,
		args: "",
		event: { isError: false },
	} as SureHookContext;
}

// scaffold_adapter.py copies a model.py template that still raises
// NotImplementedError, so the manifest it writes is always status=draft, and
// SKILL.md tells the agent to run the scaffold before implementing the wrapper.
// Following the documented order therefore has to cost nothing.
describe("the adapter manifest the scaffold writes before model.py is implemented", () => {
	it("does not spend a retry", () => {
		const ctx = ctxWithManifest("draft", "draft");

		const patch = statePatch(postToolResult(ctx));

		expect(patch.checkpoint?.data.retries.generate_adapter ?? 0).toBe(0);
	});

	it("tells the agent to implement the wrapper", () => {
		const ctx = ctxWithManifest("draft-repair", "draft");

		const result = postToolResult(ctx);

		expect(result.ok).toBe(false);
		expect(result.repair).toContain("model.py");
	});
});
