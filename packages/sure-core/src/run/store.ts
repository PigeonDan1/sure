import { basename, dirname, isAbsolute, join, normalize, relative, resolve } from "node:path/posix";
import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import { evaluatePathBoundary, type ResolvedRoot } from "../contracts/path-boundary.ts";
import type { JsonValue } from "../contracts/types.ts";
import type {
	CoreRunRecord,
	CreateRunInput,
	ResumeBinding,
	RunEvent,
	RunStatus,
	RunStoreFileSystem,
	RunStoreLock,
	RunStoreOptions,
	StateDocument,
	SuccessEvidence,
} from "./types.ts";

export type RunStoreErrorCode =
	| "INVALID_RUN_ID"
	| "PATH_OUT_OF_SCOPE"
	| "SYMLINK_ESCAPE"
	| "READ_ONLY_REFERENCE"
	| "INVALID_CONTRACT"
	| "INVALID_RECORD"
	| "CONFLICT"
	| "INVALID_STATUS_TRANSITION"
	| "TERMINAL_IMMUTABLE"
	| "UPGRADE_REQUIRED"
	| "CHECKPOINT_NOT_RESUMABLE"
	| "SUCCESS_EVIDENCE_MISSING"
	| "ALREADY_EXISTS";

export class RunStoreError extends Error {
	readonly code: RunStoreErrorCode;
	readonly path?: string;

	constructor(code: RunStoreErrorCode, message: string, path?: string) {
		super(message);
		this.name = "RunStoreError";
		this.code = code;
		this.path = path;
	}
}

export class RunStoreConflictError extends RunStoreError {
	readonly expectedRevision: number;
	readonly actualRevision: number;

	constructor(expectedRevision: number, actualRevision: number, runId: string) {
		super(
			"CONFLICT",
			`Run ${runId} changed from revision ${expectedRevision} to ${actualRevision}; reload before retrying the mutation.`,
		);
		this.name = "RunStoreConflictError";
		this.expectedRevision = expectedRevision;
		this.actualRevision = actualRevision;
	}
}

export interface AdmittedPath {
	path: string;
	resolvedPath: string;
}

export interface ResumeCheckAllowed {
	allowed: true;
	record: CoreRunRecord;
}

export interface ResumeCheckDenied {
	allowed: false;
	code: RunStoreErrorCode;
	message: string;
}

export type ResumeCheck = ResumeCheckAllowed | ResumeCheckDenied;

const RUN_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/;
const TERMINAL_STATUSES = new Set<RunStatus>(["success", "failed", "incomplete", "cancelled"]);

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringField(value: Record<string, unknown>, key: string, fallback?: string): string | undefined {
	const candidate = value[key];
	return typeof candidate === "string" ? candidate : fallback;
}

function statusField(value: unknown): RunStatus | undefined {
	return typeof value === "string" &&
		["pending", "running", "success", "failed", "incomplete", "cancelled"].includes(value)
		? (value as RunStatus)
		: undefined;
}

function positiveRevision(value: unknown): number {
	return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : 0;
}

function jsonLine(value: unknown): string {
	return `${JSON.stringify(value)}\n`;
}

function stateDigest(state: StateDocument): string {
	// State patches are assembled from optional fields and JSON serialization
	// drops `undefined`; hash the exact JSON representation that is persisted.
	const persisted = JSON.parse(JSON.stringify(state)) as JsonValue;
	return canonicalJsonDigest(persisted);
}

function pathInside(root: string, candidate: string): boolean {
	const relation = relative(normalize(root), normalize(candidate));
	return relation === "" || (relation !== ".." && !relation.startsWith("../") && !isAbsolute(relation));
}

function assertAbsoluteRoot(path: string, label: string): string {
	if (!isAbsolute(path)) throw new RunStoreError("PATH_OUT_OF_SCOPE", `${label} must be absolute: ${path}`, path);
	return normalize(path);
}

function parseState(raw: string, path: string): StateDocument {
	try {
		const parsed: unknown = JSON.parse(raw);
		if (!isRecord(parsed)) throw new Error("state document must be an object");
		return parsed;
	} catch (error) {
		throw new RunStoreError(
			"INVALID_RECORD",
			`Cannot parse state document ${path}: ${error instanceof Error ? error.message : String(error)}`,
			path,
		);
	}
}

