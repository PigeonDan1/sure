import { createHash } from "node:crypto";
import { lstatSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import {
	EXTRACT_LESSONS_UNIT_ID,
	gateDigest,
	isExtractionGateExhausted,
	type MemoryCheckpoint,
	readMemory,
	runIdOf,
	safeGateDigest,
} from "../../../../sure/runtime/memory/hooks.ts";
// Namespace import so vi.spyOn can replace loadMemoryConfig for the one test that needs
// gateDigest to run without a config, the same way sure-onboard-memory.test.ts spies on
// memoryConfigOrUndefined.
import * as matchModule from "../../../../sure/runtime/memory/match.ts";
import * as feedCheckpoints from "../../../../sure/skills/sure_feed/hooks/checkpoints.ts";
import {
	LAST_UNIT as FEED_LAST_UNIT,
	findUnit as findFeedUnit,
} from "../../../../sure/skills/sure_feed/hooks/state-machine.ts";
import * as inferCheckpoints from "../../../../sure/skills/sure_infer/hooks/checkpoints.ts";
import {
	findUnit as findInferUnit,
	LAST_UNIT as INFER_LAST_UNIT,
} from "../../../../sure/skills/sure_infer/hooks/state-machine.ts";
import * as onboardCheckpoints from "../../../../sure/skills/sure_onboard/hooks/checkpoints.ts";
import {
	findUnit as findOnboardUnit,
	LAST_UNIT as ONBOARD_LAST_UNIT,
} from "../../../../sure/skills/sure_onboard/hooks/state-machine.ts";
import * as transCheckpoints from "../../../../sure/skills/sure_trans/hooks/checkpoints.ts";
import {
	findUnit as findTransUnit,
	LAST_UNIT as TRANS_LAST_UNIT,
} from "../../../../sure/skills/sure_trans/hooks/state-machine.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

// Checkpoint memory sub-object + hooks.ts skeleton (readMemory, runIdOf, gateDigest,
// isExtractionGateExhausted). The fixtures below are private to this file; the other
// memory suites (hooks-flow, onboard, eval) carry their own copies.

type Skill = "sure_onboard" | "sure_infer" | "sure_trans" | "sure_feed";
const SKILLS: Skill[] = ["sure_onboard", "sure_infer", "sure_trans", "sure_feed"];

const SKILLS_ROOT = resolve(__dirname, "../../../../sure/skills");

/** Fresh run dir under test/suite/tmp-memhooks/<name>/ with an empty artifacts/. */
function freshRunDir(name: string): string {
	const runDir = resolve(__dirname, "tmp-memhooks", name);
	rmSync(runDir, { recursive: true, force: true });
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	return runDir;
}

/** Minimal SureHookContext the way the state-machine suites build it (ctx.run.runId stays undefined). */
function makeCtx(skill: Skill, runDir: string, point: SureHookContext["point"] = "post_tool_result"): SureHookContext {
	const packageDir = join(SKILLS_ROOT, skill);
	return {
		point,
		run: { id: `test-memhooks-${skill}`, command: `/${skill}`, status: "running" } as never,
		skill: { name: skill, command: `/${skill}` } as never,
		cwd: packageDir,
		packageDir,
		runDir,
		args: "",
	};
}

/** state.json with only a checkpoint, exactly what the skills' readCheckpoint reads. */
function seedState(runDir: string, data: unknown): void {
	writeFileSync(join(runDir, "state.json"), JSON.stringify({ checkpoint: { data } }, null, 2), "utf-8");
}

/** Write a UTF-8 file under <runDir>/artifacts/<rel>, creating parent dirs. */
function writeArtifactFile(runDir: string, rel: string, text: string): void {
	const path = join(runDir, "artifacts", rel);
	mkdirSync(dirname(path), { recursive: true });
	writeFileSync(path, text, "utf-8");
}

const SAMPLE_MEMORY: MemoryCheckpoint = {
	digestCutoff: 812,
	digestSha256: "a".repeat(64),
	digestPassed: "verdict",
	finishAttempts: 1,
	extractionStatus: "failed",
	injected: { build_env: ["sure_onboard/no-kernel-image", "_shared/vc-partition-names"] },
	pendingDisputed: { validate_infer: ["sure_onboard/no-kernel-image"] },
};

const CHECKPOINT_MODULES = {
	sure_onboard: onboardCheckpoints,
	sure_infer: inferCheckpoints,
	sure_trans: transCheckpoints,
	sure_feed: feedCheckpoints,
} as const;
const UNIT_FINDERS = {
	sure_onboard: findOnboardUnit,
	sure_infer: findInferUnit,
	sure_trans: findTransUnit,
	sure_feed: findFeedUnit,
} as const;
const GATE_UNIT_IDS = {
	sure_onboard: "build_env",
	sure_infer: "execute_inference",
	sure_trans: "validate_contract",
	sure_feed: "match_task",
} as const;
const LAST_UNITS = {
	sure_onboard: ONBOARD_LAST_UNIT,
	sure_infer: INFER_LAST_UNIT,
	sure_trans: TRANS_LAST_UNIT,
	sure_feed: FEED_LAST_UNIT,
} as const;

function unitFor(skill: Skill, id: string) {
	const unit = UNIT_FINDERS[skill](id);
	if (!unit) {
		throw new Error(`fixture: unit ${id} not found in ${skill}`);
	}
	return unit;
}

/** sure_trans and sure_feed declare failedArtifactDigests as required; onboard and eval leave it optional. */
function withDigests<T extends { failedArtifactDigests?: Record<string, string> }>(data: T) {
	return { ...data, failedArtifactDigests: data.failedArtifactDigests ?? {} };
}

describe.each<Skill>(SKILLS)("%s checkpoints carry the memory sub-object", (skill) => {
	const mod = CHECKPOINT_MODULES[skill];
	const gateUnitId = GATE_UNIT_IDS[skill];
	const lastUnit = LAST_UNITS[skill];

	it("readCheckpoint reads every memory key back by type", () => {
		const runDir = freshRunDir(`${skill}-read-memory`);
		seedState(runDir, {
			currentUnit: gateUnitId,
			completedUnits: [],
			retries: {},
			memory: SAMPLE_MEMORY,
		});
		const checkpoint = mod.readCheckpoint(makeCtx(skill, runDir));
		expect(checkpoint.data.currentUnit).toBe(gateUnitId);
		expect(checkpoint.data.memory).toEqual(SAMPLE_MEMORY);
	});

	it("readCheckpoint leaves memory undefined for a checkpoint written before the memory system", () => {
		const runDir = freshRunDir(`${skill}-no-memory`);
		seedState(runDir, { currentUnit: gateUnitId, completedUnits: [], retries: {} });
		const checkpoint = mod.readCheckpoint(makeCtx(skill, runDir));
		expect(checkpoint.data.memory).toBeUndefined();
		// What lands in state.json again: no memory key is added to a pre-memory checkpoint.
		expect(Object.keys(JSON.parse(JSON.stringify(checkpoint.data))).sort()).toEqual([
			"completedUnits",
			"currentUnit",
			"failedArtifactDigests",
			"retries",
		]);
	});

	it("readCheckpoint drops mistyped or unknown memory keys and keeps the valid ones", () => {
		const runDir = freshRunDir(`${skill}-bad-memory`);
		seedState(runDir, {
			currentUnit: gateUnitId,
			completedUnits: [],
			retries: {},
			memory: {
				digestCutoff: "812",
				digestSha256: 42,
				digestPassed: ["verdict"],
				finishAttempts: Number.NaN,
				extractionStatus: "nope",
				injected: { build_env: ["a", 3, null, "b"], broken: "not-a-list" },
				pendingDisputed: "not-a-map",
				bogus: 1,
			},
		});
		const checkpoint = mod.readCheckpoint(makeCtx(skill, runDir));
		expect(checkpoint.data.memory).toEqual({ injected: { build_env: ["a", "b"] } });
	});

	it("advance carries memory unchanged to the next unit and to the terminal checkpoint", () => {
		const unit = unitFor(skill, gateUnitId);
		const current = {
			currentUnit: unit.id,
			completedUnits: [],
			retries: { [unit.id]: 1 },
			failedArtifactDigests: {},
			memory: SAMPLE_MEMORY,
		};
		const next = mod.advance(unit, current);
		expect(next?.data.currentUnit).not.toBe(unit.id);
		expect(next?.data.memory).toBe(SAMPLE_MEMORY);
		expect(next?.data.retries[unit.id]).toBeUndefined();

		const terminal = mod.advance(lastUnit, {
			currentUnit: lastUnit.id,
			completedUnits: [],
			retries: {},
			failedArtifactDigests: {},
			memory: SAMPLE_MEMORY,
		});
		expect(terminal?.data.currentUnit).toBe(lastUnit.id);
		expect(terminal?.data.completedUnits).toContain(lastUnit.id);
		expect(terminal?.data.memory).toBe(SAMPLE_MEMORY);
	});

	it("bumpRetry carries memory unchanged while counting the retry", () => {
		const unit = unitFor(skill, gateUnitId);
		const bumped = mod.bumpRetry(
			unit,
			{ currentUnit: unit.id, completedUnits: [], retries: {}, failedArtifactDigests: {}, memory: SAMPLE_MEMORY },
			"deadbeef",
		);
		expect(bumped.data.retries[unit.id]).toBe(1);
		expect(bumped.data.failedArtifactDigests?.[unit.id]).toBe("deadbeef");
		expect(bumped.data.memory).toBe(SAMPLE_MEMORY);
	});

	it("memory survives a state.json round trip through readCheckpoint -> bumpRetry -> advance", () => {
		const runDir = freshRunDir(`${skill}-round-trip`);
		const unit = unitFor(skill, gateUnitId);
		seedState(runDir, { currentUnit: unit.id, completedUnits: [], retries: {}, memory: SAMPLE_MEMORY });
		const ctx = makeCtx(skill, runDir);
		const first = mod.readCheckpoint(ctx);
		const bumped = mod.bumpRetry(unit, withDigests(first.data), "deadbeef");
		seedState(runDir, bumped.data);
		const second = mod.readCheckpoint(ctx);
		const advanced = mod.advance(unit, withDigests(second.data));
		seedState(runDir, advanced?.data);
		const third = mod.readCheckpoint(ctx);
		expect(third.data.completedUnits).toContain(unit.id);
		expect(third.data.memory).toEqual(SAMPLE_MEMORY);
	});
});

describe("readMemory", () => {
	it("returns an empty object for a missing or non-object memory value", () => {
		expect(readMemory(undefined)).toEqual({});
		expect(readMemory({ currentUnit: "plan", completedUnits: [], retries: {} })).toEqual({});
		expect(readMemory({ memory: "nope" })).toEqual({});
		expect(readMemory({ memory: [1, 2] })).toEqual({});
	});

	it("keeps an empty injected map (a record with no keys is a valid value)", () => {
		expect(readMemory({ memory: { injected: {} } })).toEqual({ injected: {} });
	});
});

describe("runIdOf", () => {
	it("is the basename of ctx.runDir (ctx.run.runId is not set in hook fixtures)", () => {
		const runDir = freshRunDir("20260818-120000-abcdef12");
		expect(runIdOf(makeCtx("sure_onboard", runDir))).toBe("20260818-120000-abcdef12");
	});
});

describe("gateDigest", () => {
	const declaration = JSON.stringify({ schema: "sure.memory.extraction.v2", no_new_lessons: false }, null, 2);

	function plainSha(runDir: string, produces: string): string {
		return createHash("sha256")
			.update(readFileSync(join(runDir, "artifacts", produces)))
			.digest("hex");
	}

	it("equals the plain sha256 of produces when the unit has no gateInputs", () => {
		const runDir = freshRunDir("gate-digest-plain");
		writeArtifactFile(runDir, "extraction_declaration.json", declaration);
		const ctx = makeCtx("sure_onboard", runDir);
		expect(gateDigest(ctx, { produces: "extraction_declaration.json" })).toBe(
			plainSha(runDir, "extraction_declaration.json"),
		);
		expect(gateDigest(ctx, { produces: "extraction_declaration.json", gateInputs: [] })).toBe(
			plainSha(runDir, "extraction_declaration.json"),
		);
	});

	it("equals the plain sha256 when every gateInputs path is missing", () => {
		const runDir = freshRunDir("gate-digest-missing-inputs");
		writeArtifactFile(runDir, "extraction_declaration.json", declaration);
		const ctx = makeCtx("sure_onboard", runDir);
		const unit = { produces: "extraction_declaration.json", gateInputs: ["candidates", "memory_evidence"] };
		expect(gateDigest(ctx, unit)).toBe(plainSha(runDir, "extraction_declaration.json"));
	});

	it("changes when a candidate file under gateInputs changes while produces stays the same", () => {
		const runDir = freshRunDir("gate-digest-candidate-change");
		writeArtifactFile(runDir, "extraction_declaration.json", declaration);
		writeArtifactFile(
			runDir,
			"candidates/01-no-kernel-image/proposal.json",
			'{"type":"bad_case","trigger":["no kernel image"]}',
		);
		writeArtifactFile(runDir, "candidates/01-no-kernel-image/entry.md", "# CUDA arch mismatch\n");
		const ctx = makeCtx("sure_onboard", runDir);
		const unit = { produces: "extraction_declaration.json", gateInputs: ["candidates", "memory_evidence"] };
		const before = gateDigest(ctx, unit);
		expect(before).not.toBe(plainSha(runDir, "extraction_declaration.json"));

		writeArtifactFile(runDir, "candidates/01-no-kernel-image/entry.md", "# CUDA arch mismatch (fixed trigger)\n");
		const after = gateDigest(ctx, unit);
		expect(after).not.toBe(before);
		expect(gateDigest(ctx, unit)).toBe(after);
	});

	it("does not depend on file creation order, but does depend on relative paths", () => {
		const files: [string, string][] = [
			["candidates/02-b/proposal.json", '{"b":1}'],
			["candidates/01-a/proposal.json", '{"a":1}'],
			["memory_evidence/build_env.log", "line1\nline2\n"],
		];
		const forward = freshRunDir("gate-digest-order-forward");
		writeArtifactFile(forward, "extraction_declaration.json", declaration);
		for (const [rel, text] of files) {
			writeArtifactFile(forward, rel, text);
		}
		const backward = freshRunDir("gate-digest-order-backward");
		writeArtifactFile(backward, "extraction_declaration.json", declaration);
		for (const [rel, text] of [...files].reverse()) {
			writeArtifactFile(backward, rel, text);
		}
		const unit = { produces: "extraction_declaration.json", gateInputs: ["candidates", "memory_evidence"] };
		expect(gateDigest(makeCtx("sure_infer", forward), unit)).toBe(gateDigest(makeCtx("sure_infer", backward), unit));

		const renamed = freshRunDir("gate-digest-order-renamed");
		writeArtifactFile(renamed, "extraction_declaration.json", declaration);
		writeArtifactFile(renamed, "candidates/03-b/proposal.json", '{"b":1}');
		writeArtifactFile(renamed, "candidates/01-a/proposal.json", '{"a":1}');
		writeArtifactFile(renamed, "memory_evidence/build_env.log", "line1\nline2\n");
		expect(gateDigest(makeCtx("sure_infer", renamed), unit)).not.toBe(
			gateDigest(makeCtx("sure_infer", forward), unit),
		);
	});

	it("accepts a single file as a gateInputs entry", () => {
		const runDir = freshRunDir("gate-digest-file-input");
		writeArtifactFile(runDir, "extraction_declaration.json", declaration);
		writeArtifactFile(runDir, "candidates/01-a/proposal.json", '{"a":1}');
		const ctx = makeCtx("sure_onboard", runDir);
		const unit = { produces: "extraction_declaration.json", gateInputs: ["candidates/01-a/proposal.json"] };
		const before = gateDigest(ctx, unit);
		expect(before).not.toBe(plainSha(runDir, "extraction_declaration.json"));
		writeArtifactFile(runDir, "candidates/01-a/proposal.json", '{"a":2}');
		expect(gateDigest(ctx, unit)).not.toBe(before);
	});

	it("feeds produces, then each input as posix relpath NUL bytes NUL in sorted order (pinned rule)", () => {
		// The exact framing is a contract shared with the full hooks.ts that Task 12 writes:
		// sha256(produces bytes + for each file sorted by relpath: relpath + "\0" + bytes + "\0").
		// NUL is used because it cannot occur in a path, so "a b" + "c" and "a" + "b c" cannot collide.
		const runDir = freshRunDir("gate-digest-pinned");
		writeArtifactFile(runDir, "extraction_declaration.json", declaration);
		writeArtifactFile(runDir, "candidates/02-b/proposal.json", '{"b":1}');
		writeArtifactFile(runDir, "candidates/01-a/entry.md", "# A\n");
		writeArtifactFile(runDir, "memory_evidence/build_env.log", "line1\n");
		const unit = { produces: "extraction_declaration.json", gateInputs: ["candidates", "memory_evidence"] };
		const expected = createHash("sha256")
			.update(readFileSync(join(runDir, "artifacts", "extraction_declaration.json")))
			.update("candidates/01-a/entry.md\0")
			.update(Buffer.from("# A\n", "utf-8"))
			.update("\0")
			.update("candidates/02-b/proposal.json\0")
			.update(Buffer.from('{"b":1}', "utf-8"))
			.update("\0")
			.update("memory_evidence/build_env.log\0")
			.update(Buffer.from("line1\n", "utf-8"))
			.update("\0")
			.digest("hex");
		expect(gateDigest(makeCtx("sure_onboard", runDir), unit)).toBe(expected);
	});
});

describe("gateDigest over an agent-writable gateInputs tree", () => {
	// artifacts/candidates and artifacts/memory_evidence are filled by the agent with arbitrary
	// bash (EXTRACTION.md tells it to put evidence there), so the walk must survive anything it
	// finds. A throw out of here reaches the agent as "Fix the hook failure in hooks/index.ts"
	// with no state_patch: no retry is consumed and every following tool call reproduces it.
	const config = matchModule.loadMemoryConfig();
	const declaration = JSON.stringify({ schema: "sure.memory.extraction.v2", no_new_lessons: false }, null, 2);
	const unit = { produces: "extraction_declaration.json", gateInputs: ["candidates", "memory_evidence"] };

	function plainSha(runDir: string): string {
		return createHash("sha256")
			.update(readFileSync(join(runDir, "artifacts", "extraction_declaration.json")))
			.digest("hex");
	}

	it("throws when produces is absent, and safeGateDigest turns that into undefined", () => {
		// failOrRetry has no existsSync guard (unlike unchangedFailedArtifact) and runGateScript
		// opens a 60 s window before it in which the agent's own bash can delete the declaration.
		const runDir = freshRunDir("gate-digest-no-produces");
		const ctx = makeCtx("sure_onboard", runDir);
		expect(() => gateDigest(ctx, unit)).toThrow();
		expect(safeGateDigest(ctx, unit)).toBeUndefined();
	});

	it("agrees with gateDigest whenever gateDigest itself succeeds", () => {
		const runDir = freshRunDir("gate-digest-safe-agrees");
		writeArtifactFile(runDir, "extraction_declaration.json", declaration);
		writeArtifactFile(runDir, "candidates/01-a/proposal.json", '{"a":1}');
		const ctx = makeCtx("sure_onboard", runDir);
		expect(safeGateDigest(ctx, unit)).toBe(gateDigest(ctx, unit));
	});

	it("does not follow a symlink under memory_evidence that points back at an ancestor", () => {
		// statSync follows the link and the walk had no visited set: on a POSIX box that recursion
		// only ends in RangeError. lstatSync skips the link, so the digest is the one the real
		// files alone produce.
		const runDir = freshRunDir("gate-digest-symlink-loop");
		writeArtifactFile(runDir, "extraction_declaration.json", declaration);
		writeArtifactFile(runDir, "memory_evidence/build_env.log", "line1\n");
		const withoutLink = gateDigest(makeCtx("sure_onboard", runDir), unit);
		// "junction" is the one link type Windows creates without elevation; readdirSync walks
		// through it exactly like a POSIX symlink to a directory.
		symlinkSync(join(runDir, "artifacts"), join(runDir, "artifacts", "memory_evidence", "loop"), "junction");
		expect(lstatSync(join(runDir, "artifacts", "memory_evidence", "loop")).isSymbolicLink()).toBe(true);
		expect(gateDigest(makeCtx("sure_onboard", runDir), unit)).toBe(withoutLink);
	});

	it("stops at gate_digest_max_entries files instead of walking an unbounded tree", () => {
		const runDir = freshRunDir("gate-digest-entry-cap");
		writeArtifactFile(runDir, "extraction_declaration.json", declaration);
		const dir = join(runDir, "artifacts", "memory_evidence");
		mkdirSync(dir, { recursive: true });
		for (let i = 0; i <= config.gate_digest_max_entries; i++) {
			writeFileSync(join(dir, `f${String(i).padStart(6, "0")}.log`), "x", "utf-8");
		}
		const ctx = makeCtx("sure_onboard", runDir);
		const capped = gateDigest(ctx, unit);
		expect(capped).not.toBe(plainSha(runDir));
		// Deterministic: names are sorted, so the same tree always yields the same prefix.
		expect(gateDigest(ctx, unit)).toBe(capped);
		// The last file is past the cap, so its content no longer moves the digest.
		writeFileSync(join(dir, `f${String(config.gate_digest_max_entries).padStart(6, "0")}.log`), "y", "utf-8");
		expect(gateDigest(ctx, unit)).toBe(capped);
	});

	it("hashes the size of a file past gate_digest_max_bytes instead of reading it", () => {
		// A 2 GiB log copied into memory_evidence made readFileSync throw ERR_FS_FILE_TOO_LARGE.
		const runDir = freshRunDir("gate-digest-byte-cap");
		writeArtifactFile(runDir, "extraction_declaration.json", declaration);
		const big = join(runDir, "artifacts", "memory_evidence", "huge.log");
		mkdirSync(dirname(big), { recursive: true });
		writeFileSync(big, Buffer.alloc(config.gate_digest_max_bytes + 1, 0x61));
		const ctx = makeCtx("sure_onboard", runDir);
		const before = gateDigest(ctx, unit);
		expect(before).not.toBe(plainSha(runDir));
		// Same size, different bytes: never read, so the digest cannot move.
		writeFileSync(big, Buffer.alloc(config.gate_digest_max_bytes + 1, 0x62));
		expect(gateDigest(ctx, unit)).toBe(before);
		// A different size does move it.
		writeFileSync(big, Buffer.alloc(config.gate_digest_max_bytes + 2, 0x62));
		expect(gateDigest(ctx, unit)).not.toBe(before);
	});

	it("falls back to sha256(produces) when config.json carries no walk budgets", () => {
		// Without a config there is nothing to bound the walk with, so gateInputs are skipped
		// rather than walked unbounded; the value is the one every unit without gateInputs uses.
		const runDir = freshRunDir("gate-digest-no-config");
		writeArtifactFile(runDir, "extraction_declaration.json", declaration);
		writeArtifactFile(runDir, "candidates/01-a/proposal.json", '{"a":1}');
		const ctx = makeCtx("sure_onboard", runDir);
		expect(gateDigest(ctx, unit)).not.toBe(plainSha(runDir));
		const spy = vi.spyOn(matchModule, "loadMemoryConfig").mockImplementation(() => {
			throw new Error("memory config has an unknown schema");
		});
		try {
			expect(gateDigest(ctx, unit)).toBe(plainSha(runDir));
		} finally {
			spy.mockRestore();
		}
	});
});

describe("isExtractionGateExhausted", () => {
	const config = matchModule.loadMemoryConfig();

	it("uses extraction_gate_max_failures from config.json (2) when no user cap is given", () => {
		expect(config.extraction_gate_max_failures).toBe(2);
		expect(isExtractionGateExhausted(EXTRACT_LESSONS_UNIT_ID, 1, undefined, config)).toBe(false);
		expect(isExtractionGateExhausted(EXTRACT_LESSONS_UNIT_ID, 2, undefined, config)).toBe(true);
		expect(isExtractionGateExhausted(EXTRACT_LESSONS_UNIT_ID, 3, undefined, config)).toBe(true);
	});

	it("lets max_retries= raise the cap but never lower it", () => {
		expect(isExtractionGateExhausted(EXTRACT_LESSONS_UNIT_ID, 2, 5, config)).toBe(false);
		expect(isExtractionGateExhausted(EXTRACT_LESSONS_UNIT_ID, 5, 5, config)).toBe(true);
		expect(isExtractionGateExhausted(EXTRACT_LESSONS_UNIT_ID, 2, 1, config)).toBe(true);
		expect(isExtractionGateExhausted(EXTRACT_LESSONS_UNIT_ID, 2, Number.NaN, config)).toBe(true);
	});

	it("is false for every other unit no matter how many attempts", () => {
		expect(isExtractionGateExhausted("build_env", 99, undefined, config)).toBe(false);
		expect(isExtractionGateExhausted("run_report", 99, 1, config)).toBe(false);
	});
});
