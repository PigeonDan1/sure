import { randomUUID } from "node:crypto";
import { cpSync, existsSync, mkdirSync, readdirSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { delimiter, dirname, isAbsolute, join, resolve } from "node:path";
import {
	type CoreRunRecord,
	canonicalJsonDigest,
	type JsonValue,
	type ResumeBinding,
	RUN_ID_PATTERN,
	RunStoreError,
} from "@earendil-works/sure-core";
import { createNodeCoreRunStore, type NodeCoreRunStore } from "./core-run-store.ts";
import { mergeSureDisplayState } from "./state.ts";
import type { SureDisplayState, SureRunRecord, SureRunStatus, SureSkillPackage } from "./types.ts";

const SURE_RUNS_DIR = ".sure/runs";
const RESULT_FILE = "result.json";
const TERMINAL_RUN_STATUSES = new Set<SureRunStatus>(["success", "failed", "incomplete", "cancelled"]);
const COMPAT_CORE_VERSION = "sure-pi-compat-v1";

export interface SureRunManagerOptions {
	/** Version/name of the host-neutral core used for newly created records. */
	coreVersion?: string;
	workflowDigest?: string;
	validatorDigest?: string;
	executorDigest?: string;
	policyDigest?: string;
	bindingDigest?: string;
	/** Read-only roots that may never receive run/output writes. */
	referenceRoots?: readonly string[];
	/** Additional site-approved writable roots (output_dir is added per run). */
	writeRoots?: readonly string[];
}

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
	if (existsSync(outputDir)) walk(outputDir, "");
	return products;
}

function safeTimestamp(): string {
	return nowIso().replace(/[-:]/g, "").replace(/\..+$/, "").replace("T", "-");
}

function writeJsonAtomic(path: string, value: unknown): void {
	mkdirSync(dirname(path), { recursive: true });
	const temporary = `${path}.sure-tmp-${process.pid}-${randomUUID()}`;
	try {
		writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
		renameSync(temporary, path);
	} catch (error) {
		try {
			if (existsSync(temporary)) unlinkSync(temporary);
		} catch {
			// Preserve the original write/rename failure.
		}
		throw error;
	}
}

function isAbsolutePathList(values: readonly string[]): string[] {
	return values.filter((value) => isAbsolute(value));
}

function environmentReferenceRoots(): string[] {
	return [process.env.SURE_REFERENCE_ROOT, process.env.REFERENCE_ROOT]
		.flatMap((value) => (value ? value.split(delimiter) : []))
		.map((value) => value.trim())
		.filter((value) => value.length > 0 && isAbsolute(value));
}

function toSureRecord(record: CoreRunRecord): SureRunRecord {
	return record as unknown as SureRunRecord;
}

function updateRecordInPlace(target: SureRunRecord, source: SureRunRecord): void {
	// State writes advance the run revision even though the public method returns
	// only the display state. Keep the active extension record synchronized.
	Object.assign(target, source);
}

/**
 * Compatibility facade for the historical Pi run manager.
 *
 * The file names and public methods remain stable, while all authoritative run
 * and checkpoint mutations are delegated to the host-neutral CoreRunStore.
 */
export class SureRunManager {
	private readonly cwd: string;
	private readonly options: SureRunManagerOptions;

	constructor(cwd: string, options: SureRunManagerOptions = {}) {
		this.cwd = resolve(cwd);
		this.options = options;
	}

	get runsRoot(): string {
		return join(this.cwd, SURE_RUNS_DIR);
	}

	private referenceRoots(): string[] {
		return isAbsolutePathList([...(this.options.referenceRoots ?? []), ...environmentReferenceRoots()]);
	}

	private coreFor(extraWriteRoots: readonly string[] = []): NodeCoreRunStore {
		return createNodeCoreRunStore({
			rootDir: this.cwd,
			coreVersion: this.options.coreVersion ?? COMPAT_CORE_VERSION,
			referenceRoots: this.referenceRoots(),
			writeRoots: isAbsolutePathList([...(this.options.writeRoots ?? []), ...extraWriteRoots]),
		});
	}

