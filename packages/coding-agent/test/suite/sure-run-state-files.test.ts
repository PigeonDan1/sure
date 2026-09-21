import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SureRunRecord, SureSkillPackage } from "../../src/core/sure/types.ts";

// writeJson has to leave run.json/state.json either whole or untouched: a hard
// kill during the in-place write is what leaves a zero-byte file behind, and a
// single one of those used to take out /sure_resume for the whole project.
const spies = vi.hoisted(() => ({ writeFileSync: vi.fn(), renameSync: vi.fn() }));

vi.mock("node:fs", async (importOriginal) => {
	const actual = await importOriginal<typeof import("node:fs")>();
	spies.writeFileSync.mockImplementation(actual.writeFileSync);
	spies.renameSync.mockImplementation(actual.renameSync);
	return { ...actual, writeFileSync: spies.writeFileSync, renameSync: spies.renameSync };
});

const { SureRunManager } = await import("../../src/core/sure/run-manager.ts");

const skillPackage = {
	manifest: { name: "sure_eval", command: "sure_eval" },
	packageDir: "/packages/sure_eval",
} as unknown as SureSkillPackage;

function freshRoot(name: string): string {
	const root = resolve(__dirname, "tmp-run-state-files", name);
	rmSync(root, { recursive: true, force: true });
	mkdirSync(root, { recursive: true });
	return root;
}

describe("Sure run state files", () => {
	beforeEach(() => {
		spies.writeFileSync.mockClear();
		spies.renameSync.mockClear();
	});

	it("publishes run.json by rename instead of truncating it in place", () => {
		const manager = new SureRunManager(freshRoot("atomic-run"));
		const record = manager.createRun(skillPackage, "model=demo");
		const runPath = join(record.runDir, "run.json");

		expect(spies.writeFileSync.mock.calls.some(([path]) => path === runPath)).toBe(false);
		const rename = spies.renameSync.mock.calls.find(([, to]) => to === runPath);
		expect(rename).toBeDefined();
		// Rename is only atomic within one directory.
		expect(dirname(String(rename?.[0]))).toBe(record.runDir);
		expect(manager.readRun(record.runId)?.runId).toBe(record.runId);
	});

	it("publishes state.json by rename instead of truncating it in place", () => {
		const manager = new SureRunManager(freshRoot("atomic-state"));
		const record = manager.createRun(skillPackage, "model=demo");
		manager.updateState(record, { message: "working" });
		const statePath = join(record.runDir, "state.json");

		expect(spies.writeFileSync.mock.calls.some(([path]) => path === statePath)).toBe(false);
		expect(spies.renameSync.mock.calls.some(([, to]) => to === statePath)).toBe(true);
		expect(manager.readState(record)?.message).toBe("working");
	});

	it("names the run record when run.json cannot be parsed", () => {
		const manager = new SureRunManager(freshRoot("corrupt-run"));
		const record = manager.createRun(skillPackage, "model=demo");
		// What a hard kill during a write leaves behind.
		writeFileSync(join(record.runDir, "run.json"), "", "utf-8");

		expect(() => manager.readRun(record.runId)).toThrowError(/run\.json/);
	});

	it("names the display state when state.json cannot be parsed", () => {
		const manager = new SureRunManager(freshRoot("corrupt-state"));
		const record = manager.createRun(skillPackage, "model=demo") as SureRunRecord;
		writeFileSync(join(record.runDir, "state.json"), "{", "utf-8");

		expect(() => manager.readState(record)).toThrowError(/state\.json/);
	});
});
