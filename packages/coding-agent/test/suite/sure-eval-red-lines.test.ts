import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

// sure_eval skill package root (repo-relative from the test file).
const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_eval");
const SCRIPTS_DIR = join(PACKAGE_DIR, "scripts");
// The only entrypoint an execution surface may name; run_infer.py writes the
// surface from it and check_execution_surface_compliance.py checks path + digest.
const ENTRYPOINT = join(SCRIPTS_DIR, "infer_entrypoint.py");

function sha256Of(path: string): string {
	return createHash("sha256").update(readFileSync(path)).digest("hex");
}

const PYTHON_BIN = (() => {
	const r = spawnSync("python3", ["-c", "import sys; print(sys.executable)"], { encoding: "utf-8", timeout: 10_000 });
	return r.status === 0 ? r.stdout.trim() : process.execPath;
})();

function freshRunDir(name: string): string {
	const dir = resolve(__dirname, "tmp-rl", name);
	mkdirSync(join(dir, "artifacts"), { recursive: true });
	return dir;
}

function writeArtifact(runDir: string, produces: string, value: unknown): void {
	writeFileSync(join(runDir, "artifacts", produces), JSON.stringify(value, null, 2), "utf-8");
}

function writeEvalInput(runDir: string): void {
	const dataset = "aishell1__v1.0.2__asr";
	writeArtifact(runDir, "eval_input_resolved.json", {
		schema: "sure.eval.input_resolved.v1",
		generated_at: "2026-01-01T00:00:00Z",
		user_input: { model: "demo", datasets: [dataset], device: "cpu", metrics: ["cer"], execution: "local" },
		model: {},
		datasets: [
			{
				name: dataset,
				jsonl_path: resolve(
					__dirname,
					"../../../../data/datasets/sure_benchmark/jsonl/aishell1__v1.0.2__asr.jsonl",
				),
				jsonl_exists: true,
				task: "ASR",
				language: "zh",
				default_metrics: ["cer"],
				display_name: dataset,
			},
		],
		task_summary: { evaluation_tasks: ["ASR"], languages: ["zh"], metrics: ["cer"] },
		runtime: {
			run_dir: runDir,
			device: { request: "cpu", resolved: "cpu" },
			execution: { requested: "local", planned: "local", path_planned: "local_docker" },
		},
		evaluation: { backend: "external" },
	});
}

function runGate(script: string, runDir: string, produces: string): { ok: boolean; stderr: string } {
	const r = spawnSync(
		PYTHON_BIN,
		[join(SCRIPTS_DIR, script), "--run-dir", runDir, "--produces", join(runDir, "artifacts", produces)],
		{ cwd: PACKAGE_DIR, encoding: "utf-8", timeout: 30_000 },
	);
	return { ok: r.status === 0, stderr: r.stderr ?? "" };
}

// The surface run_infer.py writes for the bundled entrypoint, reduced to the
// fields check_entrypoint_provenance reads.
function surfaceFor(entrypoint: string, sha256: string): Record<string, unknown> {
	return {
		entrypoint_path: entrypoint,
		execution: { requested: "local", path_planned: "local_docker" },
		source_provenance: {
			template_file: entrypoint,
			template_sha256: sha256,
			isolation_compliance: { eval_runs_referenced: false, prior_run_scripts_copied: false },
		},
	};
}

const cleanups: Array<() => void> = [];
afterEach(() => {
	while (cleanups.length) {
		const clean = cleanups.pop();
		if (clean) clean();
	}
});

describe("sure_eval red line 1 — EXECUTION_SURFACE_ISOLATION", () => {
	// The full gate also runs the inference_runtime check, which needs an approved
	// binding and a live container probe no unit test has; so the pass case calls
	// the provenance check directly, the way the old template check was exercised,
	// and the reject cases go through the gate CLI (their provenance evidence
	// reaches stderr whatever the runtime check says).
	it("passes when the surface was written for the bundled infer_entrypoint.py", () => {
		const runDir = freshRunDir("iso-pass");
		expect(existsSync(ENTRYPOINT)).toBe(true);
		writeArtifact(runDir, "execution_surface.json", surfaceFor(ENTRYPOINT, sha256Of(ENTRYPOINT)));
		writeEvalInput(runDir);
		const surfacePath = join(runDir, "artifacts", "execution_surface.json");
		const result = spawnSync(PYTHON_BIN, ["-"], {
			cwd: SCRIPTS_DIR,
			input: `
from pathlib import Path
from check_execution_surface_compliance import check_entrypoint_provenance
result = check_entrypoint_provenance(Path(${JSON.stringify(surfacePath)}))
assert result["passed"], result
assert result["entrypoint_sha256"] == ${JSON.stringify(sha256Of(ENTRYPOINT))}, result
`,
			encoding: "utf-8",
			env: { ...process.env, PYTHONPATH: SCRIPTS_DIR },
		});
		expect(result.status).toBe(0);
		expect(result.stderr).toBe("");
	});

	it("blocks when the surface points at another script", () => {
		const runDir = freshRunDir("iso-fail-other-script");
		const other = join(runDir, "artifacts", "run_evaluation.py");
		writeFileSync(other, "print('not the bundled entrypoint')\n", "utf-8");
		writeArtifact(runDir, "execution_surface.json", surfaceFor(other, sha256Of(other)));
		const r = runGate("check_execution_surface_compliance.py", runDir, "execution_surface.json");
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("must be the bundled entrypoint");
	});

	it("blocks when template_sha256 is stale", () => {
		const runDir = freshRunDir("iso-fail-stale-sha");
		writeArtifact(runDir, "execution_surface.json", surfaceFor(ENTRYPOINT, "0".repeat(64)));
		const r = runGate("check_execution_surface_compliance.py", runDir, "execution_surface.json");
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("template_sha256 is stale");
	});

	it("blocks when source_provenance.template_file is missing", () => {
		const runDir = freshRunDir("iso-fail-noprovenance");
		const surface = surfaceFor(ENTRYPOINT, sha256Of(ENTRYPOINT));
		delete (surface.source_provenance as Record<string, unknown>).template_file;
		writeArtifact(runDir, "execution_surface.json", surface);
		const r = runGate("check_execution_surface_compliance.py", runDir, "execution_surface.json");
		expect(r.ok).toBe(false);
		expect(r.stderr).toMatch(/source_provenance\.template_file/);
	});
});
