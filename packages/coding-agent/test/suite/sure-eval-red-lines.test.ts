import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

// sure_eval skill package root (repo-relative from the test file).
const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_eval");
const SCRIPTS_DIR = join(PACKAGE_DIR, "scripts");
const TEMPLATES_DIR = join(SCRIPTS_DIR, "templates");

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
		const surfacePath = join(runDir, "artifacts", "execution_surface.json");
		const result = spawnSync("python3", ["-"], {
			cwd: SCRIPTS_DIR,
			input: `
from pathlib import Path
from check_execution_surface_compliance import check_template_source
result = check_template_source(Path(${JSON.stringify(surfacePath)}), Path(${JSON.stringify(templateFile)}))
assert result["passed"], result
`,
			encoding: "utf-8",
			env: { ...process.env, PYTHONPATH: SCRIPTS_DIR },
		});
		expect(result.status).toBe(0);
		expect(result.stderr).toBe("");
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

describe("sure_eval [2.6/5] evaluation readiness gate", () => {
	const TEMPLATES = ["run_single_model.sh", "run_single_model_single_dataset.sh"];

	for (const template of TEMPLATES) {
		// Repair mode is not a shortcut past evaluation: both arms fall out of the
		// branch into [4/5] and [5/5], and [5/5] resolves the same binding. A gate
		// that only guards the normal arm lets a repair run pay for every
		// prediction before the runtime it needs is found missing.
		// REPO_ROOT is the skill package directory -- the gate reaches its own
		// script through $REPO_ROOT/scripts/. So the fallback engine root named
		// sure/skills/sure_eval/sure/external/sure-evaluation, which exists
		// nowhere, and the gate went red on every host run that did not set
		// SURE_EVALUATION_HOME. [5/5] resolves the engine from the repository
		// root; the gate has to check the checkout [5/5] will use.
		it(`checks the engine root [5/5] will use in ${template}`, () => {
			const text = readFileSync(join(TEMPLATES_DIR, template), "utf-8");
			expect(text).not.toContain("$REPO_ROOT/sure/external/sure-evaluation");
			expect(text).toContain("SURE_EVALUATION_HOME:-$HARNESS_REPO_ROOT/sure/external/sure-evaluation");
		});

		it(`runs before the repair branch in ${template}`, () => {
			const text = readFileSync(join(TEMPLATES_DIR, template), "utf-8");
			const gate = text.indexOf("[2.6/5] Evaluation readiness gate");
			const repairBranch = text.indexOf('if [[ "$REPAIR_INVALID_ONLY" == "1" ]]');
			expect(gate).toBeGreaterThan(-1);
			expect(repairBranch).toBeGreaterThan(-1);
			expect(gate).toBeLessThan(repairBranch);
		});
	}
});
