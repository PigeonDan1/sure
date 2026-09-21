import { randomUUID } from "node:crypto";
import {
	appendFileSync,
	cpSync,
	existsSync,
	mkdirSync,
	readdirSync,
	readFileSync,
	renameSync,
	writeFileSync,
} from "node:fs";
import { isAbsolute, join, relative, resolve } from "node:path";
import { mergeSureDisplayState } from "./state.ts";
import type { SureDisplayState, SureRunRecord, SureRunStatus, SureSkillPackage } from "./types.ts";

const SURE_RUNS_DIR = ".sure/runs";
const RESULT_FILE = "result.json";
const TERMINAL_RUN_STATUSES = new Set<SureRunStatus>(["success", "failed", "incomplete", "cancelled"]);

function nowIso(): string {
	return new Date().toISOString();
}

// Everything the run put in the output directory, result.json aside, as paths
// relative to that directory.
function listProducts(outputDir: string): string[] {
	const products: string[] = [];
	const walk = (dir: string, prefix: string): void => {
		for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
			const relPath = prefix ? `${prefix}/${entry.name}` : entry.name;
			if (entry.isDirectory()) {
				walk(join(dir, entry.name), relPath);
			} else if (relPath !== RESULT_FILE) {
				products.push(relPath);
			}
		}
	};
	walk(outputDir, "");
	return products;
}

function safeTimestamp(): string {
	return nowIso().replace(/[-:]/g, "").replace(/\..+$/, "").replace("T", "-");
}

// A run's record is rewritten on every tool call, so a hard kill during the
// write is what leaves a zero-byte run.json behind. Publish through a rename
// inside the same directory: the file is either the old one or the new one.
function writeJson(path: string, value: unknown): void {
	const tmpPath = `${path}.sure-run-tmp`;
	writeFileSync(tmpPath, `${JSON.stringify(value, null, 2)}\n`, "utf-8");
	renameSync(tmpPath, path);
}

// One unreadable file must not answer with a bare "Unexpected end of JSON
// input": /sure_resume walks every run, so the user has to be told which file
// to look at. Never repaired here — the record may still be recoverable.
function readJson<T>(path: string, description: string): T {
	const raw = readFileSync(path, "utf-8");
	try {
		return JSON.parse(raw) as T;
	} catch (error) {
		const detail = error instanceof Error ? error.message : String(error);
		throw new Error(
			`Sure ${description} is corrupt: ${path} (${detail}). Inspect or move that file aside, then retry.`,
		);
	}
}

function isPathInside(baseDir: string, candidate: string): boolean {
	const rel = relative(baseDir, candidate);
	return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel) && rel !== "..");
}

export interface SureRunEvent {
	type: string;
	timestamp: string;
	data?: unknown;
}

export class SureRunManager {
	private cwd: string;

	constructor(cwd: string) {
		this.cwd = cwd;
	}

	get runsRoot(): string {
		return join(this.cwd, SURE_RUNS_DIR);
	}

	createRun(skillPackage: SureSkillPackage, args: string, outputDir?: string): SureRunRecord {
		const runId = `${safeTimestamp()}-${randomUUID().slice(0, 8)}`;
		const runDir = join(this.runsRoot, runId);
		mkdirSync(runDir, { recursive: true });
		mkdirSync(join(runDir, "logs"), { recursive: true });
		mkdirSync(join(runDir, "artifacts"), { recursive: true });

		const record: SureRunRecord = {
			runId,
			skillName: skillPackage.manifest.name,
			command: skillPackage.manifest.command,
			status: "pending",
			cwd: this.cwd,
			packageDir: skillPackage.packageDir,
			runDir,
			args,
			outputDir,
			startedAt: nowIso(),
			updatedAt: nowIso(),
		};
		this.writeRecord(record);
		this.appendEvent(record.runId, { type: "created", timestamp: nowIso(), data: record });
		return record;
	}