function normalizeRecord(raw: unknown, expectedRunId: string, expectedRunDir: string): CoreRunRecord {
	if (!isRecord(raw)) throw new RunStoreError("INVALID_RECORD", `Run ${expectedRunId} is not a JSON object.`);
	const runId = stringField(raw, "runId", stringField(raw, "run_id"));
	const status = statusField(raw.status);
	if (!runId || runId !== expectedRunId || !status) {
		throw new RunStoreError("INVALID_RECORD", `Run ${expectedRunId} has an invalid identity or status.`);
	}
	const runDir = stringField(raw, "runDir", stringField(raw, "run_dir"));
	if (!runDir || normalize(runDir) !== normalize(expectedRunDir)) {
		throw new RunStoreError(
			"PATH_OUT_OF_SCOPE",
			`Run ${expectedRunId} points outside its descriptor directory.`,
			runDir,
		);
	}
	const cwd = stringField(raw, "cwd");
	const packageDir = stringField(raw, "packageDir", stringField(raw, "package_dir"));
	const args = stringField(raw, "args", "");
	const startedAt = stringField(raw, "startedAt", stringField(raw, "started_at"));
	const updatedAt = stringField(raw, "updatedAt", stringField(raw, "updated_at"));
	if (!cwd || !packageDir || !startedAt || !updatedAt || args === undefined) {
		throw new RunStoreError("INVALID_RECORD", `Run ${expectedRunId} is missing legacy descriptor fields.`);
	}
	const policySnapshotDigest = stringField(raw, "policySnapshotDigest", stringField(raw, "policy_snapshot_digest"));
	const policySnapshotPath = stringField(raw, "policySnapshotPath", stringField(raw, "policy_snapshot_path"));
	if ((policySnapshotDigest === undefined) !== (policySnapshotPath === undefined)) {
		throw new RunStoreError("INVALID_RECORD", `Run ${expectedRunId} has an incomplete policy snapshot binding.`);
	}
	if (policySnapshotDigest !== undefined && !SHA256_DIGEST.test(policySnapshotDigest)) {
		throw new RunStoreError("INVALID_RECORD", `Run ${expectedRunId} has an invalid policy snapshot digest.`);
	}
	if (policySnapshotPath !== undefined) {
		const snapshotRoot = normalize(join(expectedRunDir, "artifacts"));
		if (!isAbsolute(policySnapshotPath) || !pathInside(snapshotRoot, normalize(policySnapshotPath))) {
			throw new RunStoreError(
				"PATH_OUT_OF_SCOPE",
				`Run ${expectedRunId} policy snapshot path must be inside its artifacts directory.`,
				policySnapshotPath,
			);
		}
	}
	return {
		...(raw as unknown as CoreRunRecord),
		runId,
		skillName: stringField(raw, "skillName", stringField(raw, "skill_name")) ?? "",
		command: stringField(raw, "command") ?? "",
		status,
		cwd,
		packageDir,
		runDir: expectedRunDir,
		args,
		outputDir: stringField(raw, "outputDir", stringField(raw, "output_dir")),
		startedAt,
		updatedAt,
		finishedAt: stringField(raw, "finishedAt", stringField(raw, "finished_at")),
		coreVersion: stringField(raw, "coreVersion", stringField(raw, "core_version")),
		workflowDigest: stringField(raw, "workflowDigest", stringField(raw, "workflow_digest")),
		validatorDigest: stringField(raw, "validatorDigest", stringField(raw, "validator_digest")),
		executorDigest: stringField(raw, "executorDigest", stringField(raw, "executor_digest")),
		policyDigest: stringField(raw, "policyDigest", stringField(raw, "policy_digest")),
		bindingDigest: stringField(raw, "bindingDigest", stringField(raw, "binding_digest")),
		policySnapshotDigest,
		policySnapshotPath,
		stateDigest: stringField(raw, "stateDigest", stringField(raw, "state_digest")),
		revision: positiveRevision(raw.revision),
		legacyCompatibility:
			raw.coreVersion === undefined && raw.core_version === undefined ? true : Boolean(raw.legacyCompatibility),
	};
}

function transitionAllowed(from: RunStatus, to: RunStatus): boolean {
	if (from === to) return true;
	if (from === "pending") return to === "running" || to === "failed";
	if (from === "running") return TERMINAL_STATUSES.has(to);
	return false;
}

