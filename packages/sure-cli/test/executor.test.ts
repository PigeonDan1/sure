import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { ExecutionRequest, JsonValue } from "../../sure-core/src/index.ts";
import { executeRequest } from "../src/executor.ts";

const A = `sha256:${"a".repeat(64)}`;
const B = `sha256:${"b".repeat(64)}`;
const roots: string[] = [];

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

afterEach(() => {
	for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe("cooperative executor capability probes", () => {
	it("probes Docker itself instead of trusting the business entrypoint", () => {
		const root = freshRoot();
		const missingDocker = join(root, "missing-docker");
		const result = executeRequest(request(root, "sure.execution.docker", { docker_executable: missingDocker }), {
			kind: "docker",
			executor_digest: A,
			executor_version: "test",
			working_directory: root,
			allowed_output_roots: [root],
			forbidden_output_roots: [],
			timeout_ms: 1000,
		});

		expect(result.capability.missing).toContain("sure.execution.docker");
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
});