	private bindingFor(skillPackage: SureSkillPackage): ResumeBinding {
		const definitionIdentity = JSON.parse(
			JSON.stringify({
				profile: "legacy-v1",
				skill: skillPackage.manifest.name,
				command: skillPackage.manifest.command,
				hooks: skillPackage.manifest.hooks ?? null,
				artifacts: skillPackage.manifest.artifacts ?? null,
			}),
		) as Record<string, unknown>;
		const digest = (kind: string): string =>
			canonicalJsonDigest({ kind, definition: definitionIdentity } as unknown as JsonValue);
		const workflowDigest = this.options.workflowDigest ?? digest("workflow");
		const validatorDigest = this.options.validatorDigest ?? digest("validator");
		const executorDigest = this.options.executorDigest ?? digest("executor");
		const policyDigest = this.options.policyDigest ?? digest("policy");
		return {
			coreVersion: this.options.coreVersion ?? COMPAT_CORE_VERSION,
			workflowDigest,
			validatorDigest,
			executorDigest,
			policyDigest,
			bindingDigest:
				this.options.bindingDigest ??
				canonicalJsonDigest({
					workflowDigest,
					validatorDigest,
					executorDigest,
					policyDigest,
				} as unknown as JsonValue),
		};
	}

	createRun(skillPackage: SureSkillPackage, args: string, outputDir?: string): SureRunRecord {
		const runId = `${safeTimestamp()}-${randomUUID().slice(0, 8)}`;
		const binding = this.bindingFor(skillPackage);
		const normalizedOutputDir = outputDir === undefined ? undefined : resolve(outputDir);
		const core = this.coreFor(normalizedOutputDir === undefined ? [] : [normalizedOutputDir]);
		const record = core.createRun({
			runId,
			skillName: skillPackage.manifest.name,
			command: skillPackage.manifest.command,
			cwd: this.cwd,
			packageDir: resolve(skillPackage.packageDir),
			args,
			...(normalizedOutputDir === undefined ? {} : { outputDir: normalizedOutputDir }),
			...binding,
		});
		const sureRecord = toSureRecord(record);
		// The historical facade did not materialize state.json until the first
		// checkpoint patch. Core keeps an empty state digest for new runs, but the
		// compatibility surface preserves that observable absence.
		const initialStatePath = join(sureRecord.runDir, "state.json");
		if (existsSync(initialStatePath)) unlinkSync(initialStatePath);
		this.writeResult(sureRecord);
		return sureRecord;
	}

	/** Run ids oldest first; the timestamp prefix makes the plain sort chronological. */
	listRunIds(): string[] {
		if (!existsSync(this.runsRoot)) return [];
		return readdirSync(this.runsRoot, { withFileTypes: true })
			.filter((entry) => entry.isDirectory() && RUN_ID_PATTERN.test(entry.name))
			.map((entry) => entry.name)
			.sort();
	}

	readRun(runId: string): SureRunRecord | undefined {
		const record = this.coreFor().readRun(runId);
		return record === undefined ? undefined : toSureRecord(record);
	}

	readState(record: SureRunRecord): SureDisplayState | undefined {
		const state = this.coreFor(record.outputDir === undefined ? [] : [record.outputDir]).readState(record.runId);
		return state as SureDisplayState | undefined;
	}

	updateRun(record: SureRunRecord, patch: Partial<SureRunRecord>, eventType: string, data?: unknown): SureRunRecord {
		const outputRoots = [record.outputDir, patch.outputDir].filter((value): value is string => value !== undefined);
		const next = this.coreFor(outputRoots).updateRun(
			record.runId,
			patch as Partial<CoreRunRecord>,
			eventType,
			data,
			record.revision,
		);
		const sureRecord = toSureRecord(next);
		this.writeResult(sureRecord);
		return sureRecord;
	}