function pathErrorCode(reason: string | undefined): RunStoreErrorCode {
	if (reason === "READ_ONLY_REFERENCE" || reason === "SYMLINK_ESCAPE" || reason === "INVALID_CONTRACT") return reason;
	return "PATH_OUT_OF_SCOPE";
}

function bindingMismatch(record: CoreRunRecord, binding: ResumeBinding): string | undefined {
	const fields: Array<[keyof ResumeBinding, keyof CoreRunRecord]> = [
		["coreVersion", "coreVersion"],
		["workflowDigest", "workflowDigest"],
		["validatorDigest", "validatorDigest"],
		["executorDigest", "executorDigest"],
		["policyDigest", "policyDigest"],
		["policySnapshotDigest", "policySnapshotDigest"],
		["bindingDigest", "bindingDigest"],
	];
	for (const [bindingKey, recordKey] of fields) {
		const expected = binding[bindingKey];
		if (expected === undefined) continue;
		const actual = record[recordKey];
		if (typeof actual !== "string" || actual !== expected) return bindingKey;
	}
	// A snapshot-bound run must never be resumed through an older caller that
	// silently omits the snapshot binding.  Legacy runs remain compatible
	// because they have no snapshot field to enforce.
	if (record.policySnapshotDigest !== undefined && binding.policySnapshotDigest === undefined) {
		return "policySnapshotDigest";
	}
	return undefined;
}

/**
 * Host-neutral durable run store. All filesystem and locking effects are
 * supplied by ports so this module can be exercised without Pi or Node fs.
 */
export class CoreRunStore {
	readonly rootDir: string;
	readonly runsRoot: string;
	private readonly filesystem: RunStoreFileSystem;
	private readonly lock: RunStoreLock;
	private readonly clock: () => string;
	private readonly coreVersion: string;
	private readonly writeRoots: string[];
	private readonly referenceRoots: string[];

	constructor(options: RunStoreOptions) {
		this.rootDir = assertAbsoluteRoot(options.rootDir, "rootDir");
		this.runsRoot = normalize(join(this.rootDir, ".sure", "runs"));
		this.filesystem = options.filesystem;
		this.lock = options.lock;
		this.clock = options.clock ?? (() => new Date().toISOString());
		this.coreVersion = options.coreVersion;
		this.writeRoots = [this.rootDir, ...(options.writeRoots ?? [])].map((path) =>
			assertAbsoluteRoot(path, "write root"),
		);
		this.referenceRoots = (options.referenceRoots ?? []).map((path) => assertAbsoluteRoot(path, "reference root"));
		if (this.referenceRoots.some((root) => pathInside(root, this.rootDir))) {
			throw new RunStoreError("READ_ONLY_REFERENCE", "rootDir overlaps a read-only reference root.", this.rootDir);
		}
		this.admitPath(this.runsRoot, [this.rootDir]);
	}

	static validateRunId(runId: string): void {
		if (
			!RUN_ID_PATTERN.test(runId) ||
			runId === "." ||
			runId === ".." ||
			runId.includes("/") ||
			runId.includes("\\")
		) {
			throw new RunStoreError("INVALID_RUN_ID", `Invalid run id: ${JSON.stringify(runId)}.`);
		}
	}

	private runDir(runId: string): string {
		CoreRunStore.validateRunId(runId);
		const candidate = normalize(join(this.runsRoot, runId));
		if (!pathInside(this.runsRoot, candidate))
			throw new RunStoreError("PATH_OUT_OF_SCOPE", "Run id escaped runs root.", candidate);
		return candidate;
	}

	private runFile(runId: string, name: string): string {
		const dir = this.runDir(runId);
		const path = normalize(join(dir, name));
		if (!pathInside(dir, path))
			throw new RunStoreError("PATH_OUT_OF_SCOPE", `Run file escaped descriptor directory: ${name}.`, path);
		return path;
	}

	private realpathWithParent(path: string): string | undefined {
		const direct = this.filesystem.realpath(path);
		if (direct) return normalize(direct);
		let cursor = normalize(path);
		const suffix: string[] = [];
		while (!this.filesystem.exists(cursor)) {
			const parent = dirname(cursor);
			if (parent === cursor) return undefined;
			suffix.unshift(basename(cursor));
			cursor = parent;
		}
		const parentReal = this.filesystem.realpath(cursor);
		return parentReal ? normalize(join(parentReal, ...suffix)) : undefined;
	}

