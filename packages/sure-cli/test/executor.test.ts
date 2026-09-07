import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { ExecutionRequest, JsonValue } from "../../sure-core/src/index.ts";
import { projectExecutionEvidence } from "../../sure-core/src/index.ts";
import { executeRequest } from "../src/executor.ts";

const A = `sha256:${"a".repeat(64)}`;
const B = `sha256:${"b".repeat(64)}`;
const roots: string[] = [];

interface DifferentialCase {
	id: string;
	surectl: {
		evidence_verdict: "PASS" | "FAIL" | "NOT_EXECUTED";
		evidence_reason_code: string;
		receipt_valid: boolean;
		capability_admitted: boolean;
	};
}

const DIFFERENTIAL_FIXTURE = JSON.parse(
	readFileSync(
		new URL("../../../sure/canonical/fixtures/execution-differential-traces.json", import.meta.url),
		"utf8",
	),
) as { schema: string; cases: DifferentialCase[] };

function request(
	root: string,
	capabilityId: string,
	runtimeRequirements: Record<string, JsonValue> = {},
	overrides: Partial<ExecutionRequest> = {},
): ExecutionRequest {
	const artifacts = join(root, "artifacts");
	return {
		schema: "sure.execution_request.v1",
		request_id: "request-probe",
		semantic_request_digest: A,
		run_id: "run-probe",
		unit_id: "execute",
		attempt: 1,
		operation: "validation",
		subject: {
			bundle_manifest_path: join(artifacts, "bundle.json"),
			bundle_digest: A,
			runtime_identity_digest: B,
		},
		inputs: [],
		entrypoint: { executable: process.execPath, argv: ["-e", "process.exit(0)"] },
		runtime_requirements: runtimeRequirements,
		capability_requirements: [
			{ capability_id: capabilityId, capability_class: "execution_capability", required: true },
		],
		reference_snapshot_digest: B,
		output_root: {
			path: artifacts,
			resolved_path: artifacts,
			scope_id: "run-probe",
			policy_digest: A,
			writable: true,
		},
		policy_digest: A,
		created_at: "2026-09-06T00:00:00.000Z",
		...overrides,
	} as unknown as ExecutionRequest;
}

function freshRoot(): string {
	const root = mkdtempSync(join(tmpdir(), "sure-executor-test-"));
	roots.push(root);
	return root;
}

function outputContract() {
	return {
		schema: "sure.execution_output_contract.v1" as const,
		mode: "producing" as const,
		outputs: [
			{ artifact_id: "manifest", path: "manifest.json", kind: "file" as const, required: true },
			{ artifact_id: "bundle", path: "bundle", kind: "directory" as const, required: false },
		],
		temporary_paths: [".staging"],
		allow_missing_on_failure: true,
		retain_failed_outputs: true,
	};
}

function fakeDocker(root: string): string {
	const executable = join(root, "fake-docker");
	writeFileSync(
		executable,
		`#!${process.execPath}
const fs = require("node:fs");
const path = require("node:path");
const args = process.argv.slice(2);
if (args.length === 1 && args[0] === "--version") process.exit(0);
if (args[0] === "image" && args[1] === "inspect") {
  const image = args.at(-1) ?? "";
  const digest = image.split("@sha256:")[1] ?? "";
  process.stdout.write(JSON.stringify(digest ? ["registry.example/sure/test@sha256:" + digest] : []));
  process.exit(digest ? 0 : 1);
}
fs.writeFileSync(process.env.SURE_FAKE_DOCKER_ARGS, JSON.stringify(args));
const envIndex = args.indexOf("--env");
const outputInContainer = envIndex >= 0 ? args[envIndex + 1].split("=").slice(1).join("=") : "";
const mountArgs = args.reduce((mounts, value, index) => {
  if (value !== "--mount") return mounts;
  const spec = args[index + 1] ?? "";
  const fields = Object.fromEntries(spec.split(",").map((field) => field.split("=")));
  if (fields.src && fields.dst) mounts.push({ source: fields.src, target: fields.dst });
  return mounts;
}, []);
const mount = mountArgs.find((candidate) => outputInContainer === candidate.target || outputInContainer.startsWith(candidate.target + "/"));
const output = mount ? mount.source + outputInContainer.slice(mount.target.length) : outputInContainer;
if (output) {
  fs.writeFileSync(output, "docker-output");
  fs.mkdirSync(path.join(path.dirname(output), "bundle"), { recursive: true });
  fs.writeFileSync(path.join(path.dirname(output), "bundle", "part.bin"), "part");
}
process.exit(output ? 0 : 17);
`,
	);
	chmodSync(executable, 0o755);
	return executable;
}

