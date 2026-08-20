import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { buildInvocationPrompt } from "../../src/core/sure/extension.ts";
import { NFS_ROOT, resolveOutputDir, stripOutputDir } from "../../src/core/sure/output-dir.ts";
import { SureRunManager } from "../../src/core/sure/run-manager.ts";
import type { SureRunRecord, SureSkillPackage } from "../../src/core/sure/types.ts";

function freshRoot(name: string): string {
	const root = resolve(__dirname, "tmp-run-output-dir", name);
	rmSync(root, { recursive: true, force: true });
	mkdirSync(root, { recursive: true });
	return root;
}

function skillPackage(): SureSkillPackage {
	return {
		manifest: { name: "sure_eval", command: "sure_eval" },
		packageDir: "/packages/sure_eval",
	} as never;
}

function readResult(outputDir: string): Record<string, unknown> {
	return JSON.parse(readFileSync(join(outputDir, "result.json"), "utf-8"));
}

describe("output_dir removal from skill arguments", () => {
	it("drops the requested directory", () => {
		expect(stripOutputDir("url=https://example.com/m output_dir=/jobs/job-1234")).toBe("url=https://example.com/m");
	});

	it("drops the spaced spelling", () => {
		expect(stripOutputDir("model=demo output_dir /jobs/job-1234 datasets=/ds/demo")).toBe(
			"model=demo datasets=/ds/demo",
		);
	});

	it("leaves arguments without output_dir alone", () => {
		expect(stripOutputDir("model=demo datasets=/ds/demo")).toBe("model=demo datasets=/ds/demo");
	});

	it("returns nothing when output_dir was the only argument", () => {
		expect(stripOutputDir("output_dir=/jobs/job-1234")).toBe("");
	});

	it("keeps the directory out of the prompt the agent reads", () => {
		const run = {
			runId: "20260817-000000-abcdefgh",
			cwd: "/repo",
			runDir: "/repo/.sure/runs/20260817-000000-abcdefgh",
			args: "url=https://example.com/m output_dir=/jobs/job-1234",
		} as SureRunRecord;

		const prompt = buildInvocationPrompt(skillPackage(), run);

		expect(prompt).toContain("User arguments: url=https://example.com/m");
		expect(prompt).not.toContain("/jobs/job-1234");
	});
});

describe("output_dir resolution", () => {
	it("returns no directory when the command did not ask for one", () => {
		const result = resolveOutputDir("model=demo datasets=/ds/demo");

		expect(result).toEqual({ ok: true, dir: undefined });
	});

	it("creates the requested directory", () => {
		const root = freshRoot("creates");
		const target = join(root, "job-1234");

		const result = resolveOutputDir(`model=demo output_dir=${target}`);

		expect(result).toEqual({ ok: true, dir: target });
		expect(existsSync(target)).toBe(true);
	});

	it("refuses a relative directory", () => {
		const result = resolveOutputDir("model=demo output_dir=jobs/job-1234");

		expect(result.ok).toBe(false);
		expect(result.error ?? "").toContain("absolute");
	});

	it("refuses a directory inside NFS", () => {
		const result = resolveOutputDir(`model=demo output_dir=${join(NFS_ROOT, "results", "job-1234")}`);

		expect(result.ok).toBe(false);
		expect(result.error ?? "").toContain(NFS_ROOT);
	});

	it("refuses a directory it cannot create", () => {
		const root = freshRoot("blocked");
		const blocker = join(root, "blocker");
		writeFileSync(blocker, "not a directory", "utf-8");

		const result = resolveOutputDir(`model=demo output_dir=${join(blocker, "job-1234")}`);

		expect(result.ok).toBe(false);
		expect(result.error ?? "").toContain("blocker");
	});
});

describe("result.json", () => {
	it("appears as soon as the run is created", () => {
		const root = freshRoot("created");
		const outputDir = join(root, "out");
		mkdirSync(outputDir, { recursive: true });
		const manager = new SureRunManager(join(root, "cwd"));

		const record = manager.createRun(skillPackage(), "model=demo", outputDir);

		const result = readResult(outputDir);
		expect(result.schema).toBe("sure.run_result.v1");
		expect(result.command).toBe("/sure_eval");
		expect(result.status).toBe("pending");
		expect(result.run_id).toBe(record.runId);
		expect(result.run_dir).toBe(record.runDir);
	});

	it("carries the terminal status and the failure reason", () => {
		const root = freshRoot("failed");
		const outputDir = join(root, "out");
		mkdirSync(outputDir, { recursive: true });
		const manager = new SureRunManager(join(root, "cwd"));
		const record = manager.createRun(skillPackage(), "model=demo", outputDir);

		manager.updateRun(record, { status: "failed", lastRepair: "dataset version is missing" }, "pre_start_repair");

		const result = readResult(outputDir);
		expect(result.status).toBe("failed");
		expect(result.error).toBe("dataset version is missing");
	});

	it("copies control artifacts into the output directory once the run is terminal", () => {
		const root = freshRoot("artifacts");
		const outputDir = join(root, "out");
		mkdirSync(outputDir, { recursive: true });
		const manager = new SureRunManager(join(root, "cwd"));
		const record = manager.createRun(skillPackage(), "model=demo", outputDir);
		writeFileSync(
			join(record.runDir, "artifacts", "main_agent_run_report.json"),
			'{"report_persisted":true}',
			"utf-8",
		);

		manager.setStatus(record, "success");

		expect(existsSync(join(outputDir, "artifacts", "main_agent_run_report.json"))).toBe(true);
	});

	it("stays absent while the run is still going", () => {
		const root = freshRoot("running");
		const outputDir = join(root, "out");
		mkdirSync(outputDir, { recursive: true });
		const manager = new SureRunManager(join(root, "cwd"));
		const record = manager.createRun(skillPackage(), "model=demo", outputDir);
		writeFileSync(join(record.runDir, "artifacts", "task_classification.json"), "{}", "utf-8");

		manager.setStatus(record, "running", "started");

		expect(readResult(outputDir).status).toBe("running");
		expect(existsSync(join(outputDir, "artifacts"))).toBe(false);
	});

	it("is not written when the run has no output directory", () => {
		const root = freshRoot("no-output-dir");
		const manager = new SureRunManager(join(root, "cwd"));

		manager.createRun(skillPackage(), "model=demo");

		expect(existsSync(join(root, "result.json"))).toBe(false);
	});
});