	private rootDescriptor(path: string): ResolvedRoot {
		const lexical = assertAbsoluteRoot(path, "path");
		return { path: lexical, resolved_path: this.realpathWithParent(lexical) ?? lexical };
	}

	/** Check lexical and resolved containment, including forbidden reference roots. */
	admitPath(pathValue: string, allowedRoots = this.writeRoots): AdmittedPath {
		return this.admitPathInternal(pathValue, allowedRoots, this.referenceRoots);
	}

	/**
	 * Admit a read-only input.  Reference roots are valid inputs but are never
	 * valid outputs, so this deliberately uses a separate API instead of
	 * weakening `admitPath`'s write boundary.
	 */
	admitReadPath(pathValue: string, allowedRoots = [...this.writeRoots, ...this.referenceRoots]): AdmittedPath {
		return this.admitPathInternal(pathValue, allowedRoots, []);
	}

	private admitPathInternal(
		pathValue: string,
		allowedRoots: readonly string[],
		forbiddenRoots: readonly string[],
	): AdmittedPath {
		const lexical = normalize(isAbsolute(pathValue) ? pathValue : resolve(this.rootDir, pathValue));
		const allowed = allowedRoots.map((root) => this.rootDescriptor(root));
		const forbidden = forbiddenRoots.map((root) => this.rootDescriptor(root));
		const resolved = this.realpathWithParent(lexical);
		if (!resolved) {
			throw new RunStoreError("SYMLINK_ESCAPE", `Cannot resolve path for containment: ${lexical}.`, lexical);
		}
		const evaluation = evaluatePathBoundary({
			candidate_path: lexical,
			candidate_resolved_path: resolved,
			allowed_roots: allowed,
			forbidden_roots: forbidden,
		});
		if (!evaluation.admitted) {
			const code = pathErrorCode(evaluation.reason_code);
			throw new RunStoreError(
				code,
				evaluation.blocking_outcome?.diagnostics[0]?.message ?? `Path rejected: ${lexical}.`,
				lexical,
			);
		}
		return { path: lexical, resolvedPath: resolved };
	}

	private recordAdmissionDiagnostic(message: string, path?: string): void {
		const diagnosticPath = normalize(join(this.rootDir, ".sure", "diagnostics", "events.jsonl"));
		try {
			this.filesystem.mkdir(dirname(diagnosticPath));
			this.lock.withLock(diagnosticPath, () => {
				this.filesystem.appendLine(
					diagnosticPath,
					jsonLine({ type: "path_admission_rejected", timestamp: this.clock(), path, message }),
				);
			});
		} catch {
			// A failed diagnostic write must never cause a fallback write to the
			// rejected/reference path. The original admission error remains primary.
		}
	}

	private admittedRunDir(runId: string): string {
		const dir = this.runDir(runId);
		this.admitPath(dir, [this.runsRoot]);
		return dir;
	}

	private parseRun(runId: string): CoreRunRecord | undefined {
		const dir = this.admittedRunDir(runId);
		const path = this.runFile(runId, "run.json");
		const raw = this.filesystem.readFile(path);
		if (raw === undefined) return undefined;
		try {
			return normalizeRecord(JSON.parse(raw) as unknown, runId, dir);
		} catch (error) {
			if (error instanceof RunStoreError) throw error;
			throw new RunStoreError(
				"INVALID_RECORD",
				`Cannot parse ${path}: ${error instanceof Error ? error.message : String(error)}`,
				path,
			);
		}
	}

	private writeRun(record: CoreRunRecord): void {
		this.filesystem.writeFileAtomic(this.runFile(record.runId, "run.json"), `${JSON.stringify(record, null, 2)}\n`);
	}

	private appendRunEvent(runId: string, revision: number, type: string, data?: unknown): void {
		if (!type.trim()) throw new RunStoreError("INVALID_RECORD", "Run event type must not be empty.");
		const event: RunEvent = {
			type,
			timestamp: this.clock(),
			run_id: runId,
			revision,
			...(data === undefined ? {} : { data }),
		};
		this.filesystem.appendLine(this.runFile(runId, "events.jsonl"), jsonLine(event));
	}

