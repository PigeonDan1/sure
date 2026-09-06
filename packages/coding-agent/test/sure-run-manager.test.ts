import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { RunStoreConflictError, RunStoreError } from "@earendil-works/sure-core";
import { afterEach, describe, expect, it } from "vitest";
import { SureRunManager } from "../src/core/sure/run-manager.ts";
import type { SureRunRecord, SureSkillPackage } from "../src/core/sure/types.ts";

const roots: string[] = [];

function skillPackage(root: string): SureSkillPackage {
	const packageDir = join(root, "sure", "skills", "sure_feed");
	mkdirSync(packageDir, { recursive: true });
	return {
		manifest: { name: "sure_feed", command: "sure_feed", prompt: "" },
		manifestPath: join(packageDir, "sure.skill.json"),
		packageDir,
		promptPath: join(packageDir, "SKILL.md"),
		prompt: "",
		source: "repository",
		sourceRoot: root,
	};
}

function freshRoot(): string {
	const root = mkdtempSync(join(tmpdir(), "sure-run-manager-"));
	roots.push(root);
	return root;
}

afterEach(() => {
	for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe("SureRunManager Core facade", () => {
	it("keeps the legacy files while enforcing revision CAS", () => {
		const root = freshRoot();
		const skill = skillPackage(root);
		const first = new SureRunManager(root);
		const second = new SureRunManager(root);
		const created = first.createRun(skill, "topic=demo");
		const running = first.setStatus(created, "running", "started");
		const stale = second.readRun(created.runId) as SureRunRecord;
		const current = first.updateRun(running, { lastRepair: "first writer" }, "repair");

		expect(current.revision).toBe(2);
		expect(() => second.updateRun(stale, { lastRepair: "stale writer" }, "repair")).toThrow(RunStoreConflictError);
		expect(existsSync(join(created.runDir, "run.json"))).toBe(true);
		expect(existsSync(join(created.runDir, "events.jsonl"))).toBe(true);
	});

	it("rejects lexical escapes, symlink escapes, and reference-root output", () => {
		const root = freshRoot();
		const reference = join(root, "reference");
		const outside = mkdtempSync(join(tmpdir(), "sure-run-manager-outside-"));
		roots.push(outside);
		mkdirSync(reference, { recursive: true });
		mkdirSync(outside, { recursive: true });
		const manager = new SureRunManager(root, { referenceRoots: [reference] });
		const record = manager.createRun(skillPackage(root), "topic=demo");

		expect(manager.resolveRunPath(record, "../outside/file")).toBeUndefined();
		expect(manager.resolveRunPath(record, join(outside, "file"))).toBeUndefined();
		symlinkSync(outside, join(root, "escape"), "dir");
		expect(manager.resolveRunPath(record, "escape/file")).toBeUndefined();

		const referenceOutput = join(reference, "results", "job-1");
		expect(() => manager.createRun(skillPackage(root), "topic=reference", referenceOutput)).toThrow(RunStoreError);
		expect(existsSync(join(referenceOutput, "result.json"))).toBe(false);
	});

	it("requires the same binding when resuming a failed run", () => {
		const root = freshRoot();
		const skill = skillPackage(root);
		const manager = new SureRunManager(root);
		const created = manager.createRun(skill, "topic=demo");
		const running = manager.setStatus(created, "running", "started");
		manager.updateState(running, { checkpoint: { id: "main_flow", resumable: true } });
		const failed = manager.setStatus(running, "failed", "stopped");

		const changed = new SureRunManager(root, { workflowDigest: "sha256:changed" });
		try {
			changed.resumeRun(failed, skill);
			throw new Error("expected resume binding rejection");
		} catch (error) {
			expect(error).toBeInstanceOf(RunStoreError);
			expect((error as RunStoreError).code).toBe("CONFLICT");
		}

		const resumed = manager.resumeRun(failed, skill);
		expect(resumed.status).toBe("running");
		expect(resumed.runId).toBe(created.runId);
	});

	it("does not copy a symlinked run artifact directory into a terminal output", () => {
		const root = freshRoot();
		const output = join(root, "output");
		const outside = join(root, "outside-artifacts");
		mkdirSync(outside, { recursive: true });
		writeFileSync(join(outside, "secret.json"), "secret\n", "utf8");
		const manager = new SureRunManager(root);
		const created = manager.createRun(skillPackage(root), "topic=demo", output);
		const running = manager.setStatus(created, "running", "started");
		rmSync(join(created.runDir, "artifacts"), { recursive: true, force: true });
		symlinkSync(outside, join(created.runDir, "artifacts"), "dir");

		expect(() => manager.setStatus(running, "failed", "stopped")).toThrow(RunStoreError);
		expect(existsSync(join(output, "artifacts", "secret.json"))).toBe(false);
		expect(readFileSync(join(outside, "secret.json"), "utf8")).toBe("secret\n");
	});
});
