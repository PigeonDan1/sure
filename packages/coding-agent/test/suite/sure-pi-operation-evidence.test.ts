import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import { canonicalJsonDigest, createPolicySnapshot, type JsonValue } from "@earendil-works/sure-core";
import { afterEach, describe, expect, it } from "vitest";
import { runPiRegisteredOperation } from "../../../../sure/runtime/harness/registered-operation.ts";

const REPOSITORY_ROOT = resolve(import.meta.dirname, "../../../..");
const DISTRIBUTED_ENTRYPOINT = join(
	REPOSITORY_ROOT,
	"sure",
	"canonical",
	"shared",
	"onboard-execution",
	"scripts",
	"run_validate.py",
);
const POLICY_BOUND_ENTRYPOINT = join(
	REPOSITORY_ROOT,
	"sure",
	"canonical",
	"shared",
	"onboard-execution",
	"scripts",
	"check_env.py",
);
const CANONICAL_REGISTRY = join(REPOSITORY_ROOT, "sure", "canonical", "shared", "evaluation", "backend-manifest.json");
const TEMP_ROOT = join(import.meta.dirname, "tmp-pi-operation-evidence");

interface Fixture {
	ctx: SureHookContext;
	artifactPath: string;
	entrypointPath: string;
}

function fixture(name: string): Fixture {
	const root = join(TEMP_ROOT, name);
	const packageDir = join(root, "sure_onboard");
	const runDir = join(root, "run");
	const artifactPath = join(runDir, "artifacts", "import_result.json");
	const entrypointPath = join(packageDir, "scripts", "run_validate.py");
	mkdirSync(join(packageDir, "scripts"), { recursive: true });
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	copyFileSync(DISTRIBUTED_ENTRYPOINT, entrypointPath);
	writeFileSync(artifactPath, '{"stage":"before"}\n', "utf-8");
	const registry = JSON.parse(readFileSync(CANONICAL_REGISTRY, "utf-8")) as { registry_digest: string };
	writeFileSync(
		join(packageDir, "generation.lock.json"),
		`${JSON.stringify({
			schema: "sure.skill.generation.lock.v1",
			host: "pi",
			skill_id: "sure_onboard",
			semantic_backend_registry_digest: registry.registry_digest,
		})}\n`,
		"utf-8",
	);
	return {
		ctx: {
			point: "post_tool_result",
			run: { runId: name, command: "/sure_onboard", status: "running" } as never,
			skill: { name: "sure_onboard", command: "/sure_onboard" } as never,
			cwd: REPOSITORY_ROOT,
			packageDir,
			runDir,
			args: "",
			repoRoot: REPOSITORY_ROOT,
		},
		artifactPath,
		entrypointPath,
	};
}

afterEach(() => {
	rmSync(TEMP_ROOT, { recursive: true, force: true });
});

