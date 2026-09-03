import { existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { findUnit, MAIN_FLOW_UNITS, TOTAL_UNITS } from "../../../../sure/skills/sure_eval/hooks/state-machine.ts";
import { MAIN_FLOW_UNITS as INFER_UNITS } from "../../../../sure/skills/sure_infer/hooks/state-machine.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// sure_eval skill package root (repo-relative from the test file).
const PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_eval");
const SCRIPTS_DIR = join(PACKAGE_DIR, "scripts");
const SCHEMAS_DIR = join(PACKAGE_DIR, "schemas");

const UNIT_IDS = ["dataset_scope", "execute_evaluation", "assessment", "extract_lessons", "run_report"];
const GATE_SCRIPTS: Array<[string, string]> = [
	["execute_evaluation", "check_eval_run_report.py"],
	["assessment", "check_assessment.py"],
	["extract_lessons", "check_memory_extraction.py"],
	["run_report", "check_run_report.py"],
];

describe("sure_eval state machine shape", () => {
	it("has exactly five units in order", () => {
		expect(MAIN_FLOW_UNITS.map((unit) => unit.id)).toEqual(UNIT_IDS);
		expect(TOTAL_UNITS).toBe(5);
	});

	// Gate units must have NO in-process gateCheck: the python gateScript is the
	// single authoritative semantic checker, and it has to exist under scripts/
	// so runGateScript can spawn it.
	it.each(GATE_SCRIPTS)(
		"%s delegates semantics to its python gateScript (no in-process gateCheck)",
		(unitId, script) => {
			const unit = findUnit(unitId)!;
			expect(unit.kind).toBe("gate");
			expect(unit.gateScript).toBe(script);
			expect(unit.gateCheck).toBeUndefined();
			expect(existsSync(join(SCRIPTS_DIR, script))).toBe(true);
		},
	);

	it("those are all the gate units", () => {
		const gates = MAIN_FLOW_UNITS.filter((unit) => unit.kind === "gate").map((unit) => unit.id);
		expect(gates).toEqual(GATE_SCRIPTS.map(([unitId]) => unitId));
	});

	it("run_report runs check_run_report.py with --profile eval", () => {
		expect(findUnit("run_report")!.gateScriptArgs?.({} as SureHookContext)).toEqual(["--profile", "eval"]);
	});

	it("every schemaRef resolves under the package schemas/", () => {
		for (const unit of MAIN_FLOW_UNITS) {
			expect(unit.schemaRef, `${unit.id} has no schemaRef`).toBeTruthy();
			const path = join(SCHEMAS_DIR, unit.schemaRef ?? "");
			expect(existsSync(path), path).toBe(true);
		}
	});

	it("carries the four memory wrapper scripts", () => {
		for (const script of [
			"build_run_digest.py",
			"check_memory_extraction.py",
			"check_memory_index.py",
			"publish_memory.py",
		]) {
			expect(existsSync(join(SCRIPTS_DIR, script)), script).toBe(true);
		}
	});

	it("shares the extract_lessons unit with sure_infer", () => {
		expect(findUnit("extract_lessons")).toEqual(INFER_UNITS.find((unit) => unit.id === "extract_lessons"));
	});
});