	/** Run ids oldest first; the timestamp prefix makes the plain sort chronological. */
	listRunIds(): string[] {
		if (!existsSync(this.runsRoot)) {
			return [];
		}
		return readdirSync(this.runsRoot, { withFileTypes: true })
			.filter((entry) => entry.isDirectory())
			.map((entry) => entry.name)
			.sort();
	}

	readRun(runId: string): SureRunRecord | undefined {
		const runPath = join(this.runsRoot, runId, "run.json");
		if (!existsSync(runPath)) {
			return undefined;
		}
		return readJson<SureRunRecord>(runPath, "run record");
	}

	readState(record: SureRunRecord): SureDisplayState | undefined {
		const statePath = join(record.runDir, "state.json");
		if (!existsSync(statePath)) {
			return undefined;
		}
		return readJson<SureDisplayState>(statePath, "display state");
	}

	updateRun(record: SureRunRecord, patch: Partial<SureRunRecord>, eventType: string, data?: unknown): SureRunRecord {
		const next: SureRunRecord = {
			...record,
			...patch,
			updatedAt: nowIso(),
		};
		this.writeRecord(next);
		this.appendEvent(next.runId, { type: eventType, timestamp: nowIso(), data });
		return next;
	}

	setStatus(record: SureRunRecord, status: SureRunStatus, eventType = "status"): SureRunRecord {
		const finishedAt =
			status === "success" || status === "failed" || status === "incomplete" || status === "cancelled"
				? nowIso()
				: record.finishedAt;
		return this.updateRun(record, { status, finishedAt }, eventType, { status });
	}

	updateState(record: SureRunRecord, patch: SureDisplayState, eventType = "state_patch"): SureDisplayState {
		const state = mergeSureDisplayState(this.readState(record), patch);
		this.writeState(record, state);
		this.appendEvent(record.runId, { type: eventType, timestamp: nowIso(), data: { patch, state } });
		return state;
	}

	resolveRunPath(record: SureRunRecord, pathValue: string): string | undefined {
		const resolved = isAbsolute(pathValue) ? resolve(pathValue) : resolve(record.cwd, pathValue);
		const allowed = isPathInside(record.cwd, resolved) || isPathInside(record.runDir, resolved);
		return allowed ? resolved : undefined;
	}

	private writeRecord(record: SureRunRecord): void {
		mkdirSync(record.runDir, { recursive: true });
		writeJson(join(record.runDir, "run.json"), record);
		this.writeResult(record);
	}

	// Callers that drive Sure from a script read one directory per invocation,
	// so every status change republishes result.json there. Products of a
	// finished run are copied next to it.
	private writeResult(record: SureRunRecord): void {
		const outputDir = record.outputDir;
		if (!outputDir) {
			return;
		}
		mkdirSync(outputDir, { recursive: true });
		if (TERMINAL_RUN_STATUSES.has(record.status)) {
			const artifactsDir = join(record.runDir, "artifacts");
			if (existsSync(artifactsDir)) {
				cpSync(artifactsDir, join(outputDir, "artifacts"), { recursive: true });
			}
		}
		writeJson(join(outputDir, RESULT_FILE), {
			schema: "sure.run_result.v1",
			command: `/${record.command.replace(/^\//, "")}`,
			args: record.args,
			run_id: record.runId,
			run_dir: record.runDir,
			status: record.status,
			started_at: record.startedAt,
			updated_at: record.updatedAt,
			finished_at: record.finishedAt,
			stale_since: record.staleSince,
			error: record.lastRepair ?? record.errorSummary,
			products: listProducts(outputDir),
		});
	}

	private writeState(record: SureRunRecord, state: SureDisplayState): void {
		mkdirSync(record.runDir, { recursive: true });
		writeJson(join(record.runDir, "state.json"), state);
	}

	private appendEvent(runId: string, event: SureRunEvent): void {
		const runDir = join(this.runsRoot, runId);
		mkdirSync(runDir, { recursive: true });
		appendFileSync(join(runDir, "events.jsonl"), `${JSON.stringify(event)}\n`, "utf-8");
	}
}
