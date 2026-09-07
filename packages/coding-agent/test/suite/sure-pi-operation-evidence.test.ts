import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import { canonicalJsonDigest, createPolicySnapshot, type JsonValue } from "@earendil-works/sure-core";
import { afterEach, describe, expect, it } from "vitest";
import {
	runPiRegisteredOperation,
	runPiRegisteredValidator,
} from "../../../../sure/runtime/harness/registered-operation.ts";

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
const TRANS_VALIDATOR_ENTRYPOINT = join(
	REPOSITORY_ROOT,
	"sure",
	"canonical",
	"skills",
	"sure-trans",
	"scripts",
	"check_artifact.py",
);
const CANONICAL_REGISTRY = join(REPOSITORY_ROOT, "sure", "canonical", "shared", "evaluation", "backend-manifest.json");
const TEMP_ROOT = join(import.meta.dirname, "tmp-pi-operation-evidence");

interface Fixture {
	ctx: SureHookContext;
	artifactPath: string;
	entrypointPath: string;
}

interface ValidatorFixture {
	ctx: SureHookContext;
	artifactPath: string;
	entrypointPath: string;
}

interface ProducerFixture {
	ctx: SureHookContext;
	inputPath: string;
	outputPath: string;
}

interface DifferentialCase {
	id: string;
	pi: {
		evidence_verdict: "PASS" | "FAIL" | "NOT_EXECUTED";
		evidence_reason_code: string;
		request_receipt: "present" | "absent";
	};
}

interface ExternalDifferentialCase {
	id: string;
	pi: {
		evidence_verdict: "PASS" | "FAIL" | "NOT_EXECUTED";
		evidence_reason_code: string;
		request_receipt: "present" | "absent";
		assurance_profile: "cooperative" | "hook_enforced";
	};
}

const DIFFERENTIAL_FIXTURE = JSON.parse(
	readFileSync(
		new URL("../../../../sure/canonical/fixtures/execution-differential-traces.json", import.meta.url),
		"utf8",
	),
) as { schema: string; cases: DifferentialCase[] };

const EXTERNAL_DIFFERENTIAL_FIXTURE = JSON.parse(
	readFileSync(
		new URL("../../../../sure/canonical/fixtures/external-adapter-differential-traces.v1.json", import.meta.url),
		"utf8",
	),
) as { schema: string; cases: ExternalDifferentialCase[] };

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