	createRun(input: CreateRunInput): CoreRunRecord {
		CoreRunStore.validateRunId(input.runId);
		let outputDir: string | undefined;
		try {
			if (input.outputDir !== undefined) outputDir = this.admitPath(input.outputDir).path;
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			this.recordAdmissionDiagnostic(message, input.outputDir);
			throw error;
		}
		const dir = this.admittedRunDir(input.runId);
		if ((input.policySnapshotDigest === undefined) !== (input.policySnapshotPath === undefined)) {
			throw new RunStoreError("INVALID_RECORD", "Policy snapshot digest and path must be supplied together.");
		}
		if (input.policySnapshotDigest !== undefined && !SHA256_DIGEST.test(input.policySnapshotDigest)) {
			throw new RunStoreError("INVALID_RECORD", "Policy snapshot digest must be a SHA-256 digest.");
		}
		if (input.policySnapshotPath !== undefined) {
			this.admitPath(input.policySnapshotPath, [join(dir, "artifacts")]);
		}
		return this.lock.withLock(dir, () => {
			if (this.filesystem.exists(this.runFile(input.runId, "run.json"))) {
				throw new RunStoreError("ALREADY_EXISTS", `Run ${input.runId} already exists.`, dir);
			}
			this.filesystem.mkdir(dir);
			this.filesystem.mkdir(join(dir, "logs"));
			this.filesystem.mkdir(join(dir, "artifacts"));
			const startedAt = input.startedAt ?? this.clock();
			const record: CoreRunRecord = {
				runId: input.runId,
				skillName: input.skillName,
				command: input.command,
				status: "pending",
				cwd: input.cwd,
				packageDir: input.packageDir,
				runDir: dir,
				args: input.args,
				...(outputDir === undefined ? {} : { outputDir }),
				startedAt,
				updatedAt: startedAt,
				coreVersion: input.coreVersion,
				workflowDigest: input.workflowDigest,
				validatorDigest: input.validatorDigest,
				executorDigest: input.executorDigest,
				policyDigest: input.policyDigest,
				...(input.policySnapshotDigest === undefined ? {} : { policySnapshotDigest: input.policySnapshotDigest }),
				...(input.policySnapshotPath === undefined ? {} : { policySnapshotPath: input.policySnapshotPath }),
				stateDigest: stateDigest({}),
				...(input.bindingDigest === undefined ? {} : { bindingDigest: input.bindingDigest }),
				revision: 0,
			};
			this.writeRun(record);
			this.filesystem.writeFileAtomic(this.runFile(input.runId, "state.json"), "{}\n");
			this.appendRunEvent(input.runId, 0, "created", record);
			return record;
		});
	}

	readRun(runId: string): CoreRunRecord | undefined {
		return this.parseRun(runId);
	}

	readState(run: string | CoreRunRecord): StateDocument | undefined {
		const runId = typeof run === "string" ? run : run.runId;
		const path = this.runFile(runId, "state.json");
		const raw = this.filesystem.readFile(path);
		return raw === undefined ? undefined : parseState(raw, path);
	}

	private updateRunInternal(
		runId: string,
		patch: Partial<CoreRunRecord>,
		eventType: string,
		data?: unknown,
		expectedRevision?: number,
		allowResume = false,
	): CoreRunRecord {
		const dir = this.admittedRunDir(runId);
		return this.lock.withLock(dir, () => {
			const current = this.parseRun(runId);
			if (!current) throw new RunStoreError("INVALID_RECORD", `Run ${runId} does not exist.`, dir);
			const actualRevision = current.revision ?? 0;
			if (expectedRevision !== undefined && actualRevision !== expectedRevision) {
				throw new RunStoreConflictError(expectedRevision, actualRevision, runId);
			}
			if (patch.runId !== undefined && patch.runId !== runId)
				throw new RunStoreError("INVALID_RECORD", "runId is immutable.");
			if (patch.runDir !== undefined && normalize(patch.runDir) !== normalize(current.runDir)) {
				throw new RunStoreError("PATH_OUT_OF_SCOPE", "runDir is immutable.", patch.runDir);
			}
			const nextStatus = patch.status ?? current.status;
			if (
				!transitionAllowed(current.status, nextStatus) &&
				!(allowResume && current.status === "failed" && nextStatus === "running")
			) {
				throw new RunStoreError(
					"INVALID_STATUS_TRANSITION",
					`Cannot transition run ${runId} from ${current.status} to ${nextStatus}.`,
				);
			}
			if (
				TERMINAL_STATUSES.has(current.status) &&
				!(allowResume && current.status === "failed" && nextStatus === "running")
			) {
				throw new RunStoreError(
					"TERMINAL_IMMUTABLE",
					`Run ${runId} is terminal (${current.status}) and cannot be mutated.`,
				);
			}
			const next: CoreRunRecord = {
				...current,
				...patch,
				runId,
				runDir: current.runDir,
				status: nextStatus,
				updatedAt: this.clock(),
				revision: actualRevision + 1,
			};
			if (nextStatus === "running") next.finishedAt = undefined;
			if (TERMINAL_STATUSES.has(nextStatus) && next.finishedAt === undefined) next.finishedAt = next.updatedAt;
			this.writeRun(next);
			this.appendRunEvent(runId, next.revision ?? actualRevision + 1, eventType, data);
			return next;
		});
	}

