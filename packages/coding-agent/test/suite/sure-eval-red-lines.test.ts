import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

// sure_eval skill package root (repo-relative from the test file).
const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_eval");
const SCRIPTS_DIR = join(PACKAGE_DIR, "scripts");
const TEMPLATES_DIR = join(SCRIPTS_DIR, "templates");

// Whether vc is installed in this environment (the VC_SUBMIT_MANDATORY red
// line only forces vc_submit when `which vc && vc info` both succeed).
const VC_AVAILABLE = (() => {
	const r = spawnSync("vc", ["info"], { encoding: "utf-8", timeout: 10_000 });
	return r.status === 0;
})();

function freshRunDir(name: string): string {
	const dir = resolve(__dirname, "tmp-rl", name);
	mkdirSync(join(dir, "artifacts"), { recursive: true });
	return dir;
}

function writeArtifact(runDir: string, produces: string, value: unknown): void {
	writeFileSync(join(runDir, "artifacts", produces), JSON.stringify(value, null, 2), "utf-8");
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
			source_provenance: { template_file: templateFile },
		});
		writeFileSync(
			join(runDir, "artifacts", "run_evaluation.sh"),
			"python evaluate_predictions.py --results-dir x --protocol-id y --model-dir z || EVAL_EXIT=$?\n",
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

describe("sure_eval red line 2 — VC_SUBMIT_MANDATORY", () => {
	it("forces execution_path=vc_submit (and blocks local_bash) when vc is available", () => {
		if (!VC_AVAILABLE) {
			// Red line only bites when vc is installed; skip semantics in CI without vc.
			return;
		}
		const runDir = freshRunDir("vc-block-local");

		// local_bash without an approved fallback must be blocked.
		writeArtifact(runDir, "submit_result.json", {
			execution_path: "local_bash",
			vc_available: true,
			fallback_approved: false,
			local_fallback_reason: "",
		});
		const blocked = runGate("vc_check.py", runDir, "submit_result.json");
		expect(blocked.ok).toBe(false);
		expect(blocked.stderr).toContain("vc_submit");

		// vc_submit must pass.
		writeArtifact(runDir, "submit_result.json", {
			execution_path: "vc_submit",
			vc_available: true,
		});
		const passed = runGate("vc_check.py", runDir, "submit_result.json");
		expect(passed.ok).toBe(true);
	});

	it("accepts a documented local fallback when fallback_approved + reason are set", () => {
		if (!VC_AVAILABLE) {
			return;
		}
		const runDir = freshRunDir("vc-fallback");
		writeArtifact(runDir, "submit_result.json", {
			execution_path: "local_bash",
			vc_available: true,
			fallback_approved: true,
			local_fallback_reason: "vc cluster down for maintenance",
		});
		// An explicitly-approved fallback lets the run proceed (degraded), while
		// an unapproved one is blocked (covered by the previous test).
		const r = runGate("vc_check.py", runDir, "submit_result.json");
		expect(r.ok).toBe(true);
	});

	it("does not trust a forged vc_available=false when vc is available", () => {
		if (!VC_AVAILABLE) {
			return;
		}
		const runDir = freshRunDir("vc-forged-unavailable");
		const artifactPath = join(runDir, "artifacts", "submit_result.json");
		writeArtifact(runDir, "submit_result.json", {
			execution_path: "local_bash",
			vc_available: false,
			fallback_approved: false,
			local_fallback_reason: "",
		});

		const r = runGate("vc_check.py", runDir, "submit_result.json");
		const stamped = JSON.parse(readFileSync(artifactPath, "utf-8")) as { vc_available?: unknown };
		expect(r.ok).toBe(false);
		expect(r.stderr).toContain("vc_submit");
		expect(stamped.vc_available).toBe(true);
	});
});
