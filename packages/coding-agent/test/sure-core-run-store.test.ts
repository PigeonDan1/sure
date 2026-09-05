import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { RunStoreConflictError, RunStoreError } from "@earendil-works/sure-core";
import { afterEach, describe, expect, it } from "vitest";
import { createNodeCoreRunStore } from "../src/core/sure/core-run-store.ts";

const binding = {
	coreVersion: "node-core-1",
	workflowDigest: "workflow-1",
	validatorDigest: "validator-1",
	executorDigest: "executor-1",
	policyDigest: "policy-1",
	bindingDigest: "binding-1",
};

const roots: string[] = [];

function storeFor(root: string) {
	const reference = join(root, "reference");
	mkdirSync(reference, { recursive: true });
	return createNodeCoreRunStore({
		rootDir: root,
		referenceRoots: [reference],
		coreVersion: binding.coreVersion,
		clock: () => "2026-09-06T00:00:00.000Z",
	});
}

function input(runId = "run-node-1") {
	return {
		runId: runId,
		skillName: "sure_infer",
		command: "/sure_infer",
		cwd: roots[0] ?? "/tmp",
		packageDir: join(roots[0] ?? "/tmp", "sure", "skills", "sure_infer"),
		args: "model=demo",
		...binding,
	};
}

afterEach(() => {
	for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe("NodeCoreRunStore adapter", () => {
	it("uses real atomic files and proper-lockfile CAS", () => {
		const root = mkdtempSync(join(tmpdir(), "sure-core-store-"));
		roots.push(root);
		const first = storeFor(root);
		const second = storeFor(root);
		const created = first.createRun({ ...input(), outputDir: join(root, "output") });
		first.setStatus(created.runId, "running", "started", created.revision);
		expect(() => second.setStatus(created.runId, "failed", "stale", created.revision)).toThrow(RunStoreConflictError);
		expect(first.readRun(created.runId)?.status).toBe("running");
		const events = readFileSync(join(created.runDir, "events.jsonl"), "utf8").trim().split("\n");
		expect(events).toHaveLength(2);
		expect(JSON.parse(events[1] ?? "{}").revision).toBe(1);
	});

	it("rejects symlink/reference writes and keeps the reference tree untouched", () => {
		const root = mkdtempSync(join(tmpdir(), "sure-core-store-"));
		roots.push(root);
		const store = storeFor(root);
		const reference = join(root, "reference");
		const outside = join(root, "outside");
		mkdirSync(outside, { recursive: true });
		symlinkSync(reference, join(root, "link"));
		expect(() => store.admitPath(join(root, "link", "generated"))).toThrow(RunStoreError);
		expect(() => store.createRun({ ...input("nfs-node"), outputDir: join(reference, "out") })).toThrow(
			/read-only reference/i,
		);
		expect(readFileSync(join(root, ".sure", "diagnostics", "events.jsonl"), "utf8")).toContain(
			"path_admission_rejected",
		);
		expect(readFileSync(join(root, ".sure", "diagnostics", "events.jsonl"), "utf8")).not.toContain(
			"reference/out/result",
		);
	});

	it("writes and reads a terminal success with exact artifact scope", () => {
		const root = mkdtempSync(join(tmpdir(), "sure-core-store-"));
		roots.push(root);
		const store = storeFor(root);
		const created = store.createRun(input());
		store.setStatus(created.runId, "running");
		const artifacts = join(created.runDir, "artifacts");
		writeFileSync(join(artifacts, "manifest.json"), "{}\n");
		store.writeState(created.runId, {
			checkpoint: { resumable: false, data: { currentUnit: "last", completedUnits: ["last"], retries: {} } },
		});
		const finished = store.finalizeRun(created.runId, "success", {
			terminalCheckpoint: true,
			requiredArtifacts: ["manifest.json"],
			successReceipt: true,
			successReceiptDigest: `sha256:${"e".repeat(64)}`,
		});
		expect(finished.status).toBe("success");
		expect(store.resolveRunPath(created.runId, "../outside")).toBeUndefined();
	});
});
