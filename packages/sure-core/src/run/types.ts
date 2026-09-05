export const RUN_STATUSES = ["pending", "running", "success", "failed", "incomplete", "cancelled"] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

export interface CoreRunRecord {
	runId: string;
	skillName: string;
	command: string;
	status: RunStatus;
	cwd: string;
	packageDir: string;
	runDir: string;
	args: string;
	outputDir?: string;
	startedAt: string;
	updatedAt: string;
	finishedAt?: string;
	manifestPath?: string;
	summary?: string;
	errorSummary?: string;
	lastRepair?: string;
	staleSince?: string;
	artifacts?: unknown;
	/** Digests are required for newly created runs and optional on legacy reads. */
	coreVersion?: string;
	workflowDigest?: string;
	validatorDigest?: string;
	executorDigest?: string;
	policyDigest?: string;
	bindingDigest?: string;
	/** Monotonic CAS revision; legacy records are read as revision zero. */
	revision?: number;
	legacyCompatibility?: boolean;
}

export interface CreateRunInput {
	runId: string;
	skillName: string;
	command: string;
	cwd: string;
	packageDir: string;
	args: string;
	outputDir?: string;
	coreVersion: string;
	workflowDigest: string;
	validatorDigest: string;
	executorDigest: string;
	policyDigest: string;
	bindingDigest?: string;
	startedAt?: string;
}

export interface RunStoreFileSystem {
	/** Create a directory and all missing parents. */
	mkdir(path: string): void;
	/** Return file contents, or undefined when the path does not exist. */
	readFile(path: string): string | undefined;
	/** Replace a file atomically. */
	writeFileAtomic(path: string, content: string): void;
	/** Append one complete line atomically from the store's perspective. */
	appendLine(path: string, line: string): void;
	/** Existence check used for legacy reads and path admission. */
	exists(path: string): boolean;
	/** Resolve a path through symlinks. Return undefined if no resolution is possible. */
	realpath(path: string): string | undefined;
}

export interface RunStoreLock {
	/** Execute a synchronous critical section under an exclusive key. */
	withLock<T>(key: string, operation: () => T): T;
}

export interface RunStoreOptions {
	/** Absolute workspace root in which writable run/output roots are permitted. */
	rootDir: string;
	filesystem: RunStoreFileSystem;
	lock: RunStoreLock;
	clock?: () => string;
	coreVersion: string;
	referenceRoots?: readonly string[];
	/** Additional writable roots, e.g. a site-approved output root. */
	writeRoots?: readonly string[];
}

export interface ResumeBinding {
	coreVersion: string;
	workflowDigest: string;
	validatorDigest: string;
	executorDigest: string;
	policyDigest: string;
	bindingDigest?: string;
}

export interface StateDocument {
	[key: string]: unknown;
}

export interface RunEvent {
	type: string;
	timestamp: string;
	run_id: string;
	revision: number;
	data?: unknown;
}

export interface SuccessEvidence {
	terminalCheckpoint: boolean;
	requiredArtifacts: readonly string[];
	successReceipt: boolean;
	/** SHA-256 digest of the persisted execution receipt bytes. */
	successReceiptDigest?: string;
}
