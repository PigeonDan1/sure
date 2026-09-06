import { describe, expect, it } from "vitest";
import { CoreRunStore, RunStoreConflictError, RunStoreError } from "../src/run/store.ts";
import type { ResumeBinding, RunStoreFileSystem, RunStoreLock, StateDocument } from "../src/run/types.ts";

class MemoryFileSystem implements RunStoreFileSystem {
	readonly files = new Map<string, string>();
	readonly directories = new Set<string>(["/"]);
	readonly symlinks = new Map<string, string>();

	mkdir(path: string): void {
		const normalized = this.normalize(path);
		const parts = normalized.split("/").filter(Boolean);
		let current = "";
		for (const part of parts) {
			current += `/${part}`;
			this.directories.add(current);
		}
	}

	readFile(path: string): string | undefined {
		return this.files.get(this.normalize(path));
	}

	writeFileAtomic(path: string, content: string): void {
		const normalized = this.normalize(path);
		this.mkdir(this.parent(normalized));
		this.files.set(normalized, content);
	}

	appendLine(path: string, line: string): void {
		const normalized = this.normalize(path);
		this.mkdir(this.parent(normalized));
		this.files.set(normalized, `${this.files.get(normalized) ?? ""}${line}`);
	}

	exists(path: string): boolean {
		const normalized = this.normalize(path);
		return this.files.has(normalized) || this.directories.has(normalized) || this.symlinks.has(normalized);
	}