	updateRun(
		runId: string,
		patch: Partial<CoreRunRecord>,
		eventType: string,
		data?: unknown,
		expectedRevision?: number,
	): CoreRunRecord {
		let admittedPatch = patch;
		if (patch.outputDir !== undefined) {
			try {
				admittedPatch = { ...patch, outputDir: this.admitPath(patch.outputDir).path };
			} catch (error) {
				this.recordAdmissionDiagnostic(error instanceof Error ? error.message : String(error), patch.outputDir);
				throw error;
			}
		}
		return this.updateRunInternal(runId, admittedPatch, eventType, data, expectedRevision);
	}

	setStatus(runId: string, status: RunStatus, eventType = "status", expectedRevision?: number): CoreRunRecord {
		return this.updateRun(runId, { status }, eventType, { status }, expectedRevision);
	}

	writeState(
		runId: string,
		state: StateDocument,
		eventType = "state_patch",
		data?: unknown,
		expectedRevision?: number,
	): CoreRunRecord {
		const dir = this.admittedRunDir(runId);
		return this.lock.withLock(dir, () => {
			const current = this.parseRun(runId);
			if (!current) throw new RunStoreError("INVALID_RECORD", `Run ${runId} does not exist.`, dir);
			if (TERMINAL_STATUSES.has(current.status)) {
				throw new RunStoreError(
					"TERMINAL_IMMUTABLE",
					`Run ${runId} is terminal (${current.status}) and its checkpoint cannot be changed.`,
				);
			}
			const actualRevision = current.revision ?? 0;
			if (expectedRevision !== undefined && actualRevision !== expectedRevision) {
				throw new RunStoreConflictError(expectedRevision, actualRevision, runId);
			}
			const nextStateDigest = stateDigest(state);
			this.filesystem.writeFileAtomic(this.runFile(runId, "state.json"), `${JSON.stringify(state, null, 2)}\n`);
			const next: CoreRunRecord = { ...current, updatedAt: this.clock(), revision: actualRevision + 1 };
			next.stateDigest = nextStateDigest;
			this.writeRun(next);
			this.appendRunEvent(runId, next.revision ?? actualRevision + 1, eventType, data ?? { state });
			return next;
		});
	}

	appendEvent(runId: string, type: string, data?: unknown): void {
		const dir = this.admittedRunDir(runId);
		this.lock.withLock(dir, () => {
			const current = this.parseRun(runId);
			if (!current) throw new RunStoreError("INVALID_RECORD", `Run ${runId} does not exist.`, dir);
			if (TERMINAL_STATUSES.has(current.status)) {
				throw new RunStoreError(
					"TERMINAL_IMMUTABLE",
					`Run ${runId} is terminal (${current.status}) and its events cannot be changed.`,
				);
			}
			this.appendRunEvent(runId, current.revision ?? 0, type, data);
		});
	}

