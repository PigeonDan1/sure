import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

// sure_eval skill package root (repo-relative from the test file).
const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_eval");
const SCRIPTS_DIR = join(PACKAGE_DIR, "scripts");
const TEMPLATES_DIR = join(SCRIPTS_DIR, "templates");

// Whether vc is installed in this environment. execution=auto prefers vc when
// this probe succeeds; execution=local remains explicitly legal.
const VC_AVAILABLE = (() => {
	const r = spawnSync("vc", ["info"], { encoding: "utf-8", timeout: 10_000 });
	return r.status === 0;
})();

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
				jsonl_path: resolve(__dirname, "../../../../data/datasets/sure_benchmark/jsonl/aishell1__v1.0.2__asr.jsonl"),
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
			execution: { requested: "local", planned: "local", path_planned: "local_bash" },
		},
		evaluation: { backend: "external", strict_main_flow: true },
		main_flow_input: {},
	});
}

function runGate(script: string, runDir: string, produces: string): { ok: boolean; stderr: string } {
	const r = spawnSync(
		"python3",
		[join(SCRIPTS_DIR, script), "--run-dir", runDir, "--produces", join(runDir, "artifacts", produces)],
		{ cwd: PACKAGE_DIR, encoding: "utf-8", timeout: 30_000 },
	);
	return { ok: r.status === 0, stderr: r.stderr ?? "" };
}

const cleanups: Array<() => void> = [];
afterEach(() => {
	while (cleanups.length) {
		const clean = cleanups.pop();
		if (clean) clean();
	}
});

describe("sure_eval red line 1 — EXECUTION_SURFACE_ISOLATION", () => {
	it("passes when the surface references a bundled scripts/templates/ template", () => {
		const runDir = freshRunDir("iso-pass");
		const templateFile = join(TEMPLATES_DIR, "run_single_model_single_dataset.sh");
		expect(existsSync(templateFile)).toBe(true);
		writeArtifact(runDir, "execution_surface.json", {
			entrypoint: "run_evaluation.sh",
			execution: { requested: "local", path_planned: "local_bash" },
			env: { MODEL_PYTHON: PYTHON_BIN },
			inference_runtime: { required_imports: [] },
			source_provenance: {
				template_file: templateFile,
				isolation_compliance: {
					eval_runs_referenced: false,
					prior_run_scripts_copied: false,
				},
			},
		});
		writeEvalInput(runDir);
		writeFileSync(
			join(runDir, "artifacts", "run_evaluation.sh"),
			"python evaluate_predictions.py --results-dir x --protocol-id y --model-dir z --evaluation-backend external || EVAL_EXIT=$?\n",
			"utf-8",
		);
		const r = runGate("check_execution_surface_compliance.py", runDir, "execution_surface.json");
		expect(r.ok).toBe(true);
	});

	it("blocks when the surface references a template OUTSIDE scripts/templates/", () => {
		const runDir = freshRunDir("iso-fail-external");
		writeArtifact(runDir, "execution_surface.json", {
			entrypoint: "run_evaluation.sh",
			source_provenance: { template_file: "/tmp/evil_template.sh" },
		});
		const r = runGate("check_execution_surface_compliance.py", runDir, "execution_surface.json");
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("approved template root");
	});

	it("blocks when source_provenance.template_file is missing", () => {
		const runDir = freshRunDir("iso-fail-noprovenance");
		writeArtifact(runDir, "execution_surface.json", { entrypoint: "run_evaluation.sh" });
		const r = runGate("check_execution_surface_compliance.py", runDir, "execution_surface.json");
		expect(r.ok).toBe(false);
		expect(r.stderr).toMatch(/source_provenance|template_file/);
	});
});

describe("sure_eval execution policy gate", () => {
	it("allows explicit execution=local even when vc is available", () => {
		if (!VC_AVAILABLE) {
			return;
		}
		const runDir = freshRunDir("execution-local-explicit");
		writeEvalInput(runDir);

		writeArtifact(runDir, "submit_result.json", {
			execution_path: "local_bash",
			execution_requested: "local",
			vc_available: true,
			fallback_approved: false,
			local_fallback_reason: "",
		});
		const passed = runGate("vc_check.py", runDir, "submit_result.json");
		expect(passed.ok).toBe(true);
	});

	it("requires vc_submit for execution=vc", () => {
		const runDir = freshRunDir("execution-vc");
		writeArtifact(runDir, "eval_input_resolved.json", { runtime: { execution: { requested: "vc" } } });
		writeArtifact(runDir, "submit_result.json", {
			execution_path: "local_bash",
			execution_requested: "vc",
			vc_available: VC_AVAILABLE,
		});
		const blocked = runGate("vc_check.py", runDir, "submit_result.json");
		expect(blocked.ok).toBe(false);
		expect(blocked.stderr).toContain("execution=vc");
	});

	it("blocks execution=auto local_bash when vc is available unless fallback is approved", () => {
		if (!VC_AVAILABLE) {
			return;
		}
		const runDir = freshRunDir("execution-auto-local-block");
		writeArtifact(runDir, "eval_input_resolved.json", { runtime: { execution: { requested: "auto" } } });
		writeArtifact(runDir, "submit_result.json", {
			execution_path: "local_bash",
			execution_requested: "auto",
			vc_available: true,
			fallback_approved: false,
			local_fallback_reason: "",
		});
		const blocked = runGate("vc_check.py", runDir, "submit_result.json");
		expect(blocked.ok).toBe(false);
		expect(blocked.stderr).toContain("execution=auto");
	});

	it("does not trust a forged vc_available=false for execution=auto when vc is available", () => {
		if (!VC_AVAILABLE) {
			return;
		}
		const runDir = freshRunDir("vc-forged-unavailable");
		const artifactPath = join(runDir, "artifacts", "submit_result.json");
		writeArtifact(runDir, "submit_result.json", {
			execution_path: "local_bash",
			execution_requested: "auto",
			vc_available: false,
			fallback_approved: false,
			local_fallback_reason: "",
		});

		const r = runGate("vc_check.py", runDir, "submit_result.json");
		const stamped = JSON.parse(readFileSync(artifactPath, "utf-8")) as { vc_available?: unknown };
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("execution=auto");
		expect(stamped.vc_available).toBe(true);
	});
});

