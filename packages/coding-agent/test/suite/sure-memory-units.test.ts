// packages/coding-agent/test/suite/sure-memory-units.test.ts
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { MAIN_FLOW_UNITS } from "../../../../sure/skills/sure_eval/hooks/state-machine.ts";
import { MODEL_FEED_UNITS } from "../../../../sure/skills/sure_feed/hooks/state-machine.ts";
import { MODEL_TOOL_UNITS } from "../../../../sure/skills/sure_onboard/hooks/state-machine.ts";

// The memory library keeps a hand-written copy of every skill's unit list
// (units.json) and a table of template phrases it strips out of candidate
// triggers (config.json). Nothing generates them, so this suite pins both to
// the state machines and hook sources they describe. It also pins the
// extract_lessons unit (spec §4.1) to one definition shared by both skills and
// checks that every unit's schemaRef / gateScript / helperScripts point at
// files that exist.

const REPO_ROOT = resolve(__dirname, "../../../..");
const MEMORY_LIB = join(REPO_ROOT, "sure", "runtime", "memory");
const SKILLS_DIR = join(REPO_ROOT, "sure", "skills");

function readJson(path: string): any {
	return JSON.parse(readFileSync(path, "utf-8"));
}

const unitsRegistry = readJson(join(MEMORY_LIB, "units.json"));
const memoryConfig = readJson(join(MEMORY_LIB, "config.json"));

// Spec §4.1 / plan §1.10: the same object in sure_onboard and sure_eval.
const EXTRACT_LESSONS_UNIT = {
	id: "extract_lessons",
	label: "Extract lessons",
	kind: "gate",
	produces: "extraction_declaration.json",
	schemaRef: "extraction_declaration.schema.json",
	requiredFields: [
		"schema",
		"no_new_lessons",
		"no_lessons_reason",
		"covered_by",
		"candidates",
		"infra_noise",
		"infra_evidence",
	],
	allowedValues: { schema: ["sure.memory.extraction.v2"] },
	gateScript: "check_memory_extraction.py",
	gateInputs: ["candidates", "memory_evidence"],
	helperScripts: ["build_run_digest.py"],
};

// Only the fields this suite reads; sure_feed's Unit has no helperScripts.
type UnitLike = { id: string; schemaRef?: string; gateScript?: string; helperScripts?: string[] };
const MACHINES: Array<{ skill: string; units: UnitLike[] }> = [
	{ skill: "sure_onboard", units: MODEL_TOOL_UNITS },
	{ skill: "sure_eval", units: MAIN_FLOW_UNITS },
	{ skill: "sure_feed", units: MODEL_FEED_UNITS },
];

describe("sure/runtime/memory/units.json mirrors the state machines", () => {
	it("has the four skill keys and the v1 schema tag", () => {
		expect(unitsRegistry.schema).toBe("sure.memory.units.v1");
		const skills = Object.keys(unitsRegistry.skills).sort();
		expect(skills).toEqual(["sure_eval", "sure_feed", "sure_onboard", "sure_reval"]);
	});

	it("lists sure_onboard units in MODEL_TOOL_UNITS order", () => {
		expect(unitsRegistry.skills.sure_onboard).toEqual(MODEL_TOOL_UNITS.map((unit) => unit.id));
	});

	it("lists sure_eval units in MAIN_FLOW_UNITS order", () => {
		expect(unitsRegistry.skills.sure_eval).toEqual(MAIN_FLOW_UNITS.map((unit) => unit.id));
	});

	it("lists sure_feed units in MODEL_FEED_UNITS order and nothing for sure_reval", () => {
		expect(unitsRegistry.skills.sure_feed).toEqual(MODEL_FEED_UNITS.map((unit) => unit.id));
		expect(unitsRegistry.skills.sure_reval).toEqual([]);
	});
});