	checkResume(runId: string, binding: ResumeBinding): ResumeCheck {
		const record = this.parseRun(runId);
		if (!record) return { allowed: false, code: "INVALID_RECORD", message: `Run ${runId} does not exist.` };
		if (record.status !== "failed") {
			return {
				allowed: false,
				code: TERMINAL_STATUSES.has(record.status) ? "TERMINAL_IMMUTABLE" : "INVALID_STATUS_TRANSITION",
				message: `Only failed runs can resume; ${runId} is ${record.status}.`,
			};
		}
		const mismatch = bindingMismatch(record, binding);
		if (mismatch) {
			return {
				allowed: false,
				code: record.legacyCompatibility ? "UPGRADE_REQUIRED" : "CONFLICT",
				message: `Run ${runId} binding ${mismatch} does not match the current registry.`,
			};
		}
		const state = this.readState(record);
		if (record.stateDigest !== undefined) {
			if (state === undefined || stateDigest(state) !== record.stateDigest) {
				return {
					allowed: false,
					code: "INVALID_RECORD",
					message: `Run ${runId} state digest does not match the run descriptor.`,
				};
			}
		}
		const checkpoint = state?.checkpoint;
		if (!isRecord(checkpoint) || checkpoint.resumable !== true) {
			return {
				allowed: false,
				code: "CHECKPOINT_NOT_RESUMABLE",
				message: `Run ${runId} has no resumable checkpoint.`,
			};
		}
		return { allowed: true, record };
	}

	resumeRun(runId: string, binding: ResumeBinding): CoreRunRecord {
		const check = this.checkResume(runId, binding);
		if (!check.allowed) throw new RunStoreError(check.code, check.message);
		return this.updateRunInternal(
			runId,
			{ status: "running", finishedAt: undefined, staleSince: undefined },
			"resumed",
			{ binding },
			check.record.revision,
			true,
		);
	}

	finalizeRun(
		runId: string,
		status: "success" | "incomplete" | "failed" | "cancelled",
		evidence?: SuccessEvidence,
	): CoreRunRecord {
		if (status === "success") {
			if (
				!evidence?.terminalCheckpoint ||
				!evidence.successReceipt ||
				!Array.isArray(evidence.requiredArtifacts) ||
				typeof evidence.successReceiptDigest !== "string" ||
				!SHA256_DIGEST.test(evidence.successReceiptDigest)
			) {
				throw new RunStoreError(
					"SUCCESS_EVIDENCE_MISSING",
					"A successful run requires a terminal checkpoint, explicit artifacts, and a SHA-256 success receipt digest.",
				);
			}
			const record = this.parseRun(runId);
			if (!record) throw new RunStoreError("INVALID_RECORD", `Run ${runId} does not exist.`);
			const state = this.readState(record);
			if (record.stateDigest !== undefined && (state === undefined || stateDigest(state) !== record.stateDigest)) {
				throw new RunStoreError("INVALID_RECORD", `Run ${runId} state digest does not match the run descriptor.`);
			}
			const checkpoint = state?.checkpoint;
			if (!isRecord(checkpoint) || checkpoint.resumable !== false) {
				throw new RunStoreError("SUCCESS_EVIDENCE_MISSING", "The persisted checkpoint is not terminal.");
			}
			for (const artifact of evidence.requiredArtifacts) {
				try {
					const candidate = isAbsolute(artifact) ? artifact : join(record.runDir, "artifacts", artifact);
					const admitted = this.admitPath(candidate, [
						join(record.runDir, "artifacts"),
						record.runDir,
						...(record.outputDir ? [record.outputDir] : []),
					]);
					if (!this.filesystem.exists(admitted.path)) {
						throw new RunStoreError(
							"SUCCESS_EVIDENCE_MISSING",
							`Required artifact is missing: ${artifact}.`,
							artifact,
						);
					}
				} catch (error) {
					if (error instanceof RunStoreError && error.code === "SUCCESS_EVIDENCE_MISSING") throw error;
					throw new RunStoreError(
						"SUCCESS_EVIDENCE_MISSING",
						`Required artifact is not admitted: ${artifact}.`,
						artifact,
					);
				}
			}
		}
		return this.setStatus(runId, status, "finalized");
	}

	/** Compatibility resolver: invalid paths return undefined after a local diagnostic. */
	resolveRunPath(runId: string, pathValue: string): string | undefined {
		try {
			const record = this.parseRun(runId);
			if (!record) return undefined;
			const candidate = isAbsolute(pathValue) ? pathValue : resolve(record.cwd, pathValue);
			return this.admitPath(candidate, [record.runDir, record.cwd]).path;
		} catch (error) {
			this.recordAdmissionDiagnostic(error instanceof Error ? error.message : String(error), pathValue);
			return undefined;
		}
	}
}

export { RUN_ID_PATTERN };
