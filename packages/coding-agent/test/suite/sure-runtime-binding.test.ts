import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { HarnessRuntimeContract } from "../../../../sure/runtime/harness/resolve.ts";
import { validateSkillRuntimeBinding, writeSkillRuntimeBinding } from "../../../../sure/runtime/usage.ts";

describe("SURE skill runtime responsibility binding", () => {
	let root: string;
	let harness: HarnessRuntimeContract;

	beforeEach(() => {
		root = join(tmpdir(), `sure-runtime-binding-${Date.now()}-${Math.random().toString(36).slice(2)}`);
		const runtimeRoot = join(root, "harness");
		const python = join(runtimeRoot, "bin", "python");
		const manifestPath = join(runtimeRoot, "runtime-manifest.json");
		mkdirSync(join(runtimeRoot, "bin"), { recursive: true });
		writeFileSync(python, "#!/bin/sh\n", "utf-8");
		writeFileSync(manifestPath, JSON.stringify({ runtime_id: "harness-test", lock_sha256: "a".repeat(64) }), "utf-8");
		harness = {
			runtime_id: "harness-test",
			python_executable: python,
			python_abi: "cp311",
			python_version: "3.11",
			lock_sha256: "a".repeat(64),
			harness_version: "test",
			manifest_path: manifestPath,
			runtime_root: runtimeRoot,
		};
	});

	afterEach(() => {
		if (root) {
			rmSync(root, { recursive: true, force: true });
		}
	});

	it("validates Feed with only Harness Runtime required", () => {
		const path = writeSkillRuntimeBinding({
			runDir: root,
			skill: "sure_feed",
			harnessRuntime: harness,
			harnessRole: "research and gates",
			modelRuntimeReason: "no inference",
			evaluationRuntime: { reason: "no evaluation" },
		});
		expect(validateSkillRuntimeBinding(path, "sure_feed", false)).toBeUndefined();
		const payload = JSON.parse(readFileSync(path, "utf-8"));
		expect(payload.runtimes.model.required).toBe(false);
		expect(payload.runtimes.evaluation.required).toBe(false);
	});

	it("keeps the harness binding schema when the contract carries its own", () => {
		// bootstrap.py copies the runtime manifest schema onto the contract object,
		// so the contract can arrive carrying a schema of its own.
		const carriesSchema = {
			...harness,
			schema: "sure.harness.runtime.manifest.v1",
			runtime_type: "not_harness_python",
		} as HarnessRuntimeContract;
		const path = writeSkillRuntimeBinding({
			runDir: root,
			skill: "sure_feed",
			harnessRuntime: carriesSchema,
			harnessRole: "research and gates",
			modelRuntimeReason: "no inference",
			evaluationRuntime: { reason: "no evaluation" },
		});
		const payload = JSON.parse(readFileSync(path, "utf-8"));
		expect(payload.runtimes.harness.binding.schema).toBe("sure.harness.runtime.binding.v1");
		expect(payload.runtimes.harness.binding.runtime_type).toBe("harness_python");
		expect(payload.runtimes.harness.binding.runtime_id).toBe("harness-test");
	});

	it("validates Reval with Harness and Evaluation runtimes cross-bound", () => {
		const evaluationRoot = join(root, "evaluation");
		const evaluationPython = join(evaluationRoot, "bin", "python");
		const evaluationManifest = join(evaluationRoot, "runtime-manifest.json");
		mkdirSync(join(evaluationRoot, "bin"), { recursive: true });
		writeFileSync(evaluationPython, "#!/bin/sh\n", "utf-8");
		writeFileSync(
			evaluationManifest,
			JSON.stringify({ runtime_id: "evaluation-test", lock_sha256: "b".repeat(64) }),
			"utf-8",
		);
		const path = writeSkillRuntimeBinding({
			runDir: root,
			skill: "sure_reval",
			harnessRuntime: harness,
			harnessRole: "orchestration",
			modelRuntimeReason: "approved predictions are reused",
			evaluationRuntime: {
				role: "metrics",
				binding: {
					schema: "sure.evaluation.runtime.binding.v1",
					runtime_id: "evaluation-test",
					runtime_type: "evaluation_python",
					python_executable: evaluationPython,
					manifest_path: evaluationManifest,
					lock_sha256: "b".repeat(64),
					harness_runtime_id: "harness-test",
				},
			},
		});
		expect(validateSkillRuntimeBinding(path, "sure_reval", true)).toBeUndefined();
	});

	it("rejects a changed materialized Harness Runtime identity", () => {
		const path = writeSkillRuntimeBinding({
			runDir: root,
			skill: "sure_feed",
			harnessRuntime: harness,
			harnessRole: "research and gates",
			modelRuntimeReason: "no inference",
			evaluationRuntime: { reason: "no evaluation" },
		});
		writeFileSync(harness.manifest_path, JSON.stringify({ runtime_id: "changed" }), "utf-8");
		expect(validateSkillRuntimeBinding(path, "sure_feed", false)).toContain("runtime_id differs");
	});
});