describe("extract_lessons unit (spec §4.1)", () => {
	it("sits between verdict and finalize_model_bundle in sure_onboard", () => {
		const ids = MODEL_TOOL_UNITS.map((unit) => unit.id);
		expect(ids.length).toBe(23);
		expect(ids.indexOf("extract_lessons")).toBe(21);
		expect(ids[20]).toBe("verdict");
		expect(ids[22]).toBe("finalize_model_bundle");
	});

	it("sits between assessment and run_report in sure_eval", () => {
		const ids = MAIN_FLOW_UNITS.map((unit) => unit.id);
		expect(ids.length).toBe(13);
		expect(ids.indexOf("extract_lessons")).toBe(11);
		expect(ids[10]).toBe("assessment");
		expect(ids[12]).toBe("run_report");
	});

	it("is defined identically in both skills", () => {
		const onboard = MODEL_TOOL_UNITS.find((unit) => unit.id === "extract_lessons");
		const evaluation = MAIN_FLOW_UNITS.find((unit) => unit.id === "extract_lessons");
		expect(onboard).toEqual(EXTRACT_LESSONS_UNIT);
		expect(evaluation).toEqual(EXTRACT_LESSONS_UNIT);
	});
});

describe("schemaRef / gateScript / helperScripts point at real files", () => {
	for (const machine of MACHINES) {
		it(`${machine.skill}: every unit has a schemaRef and the file exists under schemas/`, () => {
			for (const unit of machine.units) {
				expect(unit.schemaRef, `${machine.skill}/${unit.id} has no schemaRef`).toBeTruthy();
				const path = join(SKILLS_DIR, machine.skill, "schemas", unit.schemaRef ?? "");
				expect(existsSync(path), path).toBe(true);
			}
		});

		it(`${machine.skill}: every gateScript and helperScript exists under scripts/`, () => {
			for (const unit of machine.units) {
				for (const script of [unit.gateScript, ...(unit.helperScripts ?? [])]) {
					if (!script) {
						continue;
					}
					const path = join(SKILLS_DIR, machine.skill, "scripts", script);
					expect(existsSync(path), path).toBe(true);
				}
			}
		});
	}
});

describe("extraction_declaration.schema.json copies", () => {
	const sharedPath = join(MEMORY_LIB, "schemas", "extraction_declaration.schema.json");

	it("exists in the shared library, uses enum (not const) and requires exactly the unit's fields", () => {
		expect(existsSync(sharedPath), sharedPath).toBe(true);
		const schema = readJson(sharedPath);
		expect(schema.properties.schema.enum).toEqual(["sure.memory.extraction.v2"]);
		// validate.ts only understands enum; a const would silently not be checked.
		expect(readFileSync(sharedPath, "utf-8")).not.toContain('"const"');
		expect([...schema.required].sort()).toEqual([...EXTRACT_LESSONS_UNIT.requiredFields].sort());
	});

	it.each(["sure_onboard", "sure_eval"])("%s keeps a byte-identical copy under schemas/", (skill) => {
		const copyPath = join(SKILLS_DIR, skill, "schemas", "extraction_declaration.schema.json");
		expect(existsSync(copyPath), copyPath).toBe(true);
		const same = readFileSync(copyPath).equals(readFileSync(sharedPath));
		expect(same, `${copyPath} differs from ${sharedPath}`).toBe(true);
	});
});

describe("config.json trigger_template_phrases still appear in the hook sources", () => {
	// Rule 4 of the extraction gate strips these phrases out of candidate
	// triggers because they come from the hooks' own repair templates. If a
	// phrase is reworded in validate.ts / index.ts the table must follow.
	const sourceFiles: string[] = [];
	for (const skill of ["sure_onboard", "sure_eval"]) {
		for (const file of ["validate.ts", "index.ts"]) {
			sourceFiles.push(join(SKILLS_DIR, skill, "hooks", file));
		}
	}
	const sources = sourceFiles.map((path) => readFileSync(path, "utf-8")).join("\n");
	const phrases: string[] = memoryConfig.trigger_template_phrases;

	it("is a non-empty list of distinct strings", () => {
		expect(phrases.length).toBeGreaterThan(0);
		expect(new Set(phrases).size).toBe(phrases.length);
	});

	it.each(phrases)("phrase %s appears verbatim in a hooks/validate.ts or hooks/index.ts", (phrase) => {
		const found = sources.includes(phrase);
		expect(found, `not found verbatim in any hook source: ${phrase}`).toBe(true);
	});
});