afterEach(() => {
	for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe("cooperative executor capability probes", () => {
	it("executes Docker requests through docker run and binds host outputs", () => {
		const root = freshRoot();
		const artifacts = join(root, "artifacts");
		mkdirSync(artifacts);
		const argsPath = join(root, "docker-argv.json");
		const outputPath = join(artifacts, "manifest.json");
		const dockerExecutable = fakeDocker(root);
		const result = executeRequest(
			request(
				root,
				"sure.execution.docker",
				{
					executor_kind: "docker",
					docker_executable: dockerExecutable,
					docker_image: `registry.example/sure/test@sha256:${"c".repeat(64)}`,
					docker_mounts: [{ source: artifacts, target: "/work", read_only: false }],
					docker_workdir: "/work",
					docker_env: { SURE_OUTPUT: "/work/manifest.json" },
				},
				{
					entrypoint: { executable: "/not-run-on-host", argv: ["--inside-image"] },
					output_contract: outputContract(),
				},
			),
			{
				kind: "docker",
				executor_digest: A,
				executor_version: "test",
				working_directory: root,
				allowed_output_roots: [artifacts],
				forbidden_output_roots: [],
				timeout_ms: 1000,
				environment: { SURE_FAKE_DOCKER_ARGS: argsPath },
			},
		);

		expect(result.capability.admitted).toBe(true);
		expect(result.receipt?.lifecycle).toBe("SUCCEEDED");
		expect(result.receipt?.executor.kind).toBe("docker");
		expect(result.receipt_validation?.valid).toBe(true);
		expect(result.admission_trace).toMatchObject({
			status: "ADMITTED",
			probe_invoked: true,
			execute_invoked: true,
			receipt_present: true,
			receipt_valid: true,
		});
		expect(readFileSync(outputPath, "utf8")).toBe("docker-output");
		const argv = JSON.parse(readFileSync(argsPath, "utf8")) as string[];
		expect(argv).toEqual(
			expect.arrayContaining([
				"run",
				"--rm",
				"--workdir",
				"/work",
				"--entrypoint",
				"/not-run-on-host",
				`registry.example/sure/test@sha256:${"c".repeat(64)}`,
				"--inside-image",
			]),
		);
		expect(argv.join(" ")).toContain(`type=bind,src=${artifacts},dst=/work`);
	});

	it("rejects Docker execution before capability probing when image metadata is absent", () => {
		const root = freshRoot();
		const result = executeRequest(
			request(root, "sure.execution.docker", {
				executor_kind: "docker",
				docker_executable: join(root, "missing-docker"),
			}),
			{
				kind: "docker",
				executor_digest: A,
				executor_version: "test",
				working_directory: root,
				allowed_output_roots: [join(root, "artifacts")],
				forbidden_output_roots: [],
				timeout_ms: 1000,
			},
		);

		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		expect(result.receipt).toBeUndefined();
		expect(result.request_validation.valid).toBe(false);
		expect(result.admission_trace).toMatchObject({
			status: "REJECTED",
			probe_invoked: false,
			execute_invoked: false,
			receipt_present: false,
			receipt_valid: false,
		});
		expect(result.request_validation.errors).toEqual(
			expect.arrayContaining([
				"runtime_requirements.docker_image must be a non-empty string",
				"runtime_requirements.docker_mounts must be an array",
			]),
		);
	});

	it("rejects a writable mount that overlaps a forbidden reference root", () => {
		const root = freshRoot();
		const reference = mkdtempSync(join(tmpdir(), "sure-docker-reference-"));
		roots.push(reference);
		const result = executeRequest(
			request(root, "sure.execution.docker", {
				executor_kind: "docker",
				docker_image: `registry.example/sure/test@sha256:${"d".repeat(64)}`,
				docker_mounts: [{ source: reference, target: "/reference", read_only: false }],
			}),
			{
				kind: "docker",
				executor_digest: A,
				executor_version: "test",
				working_directory: root,
				allowed_output_roots: [join(root, "artifacts")],
				forbidden_output_roots: [reference],
				timeout_ms: 1000,
			},
		);

		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		expect(result.receipt?.lifecycle).toBe("NOT_STARTED");
	});

	it("rejects a writable input mount even when no explicit forbidden root is supplied", () => {
		const root = freshRoot();
		const inputRoot = join(root, "input");
		mkdirSync(inputRoot);
		const inputPath = join(inputRoot, "input.bin");
		writeFileSync(inputPath, "input");
		const result = executeRequest(
			request(
				root,
				"sure.execution.docker",
				{
					executor_kind: "docker",
					docker_image: `registry.example/sure/test@sha256:${"f".repeat(64)}`,
					docker_mounts: [{ source: inputRoot, target: "/input", read_only: false }],
				},
				{
					inputs: [
						{
							artifact_id: "input",
							path: inputPath,
							resolved_path: inputPath,
							sha256: A,
							size: 5,
							media_type: "application/octet-stream",
							origin: "local_staging",
							source_root: inputRoot,
						},
					],
				},
			),
			{
				kind: "docker",
				executor_digest: A,
				executor_version: "test",
				working_directory: root,
				allowed_output_roots: [join(root, "artifacts")],
				forbidden_output_roots: [],
				timeout_ms: 1000,
			},
		);

		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		expect(result.receipt?.lifecycle).toBe("NOT_STARTED");
	});

	it("probes Docker itself instead of trusting the business entrypoint", () => {
		const root = freshRoot();
		const missingDocker = join(root, "missing-docker");
		const result = executeRequest(
			request(root, "sure.execution.docker", {
				executor_kind: "docker",
				docker_executable: missingDocker,
				docker_image: `registry.example/sure/test@sha256:${"e".repeat(64)}`,
				docker_mounts: [],
			}),
			{
				kind: "docker",
				executor_digest: A,
				executor_version: "test",
				working_directory: root,
				allowed_output_roots: [root],
				forbidden_output_roots: [],
				timeout_ms: 1000,
			},
		);

		expect(result.capability.missing).toContain("sure.execution.docker");
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "CAPABILITY_MISSING" });
		expect(result.receipt?.lifecycle).toBe("NOT_STARTED");
		expect(result.admission_trace).toMatchObject({ status: "CAPABILITY_MISSING", execute_invoked: false });
	});

	it("reports a missing static host capability before launching the operation", () => {
		const root = freshRoot();
		const missingUv = join(root, "missing-uv");
		const sentinel = join(root, "started");
		const result = executeRequest(
			request(
				root,
				"sure.execution.uv",
				{ uv_executable: missingUv },
				{
					entrypoint: {
						executable: process.execPath,
						argv: ["-e", `require('node:fs').writeFileSync(${JSON.stringify(sentinel)}, 'started')`],
					},
				},
			),
			{
				kind: "python",
				executor_digest: A,
				executor_version: "test",
				working_directory: root,
				allowed_output_roots: [root],
				forbidden_output_roots: [],
				timeout_ms: 1000,
			},
		);

		expect(existsSync(sentinel)).toBe(false);
		expect(result.capability.missing).toEqual(["sure.execution.uv"]);
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "CAPABILITY_MISSING" });
		expect(result.receipt?.lifecycle).toBe("NOT_STARTED");
	});

	it("does not infer model-runtime availability from a Node entrypoint", () => {
		const root = freshRoot();
		const result = executeRequest(request(root, "sure.execution.model-runtime"), {
			kind: "local",
			executor_digest: A,
			executor_version: "test",
			working_directory: root,
			allowed_output_roots: [root],
			forbidden_output_roots: [],
			timeout_ms: 1000,
		});

		expect(result.capability.missing).toContain("sure.execution.model-runtime");
		expect(result.receipt?.lifecycle).toBe("NOT_STARTED");
	});

	it("does not launch a local entrypoint for an uninstalled remote or trusted adapter", () => {
		for (const kind of ["remote", "trusted"] as const) {
			const root = freshRoot();
			const sentinel = join(root, `${kind}-started`);
			const input = request(
				root,
				`sure.execution.${kind}`,
				{},
				{
					entrypoint: {
						executable: process.execPath,
						argv: ["-e", `require('node:fs').writeFileSync(${JSON.stringify(sentinel)}, 'started')`],
					},
				},
			);
			const result = executeRequest(input, {
				kind,
				executor_digest: A,
				executor_version: "test",
				working_directory: root,
				allowed_output_roots: [root],
				forbidden_output_roots: [],
				timeout_ms: 1000,
			});

			expect(existsSync(sentinel)).toBe(false);
			expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "CAPABILITY_MISSING" });
			expect(result.receipt?.lifecycle).toBe("NOT_STARTED");
			expect(result.admission_trace).toMatchObject({
				status: "CAPABILITY_MISSING",
				probe_invoked: false,
				execute_invoked: false,
			});
			expect(result.capability.missing).toContain(`sure.execution.${kind}`);
		}
	});

	it("accepts an explicitly declared Python interpreter probe", () => {
		const root = freshRoot();
		const result = executeRequest(
			request(root, "sure.execution.harness-python", {
				harness_python_executable: process.execPath,
			}),
			{
				kind: "python",
				executor_digest: A,
				executor_version: "test",
				working_directory: root,
				allowed_output_roots: [root],
				forbidden_output_roots: [],
				timeout_ms: 1000,
			},
		);

		expect(result.capability.admitted).toBe(true);
		expect(result.capability.missing).toEqual([]);
		expect(result.receipt?.lifecycle).toBe("SUCCEEDED");
		expect(existsSync(join(root, "artifacts"))).toBe(false);
	});

	it("uses the same locked environment for capability probes and execution", () => {
		const root = freshRoot();
		const input = request(
			root,
			"sure.execution.harness-python",
			{ harness_python_executable: process.execPath },
			{
				entrypoint: {
					executable: process.execPath,
					argv: [
						"-e",
						"process.exit(process.env.SURE_EXECUTOR_TEST === 'locked' && process.env.SURE_EXECUTOR_LEAK === undefined ? 0 : 9)",
					],
				},
			},
		);
		const result = executeRequest(input, {
			kind: "python",
			executor_digest: A,
			executor_version: "test",
			working_directory: root,
			allowed_output_roots: [root],
			forbidden_output_roots: [],
			timeout_ms: 1000,
			environment: { SURE_EXECUTOR_TEST: "locked" },
		});

		expect(result.capability.admitted).toBe(true);
		expect(result.receipt?.lifecycle).toBe("SUCCEEDED");
	});

	it("does not admit an output replaced with a symlink after execution", () => {
		const root = freshRoot();
		const outside = join(root, "outside.txt");
		writeFileSync(outside, "reference\n");
		const output = join(root, "generated.txt");
		const requestValue = request(
			root,
			"sure.execution.local-python",
			{ python_executable: process.execPath },
			{
				output_root: {
					path: root,
					resolved_path: root,
					scope_id: "run-probe",
					policy_digest: A,
					writable: true,
				},
				entrypoint: {
					executable: process.execPath,
					argv: ["-e", `require('node:fs').symlinkSync(${JSON.stringify(outside)}, ${JSON.stringify(output)})`],
				},
			},
		);
		const result = executeRequest(requestValue, {
			kind: "local",
			executor_digest: A,
			executor_version: "test",
			working_directory: root,
			allowed_output_roots: [root],
			forbidden_output_roots: [],
			timeout_ms: 1000,
			output_paths: [output],
		});

		expect(result.receipt?.outputs).toEqual([]);
		expect(result.receipt?.lifecycle).toBe("PARTIAL");
		expect(result.receipt?.diagnostics).toEqual(
			expect.arrayContaining([expect.objectContaining({ code: "OUTPUT_REJECTED" })]),
		);
		expect(result.outcome.outcome).not.toBe("PASS");
	});

	it("collects and validates declared file and directory outputs", () => {
		const root = freshRoot();
		const artifacts = join(root, "artifacts");
		const contract = outputContract();
		const result = executeRequest(
			request(
				root,
				"sure.execution.local-python",
				{ python_executable: process.execPath },
				{
					output_contract: contract,
					entrypoint: {
						executable: process.execPath,
						argv: [
							"-e",
							`const fs=require('node:fs');fs.mkdirSync(${JSON.stringify(join(artifacts, "bundle"))},{recursive:true});fs.writeFileSync(${JSON.stringify(join(artifacts, "manifest.json"))},'ok');fs.writeFileSync(${JSON.stringify(join(artifacts, "bundle", "part.bin"))},'part');`,
						],
					},
				},
			),
			{
				kind: "local",
				executor_digest: A,
				executor_version: "test",
				working_directory: root,
				allowed_output_roots: [root],
				forbidden_output_roots: [],
				timeout_ms: 1000,
			},
		);

		expect(result.receipt?.lifecycle).toBe("SUCCEEDED");
		expect(result.receipt?.outputs).toEqual(
			expect.arrayContaining([
				expect.objectContaining({ artifact_id: "manifest" }),
				expect.objectContaining({ artifact_id: "bundle", kind: "directory", digest_kind: "tree_sha256" }),
			]),
		);
		expect(result.receipt?.outputs?.find((output) => output.artifact_id === "manifest")).not.toHaveProperty("kind");
		expect(result.receipt?.outputs?.find((output) => output.artifact_id === "manifest")).not.toHaveProperty(
			"digest_kind",
		);
		expect(result.receipt_validation?.valid).toBe(true);
		expect(result.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "VALIDATION_PENDING" });
	});

	it("turns a missing required producer output into a non-pass partial execution", () => {
		const root = freshRoot();
		const result = executeRequest(
			request(
				root,
				"sure.execution.local-python",
				{ python_executable: process.execPath },
				{
					output_contract: outputContract(),
				},
			),
			{
				kind: "local",
				executor_digest: A,
				executor_version: "test",
				working_directory: root,
				allowed_output_roots: [root],
				forbidden_output_roots: [],
				timeout_ms: 1000,
			},
		);

		expect(result.receipt?.lifecycle).toBe("PARTIAL");
		expect(result.receipt_validation?.valid).toBe(true);
		expect(result.outcome.outcome).not.toBe("PASS");
	});

	it("fails closed when a declared temporary path is replaced by a symlink", () => {
		const root = freshRoot();
		const artifacts = join(root, "artifacts");
		const outside = join(root, "outside");
		const result = executeRequest(
			request(
				root,
				"sure.execution.local-python",
				{ python_executable: process.execPath },
				{
					output_contract: outputContract(),
					entrypoint: {
						executable: process.execPath,
						argv: [
							"-e",
							`const fs=require('node:fs');fs.mkdirSync(${JSON.stringify(artifacts)},{recursive:true});fs.writeFileSync(${JSON.stringify(join(artifacts, "manifest.json"))},'ok');fs.mkdirSync(${JSON.stringify(outside)});fs.symlinkSync(${JSON.stringify(outside)},${JSON.stringify(join(artifacts, ".staging"))});`,
						],
					},
				},
			),
			{
				kind: "local",
				executor_digest: A,
				executor_version: "test",
				working_directory: root,
				allowed_output_roots: [root],
				forbidden_output_roots: [],
				timeout_ms: 1000,
			},
		);

		expect(result.receipt?.lifecycle).toBe("PARTIAL");
		expect(result.receipt?.diagnostics).toEqual(
			expect.arrayContaining([expect.objectContaining({ code: "OUTPUT_RESIDUAL_REJECTED" })]),
		);
		expect(result.outcome.outcome).not.toBe("PASS");
	});

	it("projects Docker and local execution traces through the canonical evidence vocabulary", () => {
		expect(DIFFERENTIAL_FIXTURE.schema).toBe("sure.execution.differential_traces.v1");
		const expected = new Map(
			DIFFERENTIAL_FIXTURE.cases.map((item) => [
				item.id,
				{ verdict: item.surectl.evidence_verdict, reason_code: item.surectl.evidence_reason_code },
			]),
		);

		const invalidRoot = freshRoot();
		const invalid = executeRequest(
			request(invalidRoot, "sure.execution.docker", {
				executor_kind: "docker",
				docker_image: `registry.example/sure/test@sha256:${"1".repeat(64)}`,
				docker_mounts: [{ source: join(invalidRoot, "missing"), target: "/work", read_only: false }],
			}),
			{
				kind: "docker",
				executor_digest: A,
				executor_version: "test",
				working_directory: invalidRoot,
				allowed_output_roots: [join(invalidRoot, "artifacts")],
				forbidden_output_roots: [],
				timeout_ms: 1000,
			},
		);
		const invalidProjection = projectExecutionEvidence({
			lifecycle: invalid.receipt?.lifecycle,
			receipt_valid: invalid.receipt !== undefined && invalid.receipt_validation?.valid === true,
			capability_admitted: invalid.capability.admitted,
			outcome_reason_code: invalid.outcome.reason_code,
		});
		expect(invalid.receipt?.lifecycle).toBe("NOT_STARTED");
		expect(invalid.receipt_validation?.valid).toBe(false);
		expect(invalid.capability.admitted).toBe(false);
		expect(invalidProjection).toEqual(expected.get("invalid_contract"));

		const capabilityRoot = freshRoot();
		const capability = executeRequest(
			request(capabilityRoot, "sure.execution.docker", {
				executor_kind: "docker",
				docker_executable: join(capabilityRoot, "missing-docker"),
				docker_image: `registry.example/sure/test@sha256:${"2".repeat(64)}`,
				docker_mounts: [],
			}),
			{
				kind: "docker",
				executor_digest: A,
				executor_version: "test",
				working_directory: capabilityRoot,
				allowed_output_roots: [capabilityRoot],
				forbidden_output_roots: [],
				timeout_ms: 1000,
			},
		);
		const capabilityProjection = projectExecutionEvidence({
			lifecycle: capability.receipt?.lifecycle,
			receipt_valid: capability.receipt !== undefined && capability.receipt_validation?.valid === true,
			capability_admitted: capability.capability.admitted,
			outcome_reason_code: capability.outcome.reason_code,
		});
		expect(capability.receipt?.lifecycle).toBe("NOT_STARTED");
		expect(capability.receipt_validation?.valid).toBe(false);
		expect(capability.capability.admitted).toBe(false);
		expect(capabilityProjection).toEqual(expected.get("capability_missing"));

		const failedRoot = freshRoot();
		const failed = executeRequest(
			request(
				failedRoot,
				"sure.execution.local-python",
				{ python_executable: process.execPath },
				{ entrypoint: { executable: process.execPath, argv: ["-e", "process.exit(7)"] } },
			),
			{
				kind: "local",
				executor_digest: A,
				executor_version: "test",
				working_directory: failedRoot,
				allowed_output_roots: [failedRoot],
				forbidden_output_roots: [],
				timeout_ms: 1000,
			},
		);
		const failedProjection = projectExecutionEvidence({
			lifecycle: failed.receipt?.lifecycle,
			receipt_valid: failed.receipt !== undefined && failed.receipt_validation?.valid === true,
			capability_admitted: failed.capability.admitted,
			outcome_reason_code: failed.outcome.reason_code,
		});
		expect(failed.receipt?.lifecycle).toBe("FAILED");
		expect(failed.receipt_validation?.valid).toBe(true);
		expect(failed.capability.admitted).toBe(true);
		expect(failedProjection).toEqual(expected.get("executor_failed"));

		const partialRoot = freshRoot();
		const partial = executeRequest(
			request(
				partialRoot,
				"sure.execution.local-python",
				{ python_executable: process.execPath },
				{ output_contract: outputContract() },
			),
			{
				kind: "local",
				executor_digest: A,
				executor_version: "test",
				working_directory: partialRoot,
				allowed_output_roots: [partialRoot],
				forbidden_output_roots: [],
				timeout_ms: 1000,
			},
		);
		const partialProjection = projectExecutionEvidence({
			lifecycle: partial.receipt?.lifecycle,
			receipt_valid: partial.receipt !== undefined && partial.receipt_validation?.valid === true,
			capability_admitted: partial.capability.admitted,
			outcome_reason_code: partial.outcome.reason_code,
		});
		expect(partial.receipt?.lifecycle).toBe("PARTIAL");
		expect(partial.receipt_validation?.valid).toBe(true);
		expect(partial.capability.admitted).toBe(true);
		expect(partialProjection).toEqual(expected.get("partial_output"));

		const successRoot = freshRoot();
		const success = executeRequest(
			request(successRoot, "sure.execution.local-python", { python_executable: process.execPath }),
			{
				kind: "local",
				executor_digest: A,
				executor_version: "test",
				working_directory: successRoot,
				allowed_output_roots: [successRoot],
				forbidden_output_roots: [],
				timeout_ms: 1000,
			},
		);
		const successProjection = projectExecutionEvidence({
			lifecycle: success.receipt?.lifecycle,
			receipt_valid: success.receipt !== undefined && success.receipt_validation?.valid === true,
			capability_admitted: success.capability.admitted,
			outcome_reason_code: success.outcome.reason_code,
		});
		expect(success.receipt?.lifecycle).toBe("SUCCEEDED");
		expect(success.receipt_validation?.valid).toBe(true);
		expect(success.capability.admitted).toBe(true);
		expect(success.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "VALIDATION_PENDING" });
		expect(successProjection).toEqual(expected.get("execution_succeeded_validation_pending"));
	});
});