describe("Pi registered operation evidence", () => {
	it("wraps a registered runner and hashes mutating input and output bytes", () => {
		const fx = fixture("pass");
		const result = runPiRegisteredOperation({
			ctx: fx.ctx,
			unit_id: "validate_import",
			attempt: 2,
			operation_id: "sure.onboard.execute_import",
			script_id: "run_validate.py",
			artifact_input_path: fx.artifactPath,
			execute: () => {
				writeFileSync(fx.artifactPath, '{"stage":"after"}\n', "utf-8");
				return { ok: true, stdout: "validated", stderr: "", status: 0 };
			},
		});

		expect(result.ok).toBe(true);
		expect(result.evidence).toMatchObject({
			schema: "sure.operation.execution.v1",
			projection_version: 2,
			source: "pi_hook",
			operation_id: "sure.onboard.execute_import",
			artifact_mode: "mutating",
			verdict: "PASS",
			reason_code: "EXECUTION_SUCCEEDED",
			unit_id: "validate_import",
			attempt: 2,
		});
		expect(result.evidence?.artifact_input_digest).toMatch(/^sha256:[0-9a-f]{64}$/);
		expect(result.evidence?.artifact_output_digest).toMatch(/^sha256:[0-9a-f]{64}$/);
		expect(result.evidence?.artifact_output_digest).not.toBe(result.evidence?.artifact_input_digest);
		expect(result.evidence?.request_digest).toBeUndefined();
		expect(result.evidence?.receipt_digest).toBeUndefined();
	});

	it("accepts the generated Pi facade when its copied entrypoint matches the legacy tier", () => {
		const fx = fixture("generated-pi-facade");
		fx.ctx.packageDir = join(REPOSITORY_ROOT, "sure", "generated", "pi", "skills", "sure_onboard");
		const result = runPiRegisteredOperation({
			ctx: fx.ctx,
			unit_id: "validate_import",
			attempt: 1,
			operation_id: "sure.onboard.execute_import",
			script_id: "run_validate.py",
			artifact_input_path: fx.artifactPath,
			execute: () => ({ ok: true, stdout: "validated", stderr: "", status: 0 }),
		});

		expect(result.ok).toBe(true);
		expect(result.evidence).toMatchObject({
			verdict: "PASS",
			reason_code: "EXECUTION_SUCCEEDED",
			backend_resource_digest: "sha256:ce206cb6be4ceee4851cab572e99f71291b04a0d6e53f8a1cf68f4160fa4d283",
		});
	});

	it("rejects changed entrypoint bytes before invoking the legacy runner", () => {
		const fx = fixture("tampered-entrypoint");
		writeFileSync(fx.entrypointPath, "# tampered\n", "utf-8");
		const manifest = JSON.parse(readFileSync(CANONICAL_REGISTRY, "utf-8")) as Record<string, unknown>;
		const bundles = manifest.bundles as Array<{ operations: Array<Record<string, unknown>> }>;
		const operation = bundles
			.flatMap((bundle) => bundle.operations)
			.find((candidate) => candidate.operation_id === "sure.onboard.execute_import");
		if (!operation) {
			throw new Error("test fixture operation is missing");
		}
		operation.legacy_resource_digest = `sha256:${createHash("sha256")
			.update(readFileSync(fx.entrypointPath))
			.digest("hex")}`;
		const { registry_digest: _oldDigest, ...unsigned } = manifest;
		manifest.registry_digest = canonicalJsonDigest(unsigned as JsonValue);
		writeFileSync(join(fx.ctx.packageDir, "semantic-backends.json"), `${JSON.stringify(manifest)}\n`, "utf-8");
		let invoked = false;
		const result = runPiRegisteredOperation({
			ctx: fx.ctx,
			unit_id: "validate_import",
			attempt: 1,
			operation_id: "sure.onboard.execute_import",
			script_id: "run_validate.py",
			artifact_input_path: fx.artifactPath,
			execute: () => {
				invoked = true;
				return { ok: true, stdout: "", stderr: "", status: 0 };
			},
		});

		expect(invoked).toBe(false);
		expect(result.ok).toBe(false);
		expect(result.status).toBeNull();
		expect(result.stderr).toContain("entrypoint digest mismatch");
		expect(result.evidence).toMatchObject({
			source: "pi_hook",
			operation_id: "sure.onboard.execute_import",
			verdict: "NOT_EXECUTED",
			reason_code: "INVALID_CONTRACT",
		});
	});

	it("does not turn a zero exit without a regular output into PASS", () => {
		const fx = fixture("missing-output");
		const result = runPiRegisteredOperation({
			ctx: fx.ctx,
			unit_id: "validate_import",
			attempt: 1,
			operation_id: "sure.onboard.execute_import",
			script_id: "run_validate.py",
			artifact_input_path: fx.artifactPath,
			execute: () => {
				rmSync(fx.artifactPath);
				return { ok: true, stdout: "", stderr: "", status: 0 };
			},
		});

		expect(result.ok).toBe(false);
		expect(result.stderr).toContain("did not leave a regular gate artifact");
		expect(result.evidence).toMatchObject({
			verdict: "NOT_EXECUTED",
			reason_code: "INVALID_CONTRACT",
		});
		expect(result.evidence?.artifact_output_digest).toBeUndefined();
	});

	it("represents an unavailable legacy runner as missing capability", () => {
		const fx = fixture("capability-missing");
		const result = runPiRegisteredOperation({
			ctx: fx.ctx,
			unit_id: "validate_import",
			attempt: 1,
			operation_id: "sure.onboard.execute_import",
			script_id: "run_validate.py",
			artifact_input_path: fx.artifactPath,
			execute: () => ({
				ok: false,
				stdout: "",
				stderr: "HARNESS_RUNTIME_NOT_READY",
				status: null,
			}),
		});

		expect(result.ok).toBe(false);
		expect(result.evidence).toMatchObject({
			verdict: "NOT_EXECUTED",
			reason_code: "CAPABILITY_MISSING",
			diagnostics: ["HARNESS_RUNTIME_NOT_READY"],
		});
	});

	it("does not invoke a policy-bound operation without an immutable snapshot", () => {
		const fx = fixture("policy-missing");
		copyFileSync(POLICY_BOUND_ENTRYPOINT, join(fx.ctx.packageDir, "scripts", "check_env.py"));
		let invoked = false;
		const result = runPiRegisteredOperation({
			ctx: fx.ctx,
			unit_id: "build_env",
			attempt: 1,
			operation_id: "sure.onboard.execute_build_env",
			script_id: "check_env.py",
			artifact_input_path: fx.artifactPath,
			execute: () => {
				invoked = true;
				return { ok: true, stdout: "", stderr: "", status: 0 };
			},
		});

		expect(invoked).toBe(false);
		expect(result.evidence).toMatchObject({
			verdict: "NOT_EXECUTED",
			reason_code: "CAPABILITY_MISSING",
		});
	});

	it("rejects a policy snapshot whose run binding digest was changed", () => {
		const fx = fixture("policy-tampered");
		copyFileSync(POLICY_BOUND_ENTRYPOINT, join(fx.ctx.packageDir, "scripts", "check_env.py"));
		const snapshot = createPolicySnapshot({
			site_id: "test",
			policy_version: 1,
			policy: {},
			source: { kind: "test", raw_sha256: "a".repeat(64) },
			path_bindings: [],
		});
		const snapshotPath = join(fx.ctx.runDir, "artifacts", "site_policy.resolved.json");
		writeFileSync(snapshotPath, JSON.stringify(snapshot), "utf-8");
		Object.assign(fx.ctx.run, {
			policyDigest: snapshot.policy_digest,
			policySnapshotDigest: `sha256:${"b".repeat(64)}`,
			policySnapshotPath: snapshotPath,
		});
		let invoked = false;
		const result = runPiRegisteredOperation({
			ctx: fx.ctx,
			unit_id: "build_env",
			attempt: 1,
			operation_id: "sure.onboard.execute_build_env",
			script_id: "check_env.py",
			artifact_input_path: fx.artifactPath,
			execute: () => {
				invoked = true;
				return { ok: true, stdout: "", stderr: "", status: 0 };
			},
		});

		expect(invoked).toBe(false);
		expect(result.evidence).toMatchObject({
			verdict: "NOT_EXECUTED",
			reason_code: "INVALID_CONTRACT",
		});
	});
});