describe("sure_eval execution surface helpers", () => {
	it("resolves vc device requests against the vc container GPU, not the host", () => {
		const script = `
import resolve_eval_input as r

auto = r._resolve_device("auto", {"planned": "vc", "path_planned": "vc_submit"})
assert auto["resolved"] == "cuda:0", auto
assert auto["execution_device_source"] == "vc_allocation", auto

indexed = r._resolve_device("cuda:3", {"planned": "vc", "path_planned": "vc_submit"})
assert indexed["request"] == "cuda:3", indexed
assert indexed["resolved"] == "cuda:0", indexed
assert indexed["notes"], indexed
`;
		const result = spawnSync("python3", ["-"], {
			cwd: SCRIPTS_DIR,
			input: script,
			encoding: "utf-8",
			env: { ...process.env, PYTHONPATH: SCRIPTS_DIR },
		});
		expect(result.status).toBe(0);
		expect(result.stderr).toBe("");
	});

	it("builds a vc command with containerized absolute entrypoint, persistent log, and provenance env", () => {
		const script = `
from sure_eval.agent import vc_submitter as v

v.select_best_image = lambda model: "repo/image:v1"
v.select_best_partition = lambda: "pdgpu-test"
v.estimate_memory_gb = lambda model: 16

cmd = v.build_vc_submit_command(
    model_name="Demo__Model",
    run_id="model-run-001",
    volume_mount="/host:/ctr",
    repo_root_in_container="/ctr/repo",
    entrypoint_path="/host/repo/.sure/runs/r/artifacts/run_evaluation.sh",
    log_path="/host/repo/.sure/runs/r/vc_logs/job.log",
    execution_requested="vc",
    device_request="auto",
    device_actual="cuda:0",
    harness_python_bin="/ctr/repo/.runtime/harness-py311/bin/python",
    job_name="demo-job",
)
text = "\\n".join(cmd)
assert cmd[cmd.index("-j") + 1] == "demo-job", cmd
assert "/ctr/repo/.sure/runs/r/artifacts/run_evaluation.sh" in text, text
assert "/ctr/repo/.sure/runs/r/vc_logs/job.log" in text, text
assert "SURE_EVAL_EXECUTION_PATH=vc_submit" in text, text
assert "SURE_EVAL_EXECUTION_REQUESTED=vc" in text, text
assert "SURE_EVAL_DEVICE_REQUEST=auto" in text, text
assert "SURE_EVAL_DEVICE_ACTUAL=cuda:0" in text, text
assert "HARNESS_PYTHON_BIN=/ctr/repo/.runtime/harness-py311/bin/python" in text, text
`;
		const result = spawnSync("python3", ["-"], {
			cwd: PACKAGE_DIR,
			input: script,
			encoding: "utf-8",
			env: { ...process.env, PYTHONPATH: SCRIPTS_DIR },
		});
		expect(result.status).toBe(0);
		expect(result.stderr).toBe("");
	});

	it("mirrors the effective vc submission into the execution surface", () => {
		const runDir = freshRunDir("vc-submission-mirror");
		const script = `
from pathlib import Path
import json
import run_vc_execution as r

surface_path = Path(${JSON.stringify(join(runDir, "artifacts", "execution_surface.json"))})
surface = {"source_provenance": {"template_file": "scripts/templates/run_single_model.sh"}}
submission = r._resolved_submission(
    image="repo/image:v1",
    partition="pdgpu-ezkws",
    memory_gb=32,
    gpus=1,
    cpus=8,
    job_name="demo-job",
    volume_mount="/host:/ctr",
    entrypoint_host=Path("/host/repo/.sure/runs/r/artifacts/vc_entrypoint.sh"),
    entrypoint_container="/ctr/repo/.sure/runs/r/artifacts/vc_entrypoint.sh",
    run_evaluation_path="/host/repo/.sure/runs/r/artifacts/run_evaluation.sh",
    log_path=Path("/host/repo/.sure/runs/r/vc_logs/job.log"),
    command="vc submit ...",
)
r._write_surface_resolved_submission(surface_path, surface, submission)
data = json.loads(surface_path.read_text())
assert data["vc_runtime"]["resolved_submission"] == submission, data
`;
		const result = spawnSync("python3", ["-"], {
			cwd: SCRIPTS_DIR,
			input: script,
			encoding: "utf-8",
			env: { ...process.env, PYTHONPATH: SCRIPTS_DIR },
		});
		expect(result.status).toBe(0);
		expect(result.stderr).toBe("");
	});
});