	setStatus(record: SureRunRecord, status: SureRunStatus, eventType = "status"): SureRunRecord {
		// A few legacy script callers finalized immediately after createRun. Keep
		// that narrow facade behavior while Core itself retains the strict
		// pending -> running|failed transition graph.
		if (record.status === "pending" && status !== "failed" && TERMINAL_RUN_STATUSES.has(status)) {
			const started = this.updateRun(record, { status: "running" }, `${eventType}_start`, {
				status: "running",
				compatibility: true,
			});
			return this.updateRun(started, { status, finishedAt: nowIso() }, eventType, { status, compatibility: true });
		}
		const finishedAt = TERMINAL_RUN_STATUSES.has(status) ? nowIso() : undefined;
		return this.updateRun(record, { status, ...(finishedAt === undefined ? {} : { finishedAt }) }, eventType, {
			status,
		});
	}

	updateState(record: SureRunRecord, patch: SureDisplayState, eventType = "state_patch"): SureDisplayState {
		const state = mergeSureDisplayState(this.readState(record), patch);
		const next = this.coreFor(record.outputDir === undefined ? [] : [record.outputDir]).writeState(
			record.runId,
			state as Record<string, unknown>,
			eventType,
			{ patch, state },
			record.revision,
		);
		updateRecordInPlace(record, toSureRecord(next));
		return state;
	}

	/** Resume a failed run only after its canonical binding matches the current skill. */
	resumeRun(record: SureRunRecord, skillPackage: SureSkillPackage): SureRunRecord {
		const binding = this.bindingFor(skillPackage);
		const core = this.coreFor(record.outputDir === undefined ? [] : [record.outputDir]);
		if (record.status !== "failed") this.assertBinding(record, binding);
		const next =
			record.status === "failed"
				? core.resumeRun(record.runId, binding)
				: core.updateRun(
						record.runId,
						{ status: "running", finishedAt: undefined, staleSince: undefined },
						"resumed",
						{ binding },
						record.revision,
					);
		const sureRecord = toSureRecord(next);
		this.writeResult(sureRecord);
		return sureRecord;
	}

	private assertBinding(record: SureRunRecord, binding: ResumeBinding): void {
		for (const key of [
			"coreVersion",
			"workflowDigest",
			"validatorDigest",
			"executorDigest",
			"policyDigest",
		] as const) {
			if (record[key] !== binding[key]) {
				throw new RunStoreError(
					record.legacyCompatibility ? "UPGRADE_REQUIRED" : "CONFLICT",
					`Run ${record.runId} binding ${key} does not match the current registry.`,
				);
			}
		}
		if (binding.bindingDigest !== undefined && record.bindingDigest !== binding.bindingDigest) {
			throw new RunStoreError(
				record.legacyCompatibility ? "UPGRADE_REQUIRED" : "CONFLICT",
				`Run ${record.runId} binding bindingDigest does not match the current registry.`,
			);
		}
	}

	resolveRunPath(record: SureRunRecord, pathValue: string): string | undefined {
		return this.coreFor(record.outputDir === undefined ? [] : [record.outputDir]).resolveRunPath(
			record.runId,
			pathValue,
		);
	}

	// Callers that drive Sure from a script read one directory per invocation,
	// so every status change republishes result.json there. Products of a
	// finished run are copied next to it.
	private writeResult(record: SureRunRecord): void {
		const outputDir = record.outputDir;
		if (!outputDir) return;
		const core = this.coreFor([outputDir]);
		const admittedOutput = core.admitPath(outputDir, [outputDir]).path;
		mkdirSync(admittedOutput, { recursive: true });
		if (TERMINAL_RUN_STATUSES.has(record.status)) {
			const artifactsDir = join(record.runDir, "artifacts");
			if (existsSync(artifactsDir)) {
				core.admitPath(artifactsDir, [record.runDir]);
				const destination = join(admittedOutput, "artifacts");
				core.admitPath(destination, [admittedOutput]);
				cpSync(artifactsDir, destination, { recursive: true });
			}
		}
		writeJsonAtomic(join(admittedOutput, RESULT_FILE), {
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
			products: listProducts(admittedOutput),
		});
	}
}