function validatorFixture(name: string): ValidatorFixture {
	const root = join(TEMP_ROOT, `validator-${name}`);
	const packageDir = join(root, "sure_trans");
	const runDir = join(root, "run");
	const artifactPath = join(runDir, "artifacts", "import_result.json");
	const entrypointPath = join(packageDir, "scripts", "check_artifact.py");
	mkdirSync(join(packageDir, "scripts"), { recursive: true });
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	copyFileSync(TRANS_VALIDATOR_ENTRYPOINT, entrypointPath);
	writeFileSync(artifactPath, '{"status":"ready"}\n', "utf-8");
	const registry = JSON.parse(readFileSync(CANONICAL_REGISTRY, "utf-8")) as { registry_digest: string };
	writeFileSync(
		join(packageDir, "generation.lock.json"),
		`${JSON.stringify({
			schema: "sure.skill.generation.lock.v1",
			host: "pi",
			skill_id: "sure_trans",
			semantic_backend_registry_digest: registry.registry_digest,
		})}\n`,
		"utf-8",
	);
	return {
		ctx: {
			point: "post_tool_result",
			run: { runId: name, command: "/sure_trans", status: "running" } as never,
			skill: { name: "sure_trans", command: "/sure_trans" } as never,
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

function producerFixture(name: string): ProducerFixture {
	const root = join(TEMP_ROOT, `producer-${name}`);
	const runDir = join(root, "run");
	const inputPath = join(runDir, "artifacts", "trans_input_resolved.json");
	const outputPath = join(runDir, "artifacts", "source_image_result.json");
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	writeFileSync(inputPath, '{"source_kind":"python"}\n', "utf-8");
	return {
		ctx: {
			point: "post_tool_result",
			run: { runId: name, command: "/sure_trans", status: "running" } as never,
			skill: { name: "sure_trans", command: "/sure_trans" } as never,
			cwd: REPOSITORY_ROOT,
			packageDir: join(REPOSITORY_ROOT, "sure", "generated", "pi", "skills", "sure_trans"),
			runDir,
			args: "",
			repoRoot: REPOSITORY_ROOT,
		},
		inputPath,
		outputPath,
	};
}

afterEach(() => {
	rmSync(TEMP_ROOT, { recursive: true, force: true });
});

describe("Pi registered operation evidence", () => {
	it("follows the canonical differential trace while recording the facade assurance boundary", () => {
		expect(DIFFERENTIAL_FIXTURE.schema).toBe("sure.execution.differential_traces.v1");
		const expected = new Map(DIFFERENTIAL_FIXTURE.cases.map((item) => [item.id, item.pi]));
		const run = (
			id: string,
			execute: (fx: Fixture) => { ok: boolean; stdout: string; stderr: string; status: number | null },
		) => {
			const fx = fixture(`differential-${id}`);
			const result = runPiRegisteredOperation({
				ctx: fx.ctx,
				unit_id: "validate_import",
				attempt: 1,
				operation_id: "sure.onboard.execute_import",
				script_id: "run_validate.py",
				artifact_input_path: fx.artifactPath,
				execute: () => execute(fx),
			});
			const evidence = result.evidence;
			if (!evidence) throw new Error(`${id} did not emit evidence`);
			const item = expected.get(id);
			if (!item) throw new Error(`${id} trace is missing`);
			expect({ evidence_verdict: evidence.verdict, evidence_reason_code: evidence.reason_code }, id).toEqual({
				evidence_verdict: item.evidence_verdict,
				evidence_reason_code: item.evidence_reason_code,
			});
			expect(item.request_receipt).toBe("absent");
			expect(evidence.request_digest, id).toBeUndefined();
			expect(evidence.receipt_digest, id).toBeUndefined();
		};

		run("invalid_contract", () => {
			throw new Error("invalid registered operation contract");
		});
		run("capability_missing", () => ({
			ok: false,
			stdout: "",
			stderr: "HARNESS_RUNTIME_NOT_READY",
			status: null,
		}));
		run("executor_failed", () => ({ ok: false, stdout: "failed", stderr: "", status: 7 }));
		run("partial_output", (fx) => {
			rmSync(fx.artifactPath);
			return { ok: true, stdout: "", stderr: "", status: 0 };
		});
		run("execution_succeeded_validation_pending", () => ({ ok: true, stdout: "ok", stderr: "", status: 0 }));
	});

	it("keeps external-adapter traces semantically aligned without fabricating request or receipt evidence", () => {
		expect(EXTERNAL_DIFFERENTIAL_FIXTURE.schema).toBe("sure.execution.external_adapter_differential_traces.v1");
		const expected = new Map(EXTERNAL_DIFFERENTIAL_FIXTURE.cases.map((item) => [item.id, item.pi]));
		const run = (
			id: string,
			execute: (fx: Fixture) => { ok: boolean; stdout: string; stderr: string; status: number | null },
		) => {
			const fx = fixture(`external-differential-${id}`);
			const result = runPiRegisteredOperation({
				ctx: fx.ctx,
				unit_id: "validate_import",
				attempt: 1,
				operation_id: "sure.onboard.execute_import",
				script_id: "run_validate.py",
				artifact_input_path: fx.artifactPath,
				execute: () => execute(fx),
			});
			const evidence = result.evidence;
			if (!evidence) throw new Error(`${id} did not emit evidence`);
			const item = expected.get(id);
			if (!item) throw new Error(`${id} trace is missing`);
			expect({ evidence_verdict: evidence.verdict, evidence_reason_code: evidence.reason_code }, id).toEqual({
				evidence_verdict: item.evidence_verdict,
				evidence_reason_code: item.evidence_reason_code,
			});
			expect(item.request_receipt, id).toBe("absent");
			expect(evidence.request_digest, id).toBeUndefined();
			expect(evidence.receipt_digest, id).toBeUndefined();
		};

		run("admitted_executor_failure", () => ({ ok: false, stdout: "failed", stderr: "", status: 7 }));
		run("missing_registration", () => ({ ok: false, stdout: "", stderr: "capability missing", status: null }));
		run("policy_drift", () => {
			throw new Error("external adapter policy snapshot drift");
		});
		run("receipt_tamper", (fx) => {
			rmSync(fx.artifactPath);
			return { ok: true, stdout: "", stderr: "", status: 0 };
		});
		run("success_waits_for_validation", () => ({ ok: true, stdout: "ok", stderr: "", status: 0 }));
	});

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

	it("binds a producing operation to an upstream artifact before creating its output", () => {
		const fx = producerFixture("producing");
		let invoked = false;
		const result = runPiRegisteredOperation({
			ctx: fx.ctx,
			unit_id: "build_source_image",
			attempt: 1,
			operation_id: "sure.trans.execute_source_image",
			script_id: "run_docker_build.py",
			artifact_input_path: fx.inputPath,
			artifact_output_path: fx.outputPath,
			execute: () => {
				invoked = true;
				writeFileSync(fx.outputPath, '{"status":"passed"}\n', "utf-8");
				return { ok: true, stdout: "built", stderr: "", status: 0 };
			},
		});

		expect(invoked).toBe(true);
		expect(result.ok).toBe(true);
		expect(result.evidence).toMatchObject({
			operation_id: "sure.trans.execute_source_image",
			artifact_mode: "producing",
			verdict: "PASS",
			reason_code: "EXECUTION_SUCCEEDED",
		});
		expect(result.evidence?.artifact_input_path).toBe(fx.inputPath);
		expect(result.evidence?.artifact_output_path).toBe(fx.outputPath);
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

	describe("registered semantic validators", () => {
		it("emits PASS evidence without allowing the validator to rewrite its input", () => {
			const fx = validatorFixture("pass");
			let resolvedPath = "";
			const before = readFileSync(fx.artifactPath, "utf-8");
			const result = runPiRegisteredValidator({
				ctx: fx.ctx,
				unit_id: "validate_import",
				attempt: 2,
				operation_id: "sure.trans.validate_import",
				validator_id: "sure.sure_trans.main.validate_import",
				script_id: "check_artifact.py",
				artifact_path: fx.artifactPath,
				execute: (path) => {
					resolvedPath = path;
					return { ok: true, stdout: "valid", stderr: "", status: 0 };
				},
			});

			expect(result.ok).toBe(true);
			expect(result.verdict).toBe("PASS");
			expect(resolvedPath).toContain("sure/canonical/shared/trans-validator/scripts/check_artifact.py");
			expect(readFileSync(fx.artifactPath, "utf-8")).toBe(before);
			expect(result.evidence).toMatchObject({
				schema: "sure.validator.evidence.v1",
				source: "pi_hook",
				validators: [
					{
						validator_id: "sure.sure_trans.main.validate_import",
						backend_operation_id: "sure.trans.validate_import",
						verdict: "PASS",
						reason_code: "VALIDATION_PASSED",
						unit_id: "validate_import",
						attempt: 2,
					},
				],
			});
			expect(result.evidence?.validators[0]?.artifact_digest).toMatch(/^sha256:[0-9a-f]{64}$/);
		});

		it("maps a validator failure to FAIL", () => {
			const fx = validatorFixture("fail");
			const result = runPiRegisteredValidator({
				ctx: fx.ctx,
				unit_id: "validate_import",
				attempt: 1,
				operation_id: "sure.trans.validate_import",
				script_id: "check_artifact.py",
				artifact_path: fx.artifactPath,
				execute: () => ({ ok: false, stdout: "repair", stderr: "", status: 1 }),
			});

			expect(result.ok).toBe(false);
			expect(result.verdict).toBe("FAIL");
			expect(result.evidence?.validators[0]).toMatchObject({
				verdict: "FAIL",
				reason_code: "VALIDATION_FAILED",
				diagnostics: ["repair"],
			});
		});

		it("keeps missing validator capability as NOT_EXECUTED", () => {
			const fx = validatorFixture("missing-capability");
			let invoked = false;
			const result = runPiRegisteredValidator({
				ctx: fx.ctx,
				unit_id: "validate_import",
				attempt: 1,
				operation_id: "sure.trans.validate_import",
				script_id: "check_artifact.py",
				artifact_path: fx.artifactPath,
				execute: () => {
					invoked = true;
					return { ok: false, stdout: "", stderr: "runtime missing", status: null };
				},
			});

			expect(invoked).toBe(true);
			expect(result.ok).toBe(false);
			expect(result.verdict).toBe("NOT_EXECUTED");
			expect(result.evidence?.validators[0]).toMatchObject({
				verdict: "NOT_EXECUTED",
				reason_code: "CAPABILITY_MISSING",
				diagnostics: ["runtime missing"],
			});
		});

		it("rejects a validator that mutates or removes its input", () => {
			const fx = validatorFixture("mutates-input");
			const result = runPiRegisteredValidator({
				ctx: fx.ctx,
				unit_id: "validate_import",
				attempt: 1,
				operation_id: "sure.trans.validate_import",
				script_id: "check_artifact.py",
				artifact_path: fx.artifactPath,
				execute: () => {
					writeFileSync(fx.artifactPath, '{"status":"tampered"}\n', "utf-8");
					return { ok: true, stdout: "", stderr: "", status: 0 };
				},
			});

			expect(result.ok).toBe(false);
			expect(result.verdict).toBe("NOT_EXECUTED");
			expect(result.evidence?.validators[0]).toMatchObject({
				verdict: "NOT_EXECUTED",
				reason_code: "INVALID_CONTRACT",
			});
		});

		it("does not invoke a policy-bound validator without the run snapshot", () => {
			const fx = validatorFixture("policy-missing");
			let invoked = false;
			const result = runPiRegisteredValidator({
				ctx: fx.ctx,
				unit_id: "package_container",
				attempt: 1,
				operation_id: "sure.trans.validate_package_container",
				script_id: "check_artifact.py",
				artifact_path: fx.artifactPath,
				execute: () => {
					invoked = true;
					return { ok: true, stdout: "", stderr: "", status: 0 };
				},
			});

			expect(invoked).toBe(false);
			expect(result.verdict).toBe("NOT_EXECUTED");
			expect(result.evidence?.validators[0]).toMatchObject({
				verdict: "NOT_EXECUTED",
				reason_code: "CAPABILITY_MISSING",
			});
		});

		it("rejects tampered generated validator facades before execution", () => {
			const fx = validatorFixture("tampered-facade");
			writeFileSync(fx.entrypointPath, "# tampered\n", "utf-8");
			let invoked = false;
			const result = runPiRegisteredValidator({
				ctx: fx.ctx,
				unit_id: "validate_import",
				attempt: 1,
				operation_id: "sure.trans.validate_import",
				script_id: "check_artifact.py",
				artifact_path: fx.artifactPath,
				execute: () => {
					invoked = true;
					return { ok: true, stdout: "", stderr: "", status: 0 };
				},
			});

			expect(invoked).toBe(false);
			expect(result.verdict).toBe("NOT_EXECUTED");
			expect(result.evidence?.validators[0]).toMatchObject({
				verdict: "NOT_EXECUTED",
				reason_code: "INVALID_CONTRACT",
			});
		});
	});
});
