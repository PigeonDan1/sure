import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
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
});
