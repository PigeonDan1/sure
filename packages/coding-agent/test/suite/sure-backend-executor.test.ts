import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { CapabilityRequirement, ExecutionRequest } from "@earendil-works/sure-core";
import { describe, expect, it } from "vitest";
import {
	createHarnessRequestDispatcher,
	prepareHarnessBackendInvocation,
	runHarnessBackend,
} from "../../../../sure/runtime/harness/backend-executor.ts";

describe("shared SURE backend executor adapter", () => {
	it("prepares the legacy argv without mutating caller arguments", () => {
		const ctx = { packageDir: "/workspace/sure/generated/pi/skills/sure_infer", runDir: "/runs/r-1" };
		const args = ["--produces", "report.json", "--kind", "infer"];

		const invocation = prepareHarnessBackendInvocation(ctx, "run_infer.py", args);

		expect(invocation).toEqual({
			scriptPath: "/workspace/sure/generated/pi/skills/sure_infer/scripts/run_infer.py",
			args: ["--run-dir", "/runs/r-1", "--produces", "/runs/r-1/artifacts/report.json", "--kind", "infer"],
		});
		expect(args).toEqual(["--produces", "report.json", "--kind", "infer"]);
	});

	it("preserves an explicitly supplied run directory and absolute output path", () => {
		const invocation = prepareHarnessBackendInvocation(
			{ packageDir: "/package", runDir: "/active-run" },
			"check.py",
			["--run-dir", "/declared-run", "--produces", "/declared-run/artifacts/out.json"],
		);

		expect(invocation.args).toEqual(["--run-dir", "/declared-run", "--produces", "/declared-run/artifacts/out.json"]);
	});

	it("fails closed before runtime resolution when the bundled script is absent", () => {
		const result = runHarnessBackend({
			ctx: { packageDir: "/package-that-does-not-exist", runDir: "/run" },
			script: "missing.py",
			args: [],
		});

		expect(result).toEqual({
			ok: false,
			stdout: "",
			stderr: "Backend script not found: scripts/missing.py. Bundle the Python backend into the skill package.",
			status: null,
		});
	});

	it("keeps a present script under the package root before resolving the runtime", () => {
		const root = mkdtempSync(join(tmpdir(), "sure-backend-executor-"));
		mkdirSync(join(root, "scripts"), { recursive: true });
		writeFileSync(join(root, "scripts", "check.py"), "# test fixture\n", "utf8");

		const result = runHarnessBackend({
			ctx: { packageDir: root, runDir: join(root, "run") },
			script: "check.py",
			args: [],
		});

		// The fixture intentionally has no bootstrap runtime. It must report the
		// runtime readiness failure, not a path or fake successful execution.
		expect(result.ok).toBe(false);
		expect(result.status).toBe(null);
		expect(result.stderr).toMatch(/HARNESS_RUNTIME_NOT_READY|bootstrap is missing|bootstrap/);
	});

	it("binds an allowlisted execution request to the legacy backend adapter", () => {
		const runtime = {
			runtime_id: "runtime-test",
			python_executable: "/runtime/bin/python",
			python_abi: "cp312",
			python_version: "3.12.0",
			lock_sha256: "sha256:lock",
			harness_version: "test",
			manifest_path: "/runtime/manifest.json",
			runtime_root: "/runtime",
		};
		const request = {
			runtime_requirements: { semantic_backend_operation_id: "sure.test.execute" },
			entrypoint: {
				executable: runtime.python_executable,
				argv: [
					"/package/scripts/run_validate.py",
					"--run-dir",
					"/run",
					"--produces",
					"/run/artifacts/result.json",
					"--kind",
					"test",
				],
				working_directory: "/package",
			},
		} as unknown as ExecutionRequest;
		const calls: Array<{ script: string; args: readonly string[]; timeoutMs?: number }> = [];
		const legacyInvocation = prepareHarnessBackendInvocation(
			{ packageDir: "/package", runDir: "/run" },
			"run_validate.py",
			request.entrypoint.argv.slice(1),
		);
		const dispatcher = createHarnessRequestDispatcher({
			ctx: { packageDir: "/package", runDir: "/run" },
			allowedOperations: new Map([["sure.test.execute", "run_validate.py"]]),
			timeoutMs: 3_600_000,
			resolveRuntime: () => ({ ok: true, contract: runtime }),
			executeBackend: (options) => {
				calls.push({ script: options.script, args: options.args, timeoutMs: options.timeoutMs });
				return { ok: true, stdout: "ok", stderr: "", status: 0 };
			},
			now: () => "2026-01-01T00:00:00.000Z",
		});
		const requirements: CapabilityRequirement[] = [
			{
				capability_id: "sure.execution.harness-python",
				capability_class: "execution_capability",
				required: true,
			},
			{
				capability_id: "sure.execution.optional-test",
				capability_class: "execution_capability",
				required: false,
			},
		];

		const evidence = dispatcher.probe(request, requirements);
		expect(evidence.map((item) => item.status)).toEqual(["AVAILABLE", "UNKNOWN"]);
		expect(evidence[0]?.evidence_digest).toMatch(/^sha256:[0-9a-f]{64}$/);
		const result = dispatcher.execute(request);
		expect(result).toEqual({ ok: true, stdout: "ok", stderr: "", status: 0 });
		expect(calls).toEqual([
			{
				script: "run_validate.py",
				args: legacyInvocation.args,
				timeoutMs: 3_600_000,
			},
		]);
		expect(join("/package", "scripts", calls[0]?.script ?? "")).toBe(legacyInvocation.scriptPath);
	});

	it("reports a missing locked runtime without invoking the backend", () => {
		const request = {
			runtime_requirements: { semantic_backend_operation_id: "sure.test.execute" },
			entrypoint: { executable: "/runtime/bin/python", argv: ["/package/scripts/run.py"] },
		} as unknown as ExecutionRequest;
		let executed = false;
		const dispatcher = createHarnessRequestDispatcher({
			ctx: { packageDir: "/package", runDir: "/run" },
			allowedOperations: new Map([["sure.test.execute", "run.py"]]),
			resolveRuntime: () => ({ ok: false, error: "runtime unavailable" }),
			executeBackend: () => {
				executed = true;
				return { ok: true, stdout: "unexpected", stderr: "", status: 0 };
			},
		});
		const requirement: CapabilityRequirement = {
			capability_id: "sure.execution.harness-python",
			capability_class: "execution_capability",
			required: true,
		};

		expect(dispatcher.probe(request, [requirement])[0]).toMatchObject({ status: "MISSING", source: "host_probe" });
		expect(dispatcher.execute(request)).toMatchObject({ ok: false, status: null, stderr: "runtime unavailable" });
		expect(executed).toBe(false);
	});

	it("rejects a request that would require legacy argv mutation", () => {
		const request = {
			runtime_requirements: { semantic_backend_operation_id: "sure.test.execute" },
			entrypoint: {
				executable: "/runtime/bin/python",
				argv: ["/package/scripts/run.py"],
				working_directory: "/package",
			},
		} as unknown as ExecutionRequest;
		const dispatcher = createHarnessRequestDispatcher({
			ctx: { packageDir: "/package", runDir: "/run" },
			allowedOperations: new Map([["sure.test.execute", "run.py"]]),
			resolveRuntime: () => ({
				ok: true,
				contract: {
					runtime_id: "runtime-test",
					python_executable: "/runtime/bin/python",
					python_abi: "cp312",
					python_version: "3.12.0",
					lock_sha256: "sha256:lock",
					harness_version: "test",
					manifest_path: "/runtime/manifest.json",
					runtime_root: "/runtime",
				},
			}),
		});
		const requirement: CapabilityRequirement = {
			capability_id: "sure.execution.harness-python",
			capability_class: "execution_capability",
			required: true,
		};

		expect(() => dispatcher.probe(request, [requirement])).toThrow(/run directory/);
	});
});
