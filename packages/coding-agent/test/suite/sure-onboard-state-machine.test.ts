import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { CheckpointData } from "../../../../sure/skills/sure_onboard/hooks/checkpoints.ts";
import { postToolResult } from "../../../../sure/skills/sure_onboard/hooks/index.ts";
import { findUnit } from "../../../../sure/skills/sure_onboard/hooks/state-machine.ts";
import { validateProduces } from "../../../../sure/skills/sure_onboard/hooks/validate.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// sure_onboard skill package root (repo-relative from the test file).
const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_onboard");

function freshCtx(name: string): { ctx: SureHookContext; runDir: string } {
	const runDir = resolve(__dirname, "tmp-ob", name);
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	const ctx: SureHookContext = {
		point: "post_tool_result",
		run: { id: "test-ob", command: "/sure_onboard", status: "running" } as never,
		skill: { name: "sure_onboard", command: "/sure_onboard" } as never,
		cwd: PACKAGE_DIR,
		packageDir: PACKAGE_DIR,
		runDir,
		args: "",
	};
	return { ctx, runDir };
}

function seedCheckpoint(runDir: string, data: CheckpointData): void {
	writeFileSync(join(runDir, "state.json"), JSON.stringify({ checkpoint: { data } }, null, 2), "utf-8");
}

function writeArtifact(runDir: string, produces: string, value: unknown): void {
	writeFileSync(join(runDir, "artifacts", produces), JSON.stringify(value, null, 2), "utf-8");
}

// Regression guard for the --kind routing bug: runGateScript injected
// ["--kind", "<unit_suffix>"] for EVERY unit whose id starts with "validate_",
// but only run_validate.py accepts --kind. check_spec.py / check_env_compat.py
// crashed with "unrecognized arguments: --kind spec", exhausting retries and
// marking validate_spec FAILED — even on a fully compliant spec_validation.json.
// Fixed: --kind is injected ONLY when gateScript === "run_validate.py".
describe("sure_onboard gate --kind routing (regression)", () => {
	// The four run_validate.py gates must each receive their --kind.
	it.each([
		["validate_import", "import"],
		["validate_load", "load"],
		["validate_infer", "infer"],
		["validate_contract", "contract"],
	])("%s routes to run_validate.py with --kind %s", (unitId, kind) => {
		const unit = findUnit(unitId)!;
		expect(unit.gateScript).toBe("run_validate.py");
		// The kind is derived from the unit id suffix in index.ts runGateScript.
		expect(unit.id.replace("validate_", "")).toBe(kind);
	});

	it("validate_spec uses check_spec.py (NOT run_validate.py) — no --kind injected", () => {
		const unit = findUnit("validate_spec")!;
		expect(unit.gateScript).toBe("check_spec.py");
		// A compliant spec_validation.json must advance cleanly, NOT crash on --kind.
		const { ctx, runDir } = freshCtx("spec-pass");
		seedCheckpoint(runDir, {
			currentUnit: "validate_spec",
			completedUnits: ["discover", "classify", "plan"],
			retries: {},
		});
		writeArtifact(runDir, "spec_validation.json", {
			status: "passed",
			checks: {
				spec_completeness: { passed: true },
				evidence_sufficiency: { passed: true },
				conflict_resolution: { passed: true },
				build_plan_executable: { passed: true },
				fixture_availability: { passed: true },
				io_contract_sufficient: { passed: true },
				preflight_compatible: { passed: true },
			},
		});
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		expect(result.repair).toBeUndefined();
		// Must have advanced past validate_spec to build_env.
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("build_env");
		expect(checkpoint?.data.completedUnits).toContain("validate_spec");
	});

	it("validate_env_compat uses check_env_compat.py (NOT run_validate.py)", () => {
		const unit = findUnit("validate_env_compat")!;
		expect(unit.gateScript).toBe("check_env_compat.py");
		const { ctx, runDir } = freshCtx("envcompat-pass");
		seedCheckpoint(runDir, {
			currentUnit: "validate_env_compat",
			completedUnits: ["discover", "classify", "plan", "validate_spec", "build_env", "fetch_weights"],
			retries: {},
		});
		writeArtifact(runDir, "env_compat_result.json", { compat_ok: true });
		const result = postToolResult(ctx);
		expect(result.ok).toBe(true);
		const checkpoint = (result.state_patch as { checkpoint?: { data: CheckpointData } }).checkpoint;
		expect(checkpoint?.data.currentUnit).toBe("validate_import");
	});
});

// Regression guard for the schema-union fix in validate.ts. The required-field
// check used to be `unit.requiredFields ?? schema?.required` — a unit with FEWER
// fields than the schema silently overrode it. save_artifacts declared only
// ["model_dir"] but artifact_manifest.schema.json requires ["model_dir",
// "artifacts"], so an artifact_manifest.json with NO artifacts field passed
// validation (the spec/wrapper paths it should record were never checked here).
// Fixed: requiredFields is now the UNION of unit + schema required.
describe("sure_onboard validateProduces schema-union (regression)", () => {
	it("save_artifacts rejects an artifact_manifest missing the schema-required `artifacts` field", () => {
		const { ctx, runDir } = freshCtx("manifest-no-artifacts");
		const unit = findUnit("save_artifacts")!;
		// unit.requiredFields = ["model_dir"]; schema.required adds "artifacts".
		writeArtifact(runDir, "artifact_manifest.json", { model_dir: "sure/models/x" });
		const result = validateProduces(ctx, unit, { model_dir: "sure/models/x" });
		expect(result.ok).toBe(false);
		expect(result.reason).toContain("artifacts");
	});

	it("save_artifacts accepts a manifest with both model_dir AND artifacts", () => {
		const { ctx } = freshCtx("manifest-ok");
		const unit = findUnit("save_artifacts")!;
		const result = validateProduces(ctx, unit, {
			model_dir: "sure/models/x",
			artifacts: { spec_path: "model.spec.yaml", wrapper_path: "model.py" },
		});
		expect(result.ok).toBe(true);
	});
});