	realpath(path: string): string | undefined {
		const normalized = this.normalize(path);
		if (!this.exists(normalized)) return undefined;
		let candidate = normalized;
		const visited = new Set<string>();
		while (true) {
			if (visited.has(candidate)) return undefined;
			visited.add(candidate);
			const match = [...this.symlinks.entries()]
				.filter(([source]) => candidate === source || candidate.startsWith(`${source}/`))
				.sort((left, right) => right[0].length - left[0].length)[0];
			if (!match) return candidate;
			const suffix = candidate.slice(match[0].length).replace(/^\//, "");
			candidate = this.normalize(suffix ? `${match[1]}/${suffix}` : match[1]);
		}
	}

	symlink(source: string, target: string): void {
		this.symlinks.set(this.normalize(source), this.normalize(target));
		this.mkdir(this.parent(this.normalize(source)));
	}

	private normalize(path: string): string {
		const parts: string[] = [];
		for (const part of path.replaceAll("\\", "/").split("/")) {
			if (!part || part === ".") continue;
			if (part === "..") {
				parts.pop();
			} else {
				parts.push(part);
			}
		}
		return `/${parts.join("/")}`;
	}

	private parent(path: string): string {
		const index = path.lastIndexOf("/");
		return index <= 0 ? "/" : path.slice(0, index);
	}
}

class MemoryLock implements RunStoreLock {
	withLock<T>(_key: string, operation: () => T): T {
		return operation();
	}
}

const BINDING: ResumeBinding = {
	coreVersion: "core-1",
	workflowDigest: "workflow-1",
	validatorDigest: "validator-1",
	executorDigest: "executor-1",
	policyDigest: "policy-1",
	bindingDigest: "binding-1",
};

function makeStore(filesystem = new MemoryFileSystem()): { store: CoreRunStore; filesystem: MemoryFileSystem } {
	filesystem.mkdir("/workspace");
	filesystem.mkdir("/nfs/reference");
	const store = new CoreRunStore({
		rootDir: "/workspace",
		filesystem,
		lock: new MemoryLock(),
		coreVersion: BINDING.coreVersion,
		referenceRoots: ["/nfs/reference"],
		clock: (() => {
			let tick = 0;
			return () => `2026-09-06T00:00:0${tick++}.000Z`;
		})(),
	});
	return { store, filesystem };
}

function createInput(runId = "run-1") {
	return {
		runId,
		skillName: "sure_infer",
		command: "/sure_infer",
		cwd: "/workspace",
		packageDir: "/workspace/sure/skills/sure_infer",
		args: "model=demo",
		outputDir: "/workspace/results/run-1",
		...BINDING,
	};
}

function checkpointState(resumable: boolean): StateDocument {
	return {
		checkpoint: {
			id: "main_flow",
			resumable,
			data: { currentUnit: "dataset_scope", completedUnits: [], retries: {} },
		},
	};
}

describe("CoreRunStore", () => {
	it("creates legacy-compatible files and monotonically revises mutations", () => {
		const { store, filesystem } = makeStore();
		const created = store.createRun(createInput());
		expect(created.status).toBe("pending");
		expect(created.revision).toBe(0);
		expect(filesystem.exists("/workspace/.sure/runs/run-1/run.json")).toBe(true);
		expect(filesystem.exists("/workspace/.sure/runs/run-1/state.json")).toBe(true);
		expect(filesystem.exists("/workspace/.sure/runs/run-1/events.jsonl")).toBe(true);

		const running = store.setStatus("run-1", "running", "started", 0);
		expect(running.revision).toBe(1);
		const current = store.readRun("run-1");
		expect(current?.status).toBe("running");
		expect(current?.workflowDigest).toBe(BINDING.workflowDigest);
		expect(() => store.setStatus("run-1", "success", "stale", 0)).toThrow(RunStoreConflictError);
	});

	it("enforces the run status graph and strict success evidence", () => {
		const { store, filesystem } = makeStore();
		store.createRun(createInput());
		expect(() => store.setStatus("run-1", "success")).toThrow(/pending to success/);
		store.setStatus("run-1", "running", "started");
		expect(() => store.updateRun("run-1", { outputDir: "/nfs/reference/late" }, "bad-output")).toThrow(
			/read-only reference root/i,
		);
		store.writeState("run-1", checkpointState(true), "checkpoint");
		filesystem.writeFileAtomic("/workspace/.sure/runs/run-1/artifacts/manifest.json", "{}\n");
		store.setStatus("run-1", "failed", "failed");
		expect(() =>
			store.finalizeRun("run-1", "success", {
				terminalCheckpoint: false,
				requiredArtifacts: ["manifest.json"],
				successReceipt: true,
				successReceiptDigest: `sha256:${"e".repeat(64)}`,
			}),
		).toThrow("terminal checkpoint");
		const resumed = store.resumeRun("run-1", BINDING);
		expect(resumed.status).toBe("running");
		store.writeState("run-1", checkpointState(false), "terminal");
		store.setStatus("run-1", "failed", "failed-again");
		expect(() =>
			store.finalizeRun("run-1", "success", {
				terminalCheckpoint: true,
				requiredArtifacts: ["manifest.json"],
				successReceipt: true,
				successReceiptDigest: `sha256:${"e".repeat(64)}`,
			}),
		).toThrow("failed to success");
	});

	it("allows only matching failed-run bindings to resume", () => {
		const { store } = makeStore();
		store.createRun(createInput());
		store.setStatus("run-1", "running", "started");
		store.writeState("run-1", checkpointState(true));
		store.setStatus("run-1", "failed", "failed");
		expect(store.checkResume("run-1", { ...BINDING, policyDigest: "changed" })).toMatchObject({
			allowed: false,
			code: "CONFLICT",
		});
		expect(store.checkResume("run-1", BINDING)).toMatchObject({ allowed: true });
		const resumed = store.resumeRun("run-1", BINDING);
		expect(resumed.status).toBe("running");
		expect(() => store.resumeRun("run-1", BINDING)).toThrow(/Only failed runs can resume/);
	});

	it("does not resume a snapshot-bound run through an unbound caller", () => {
		const { store, filesystem } = makeStore();
		const snapshotPath = "/workspace/.sure/runs/run-snapshot/artifacts/site_policy.resolved.json";
		store.createRun({
			...createInput("run-snapshot"),
			policySnapshotDigest: `sha256:${"f".repeat(64)}`,
			policySnapshotPath: snapshotPath,
		});
		store.setStatus("run-snapshot", "running", "started");
		store.writeState("run-snapshot", checkpointState(true));
		store.setStatus("run-snapshot", "failed", "failed");
		filesystem.writeFileAtomic(snapshotPath, "{}\n");
		const { policySnapshotDigest: _ignored, ...unbound } = BINDING;
		expect(store.checkResume("run-snapshot", unbound)).toMatchObject({
			allowed: false,
			code: "CONFLICT",
		});
	});

	it("rejects traversal, reference roots, and symlink escapes while recording local diagnostics", () => {
		const { store, filesystem } = makeStore();
		for (const runId of ["../escape", "/absolute", "a/b", "..", "bad\\name"]) {
			expect(() => store.createRun(createInput(runId))).toThrow(RunStoreError);
		}
		expect(() => store.createRun({ ...createInput("nfs-run"), outputDir: "/nfs/reference/output" })).toThrow(
			/read-only reference root/i,
		);
		filesystem.symlink("/workspace/link", "/nfs/reference");
		expect(() => store.admitPath("/workspace/link/generated")).toThrow(/escapes|reference/i);
		const diagnostics = filesystem.readFile("/workspace/.sure/diagnostics/events.jsonl") ?? "";
		expect(diagnostics).toContain("path_admission_rejected");
		expect(diagnostics).not.toContain("/nfs/reference/output/result.json");
	});

	it("reads old run records and marks missing bindings as upgrade-required", () => {
		const { store, filesystem } = makeStore();
		store.createRun(createInput());
		store.setStatus("run-1", "running", "started");
		store.writeState("run-1", checkpointState(false));
		store.setStatus("run-1", "failed", "failed");
		const path = "/workspace/.sure/runs/run-1/run.json";
		const old = JSON.parse(filesystem.readFile(path) ?? "{}") as Record<string, unknown>;
		delete old.coreVersion;
		delete old.workflowDigest;
		delete old.validatorDigest;
		delete old.executorDigest;
		delete old.policyDigest;
		filesystem.writeFileAtomic(path, `${JSON.stringify(old)}\n`);
		const read = store.readRun("run-1");
		expect(read?.legacyCompatibility).toBe(true);
		expect(store.checkResume("run-1", BINDING)).toMatchObject({ allowed: false, code: "UPGRADE_REQUIRED" });
	});

	it("permits a fully evidenced success only once", () => {
		const { store, filesystem } = makeStore();
		store.createRun(createInput());
		store.setStatus("run-1", "running", "started");
		store.writeState("run-1", checkpointState(false));
		filesystem.writeFileAtomic("/workspace/.sure/runs/run-1/artifacts/manifest.json", "{}\n");
		const success = store.finalizeRun("run-1", "success", {
			terminalCheckpoint: true,
			requiredArtifacts: ["manifest.json"],
			successReceipt: true,
			successReceiptDigest: `sha256:${"e".repeat(64)}`,
		});
		expect(success.status).toBe("success");
		expect(() => store.setStatus("run-1", "running")).toThrow(/success to running/);
		expect(() => store.writeState("run-1", checkpointState(false))).toThrow(/terminal/);
		expect(() => store.appendEvent("run-1", "late-mutating-event")).toThrow(/terminal/);
	});
});
