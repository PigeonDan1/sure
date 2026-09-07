#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import {
	closeSync,
	constants,
	existsSync,
	fsyncSync,
	linkSync,
	lstatSync,
	mkdirSync,
	openSync,
	readdirSync,
	readFileSync,
	readlinkSync,
	renameSync,
	unlinkSync,
	writeFileSync,
} from "node:fs";
import { delimiter, dirname, isAbsolute, join, resolve } from "node:path";
import {
	type AssuranceProfile,
	admitOperationExecutionEvidence,
	applyValidation,
	assessFormalEligibility,
	auditCheckpointState,
	bindExecutionInputs,
	type CapabilityEvidence,
	type CapabilityReport,
	type CoreRunRecord,
	canonicalJsonDigest,
	createFrozenEvaluationSubject,
	createOutcome,
	decodeLegacyCheckpoint,
	decodeOperationExecutionEvidence,
	type ExecutionInputBindingResolver,
	type ExecutionReceipt,
	type ExecutionRequest,
	encodeLegacyCheckpoint,
	evaluateCapabilityRequirements,
	executionInputContractDigest,
	executionOutputContractDigest,
	executorDescriptor,
	executorRegistrySnapshot,
	type FrozenEvaluationSubject,
	type FrozenFormalSubject,
	initialCheckpoint,
	inspectExecutionArtifact,
	type JsonValue,
	type PolicyPathRole,
	type PolicySnapshot,
	type PublicOutcome,
	parseMemoryUri,
	type StateDocument,
	type StructuralValidationResult,
	selectExecutionDispatch,
	validateExecutionInputBinding,
	validateExecutionReceipt,
	validateExecutionRequest,
	validateFrozenEvaluationSubject,
	validateMemoryContract,
	validatePolicySnapshot,
	validateStructuralArtifact,
	type WorkflowCheckpoint,
	type WorkflowDefinition,
	type WorkflowUnit,
} from "@earendil-works/sure-core";
import { resolveSemanticBackendOperation, verifyPortableRuntime } from "@earendil-works/sure-core/evaluation";
import { type LoadedDefinition, loadDefinition, unitForCurrent } from "./definition.ts";
import { executeRequest } from "./executor.ts";
import { NodeRunStore } from "./node-run-store.ts";
import {
	registeredOperationCapabilityRequirementsDigest,
	registeredOperationSemanticDigest,
	runRegisteredOperation,
} from "./registered-operation.ts";
import {
	artifactRef,
	loadSkillRuntimeBinding,
	type RegisteredValidationResult,
	type RegisteredValidatorDescriptor,
	runRegisteredValidators,
	semanticRuntimeEnvironment,
	unavailableRegisteredValidation,
} from "./registered-validator.ts";

const CORE_VERSION = "0.80.3";
const SHA256 = /^[0-9a-f]{64}$/;

/** Stable process exit codes for machine callers of the cooperative CLI. */
export const SURECTL_EXIT_CODES = {
	PASS: 0,
	ERROR: 1,
	FAIL: 2,
	BLOCKED: 3,
	RETRY: 4,
	NOT_EXECUTED: 5,
} as const;

const PUBLIC_OUTCOME_EXIT_CODES: Readonly<Record<PublicOutcome, number>> = {
	PASS: SURECTL_EXIT_CODES.PASS,
	FAIL: SURECTL_EXIT_CODES.FAIL,
	BLOCKED: SURECTL_EXIT_CODES.BLOCKED,
	RETRY: SURECTL_EXIT_CODES.RETRY,
	NOT_EXECUTED: SURECTL_EXIT_CODES.NOT_EXECUTED,
};

interface ParsedArgs {
	command: string;
	flags: Map<string, string[]>;
}

function parseArgs(argv: readonly string[]): ParsedArgs {
	const [command = "help", ...rest] = argv;
	const flags = new Map<string, string[]>();
	for (let index = 0; index < rest.length; index++) {
		const token = rest[index];
		if (!token?.startsWith("--")) throw new Error(`Unexpected argument: ${token ?? ""}`);
		const equal = token.indexOf("=");
		const key = equal >= 0 ? token.slice(2, equal) : token.slice(2);
		if (!key) throw new Error("Empty option name.");
		const value = equal >= 0 ? token.slice(equal + 1) : rest[index + 1]?.startsWith("--") ? "true" : rest[++index];
		if (value === undefined) throw new Error(`Option --${key} needs a value.`);
		const values = flags.get(key) ?? [];
		values.push(value);
		flags.set(key, values);
	}
	return { command, flags };
}

function one(args: ParsedArgs, key: string): string | undefined {
	return args.flags.get(key)?.at(-1);
}

function many(args: ParsedArgs, key: string): string[] {
	return [...(args.flags.get(key) ?? [])];
}

function required(args: ParsedArgs, key: string): string {
	const value = one(args, key)?.trim();
	if (!value) throw new Error(`--${key} is required.`);
	return value;
}

function requiredValue(args: ParsedArgs, key: string, environmentName: string): string {
	return one(args, key)?.trim() || process.env[environmentName]?.trim() || required(args, key);
}

function absolute(value: string, label: string): string {
	if (!isAbsolute(value)) throw new Error(`${label} must be absolute: ${value}`);
	return resolve(value);
}

function envPaths(...names: string[]): string[] {
	return names.flatMap((name) => {
		const value = process.env[name];
		return value
			? value
					.split(delimiter)
					.map((entry) => entry.trim())
					.filter((entry) => entry.length > 0)
			: [];
	});
}

interface LoadedPolicySnapshot {
	snapshot: PolicySnapshot;
	path: string;
}

function policySnapshotFor(args: ParsedArgs): LoadedPolicySnapshot | undefined {
	const candidate = one(args, "policy-snapshot") ?? process.env.SURE_POLICY_SNAPSHOT;
	if (candidate === undefined || candidate.trim() === "") return undefined;
	const path = absolute(candidate, "--policy-snapshot");
	const stat = lstatSync(path);
	if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`--policy-snapshot must name a regular file: ${path}`);
	return { snapshot: validatePolicySnapshot(readJson(path)), path };
}

function snapshotPaths(snapshot: PolicySnapshot | undefined, roles: readonly PolicyPathRole[]): string[] {
	if (snapshot === undefined) return [];
	return snapshot.path_bindings
		.filter((binding) => roles.includes(binding.role))
		.flatMap((binding) => [binding.path, binding.resolved_path]);
}

function referenceRoots(args: ParsedArgs, snapshot = policySnapshotFor(args)?.snapshot): string[] {
	return [
		...many(args, "reference-root").map((value) => absolute(value, "--reference-root")),
		...envPaths("SURE_REFERENCE_ROOT", "REFERENCE_ROOT").map((value) => absolute(value, "reference root")),
		...snapshotPaths(snapshot, ["read_only_reference", "dataset_source", "forbidden_output"]),
	].filter((value, index, values) => values.indexOf(value) === index);
}

function writeRoots(args: ParsedArgs, snapshot = policySnapshotFor(args)?.snapshot): string[] {
	return [
		...many(args, "write-root").map((value) => absolute(value, "--write-root")),
		...snapshotPaths(snapshot, ["controlled_publication", "runtime_cache"]),
	].filter((value, index, values) => values.indexOf(value) === index);
}

function rootFor(args: ParsedArgs): string {
	return absolute(one(args, "root") ?? process.env.SURE_DEV_ROOT ?? process.cwd(), "--root");
}

function outputRootFor(args: ParsedArgs): string | undefined {
	const explicit = one(args, "output-root") ?? one(args, "output-dir");
	return explicit === undefined ? undefined : absolute(explicit, "--output-root");
}

function json(value: unknown): string {
	return `${JSON.stringify(value, null, 2)}\n`;
}

function output(value: unknown): void {
	process.stdout.write(json(value));
}

const DIRECTORY_OPEN_FLAG = process.platform === "win32" ? 0 : (constants.O_DIRECTORY ?? 0);

function syncDirectory(path: string): void {
	let descriptor: number | undefined;
	try {
		descriptor = openSync(path, constants.O_RDONLY | DIRECTORY_OPEN_FLAG);
		fsyncSync(descriptor);
	} catch (error) {
		if (!isUnsupportedDirectorySync(error)) throw error;
	} finally {
		if (descriptor !== undefined) closeSync(descriptor);
	}
}

function isUnsupportedDirectorySync(error: unknown): boolean {
	return (
		typeof error === "object" &&
		error !== null &&
		"code" in error &&
		(error.code === "EINVAL" || error.code === "ENOTSUP" || error.code === "EBADF" || error.code === "EPERM")
	);
}

function writeDurableFile(path: string, content: string, exclusive: boolean): void {
	const descriptor = openSync(
		path,
		constants.O_WRONLY | constants.O_CREAT | (exclusive ? constants.O_EXCL : 0),
		0o666,
	);
	try {
		writeFileSync(descriptor, content, { encoding: "utf8" });
		fsyncSync(descriptor);
	} finally {
		closeSync(descriptor);
	}
}

function writeJsonAtomic(path: string, value: unknown): void {
	mkdirSync(dirname(path), { recursive: true });
	const temporary = `${path}.sure-tmp-${process.pid}-${randomUUID()}`;
	try {
		writeDurableFile(temporary, json(value), true);
		renameSync(temporary, path);
		syncDirectory(dirname(path));
	} catch (error) {
		try {
			// The temporary file is local to the admitted output root; cleanup is
			// best effort and must not mask the original rename failure.
			if (existsSync(temporary)) unlinkSync(temporary);
		} catch {
			// Preserve the original failure.
		}
		throw error;
	}
}

function writeJsonImmutable(path: string, value: unknown): void {
	const content = json(value);
	mkdirSync(dirname(path), { recursive: true });
	if (existsSync(path)) {
		const existing = readJson(path);
		if (canonicalJsonDigest(existing as JsonValue) !== canonicalJsonDigest(value as JsonValue)) {
			throw new Error(`Refusing to replace immutable SURE subject: ${path}`);
		}
		return;
	}
	const temporary = `${path}.sure-immutable-${process.pid}-${randomUUID()}`;
	try {
		writeDurableFile(temporary, content, true);
		// A hard-link publish is no-clobber on POSIX filesystems, unlike rename().
		linkSync(temporary, path);
		syncDirectory(dirname(path));
	} catch (error) {
		if (isAlreadyExists(error)) {
			const existing = readJson(path);
			if (canonicalJsonDigest(existing as JsonValue) !== canonicalJsonDigest(value as JsonValue)) {
				throw new Error(`Refusing to replace immutable SURE subject: ${path}`);
			}
		} else {
			throw error;
		}
	} finally {
		try {
			if (existsSync(temporary)) unlinkSync(temporary);
		} catch {
			// Preserve the publish or comparison result.
		}
	}
}

function isAlreadyExists(error: unknown): boolean {
	return typeof error === "object" && error !== null && "code" in error && error.code === "EEXIST";
}

function readJson(path: string): unknown {
	return JSON.parse(readFileSync(path, "utf8")) as unknown;
}

function assertRegularFile(path: string, label: string): void {
	let stat: ReturnType<typeof lstatSync>;
	try {
		stat = lstatSync(path);
	} catch {
		throw new Error(`${label} is missing: ${path}`);
	}
	if (stat.isSymbolicLink() || !stat.isFile()) throw new Error(`${label} must be a regular file: ${path}`);
}

/** Pick a stable predecessor artifact when a registered producer has no output yet. */
function producerInputArtifactPath(
	store: NodeRunStore,
	run: CoreRunRecord,
	loaded: LoadedDefinition,
	checkpoint: WorkflowCheckpoint,
	currentUnit: WorkflowUnit,
	requested: string | undefined,
	targetPath: string,
): string {
	const declared = currentUnit.gate?.execution_input_produces;
	const declaredPath =
		declared === undefined ? undefined : admittedRunArtifactPath(store, run, join(run.runDir, "artifacts", declared));
	if (requested !== undefined) {
		const requestedPath = admittedRunArtifactPath(store, run, absolute(requested, "registered operation artifact"));
		if (declaredPath !== undefined && resolve(requestedPath) !== resolve(declaredPath)) {
			throw new Error(
				`Registered operation input must be the declared upstream artifact ${declared}; received ${requested}.`,
			);
		}
		return requestedPath;
	}
	if (declaredPath !== undefined) {
		assertRegularFile(declaredPath, `Declared producer input artifact for ${currentUnit.id}`);
		return declaredPath;
	}
	try {
		assertRegularFile(targetPath, "Registered operation artifact");
		return targetPath;
	} catch {
		const branch = loaded.definition.branches.find((candidate) => candidate.id === checkpoint.branch_id);
		const currentIndex = branch?.units.findIndex((candidate) => candidate.id === currentUnit.id) ?? -1;
		for (const unit of branch?.units.slice(0, currentIndex).reverse() ?? []) {
			const candidate = join(run.runDir, "artifacts", unit.produces);
			try {
				assertRegularFile(candidate, `Producer input artifact for ${currentUnit.id}`);
				return candidate;
			} catch {
				// A prior unit may be agent-owned and legitimately have no artifact yet.
			}
		}
		const transInput = join(run.runDir, "artifacts", "trans_input_resolved.json");
		assertRegularFile(transInput, `Producer input artifact for ${currentUnit.id}`);
		return transInput;
	}
}

function recordObject(value: unknown, label: string): Record<string, unknown> {
	if (typeof value !== "object" || value === null || Array.isArray(value))
		throw new Error(`${label} must be an object.`);
	return value as Record<string, unknown>;
}

function digestFile(path: string): string {
	return `sha256:${createHash("sha256").update(readFileSync(path)).digest("hex")}`;
}

function digestPath(path: string): string {
	const root = resolve(path);
	const stat = lstatSync(root);
	if (stat.isSymbolicLink())
		return canonicalJsonDigest({
			kind: "symlink",
			target: readlinkSync(root, { encoding: "utf8" }),
		} as unknown as JsonValue);
	if (stat.isFile()) return digestFile(root);
	if (!stat.isDirectory()) throw new Error(`Cannot digest unsupported path: ${root}`);
	const rows: JsonValue[] = [];
	const walk = (directory: string, relativePrefix: string): void => {
		for (const entry of readdirSync(directory, { withFileTypes: true }).sort((left, right) =>
			left.name.localeCompare(right.name),
		)) {
			const child = join(directory, entry.name);
			const childRelative = relativePrefix ? `${relativePrefix}/${entry.name}` : entry.name;
			const childStat = lstatSync(child);
			if (childStat.isSymbolicLink()) {
				rows.push({
					path: childRelative,
					kind: "symlink",
					target: readlinkSync(child, { encoding: "utf8" }),
				});
			} else if (childStat.isDirectory()) {
				walk(child, childRelative);
			} else if (childStat.isFile()) {
				rows.push({ path: childRelative, kind: "file", digest: digestFile(child), size: childStat.size });
			}
		}
	};
	walk(root, "");
	return canonicalJsonDigest(rows);
}

function normalizeDigest(value: string, label: string): string {
	const digest = value.startsWith("sha256:") ? value.slice(7) : value;
	if (!SHA256.test(digest)) throw new Error(`${label} must be a SHA-256 digest.`);
	return `sha256:${digest}`;
}

function validDigestValue(value: unknown): value is string {
	return typeof value === "string" && /^(?:sha256:)?[0-9a-f]{64}$/i.test(value);
}

function sameDigest(left: string, right: string): boolean {
	return left.replace(/^sha256:/, "").toLowerCase() === right.replace(/^sha256:/, "").toLowerCase();
}

function policyDigestFor(args: ParsedArgs, snapshot: PolicySnapshot | undefined): string {
	const explicit = one(args, "policy-digest") ?? process.env.SURE_POLICY_DIGEST;
	if (snapshot !== undefined) {
		const expected = normalizeDigest(snapshot.policy_digest, "policy snapshot policy_digest");
		if (explicit !== undefined && !sameDigest(expected, normalizeDigest(explicit, "--policy-digest"))) {
			throw new Error("--policy-digest does not match the supplied policy snapshot.");
		}
		return expected;
	}
	return normalizeDigest(requiredValue(args, "policy-digest", "SURE_POLICY_DIGEST"), "--policy-digest");
}

function runPolicySnapshot(run: CoreRunRecord): LoadedPolicySnapshot | undefined {
	if (run.policySnapshotDigest === undefined && run.policySnapshotPath === undefined) return undefined;
	if (run.policySnapshotDigest === undefined || run.policySnapshotPath === undefined)
		throw new Error("Run has an incomplete policy snapshot binding.");
	const path = run.policySnapshotPath;
	const stat = lstatSync(path);
	if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`Run policy snapshot is not a regular file: ${path}`);
	const snapshot = validatePolicySnapshot(readJson(path));
	if (!sameDigest(snapshot.snapshot_digest, run.policySnapshotDigest))
		throw new Error("Run policy snapshot digest does not match its persisted snapshot.");
	if (run.policyDigest === undefined || !sameDigest(snapshot.policy_digest, run.policyDigest))
		throw new Error("Run policy digest does not match its persisted policy snapshot.");
	return { snapshot, path };
}

function assertRunPolicySnapshot(
	run: CoreRunRecord,
	supplied: LoadedPolicySnapshot | undefined,
	options: { requireCurrent?: boolean } = {},
): LoadedPolicySnapshot | undefined {
	const persisted = runPolicySnapshot(run);
	if (persisted === undefined) {
		if (supplied !== undefined)
			throw new Error("A policy snapshot was supplied for a run without a snapshot binding.");
		return undefined;
	}
	if (supplied === undefined) {
		if (options.requireCurrent) throw new Error("A current --policy-snapshot is required to resume this run.");
		return persisted;
	}
	if (!sameDigest(persisted.snapshot.snapshot_digest, supplied.snapshot.snapshot_digest))
		throw new Error("Supplied policy snapshot does not match the run binding.");
	return supplied;
}

interface RunContext {
	store: NodeRunStore;
	run: CoreRunRecord;
	policySnapshot?: PolicySnapshot;
}

function contextForRun(
	args: ParsedArgs,
	root: string,
	runId: string,
	options: { requireCurrentPolicy?: boolean } = {},
): RunContext {
	const supplied = policySnapshotFor(args);
	let store = storeFor(args, root, {}, supplied?.snapshot);
	let run = store.readRun(runId);
	if (!run) throw new Error(`Run ${runId} does not exist.`);
	const active = assertRunPolicySnapshot(run, supplied, { requireCurrent: options.requireCurrentPolicy });
	if (active !== undefined && supplied === undefined) {
		// Recreate the adapter with the immutable run policy roots. Bootstrap
		// lookup above only reads the descriptor inside rootDir.
		store = storeFor(args, root, {}, active.snapshot);
		run = store.readRun(runId);
		if (!run) throw new Error(`Run ${runId} does not exist.`);
		assertRunPolicySnapshot(run, active);
	}
	return { store, run, ...(active === undefined ? {} : { policySnapshot: active.snapshot }) };
}

function storeFor(
	args: ParsedArgs,
	root: string,
	digestOverrides: Partial<Record<string, string>> = {},
	snapshot = policySnapshotFor(args)?.snapshot,
): NodeRunStore {
	return new NodeRunStore({
		rootDir: root,
		coreVersion: CORE_VERSION,
		referenceRoots: referenceRoots(args, snapshot),
		writeRoots: writeRoots(args, snapshot),
		...digestOverrides,
	});
}

function workflowDigest(definition: WorkflowDefinition): string {
	return canonicalJsonDigest(definition as unknown as JsonValue);
}

function registryFrom(path: string): { digest: string; validators: Array<Record<string, unknown>> } {
	if (!existsSync(path)) throw new Error(`Validator registry is missing: ${path}`);
	const raw = recordObject(readJson(path), "validator registry");
	if (
		raw.schema !== "sure.validator.registry.v1" ||
		typeof raw.digest !== "string" ||
		!Array.isArray(raw.validators)
	) {
		throw new Error(`Invalid validator registry: ${path}`);
	}
	return {
		digest: normalizeDigest(raw.digest, "validator registry digest"),
		validators: raw.validators.filter(
			(entry): entry is Record<string, unknown> =>
				typeof entry === "object" && entry !== null && !Array.isArray(entry),
		),
	};
}

function checkpointFromState(definition: WorkflowDefinition, state: StateDocument | undefined): WorkflowCheckpoint {
	const branchId =
		state &&
		typeof state.branch_id === "string" &&
		definition.branches.some((branch) => branch.id === state.branch_id)
			? state.branch_id
			: definition.default_branch_id;
	const decoded = decodeLegacyCheckpoint(definition, state ?? {}, {
		id: definition.checkpoint_id ?? "main_flow",
		label: definition.checkpoint_label ?? definition.workflow_id,
		branch_id: branchId,
		include_failed_artifact_digests: true,
	});
	const raw = state?.checkpoint;
	const envelope =
		typeof raw === "object" && raw !== null && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
	return {
		id: typeof envelope.id === "string" ? envelope.id : (definition.checkpoint_id ?? "main_flow"),
		label:
			typeof envelope.label === "string" ? envelope.label : (definition.checkpoint_label ?? definition.workflow_id),
		resumable: envelope.resumable !== false,
		resume_hint: typeof envelope.resume_hint === "string" ? envelope.resume_hint : `At ${decoded.data.currentUnit}`,
		branch_id: decoded.branch_id,
		data: decoded.data,
	};
}

function stateWithCheckpoint(
	state: StateDocument | undefined,
	checkpoint: WorkflowCheckpoint,
	metadata: Record<string, unknown>,
): StateDocument {
	const encoded = encodeLegacyCheckpoint(checkpoint, { include_failed_artifact_digests: true });
	return {
		...(state ?? {}),
		checkpoint: {
			...encoded.checkpoint,
			id: checkpoint.id,
			label: checkpoint.label,
			resumable: checkpoint.resumable,
			resume_hint: checkpoint.resume_hint,
		},
		branch_id: checkpoint.branch_id,
		...metadata,
	};
}

function stateIntegrityError(
	run: { stateDigest?: string; runId: string },
	state: StateDocument | undefined,
): string | undefined {
	if (run.stateDigest === undefined) return undefined;
	if (state === undefined) return `Run ${run.runId} state document is missing.`;
	const actual = canonicalJsonDigest(state as unknown as JsonValue);
	return sameDigest(run.stateDigest, actual)
		? undefined
		: `Run ${run.runId} state digest does not match the run descriptor.`;
}

function currentDefinition(args: ParsedArgs, root: string, fallbackSkill?: string): LoadedDefinition {
	return loadDefinition(root, one(args, "definition"), one(args, "skill") ?? fallbackSkill);
}

function registryFor(
	definition: LoadedDefinition,
	args: ParsedArgs,
): { digest: string; validators: Array<Record<string, unknown>> } {
	const explicit = one(args, "validator-registry");
	return registryFrom(explicit ? absolute(explicit, "--validator-registry") : definition.registryPath);
}

function admittedRunArtifactPath(
	store: NodeRunStore,
	run: { runDir: string; outputDir?: string },
	path: string,
): string {
	return store.admitPath(path, [join(run.runDir, "artifacts"), run.runDir, ...(run.outputDir ? [run.outputDir] : [])])
		.path;
}

function admittedReadPath(
	store: NodeRunStore,
	run: { runDir: string; outputDir?: string },
	path: string,
	additionalRoots: readonly string[] = [],
): string {
	return store.admitPath(path, [
		...additionalRoots,
		join(run.runDir, "artifacts"),
		run.runDir,
		...(run.outputDir ? [run.outputDir] : []),
	]).path;
}

function admittedReadArtifactPath(
	store: NodeRunStore,
	run: { runDir: string; outputDir?: string },
	path: string,
	additionalRoots: readonly string[] = [],
): string {
	return store.admitReadPath(path, [
		...additionalRoots,
		join(run.runDir, "artifacts"),
		run.runDir,
		...(run.outputDir ? [run.outputDir] : []),
	]).path;
}

function pathInside(pathValue: string, root: string): boolean {
	const candidate = resolve(pathValue);
	const boundary = resolve(root);
	return candidate === boundary || candidate.startsWith(`${boundary}/`);
}

interface RegisteredInputContext {
	context: Readonly<Record<string, unknown>>;
	context_digest: string;
	resolver: ExecutionInputBindingResolver;
}

/**
 * Build the one CLI-side resolver for a semantic operation's conditional input
 * contract.  Core still owns selector matching and binding digests; this
 * adapter owns only path admission and byte inspection for the current run.
 */
function registeredInputContext(
	store: NodeRunStore,
	run: CoreRunRecord,
	contract: NonNullable<ReturnType<typeof resolveSemanticBackendOperation>["input_contract"]>,
	policyReferences: readonly string[],
	loadedRoot: string,
	referenceSnapshotDigest: string,
): RegisteredInputContext {
	const contextPath = admittedReadArtifactPath(
		store,
		run,
		join(run.runDir, "artifacts", contract.context_artifact),
		policyReferences,
	);
	assertRegularFile(contextPath, "Execution input context artifact");
	const contextValue = recordObject(readJson(contextPath), "execution input context");
	const contextDigest = digestFile(contextPath);
	const makeArtifact = (
		pathValue: string,
		inputId: string,
		origin: "local_staging" | "read_only_reference" | "external",
		sourceRoot: string,
	): ReturnType<typeof artifactRef> | undefined => {
		try {
			return artifactRef(pathValue, inputId, run.runDir, {
				artifact_id: inputId,
				origin,
				source_root: sourceRoot,
				...(origin === "read_only_reference" ? { reference_snapshot_digest: referenceSnapshotDigest } : {}),
			});
		} catch {
			return undefined;
		}
	};
	const resolver: ExecutionInputBindingResolver = {
		resolveRunArtifact(pathValue): ReturnType<ExecutionInputBindingResolver["resolveRunArtifact"]> {
			try {
				const admitted = admittedReadArtifactPath(
					store,
					run,
					join(run.runDir, "artifacts", pathValue),
					policyReferences,
				);
				return makeArtifact(admitted, "run-artifact", "local_staging", run.runDir);
			} catch {
				return undefined;
			}
		},
		resolveRunPath(pathValue): ReturnType<ExecutionInputBindingResolver["resolveRunPath"]> {
			try {
				const candidate = resolve(run.cwd, pathValue);
				const admitted = admittedReadPath(store, run, candidate, [run.cwd, loadedRoot, ...policyReferences]);
				return makeArtifact(admitted, "run-path", "local_staging", run.cwd);
			} catch {
				return undefined;
			}
		},
		resolveResolvedInputField(_path, value): ReturnType<ExecutionInputBindingResolver["resolveResolvedInputField"]> {
			if (!isAbsolute(value)) return undefined;
			try {
				const admitted = admittedReadPath(store, run, value, [run.cwd, loadedRoot, ...policyReferences]);
				const reference = policyReferences.find((root) => pathInside(admitted, root));
				return makeArtifact(
					admitted,
					"resolved-input",
					reference === undefined ? "external" : "read_only_reference",
					reference ?? dirname(admitted),
				);
			} catch {
				return undefined;
			}
		},
	};
	return { context: contextValue, context_digest: contextDigest, resolver };
}

function outputRootBindingError(
	store: NodeRunStore,
	run: CoreRunRecord,
	request: ExecutionRequest,
	policyReferences: readonly string[],
): string | undefined {
	if (typeof request.output_root !== "object" || request.output_root === null) {
		return "Execution request output_root must be an object.";
	}
	const allowedOutputRoots = [run.runDir, ...(run.outputDir ? [run.outputDir] : [])];
	try {
		const admitted = store.admitPath(request.output_root.path, allowedOutputRoots);
		if (request.output_root.resolved_path !== admitted.resolvedPath) {
			return "Execution request output_root.resolved_path does not match the admitted path.";
		}
	} catch (error) {
		return error instanceof Error ? error.message : String(error);
	}
	const boundary = validateExecutionRequest(request, {
		allowed_output_roots: allowedOutputRoots,
		forbidden_output_roots: policyReferences,
	});
	return boundary.valid ? undefined : `Execution request is not admissible: ${boundary.errors.join("; ")}`;
}

function receiptOutputErrors(
	store: NodeRunStore,
	run: CoreRunRecord,
	request: ExecutionRequest,
	receipt: ExecutionReceipt,
): string[] {
	const errors: string[] = [];
	if (typeof request.output_root !== "object" || request.output_root === null) {
		errors.push("Execution request output_root must be an object.");
		return errors;
	}
	if (!Array.isArray(receipt.outputs)) return errors;
	const allowedOutputRoots = [run.runDir, ...(run.outputDir ? [run.outputDir] : [])];
	for (const output of receipt.outputs) {
		if (typeof output.path !== "string" || typeof output.resolved_path !== "string") continue;
		try {
			const admitted = store.admitPath(output.path, [request.output_root.path, ...allowedOutputRoots]);
			if (admitted.resolvedPath !== output.resolved_path) {
				errors.push(`Receipt output ${output.artifact_id} resolved path does not match the admitted path.`);
				continue;
			}
			const lexical = admitted.path;
			const inspected = inspectExecutionArtifact(lexical);
			const expectedKind = output.kind ?? "file";
			if (inspected.kind !== expectedKind)
				errors.push(`Receipt output ${output.artifact_id} kind does not match its declared artifact kind.`);
			if (output.digest_kind !== undefined && inspected.digest_kind !== output.digest_kind)
				errors.push(`Receipt output ${output.artifact_id} digest kind does not match its declared digest kind.`);
			if (!sameDigest(inspected.sha256, output.sha256))
				errors.push(`Receipt output ${output.artifact_id} digest does not match the current file.`);
			if (inspected.size !== output.size)
				errors.push(`Receipt output ${output.artifact_id} size does not match the current file.`);
		} catch (error) {
			errors.push(error instanceof Error ? error.message : String(error));
		}
	}
	return errors;
}

function assertOutputRootBinding(
	store: NodeRunStore,
	run: CoreRunRecord,
	request: ExecutionRequest,
	policyReferences: readonly string[],
): void {
	const error = outputRootBindingError(store, run, request, policyReferences);
	if (error) throw new Error(error);
}

function assertReceiptOutputFiles(
	store: NodeRunStore,
	run: CoreRunRecord,
	request: ExecutionRequest,
	receipt: ExecutionReceipt,
): void {
	const errors = receiptOutputErrors(store, run, request, receipt);
	if (errors.length > 0) throw new Error(errors.join("; "));
}

function frozenSubjectFileErrors(
	store: NodeRunStore,
	run: CoreRunRecord,
	subject: FrozenEvaluationSubject,
	policyReferences: readonly string[],
): string[] {
	const errors: string[] = [];
	try {
		const predictionPath = admittedReadArtifactPath(
			store,
			run,
			absolute(subject.prediction_path, "frozen prediction"),
			policyReferences,
		);
		assertRegularFile(predictionPath, "Frozen prediction");
		if (!sameDigest(digestFile(predictionPath), subject.prediction_digest)) {
			errors.push("Frozen prediction digest does not match the current file.");
		}
	} catch (error) {
		errors.push(error instanceof Error ? error.message : String(error));
	}
	return errors;
}

function lastValidationOutcome(state: StateDocument | undefined): Record<string, unknown> | undefined {
	if (!state || typeof state.last_validation !== "object" || state.last_validation === null) return undefined;
	const validation = state.last_validation as Record<string, unknown>;
	return typeof validation.outcome === "object" && validation.outcome !== null
		? (validation.outcome as Record<string, unknown>)
		: undefined;
}

interface FormalValidationBinding {
	validatorVerdict: "PASS" | "FAIL" | "NOT_EXECUTED";
	workflowDisposition: "ADVANCE" | "RETRY" | "BLOCK" | "TERMINATE" | "WAIT";
	diagnostics: string[];
}

/**
 * Formal conformance must consume the durable validation/execution record. A
 * caller-provided verdict is useful for legacy/non-formal inspection, but it
 * cannot manufacture evidence for a formal subject.
 */
function formalValidationBinding(
	store: NodeRunStore,
	run: CoreRunRecord,
	state: StateDocument | undefined,
	request: ExecutionRequest,
	receiptPath: string,
	policyReferences: readonly string[],
): FormalValidationBinding {
	const diagnostics: string[] = [];
	const rawValidation = state?.last_validation;
	const validation =
		typeof rawValidation === "object" && rawValidation !== null
			? (rawValidation as Record<string, unknown>)
			: undefined;
	const outcome =
		validation && typeof validation.outcome === "object" && validation.outcome !== null
			? (validation.outcome as Record<string, unknown>)
			: undefined;
	if (!validation || !outcome) {
		diagnostics.push("formal conformance requires a persisted validation outcome");
	} else {
		if (validation.evidence_source === "external") {
			diagnostics.push("formal conformance does not accept caller-supplied validator verdicts");
		}
		if (validation.evidence_source === "surectl_executor") {
			const rawEvidence = validation.evidence;
			const evidence =
				typeof rawEvidence === "object" && rawEvidence !== null && !Array.isArray(rawEvidence)
					? (rawEvidence as Record<string, unknown>)
					: undefined;
			const validators = evidence && Array.isArray(evidence.validators) ? evidence.validators : [];
			if (validators.length === 0) diagnostics.push("formal conformance requires validator execution receipts");
			for (const rawValidator of validators) {
				if (typeof rawValidator !== "object" || rawValidator === null || Array.isArray(rawValidator)) {
					diagnostics.push("persisted validator evidence is malformed");
					continue;
				}
				const validator = rawValidator as Record<string, unknown>;
				if (typeof validator.request_path !== "string" || typeof validator.receipt_path !== "string") {
					diagnostics.push("persisted validator evidence is missing request/receipt paths");
					continue;
				}
				try {
					const validatorRequestPath = admittedReadArtifactPath(store, run, validator.request_path);
					const validatorReceiptPath = admittedReadArtifactPath(store, run, validator.receipt_path);
					assertRegularFile(validatorRequestPath, "Validator execution request");
					assertRegularFile(validatorReceiptPath, "Validator execution receipt");
					if (
						typeof validator.request_digest !== "string" ||
						!sameDigest(digestFile(validatorRequestPath), validator.request_digest)
					) {
						diagnostics.push("persisted validator request digest does not match its file");
					}
					if (
						typeof validator.receipt_digest !== "string" ||
						!sameDigest(digestFile(validatorReceiptPath), validator.receipt_digest)
					) {
						diagnostics.push("persisted validator receipt digest does not match its file");
					}
					const validatorRequest = recordObject(
						readJson(validatorRequestPath),
						"validator execution request",
					) as unknown as ExecutionRequest;
					const validatorReceipt = recordObject(
						readJson(validatorReceiptPath),
						"validator execution receipt",
					) as unknown as ExecutionReceipt;
					const receiptValidation = validateExecutionReceipt(validatorRequest, validatorReceipt, {
						allowed_output_roots: [run.runDir, ...(run.outputDir ? [run.outputDir] : [])],
						forbidden_output_roots: policyReferences,
					});
					if (!receiptValidation.valid || validatorReceipt.lifecycle !== "SUCCEEDED") {
						diagnostics.push("persisted validator execution receipt is not a successful valid receipt");
					}
					if (validatorReceipt.executor.trust_level === "cooperative") {
						diagnostics.push("formal conformance requires host-enforced validator execution");
					}
				} catch (error) {
					diagnostics.push(error instanceof Error ? error.message : String(error));
				}
			}
		}
		if (validation.unit_id !== request.unit_id) {
			diagnostics.push("persisted validation unit_id does not match the formal execution request");
		}
		if (outcome.outcome !== "PASS" || outcome.validator_verdict !== "PASS") {
			diagnostics.push("persisted validation outcome is not PASS");
		}
		if (!(["ADVANCE", "TERMINATE"] as readonly unknown[]).includes(outcome.workflow_disposition)) {
			diagnostics.push("persisted validation did not authorize workflow advancement");
		}
		const artifactPath = validation.artifact_path;
		const artifactDigest = validation.artifact_digest;
		if (typeof artifactPath !== "string" || typeof artifactDigest !== "string") {
			diagnostics.push("persisted PASS validation is missing artifact binding");
		} else {
			try {
				const admittedArtifact = admittedReadArtifactPath(
					store,
					run,
					absolute(artifactPath, "persisted validation artifact"),
					policyReferences,
				);
				assertRegularFile(admittedArtifact, "Persisted validation artifact");
				if (!sameDigest(digestFile(admittedArtifact), artifactDigest)) {
					diagnostics.push("persisted validation artifact digest does not match the current file");
				}
			} catch (error) {
				diagnostics.push(error instanceof Error ? error.message : String(error));
			}
		}
	}
	const rawExecution = state?.last_execution;
	const execution =
		typeof rawExecution === "object" && rawExecution !== null ? (rawExecution as Record<string, unknown>) : undefined;
	if (!execution) {
		diagnostics.push("formal conformance requires persisted execution evidence");
	} else {
		const requestDigest = canonicalJsonDigest(request as unknown as JsonValue);
		if (execution.request_digest !== requestDigest) {
			diagnostics.push("persisted execution request digest does not match the formal request");
		}
		const receiptDigest = digestFile(receiptPath);
		if (execution.receipt_digest !== receiptDigest) {
			diagnostics.push("persisted execution receipt digest does not match the supplied receipt");
		}
	}
	if (diagnostics.length > 0) {
		return { validatorVerdict: "NOT_EXECUTED", workflowDisposition: "WAIT", diagnostics };
	}
	return { validatorVerdict: "PASS", workflowDisposition: "ADVANCE", diagnostics };
}

function start(args: ParsedArgs): void {
	const root = rootFor(args);
	const suppliedPolicy = policySnapshotFor(args);
	const loaded = currentDefinition(args, root);
	const registry = registryFor(loaded, args);
	const skillName = one(args, "skill") ?? loaded.definition.workflow_id;
	const runId =
		one(args, "run-id") ??
		`${new Date().toISOString().replace(/[-:.]/g, "").replace(/Z$/, "")}-${randomUUID().slice(0, 8)}`;
	const policyDigest = policyDigestFor(args, suppliedPolicy?.snapshot);
	const executorDigest = normalizeDigest(
		requiredValue(args, "executor-digest", "SURE_EXECUTOR_DIGEST"),
		"--executor-digest",
	);
	const bindingDigest = one(args, "binding-digest");
	const store = storeFor(args, root, {}, suppliedPolicy?.snapshot);
	const outputDir = outputRootFor(args);
	const branchId = one(args, "branch") ?? loaded.definition.default_branch_id;
	if (!loaded.definition.branches.some((branch) => branch.id === branchId))
		throw new Error(`Unknown workflow branch: ${branchId}`);
	const record = store.createRun({
		runId,
		skillName,
		command: loaded.definition.workflow_id,
		cwd: root,
		packageDir: loaded.root,
		args: one(args, "args") ?? "",
		...(outputDir === undefined ? {} : { outputDir }),
		coreVersion: CORE_VERSION,
		workflowDigest: workflowDigest(loaded.definition),
		validatorDigest: registry.digest,
		executorDigest,
		policyDigest,
		...(suppliedPolicy === undefined
			? {}
			: {
					policySnapshotDigest: suppliedPolicy.snapshot.snapshot_digest,
					policySnapshotPath: join(store.runsRoot, runId, "artifacts", "site_policy.resolved.json"),
				}),
		...(bindingDigest === undefined ? {} : { bindingDigest: normalizeDigest(bindingDigest, "--binding-digest") }),
	});
	if (suppliedPolicy !== undefined) {
		const snapshotPath = join(store.runsRoot, runId, "artifacts", "site_policy.resolved.json");
		writeJsonImmutable(snapshotPath, suppliedPolicy.snapshot);
		// Verify the bytes and file type that were published, not just the
		// in-memory object.
		runPolicySnapshot(record);
	}
	const checkpoint = initialCheckpoint(loaded.definition, branchId);
	store.writeState(
		runId,
		stateWithCheckpoint(undefined, checkpoint, {
			workflow_digest: workflowDigest(loaded.definition),
			validator_digest: registry.digest,
			definition_path: loaded.path,
		}),
		"checkpoint_started",
		{ current_unit: checkpoint.data.currentUnit },
		record.revision,
	);
	const running = store.setStatus(runId, "running", "started", (record.revision ?? 0) + 1);
	output({ ok: true, command: "start", run: running, checkpoint });
}

function status(args: ParsedArgs): void {
	const root = rootFor(args);
	const runId = required(args, "run-id");
	const { store, run } = contextForRun(args, root, runId);
	const state = store.readState(runId);
	const integrityError = stateIntegrityError(run, state);
	if (integrityError) throw new Error(integrityError);
	let checkpoint: WorkflowCheckpoint | undefined;
	let nextUnit: string | undefined;
	try {
		const loaded = currentDefinition(args, root, run.skillName);
		checkpoint = checkpointFromState(loaded.definition, state);
		const currentCheckpoint = checkpoint;
		const branch = loaded.definition.branches.find((candidate) => candidate.id === currentCheckpoint.branch_id);
		const index = branch?.units.findIndex((unit) => unit.id === currentCheckpoint.data.currentUnit) ?? -1;
		nextUnit = index >= 0 ? branch?.units[index + 1]?.id : undefined;
	} catch {
		// Status remains useful for legacy runs whose definition is no longer present.
	}
	output({ ok: true, command: "status", run, state, checkpoint, next_unit: nextUnit });
}

function validatorMatches(
	unit: WorkflowUnit,
	branchId: string,
	registry: { digest: string; validators: Array<Record<string, unknown>> },
): Array<Record<string, unknown>> {
	const entries = registry.validators.filter(
		(entry) => entry.skill_id !== undefined && entry.branch_id === branchId && entry.unit_id === unit.id,
	);
	if (unit.gate?.validator_id === "structural" && (unit.gate.auxiliary_validator_ids?.length ?? 0) === 0) return [];
	if (
		unit.gate?.execution_operation_id !== undefined &&
		unit.gate.backend_operation_id === undefined &&
		(unit.gate.auxiliary_validator_ids?.length ?? 0) === 0
	) {
		return [];
	}
	if (entries.length === 0) throw new Error(`No registered validator for ${branchId}/${unit.id}.`);
	return entries;
}

function evidenceVerdict(value: unknown): "PASS" | "FAIL" | "NOT_EXECUTED" {
	if (value === "PASS" || value === "pass") return "PASS";
	if (value === "FAIL" || value === "fail") return "FAIL";
	if (value === "NOT_EXECUTED" || value === "not_executed") return "NOT_EXECUTED";
	throw new Error("Validator evidence verdict must be PASS, FAIL, or NOT_EXECUTED.");
}

function validateGateEvidence(
	path: string | undefined,
	unit: WorkflowUnit,
	branchId: string,
	registry: { digest: string; validators: Array<Record<string, unknown>> },
	artifactDigest: string,
): { verdict: "PASS" | "FAIL" | "NOT_EXECUTED"; reason?: string; evidence: unknown } {
	if (!path) return { verdict: "NOT_EXECUTED", reason: "registered validator evidence is missing", evidence: null };
	const evidence = recordObject(readJson(path), "validator evidence");
	const entries = Array.isArray(evidence.validators)
		? evidence.validators.filter(
				(entry): entry is Record<string, unknown> =>
					typeof entry === "object" && entry !== null && !Array.isArray(entry),
			)
		: [evidence];
	if (
		evidence.registry_digest !== undefined &&
		normalizeDigest(String(evidence.registry_digest), "evidence registry_digest") !== registry.digest
	) {
		return { verdict: "NOT_EXECUTED", reason: "validator registry digest mismatch", evidence };
	}
	const expected = validatorMatches(unit, branchId, registry);
	const results: Array<"PASS" | "FAIL" | "NOT_EXECUTED"> = [];
	for (const descriptor of expected) {
		const match = entries.find((entry) => entry.validator_id === descriptor.id);
		if (!match) return { verdict: "NOT_EXECUTED", reason: `missing evidence for ${String(descriptor.id)}`, evidence };
		if (match.artifact_digest !== undefined && match.artifact_digest !== artifactDigest) {
			return { verdict: "NOT_EXECUTED", reason: `artifact digest mismatch for ${String(descriptor.id)}`, evidence };
		}
		results.push(evidenceVerdict(match.verdict));
	}
	if (results.includes("NOT_EXECUTED"))
		return { verdict: "NOT_EXECUTED", reason: "validator capability was not executed", evidence };
	if (results.includes("FAIL")) return { verdict: "FAIL", reason: "registered validator failed", evidence };
	return { verdict: "PASS", evidence };
}

function registeredValidatorDescriptors(
	unit: WorkflowUnit,
	branchId: string,
	registry: { digest: string; validators: Array<Record<string, unknown>> },
): RegisteredValidatorDescriptor[] {
	return validatorMatches(unit, branchId, registry).map((raw) => {
		if (typeof raw.id !== "string" || raw.id.trim() === "") throw new Error("Registered validator has no id.");
		if (raw.backend_operation_id !== undefined && typeof raw.backend_operation_id !== "string") {
			throw new Error(`Registered validator ${raw.id} has an invalid backend operation id.`);
		}
		if (
			raw.script_args !== undefined &&
			(!Array.isArray(raw.script_args) || raw.script_args.some((value) => typeof value !== "string"))
		) {
			throw new Error(`Registered validator ${raw.id} has invalid script arguments.`);
		}
		return {
			id: raw.id,
			...(typeof raw.skill_id === "string" ? { skill_id: raw.skill_id } : {}),
			...(typeof raw.branch_id === "string" ? { branch_id: raw.branch_id } : {}),
			...(typeof raw.unit_id === "string" ? { unit_id: raw.unit_id } : {}),
			...(typeof raw.backend_operation_id === "string" ? { backend_operation_id: raw.backend_operation_id } : {}),
			...(Array.isArray(raw.script_args) ? { script_args: raw.script_args as string[] } : {}),
		};
	});
}

function validatorReferenceDigest(run: CoreRunRecord, policyReferences: readonly string[]): string {
	return (
		run.policySnapshotDigest ??
		canonicalJsonDigest({
			schema: "sure.reference.binding.v1",
			policy_digest: run.policyDigest ?? canonicalJsonDigest(null),
			roots: [...policyReferences].sort(),
		} as unknown as JsonValue)
	);
}

function automaticGateValidation(
	args: ParsedArgs,
	store: NodeRunStore,
	run: CoreRunRecord,
	loaded: LoadedDefinition,
	unit: WorkflowUnit,
	branchId: string,
	checkpoint: WorkflowCheckpoint,
	registry: { digest: string; validators: Array<Record<string, unknown>> },
	artifactPath: string,
	policyReferences: readonly string[],
): { result: RegisteredValidationResult; evidence_path: string; evidence_digest: string } {
	const validators = registeredValidatorDescriptors(unit, branchId, registry);
	const artifact = artifactRef(artifactPath, unit.id, run.runDir);
	const invocationId = randomUUID().replaceAll("-", "").slice(0, 16);
	const invocationRoot = admittedRunArtifactPath(
		store,
		run,
		join(run.runDir, "artifacts", "validation", unit.id, invocationId),
	);
	let result = unavailableRegisteredValidation(
		registry.digest,
		validators,
		artifact.sha256,
		"skill runtime binding was not loaded",
	);
	let runtimeBinding: ReturnType<typeof loadSkillRuntimeBinding> | undefined;
	try {
		runtimeBinding = loadSkillRuntimeBinding(loaded.root, {
			skill_id: loaded.definition.workflow_id,
			workflow_digest: run.workflowDigest ?? workflowDigest(loaded.definition),
			validator_registry_digest: registry.digest,
			core_version: run.coreVersion ?? CORE_VERSION,
		});
	} catch (error) {
		result = unavailableRegisteredValidation(
			registry.digest,
			validators,
			artifact.sha256,
			error instanceof Error ? error.message : String(error),
		);
	}
	if (runtimeBinding !== undefined) {
		const runtimeValue = one(args, "semantic-runtime") ?? process.env.SURE_RUNTIME_SUPPORT_ROOT;
		const runtimeRoot = runtimeValue === undefined ? undefined : absolute(runtimeValue, "--semantic-runtime");
		const artifactsRoot = admittedRunArtifactPath(store, run, join(run.runDir, "artifacts"));
		const artifactsAdmission = store.admitPath(artifactsRoot, [run.runDir]);
		result = runRegisteredValidators({
			runtime_root: runtimeRoot,
			runtime_binding: runtimeBinding,
			run,
			branch_id: branchId,
			unit_id: unit.id,
			attempt: (checkpoint.data.retries[unit.id] ?? 0) + 1,
			artifact,
			validators,
			validator_registry_digest: registry.digest,
			python_executable:
				one(args, "validator-python") ?? process.env.HARNESS_PYTHON_BIN ?? process.env.PYTHON ?? "python3",
			package_dir: loaded.root,
			workspace_root: run.cwd,
			artifacts_root: artifactsRoot,
			artifacts_resolved_root: artifactsAdmission.resolvedPath,
			reference_snapshot_digest: validatorReferenceDigest(run, policyReferences),
			policy_digest: run.policyDigest ?? canonicalJsonDigest(null),
			forbidden_output_roots: policyReferences,
			invocation_id: invocationId,
			created_at: new Date().toISOString(),
			persist_request(key, request) {
				const path = admittedRunArtifactPath(store, run, join(invocationRoot, `${key}.request.json`));
				writeJsonImmutable(path, request);
				return { path, digest: digestFile(path) };
			},
			persist_receipt(key, receipt) {
				const path = admittedRunArtifactPath(store, run, join(invocationRoot, `${key}.receipt.json`));
				writeJsonImmutable(path, receipt);
				return { path, digest: digestFile(path) };
			},
		});
	}
	const evidencePath = admittedRunArtifactPath(store, run, join(invocationRoot, "validator-evidence.json"));
	writeJsonImmutable(evidencePath, result.evidence);
	return { result, evidence_path: evidencePath, evidence_digest: digestFile(evidencePath) };
}

interface ExecutionGateValidation {
	verdict: "PASS" | "FAIL" | "NOT_EXECUTED";
	reason: string;
	evidence: unknown;
	evidence_path?: string;
	evidence_digest?: string;
	lifecycle?: ExecutionReceipt["lifecycle"];
}

function executionGateUnavailable(reason: string, evidence: unknown = null): ExecutionGateValidation {
	return { verdict: "NOT_EXECUTED", reason, evidence };
}

function sameStrings(actual: readonly string[], expected: readonly string[]): boolean {
	return actual.length === expected.length && actual.every((value, index) => value === expected[index]);
}

/**
 * Re-admit a registered operation receipt against the current run and locked
 * runtime. A generic caller-authored execution request can never satisfy this
 * check because it lacks the Core-written registered_operation state binding.
 */
function validateRegisteredGateExecution(
	args: ParsedArgs,
	store: NodeRunStore,
	run: CoreRunRecord,
	state: StateDocument | undefined,
	loaded: LoadedDefinition,
	unit: WorkflowUnit,
	branchId: string,
	checkpoint: WorkflowCheckpoint,
	artifactPath: string,
	artifactDigest: string,
	policyReferences: readonly string[],
): ExecutionGateValidation {
	let operationId = unit.gate?.execution_operation_id;
	if (operationId === undefined && unit.gate?.execution_dispatch !== undefined) {
		try {
			const firstCase = unit.gate.execution_dispatch[0];
			if (firstCase === undefined) throw new Error("execution dispatch has no cases");
			const context = registeredInputContext(
				store,
				run,
				firstCase.input_contract,
				policyReferences,
				loaded.root,
				validatorReferenceDigest(run, policyReferences),
			);
			operationId = selectExecutionDispatch(unit.gate.execution_dispatch, context.context).operation_id;
		} catch (error) {
			return executionGateUnavailable(error instanceof Error ? error.message : String(error));
		}
	}
	if (operationId === undefined) return { verdict: "PASS", reason: "no execution operation required", evidence: null };
	const rawExecution = state?.last_execution;
	const decodedExecution = decodeOperationExecutionEvidence(rawExecution);
	if (!decodedExecution.ok) {
		return executionGateUnavailable(`registered execution ${operationId} has not run`);
	}
	const persisted = decodedExecution.evidence;
	if (persisted.source !== "registered_operation" || persisted.operation_id !== operationId) {
		return executionGateUnavailable(`current gate requires registered execution ${operationId}`, persisted);
	}
	const expectedAttempt = (checkpoint.data.retries[unit.id] ?? 0) + 1;
	if (persisted.unit_id !== unit.id || persisted.branch_id !== branchId || persisted.attempt !== expectedAttempt) {
		return executionGateUnavailable("registered execution does not match the current gate attempt", persisted);
	}
	const runtimeValue = one(args, "semantic-runtime") ?? process.env.SURE_RUNTIME_SUPPORT_ROOT;
	if (runtimeValue === undefined) {
		return executionGateUnavailable("portable semantic operation runtime was not provided", persisted);
	}
	const runtimeRoot = absolute(runtimeValue, "--semantic-runtime");
	let runtimeBinding: ReturnType<typeof loadSkillRuntimeBinding>;
	let verification: ReturnType<typeof verifyPortableRuntime>;
	let operation: ReturnType<typeof resolveSemanticBackendOperation>;
	try {
		runtimeBinding = loadSkillRuntimeBinding(loaded.root, {
			skill_id: loaded.definition.workflow_id,
			workflow_digest: run.workflowDigest ?? workflowDigest(loaded.definition),
			validator_registry_digest: run.validatorDigest ?? "",
			core_version: run.coreVersion ?? CORE_VERSION,
		});
		verification = verifyPortableRuntime(runtimeRoot, {
			expected_runtime_digest: runtimeBinding.semantic_runtime_digest,
			expected_core_package_version: runtimeBinding.core_package_version,
			expected_semantic_backend_registry_digest: runtimeBinding.semantic_backend_registry_digest,
			expected_executor_registry_digest: runtimeBinding.executor_registry_digest,
		});
		const environment = semanticRuntimeEnvironment(
			{
				run,
				workspace_root: run.cwd,
				policy_digest: run.policyDigest ?? canonicalJsonDigest(null),
			},
			verification.root,
		);
		operation = resolveSemanticBackendOperation(verification.root, operationId, {
			manifestPath: join(verification.root, "semantic-backends.json"),
			expectedRegistryDigest: verification.lock.semantic_backend_registry_digest,
			environment,
		});
		if (operation.kind !== "execute") throw new Error(`${operationId} is not an execute operation`);
		if (operation.artifact_mode === undefined) throw new Error(`${operationId} does not declare an artifact_mode`);
		if (!operation.consumer_skill_ids.includes(runtimeBinding.skill_id)) {
			throw new Error(`${runtimeBinding.skill_id} is not an admitted consumer of ${operationId}`);
		}
	} catch (error) {
		return executionGateUnavailable(error instanceof Error ? error.message : String(error), persisted);
	}
	const diagnostics: string[] = [];
	for (const [field, expected] of [
		["runtime_digest", verification.lock.runtime_digest],
		["backend_registry_digest", operation.registry_digest],
		["backend_bundle_digest", operation.bundle_digest],
		["backend_resource_digest", operation.resource_digest],
	] as const) {
		if (expected !== undefined && persisted[field] !== expected)
			diagnostics.push(`${field} does not match locked operation`);
	}
	const artifactInputDigest =
		typeof persisted.artifact_input_digest === "string" ? persisted.artifact_input_digest : undefined;
	if (artifactInputDigest === undefined) diagnostics.push("registered execution is missing artifact_input_digest");
	const operationOutputContract = operation.output_contract;
	const inputPath = typeof persisted.artifact_input_path === "string" ? persisted.artifact_input_path : artifactPath;
	const outputPath =
		typeof persisted.artifact_output_path === "string" ? persisted.artifact_output_path : artifactPath;
	if (operationOutputContract !== undefined && typeof persisted.artifact_output_path !== "string") {
		diagnostics.push("producer execution is missing artifact_output_path");
	}
	// The registered operation may legitimately update the gate artifact in
	// place (for example, a runtime runner appends its measured result).  The
	// input digest below binds the pre-execution bytes; the receipt output digest
	// binds the post-execution bytes.  Comparing the input to the current file
	// would reject that valid producer/runner contract and conflate mutation
	// with tampering.
	const requestPathValue = typeof persisted.request_path === "string" ? persisted.request_path : undefined;
	const receiptPathValue = typeof persisted.receipt_path === "string" ? persisted.receipt_path : undefined;
	if (requestPathValue === undefined || receiptPathValue === undefined) {
		return executionGateUnavailable("registered execution has no complete request/receipt pair", persisted);
	}
	let requestPath: string;
	let receiptPath: string;
	let request: ExecutionRequest;
	let receipt: ExecutionReceipt;
	try {
		requestPath = admittedRunArtifactPath(store, run, requestPathValue);
		receiptPath = admittedRunArtifactPath(store, run, receiptPathValue);
		assertRegularFile(requestPath, "Registered execution request");
		assertRegularFile(receiptPath, "Registered execution receipt");
		if (
			typeof persisted.request_digest !== "string" ||
			!sameDigest(digestFile(requestPath), persisted.request_digest)
		) {
			diagnostics.push("registered execution request digest does not match its file");
		}
		if (
			typeof persisted.receipt_digest !== "string" ||
			!sameDigest(digestFile(receiptPath), persisted.receipt_digest)
		) {
			diagnostics.push("registered execution receipt digest does not match its file");
		}
		request = recordObject(readJson(requestPath), "registered execution request") as unknown as ExecutionRequest;
		receipt = recordObject(readJson(receiptPath), "registered execution receipt") as unknown as ExecutionReceipt;
	} catch (error) {
		return executionGateUnavailable(error instanceof Error ? error.message : String(error), persisted);
	}
	const requestOperation = unit.gate?.execution_request_operation ?? "validation";
	if (
		request.run_id !== run.runId ||
		request.unit_id !== unit.id ||
		request.attempt !== expectedAttempt ||
		request.operation !== requestOperation
	) {
		diagnostics.push("execution request does not match the current run, unit, attempt, or operation domain");
	}
	if (operationOutputContract !== undefined) {
		let requestContractDigest: string | undefined;
		try {
			requestContractDigest =
				request.output_contract === undefined ? undefined : executionOutputContractDigest(request.output_contract);
		} catch {
			requestContractDigest = undefined;
		}
		const expectedContractDigest = executionOutputContractDigest(operationOutputContract);
		if (requestContractDigest !== expectedContractDigest) {
			diagnostics.push("execution request output_contract does not match the registered operation");
		}
		const declaredOutput = operationOutputContract.outputs[0];
		const requestRootResolved =
			typeof request.output_root?.resolved_path === "string" ? request.output_root.resolved_path : undefined;
		if (declaredOutput !== undefined) {
			if (requestRootResolved === undefined) {
				diagnostics.push("execution request output_root.resolved_path is missing");
			} else if (resolve(requestRootResolved, declaredOutput.path) !== resolve(outputPath)) {
				diagnostics.push("registered execution output path does not match the output contract");
			}
		}
	}
	const expectedCapabilityRequirementsDigest = registeredOperationCapabilityRequirementsDigest(operation);
	if (expectedCapabilityRequirementsDigest !== undefined) {
		let requestCapabilityRequirementsDigest: string | undefined;
		try {
			requestCapabilityRequirementsDigest = Array.isArray(request.capability_requirements)
				? canonicalJsonDigest(request.capability_requirements as unknown as JsonValue)
				: undefined;
		} catch {
			requestCapabilityRequirementsDigest = undefined;
		}
		if (requestCapabilityRequirementsDigest !== expectedCapabilityRequirementsDigest) {
			diagnostics.push("execution request capability requirements do not match the registered operation");
		}
	}
	const expectedPolicyDigest = run.policyDigest ?? canonicalJsonDigest(null);
	const expectedReferenceDigest = validatorReferenceDigest(run, policyReferences);
	let expectedInputBindingDigest: string | undefined;
	let expectedInputContractDigest: string | undefined;
	let expectedInputSelectorId: string | undefined;
	let expectedInputContextDigest: string | undefined;
	if (operation.input_contract !== undefined) {
		expectedInputContractDigest = executionInputContractDigest(operation.input_contract);
		if (request.input_binding === undefined) {
			diagnostics.push("execution request is missing the registered operation input binding");
		} else {
			const bindingValidation = validateExecutionInputBinding(request.input_binding, request.inputs);
			diagnostics.push(...bindingValidation.errors);
			try {
				const context = registeredInputContext(
					store,
					run,
					operation.input_contract,
					policyReferences,
					loaded.root,
					expectedReferenceDigest,
				);
				const rebound = bindExecutionInputs({
					contract: operation.input_contract,
					context: context.context,
					context_digest: context.context_digest,
					resolver: context.resolver,
				});
				expectedInputBindingDigest = rebound.binding_digest;
				expectedInputSelectorId = rebound.selector_id;
				expectedInputContextDigest = rebound.context_digest;
				if (request.input_binding.binding_digest !== rebound.binding_digest) {
					diagnostics.push("execution request input binding does not match current context/artifact bytes");
				}
				if (request.input_binding.selector_id !== rebound.selector_id) {
					diagnostics.push("execution request input selector does not match current context");
				}
			} catch (error) {
				diagnostics.push(error instanceof Error ? error.message : String(error));
			}
			if (request.input_binding.contract_digest !== expectedInputContractDigest) {
				diagnostics.push("execution request input contract does not match the locked operation");
			}
		}
	}
	const runtimeRequirements =
		typeof request.runtime_requirements === "object" && request.runtime_requirements !== null
			? (request.runtime_requirements as Record<string, unknown>)
			: {};
	if (decodedExecution.compatibility === "current-v2") {
		if (runtimeRequirements.artifact_mode !== operation.artifact_mode) {
			diagnostics.push("execution request artifact_mode does not match the locked operation");
		}
	} else if (runtimeRequirements.artifact_mode !== undefined) {
		diagnostics.push("legacy operation evidence cannot bind a current execution request projection");
	}
	if (
		!validDigestValue(request.reference_snapshot_digest) ||
		!sameDigest(request.reference_snapshot_digest, expectedReferenceDigest)
	) {
		diagnostics.push("execution request reference_snapshot_digest does not match the run binding");
	}
	if (!validDigestValue(request.policy_digest) || !sameDigest(request.policy_digest, expectedPolicyDigest)) {
		diagnostics.push("execution request policy_digest does not match the run binding");
	}
	const requestSurface = runtimeRequirements.execution_surface;
	if (requestSurface === "vc" || requestSurface === "remote" || requestSurface === "trusted") {
		if (run.policySnapshotDigest === undefined) {
			diagnostics.push("external execution requires a site-policy snapshot bound to the run");
		} else if (
			!validDigestValue(request.policy_snapshot_digest) ||
			!sameDigest(request.policy_snapshot_digest, run.policySnapshotDigest)
		) {
			diagnostics.push("execution request policy_snapshot_digest does not match the run binding");
		}
	}
	const requestOutputRoot = request.output_root;
	if (
		typeof requestOutputRoot !== "object" ||
		requestOutputRoot === null ||
		!validDigestValue(requestOutputRoot.policy_digest) ||
		!sameDigest(requestOutputRoot.policy_digest, expectedPolicyDigest)
	) {
		diagnostics.push("execution request output_root policy_digest does not match the run binding");
	}
	if (
		!validDigestValue(receipt.reference_snapshot_digest) ||
		!sameDigest(receipt.reference_snapshot_digest, expectedReferenceDigest)
	) {
		diagnostics.push("execution receipt reference_snapshot_digest does not match the run binding");
	}
	if (!validDigestValue(receipt.policy_digest) || !sameDigest(receipt.policy_digest, expectedPolicyDigest)) {
		diagnostics.push("execution receipt policy_digest does not match the run binding");
	}
	if (requestSurface === "vc" || requestSurface === "remote" || requestSurface === "trusted") {
		if (
			!validDigestValue(receipt.policy_snapshot_digest) ||
			!sameDigest(receipt.policy_snapshot_digest, run.policySnapshotDigest ?? "")
		) {
			diagnostics.push("execution receipt policy_snapshot_digest does not match the run binding");
		}
	}
	for (const [field, expected] of [
		["semantic_backend_operation_id", operation.operation_id],
		["semantic_backend_registry_digest", operation.registry_digest],
		["semantic_backend_bundle_digest", operation.bundle_digest],
		["semantic_backend_resource_digest", operation.resource_digest],
		["portable_runtime_digest", verification.lock.runtime_digest],
		["workflow_digest", run.workflowDigest],
		["branch_id", branchId],
		["artifact_mode", decodedExecution.compatibility === "current-v2" ? operation.artifact_mode : undefined],
		["artifact_input_digest", artifactInputDigest],
		["artifact_input_path", operationOutputContract === undefined ? undefined : inputPath],
		["artifact_output_path", operationOutputContract === undefined ? undefined : outputPath],
		[
			"output_contract_digest",
			operationOutputContract === undefined ? undefined : executionOutputContractDigest(operationOutputContract),
		],
		["capability_requirements_digest", expectedCapabilityRequirementsDigest],
		["input_contract_digest", expectedInputContractDigest],
		["input_selector_id", expectedInputSelectorId],
		["input_context_digest", expectedInputContextDigest],
		["input_binding_digest", expectedInputBindingDigest],
	] as const) {
		if (expected !== undefined && runtimeRequirements[field] !== expected) {
			diagnostics.push(`execution request ${field} does not match its canonical binding`);
		}
	}
	const scriptArgs = [...(unit.gate?.script_args ?? [])];
	const requestScriptArgs = Array.isArray(runtimeRequirements.script_args)
		? runtimeRequirements.script_args.filter((value): value is string => typeof value === "string")
		: [];
	if (!sameStrings(requestScriptArgs, scriptArgs))
		diagnostics.push("execution request script_args do not match the gate");
	const expectedArgv = [operation.path, "--run-dir", run.runDir, "--produces", outputPath, ...scriptArgs];
	if (!sameStrings(request.entrypoint?.argv ?? [], expectedArgv)) {
		diagnostics.push("execution request entrypoint arguments do not match the locked operation");
	}
	if (request.entrypoint?.working_directory !== loaded.root) {
		diagnostics.push("execution request working directory does not match the skill package");
	}
	if (request.entrypoint?.executable !== runtimeRequirements.harness_python_executable) {
		diagnostics.push("execution request executable does not match its harness Python binding");
	}
	if (
		artifactInputDigest !== undefined &&
		(request.subject?.bundle_manifest_path !== inputPath || request.subject?.bundle_digest !== artifactInputDigest)
	) {
		diagnostics.push("execution request subject does not match the pre-execution artifact");
	}
	const input = Array.isArray(request.inputs)
		? request.inputs.find((candidate) => candidate.path === inputPath && candidate.sha256 === artifactInputDigest)
		: undefined;
	if (
		artifactInputDigest !== undefined &&
		(input === undefined || input.path !== inputPath || input.sha256 !== artifactInputDigest)
	) {
		diagnostics.push("execution request input does not match the pre-execution artifact");
	}
	if (artifactInputDigest !== undefined) {
		const semanticDigest = registeredOperationSemanticDigest({
			run_id: run.runId,
			branch_id: branchId,
			unit_id: unit.id,
			attempt: expectedAttempt,
			operation_id: operation.operation_id,
			request_operation: requestOperation,
			...(decodedExecution.compatibility === "current-v2" ? { artifact_mode: operation.artifact_mode } : {}),
			artifact_input_digest: artifactInputDigest,
			...(operationOutputContract === undefined ? {} : { artifact_input_path: inputPath }),
			...(operationOutputContract === undefined ? {} : { artifact_output_path: outputPath }),
			...(operationOutputContract === undefined
				? {}
				: { output_contract_digest: executionOutputContractDigest(operationOutputContract) }),
			...(expectedCapabilityRequirementsDigest === undefined
				? {}
				: { capability_requirements_digest: expectedCapabilityRequirementsDigest }),
			...(expectedInputContractDigest === undefined
				? {}
				: {
						input_contract_digest: expectedInputContractDigest,
						input_selector_id: expectedInputSelectorId,
						input_context_digest: expectedInputContextDigest,
						input_binding_digest: expectedInputBindingDigest,
					}),
			workflow_digest: run.workflowDigest,
			runtime_digest: verification.lock.runtime_digest,
			backend_registry_digest: operation.registry_digest,
			...(operation.bundle_digest === undefined ? {} : { backend_bundle_digest: operation.bundle_digest }),
			backend_resource_digest: operation.resource_digest,
			reference_snapshot_digest: expectedReferenceDigest,
			script_args: scriptArgs,
			policy_digest: expectedPolicyDigest,
			...(run.policySnapshotDigest === undefined ? {} : { policy_snapshot_digest: run.policySnapshotDigest }),
		});
		if (!sameDigest(request.semantic_request_digest, semanticDigest)) {
			diagnostics.push("execution request semantic digest does not match the canonical operation binding");
		}
	}
	for (const [field, expected] of [
		["input_contract_digest", expectedInputContractDigest],
		["input_selector_id", expectedInputSelectorId],
		["input_context_digest", expectedInputContextDigest],
		["input_binding_digest", expectedInputBindingDigest],
	] as const) {
		if (expected !== undefined && persisted[field] !== expected) {
			diagnostics.push(`registered execution ${field} does not match the current input binding`);
		}
	}
	const boundaryOptions = {
		allowed_output_roots: [run.runDir, ...(run.outputDir ? [run.outputDir] : [])],
		forbidden_output_roots: policyReferences,
	};
	const receiptValidation = validateExecutionReceipt(request, receipt, boundaryOptions);
	diagnostics.push(...receiptValidation.errors);
	const output = (Array.isArray(receipt.outputs) ? receipt.outputs : []).find(
		(candidate) => resolve(candidate.path) === resolve(outputPath),
	);
	if (output === undefined || !sameDigest(output.sha256, artifactDigest)) {
		diagnostics.push("execution receipt does not bind the current gate artifact digest");
	}
	const evidenceAdmission = admitOperationExecutionEvidence(decodedExecution, {
		expected_operation_id: operation.operation_id,
		expected_artifact_mode: operation.artifact_mode,
		...(output === undefined ? {} : { receipt_output_digest: output.sha256 }),
	});
	if (!evidenceAdmission.ok) diagnostics.push(...evidenceAdmission.errors);
	if (run.executorDigest && (!receipt.executor || !sameDigest(receipt.executor.digest, run.executorDigest))) {
		diagnostics.push("execution receipt executor digest does not match the run binding");
	}
	if (diagnostics.length > 0) {
		return executionGateUnavailable("registered execution evidence failed admission", {
			...persisted,
			diagnostics,
		});
	}
	if (!receiptValidation.capability.admitted || receipt.lifecycle === "NOT_STARTED") {
		return executionGateUnavailable("registered execution capability was not available", persisted);
	}
	if (receipt.lifecycle === "CANCELLED" || receipt.lifecycle === "QUEUED" || receipt.lifecycle === "RUNNING") {
		return {
			verdict: "NOT_EXECUTED",
			reason: `registered execution ended in ${receipt.lifecycle}`,
			evidence: persisted,
			evidence_path: receiptPath,
			evidence_digest: digestFile(receiptPath),
			lifecycle: receipt.lifecycle,
		};
	}
	if (receipt.lifecycle !== "SUCCEEDED") {
		return {
			verdict: "FAIL",
			reason: `registered execution ended in ${receipt.lifecycle}`,
			evidence: persisted,
			evidence_path: receiptPath,
			evidence_digest: digestFile(receiptPath),
			lifecycle: receipt.lifecycle,
		};
	}
	return {
		verdict: "PASS",
		reason: "registered execution receipt passed",
		evidence: evidenceAdmission.ok ? evidenceAdmission.evidence : persisted,
		evidence_path: receiptPath,
		evidence_digest: digestFile(receiptPath),
		lifecycle: receipt.lifecycle,
	};
}

function validate(args: ParsedArgs): PublicOutcome {
	const root = rootFor(args);
	const runId = required(args, "run-id");
	const { store, run, policySnapshot } = contextForRun(args, root, runId);
	const policyReferences = referenceRoots(args, policySnapshot);
	const state = store.readState(runId);
	const loaded = currentDefinition(args, root, run.skillName);
	const registry = registryFor(loaded, args);
	if (run.validatorDigest !== registry.digest)
		throw new Error("Run validator digest does not match the registered validator set.");
	const checkpoint = checkpointFromState(loaded.definition, state);
	const integrityError = stateIntegrityError(run, state);
	if (integrityError) {
		const outcome = createOutcome({
			validatorVerdict: "NOT_EXECUTED",
			workflowDisposition: "BLOCK",
			reasonCode: "INVALID_CONTRACT",
			diagnostics: [{ code: "STATE_DIGEST_MISMATCH", message: integrityError }],
		});
		output({ ok: false, command: "validate", run, outcome, checkpoint });
		return outcome.outcome;
	}
	const checkpointAudit = auditCheckpointState(loaded.definition, checkpoint);
	if (!checkpointAudit.ok) {
		const outcome = createOutcome({
			validatorVerdict: "NOT_EXECUTED",
			workflowDisposition: "BLOCK",
			reasonCode: "INVALID_CONTRACT",
			diagnostics: [{ code: "CHECKPOINT_INVALID", message: checkpointAudit.reason ?? "Invalid checkpoint." }],
		});
		output({ ok: false, command: "validate", run, outcome, checkpoint });
		return outcome.outcome;
	}
	const unit = unitForCurrent(loaded.definition, checkpoint.data.currentUnit, checkpoint.branch_id);
	const executionRequestPath = one(args, "execution-request");
	const executionReceiptPath = one(args, "execution-receipt");
	if (executionReceiptPath && !executionRequestPath)
		throw new Error("--execution-receipt requires --execution-request.");
	if (executionRequestPath) {
		const requestPath = admittedRunArtifactPath(store, run, absolute(executionRequestPath, "--execution-request"));
		assertRegularFile(requestPath, "Execution request");
		const request = recordObject(readJson(requestPath), "execution request") as unknown as ExecutionRequest;
		if (request.run_id !== runId) throw new Error("Execution request run_id does not match the selected run.");
		if (request.unit_id !== unit.id)
			throw new Error(`Execution request unit_id ${request.unit_id} is not the current unit ${unit.id}.`);
		const allowedOutputRoots = [run.runDir, ...(run.outputDir ? [run.outputDir] : [])];
		const boundaryOptions = {
			allowed_output_roots: allowedOutputRoots,
			forbidden_output_roots: policyReferences,
		};
		const requestValidation = validateExecutionRequest(request, boundaryOptions);
		let execution = requestValidation;
		let receiptPath: string | undefined;
		let receipt: ExecutionReceipt | undefined;
		if (executionReceiptPath) {
			receiptPath = admittedRunArtifactPath(store, run, absolute(executionReceiptPath, "--execution-receipt"));
			assertRegularFile(receiptPath, "Execution receipt");
			receipt = recordObject(readJson(receiptPath), "execution receipt") as unknown as ExecutionReceipt;
			const baseReceiptValidation = validateExecutionReceipt(request, receipt, boundaryOptions);
			const outputRootError = outputRootBindingError(store, run, request, policyReferences);
			const boundaryErrors = [
				...(outputRootError === undefined ? [] : [outputRootError]),
				...receiptOutputErrors(store, run, request, receipt),
			];
			const receiptValidation =
				boundaryErrors.length === 0
					? baseReceiptValidation
					: {
							...baseReceiptValidation,
							valid: false,
							errors: [...baseReceiptValidation.errors, ...boundaryErrors],
							outcome: createOutcome({
								validatorVerdict: "NOT_EXECUTED",
								workflowDisposition: "BLOCK",
								reasonCode: boundaryErrors.some((message) => /outside|root|symlink|reference/i.test(message))
									? "PATH_OUT_OF_SCOPE"
									: "INVALID_CONTRACT",
								diagnostics: boundaryErrors.map((message) => ({
									code: "EXECUTION_EVIDENCE_REJECTED",
									message,
								})),
							}),
						};
			if (
				run.executorDigest &&
				receipt.executor?.digest &&
				!sameDigest(run.executorDigest, receipt.executor.digest)
			) {
				receiptValidation.errors = [
					...receiptValidation.errors,
					"receipt executor digest does not match run executor digest",
				];
				receiptValidation.valid = false;
			}
			execution = receiptValidation;
		}
		const nextState = {
			...(state ?? {}),
			last_execution: {
				request_path: requestPath,
				request_digest: canonicalJsonDigest(request as unknown as JsonValue),
				...(receiptPath === undefined
					? {}
					: { receipt_path: receiptPath, receipt_digest: digestFile(receiptPath) }),
				outcome: execution.outcome,
				capability: "capability" in execution ? execution.capability : undefined,
			},
		};
		const updated = store.writeState(
			runId,
			nextState,
			"execution_validated",
			{
				unit_id: unit.id,
				outcome: execution.outcome,
			},
			run.revision,
		);
		output({
			ok: execution.valid && execution.outcome.outcome === "PASS",
			command: "validate",
			kind: "execution",
			run: updated,
			unit: unit.id,
			outcome: execution.outcome,
			execution,
			checkpoint,
		});
		return execution.outcome.outcome;
	}
	const artifactPath = admittedRunArtifactPath(
		store,
		run,
		absolute(one(args, "artifact") ?? join(run.runDir, "artifacts", unit.produces), "artifact path"),
	);
	const artifactDigest = existsSync(artifactPath) ? digestFile(artifactPath) : undefined;
	let structural: StructuralValidationResult = { ok: true };
	let artifact: unknown;
	if (!existsSync(artifactPath)) {
		structural = { ok: false, missing: true, reason: "artifact missing", repair: undefined };
	} else {
		try {
			artifact = readJson(artifactPath);
		} catch {
			structural = { ok: false, missing: false, reason: "artifact is not valid JSON", repair: undefined };
		}
		if (structural.ok) {
			let schema: Record<string, unknown> | undefined;
			const schemaValue = one(args, "schema");
			const schemaPath = schemaValue
				? admittedReadPath(store, run, absolute(schemaValue, "--schema"), [loaded.root])
				: unit.schema_ref
					? join(loaded.root, "schemas", unit.schema_ref)
					: undefined;
			const admittedSchemaPath =
				schemaPath === undefined ? undefined : admittedReadPath(store, run, schemaPath, [loaded.root]);
			if (admittedSchemaPath && existsSync(admittedSchemaPath))
				schema = recordObject(readJson(admittedSchemaPath), "structural schema");
			const checked = validateStructuralArtifact(unit, artifact, schema);
			structural = { ...checked };
		}
	}
	let validatorVerdict: "PASS" | "FAIL" | "NOT_EXECUTED" = structural.ok
		? "PASS"
		: structural.missing
			? "NOT_EXECUTED"
			: "FAIL";
	let reason = structural.reason ?? "structural validation passed";
	let evidence: unknown = null;
	let evidenceSource = "surectl_structural";
	let evidencePath: string | undefined;
	let evidenceDigest: string | undefined;
	let notExecutedReasonCode: "CAPABILITY_MISSING" | "VALIDATION_PENDING" = "VALIDATION_PENDING";
	let executionLifecycle: ExecutionReceipt["lifecycle"] | undefined;
	if (structural.ok && unit.kind === "gate") {
		let executionEvidence: ExecutionGateValidation | undefined;
		if (unit.gate?.execution_operation_id !== undefined) {
			executionEvidence = validateRegisteredGateExecution(
				args,
				store,
				run,
				state,
				loaded,
				unit,
				checkpoint.branch_id,
				checkpoint,
				artifactPath,
				artifactDigest ?? "",
				policyReferences,
			);
			validatorVerdict = executionEvidence.verdict;
			reason = executionEvidence.reason;
			evidence = executionEvidence.evidence;
			evidenceSource = "surectl_operation";
			evidencePath = executionEvidence.evidence_path;
			evidenceDigest = executionEvidence.evidence_digest;
			executionLifecycle = executionEvidence.lifecycle;
			notExecutedReasonCode = "CAPABILITY_MISSING";
		}
		if (
			validatorVerdict === "PASS" &&
			registeredValidatorDescriptors(unit, checkpoint.branch_id, registry).length > 0
		) {
			const evidenceValue = one(args, "evidence");
			if (evidenceValue !== undefined) {
				const validatorEvidencePath = admittedRunArtifactPath(store, run, absolute(evidenceValue, "--evidence"));
				assertRegularFile(validatorEvidencePath, "Validator evidence");
				const gate = validateGateEvidence(
					validatorEvidencePath,
					unit,
					checkpoint.branch_id,
					registry,
					artifactDigest ?? "",
				);
				validatorVerdict = gate.verdict;
				reason = gate.reason ?? "registered validators passed";
				evidence =
					executionEvidence === undefined
						? gate.evidence
						: {
								schema: "sure.gate.evidence.v1",
								execution: executionEvidence.evidence,
								validation: gate.evidence,
							};
				evidenceSource = "external";
				evidencePath = validatorEvidencePath;
				evidenceDigest = digestFile(validatorEvidencePath);
				notExecutedReasonCode = "CAPABILITY_MISSING";
			} else {
				const automatic = automaticGateValidation(
					args,
					store,
					run,
					loaded,
					unit,
					checkpoint.branch_id,
					checkpoint,
					registry,
					artifactPath,
					policyReferences,
				);
				validatorVerdict = automatic.result.verdict;
				reason = automatic.result.reason;
				evidence =
					executionEvidence === undefined
						? automatic.result.evidence
						: {
								schema: "sure.gate.evidence.v1",
								execution: executionEvidence.evidence,
								validation: automatic.result.evidence,
							};
				evidenceSource = "surectl_executor";
				evidencePath = automatic.evidence_path;
				evidenceDigest = automatic.evidence_digest;
				notExecutedReasonCode = "CAPABILITY_MISSING";
			}
		}
	}
	const signal =
		validatorVerdict === "PASS"
			? { kind: "pass" as const, artifact_digest: artifactDigest }
			: validatorVerdict === "NOT_EXECUTED"
				? { kind: "missing" as const, reason }
				: { kind: "fail" as const, reason, artifact_digest: artifactDigest };
	const transition = applyValidation(loaded.definition, checkpoint, signal, {
		...(one(args, "max-retries") === undefined ? {} : { max_retries: Number(one(args, "max-retries")) }),
		...(one(args, "on-exhausted") === undefined
			? {}
			: { on_exhausted: one(args, "on-exhausted") as "block" | "advance" | "terminate" }),
	});
	const disposition =
		validatorVerdict === "NOT_EXECUTED"
			? "WAIT"
			: transition.action === "retry" || transition.action === "unchanged"
				? "RETRY"
				: transition.action === "exhausted" && !transition.accepted
					? "BLOCK"
					: transition.action === "terminal"
						? "TERMINATE"
						: transition.accepted
							? "ADVANCE"
							: "BLOCK";
	const outcome = createOutcome({
		validatorVerdict,
		workflowDisposition: disposition,
		reasonCode:
			validatorVerdict === "NOT_EXECUTED"
				? executionLifecycle === "CANCELLED"
					? "EXECUTION_CANCELLED"
					: notExecutedReasonCode
				: validatorVerdict === "FAIL"
					? executionLifecycle === "PARTIAL"
						? "EXECUTION_PARTIAL"
						: executionLifecycle === "FAILED"
							? "EXECUTION_FAILED"
							: transition.action === "exhausted"
								? "RETRY_EXHAUSTED"
								: "VALIDATION_FAILED"
					: "VALIDATION_PASSED",
		...(executionLifecycle === undefined ? {} : { executionLifecycle }),
	});
	const nextState = stateWithCheckpoint(state, transition.checkpoint, {
		last_validation: {
			unit_id: unit.id,
			artifact_path: artifactPath,
			artifact_digest: artifactDigest,
			outcome,
			evidence,
			evidence_source: evidenceSource,
			...(evidencePath === undefined ? {} : { evidence_path: evidencePath }),
			...(evidenceDigest === undefined ? {} : { evidence_digest: evidenceDigest }),
		},
	});
	const updated = store.writeState(runId, nextState, "validated", { unit_id: unit.id, outcome }, run.revision);
	output({ ok: outcome.outcome === "PASS", command: "validate", run: updated, unit: unit.id, transition, outcome });
	return outcome.outcome;
}

function capabilities(args: ParsedArgs): PublicOutcome {
	const now = new Date().toISOString();
	const evidence: CapabilityEvidence[] = [];
	const executorRegistry = executorRegistrySnapshot();
	const python = one(args, "python") ?? process.env.PYTHON ?? "python3";
	const pythonProbe = spawnSync(python, ["--version"], { encoding: "utf8", timeout: 5000 });
	if (pythonProbe.status === 0) {
		const details = { executable: python, version: (pythonProbe.stdout || pythonProbe.stderr || "").trim() };
		const base = {
			capability_id: "sure.execution.local-python",
			capability_class: "execution_capability" as const,
			status: "AVAILABLE" as const,
			source: "host_probe" as const,
			observed_at: now,
			details,
		};
		evidence.push({ ...base, evidence_digest: canonicalJsonDigest(base as unknown as JsonValue) });
	} else {
		evidence.push({
			capability_id: "sure.execution.local-python",
			capability_class: "execution_capability",
			status: "UNKNOWN",
			source: "host_probe",
			observed_at: now,
		});
	}
	const dockerProbe = spawnSync("docker", ["version", "--format", "{{.Server.Version}}"], {
		encoding: "utf8",
		timeout: 5000,
	});
	if (dockerProbe.status === 0) {
		const base = {
			capability_id: "sure.execution.docker",
			capability_class: "execution_capability" as const,
			status: "AVAILABLE" as const,
			source: "host_probe" as const,
			observed_at: now,
			details: { version: (dockerProbe.stdout || "").trim() },
		};
		evidence.push({ ...base, evidence_digest: canonicalJsonDigest(base as unknown as JsonValue) });
	} else {
		evidence.push({
			capability_id: "sure.execution.docker",
			capability_class: "execution_capability",
			status: "UNKNOWN",
			source: "host_probe",
			observed_at: now,
		});
	}
	const coreEvidence = {
		capability_id: "sure.core",
		capability_class: "agent_capability" as const,
		status: "AVAILABLE" as const,
		source: "host_probe" as const,
		observed_at: now,
		details: { version: CORE_VERSION },
	};
	evidence.push({ ...coreEvidence, evidence_digest: canonicalJsonDigest(coreEvidence as unknown as JsonValue) });
	let requirements: CapabilityReport["requirements"] = [];
	try {
		requirements = currentDefinition(args, rootFor(args)).capabilities.map((entry) => ({ ...entry }));
	} catch {
		// `surectl capabilities` remains useful before a skill is selected.
	}
	const harnessPython = one(args, "harness-python") ?? process.env.HARNESS_PYTHON_BIN;
	if (requirements.some((requirement) => requirement.capability_id === "sure.execution.harness-python")) {
		const probe = harnessPython
			? spawnSync(harnessPython, ["--version"], { encoding: "utf8", timeout: 5000 })
			: undefined;
		if (probe?.status === 0 && harnessPython) {
			const base = {
				capability_id: "sure.execution.harness-python",
				capability_class: "execution_capability" as const,
				status: "AVAILABLE" as const,
				source: "host_probe" as const,
				observed_at: now,
				details: { executable: harnessPython, version: (probe.stdout || probe.stderr || "").trim() },
			};
			evidence.push({ ...base, evidence_digest: canonicalJsonDigest(base as unknown as JsonValue) });
		} else {
			evidence.push({
				capability_id: "sure.execution.harness-python",
				capability_class: "execution_capability",
				status: "MISSING",
				source: "host_probe",
				observed_at: now,
			});
		}
	}
	if (requirements.some((requirement) => requirement.capability_id === "sure.execution.evaluation-runtime")) {
		const engine = one(args, "evaluation-engine-root");
		if (engine && existsSync(engine)) {
			const base = {
				capability_id: "sure.execution.evaluation-runtime",
				capability_class: "execution_capability" as const,
				status: "AVAILABLE" as const,
				source: "host_probe" as const,
				observed_at: now,
				details: { root: absolute(engine, "--evaluation-engine-root") },
			};
			evidence.push({ ...base, evidence_digest: canonicalJsonDigest(base as unknown as JsonValue) });
		} else {
			evidence.push({
				capability_id: "sure.execution.evaluation-runtime",
				capability_class: "execution_capability",
				status: "MISSING",
				source: "host_probe",
				observed_at: now,
			});
		}
	}
	for (const requirement of requirements) {
		if (requirement.required && !evidence.some((entry) => entry.capability_id === requirement.capability_id)) {
			evidence.push({
				capability_id: requirement.capability_id,
				capability_class: requirement.capability_class,
				status: "MISSING",
				source: "host_probe",
				observed_at: now,
			});
		}
	}
	const report: CapabilityReport = { schema: "sure.capability_report.v1", requirements, evidence, checked_at: now };
	const admission = evaluateCapabilityRequirements(requirements, evidence);
	const outcome =
		admission.blocking_outcome ??
		createOutcome({
			validatorVerdict: "PASS",
			workflowDisposition: "ADVANCE",
			reasonCode: "VALIDATION_PASSED",
		});
	output({
		ok: outcome.outcome === "PASS",
		command: "capabilities",
		report,
		executor_registry: executorRegistry,
		admission,
		outcome,
	});
	return outcome.outcome;
}

type MemoryWriterOperation = "publish" | "index" | "promote";

function memoryWriterOutcome(
	args: ParsedArgs,
	contractPath: string,
	contractDigest: string,
	contract: Record<string, unknown>,
	projection: Record<string, unknown> | undefined,
	operation: MemoryWriterOperation,
): PublicOutcome {
	const unavailable = (reason: string, diagnostics: string[] = [reason]): PublicOutcome => {
		const outcome = createOutcome({
			validatorVerdict: "NOT_EXECUTED",
			workflowDisposition: "WAIT",
			reasonCode: reason.includes("reference root") ? "READ_ONLY_REFERENCE" : "CAPABILITY_MISSING",
			executionLifecycle: "NOT_STARTED",
			diagnostics: diagnostics.map((message) => ({ code: "MEMORY_WRITER_UNAVAILABLE", message })),
		});
		output({
			ok: false,
			command: "memory",
			operation,
			advisory: true,
			contract_path: contractPath,
			contract_digest: contractDigest,
			outcome,
		});
		return outcome.outcome;
	};
	if (projection?.enabled !== true) return unavailable("memory writer is disabled for this skill");

	let runtime: ReturnType<typeof verifyPortableRuntime>;
	let generation: Record<string, unknown>;
	let launcherPath: string;
	try {
		const generationPath = absolute(
			one(args, "generation-lock") ?? join(dirname(contractPath), "generation.lock.json"),
			"--generation-lock",
		);
		assertRegularFile(generationPath, "Skill generation lock");
		generation = recordObject(readJson(generationPath), "skill generation lock");
		if (generation.schema !== "sure.skill.generation.lock.v1") throw new Error("unsupported generation lock schema");
		if (generation.host !== "portable") throw new Error("memory writer requires a portable skill generation lock");
		if (generation.core_package_version !== CORE_VERSION) throw new Error("generation lock Core version mismatch");
		if (
			typeof generation.memory_contract_digest !== "string" ||
			!sameDigest(generation.memory_contract_digest, contractDigest)
		) {
			throw new Error("generation lock memory contract digest mismatch");
		}
		if (projection?.skill_id !== generation.skill_id) throw new Error("generation lock skill projection mismatch");
		if (typeof generation.semantic_runtime_digest !== "string") {
			throw new Error("generation lock has no semantic runtime digest");
		}
		const runtimeRoot = absolute(required(args, "semantic-runtime"), "--semantic-runtime");
		runtime = verifyPortableRuntime(runtimeRoot, {
			expected_runtime_digest: normalizeDigest(generation.semantic_runtime_digest, "semantic runtime digest"),
			expected_core_package_version: CORE_VERSION,
		});
		launcherPath = join(runtime.root, "sure", "runtime", "memory", "launcher.py");
		assertRegularFile(launcherPath, "Memory writer launcher");
	} catch (error) {
		return unavailable(error instanceof Error ? error.message : String(error));
	}

	const repoRoot = absolute(one(args, "repo-root") ?? rootFor(args), "--repo-root");
	let memoryRoot: string;
	let canonicalRoot: string;
	let legacySkillsRoot: string;
	try {
		memoryRoot = absolute(required(args, "memory-root"), "--memory-root");
		canonicalRoot = absolute(required(args, "canonical-root"), "--canonical-root");
		legacySkillsRoot = absolute(required(args, "legacy-skills-root"), "--legacy-skills-root");
	} catch (error) {
		return unavailable(error instanceof Error ? error.message : String(error));
	}
	const launcherArgs = [
		"-B",
		"-s",
		launcherPath,
		"--repo-root",
		repoRoot,
		"--memory-root",
		memoryRoot,
		"--canonical-root",
		canonicalRoot,
		"--legacy-skills-root",
		legacySkillsRoot,
		"--write-root",
		one(args, "memory-write-root") ?? "legacy",
		"--read-order",
		one(args, "memory-read-order") ?? "legacy,canonical",
	];
	for (const root of referenceRoots(args)) launcherArgs.push("--reference-root", root);
	launcherArgs.push(operation);
	if (operation === "publish") {
		let runDir: string;
		try {
			runDir = absolute(required(args, "run-dir"), "--run-dir");
		} catch (error) {
			return unavailable(error instanceof Error ? error.message : String(error));
		}
		launcherArgs.push("--run-dir", runDir);
		if (one(args, "no-promote") === "true") launcherArgs.push("--no-promote");
	} else if (operation === "index") {
		const mode = one(args, "mode") ?? "check";
		if (mode !== "check" && mode !== "rebuild") return unavailable("memory index mode must be check or rebuild");
		launcherArgs.push(`--${mode}`);
	} else if (one(args, "no-rebuild-index") === "true") {
		launcherArgs.push("--no-rebuild-index");
	}

	const python = one(args, "python") ?? one(args, "validator-python") ?? process.env.HARNESS_PYTHON_BIN ?? "python3";
	const executed = spawnSync(python, launcherArgs, {
		encoding: "utf8",
		timeout: 120_000,
		maxBuffer: 1024 * 1024,
		env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
	});
	let writerReceipt: Record<string, unknown> | undefined;
	try {
		writerReceipt = recordObject(JSON.parse((executed.stdout || "").trim()), "memory writer receipt");
	} catch {
		return unavailable("memory writer did not return a structured receipt");
	}
	const expectedWorkspace = {
		repo_root: `sha256:${createHash("sha256").update(repoRoot).digest("hex")}`,
		memory_root: `sha256:${createHash("sha256").update(memoryRoot).digest("hex")}`,
		canonical_root: `sha256:${createHash("sha256").update(canonicalRoot).digest("hex")}`,
		legacy_skills_root: `sha256:${createHash("sha256").update(legacySkillsRoot).digest("hex")}`,
	};
	const workspace =
		typeof writerReceipt.workspace === "object" && writerReceipt.workspace !== null
			? (writerReceipt.workspace as Record<string, unknown>)
			: undefined;
	const receiptValid =
		writerReceipt.schema === "sure.memory.writer_receipt.v1" &&
		writerReceipt.operation === operation &&
		writerReceipt.advisory === true &&
		writerReceipt.workflow_disposition === "NO_EFFECT" &&
		workspace !== undefined &&
		Object.entries(expectedWorkspace).every(([key, value]) => workspace[key] === value);
	if (!receiptValid) return unavailable("memory writer receipt binding is invalid");
	const succeeded = executed.status === 0 && writerReceipt.status === "SUCCEEDED" && writerReceipt.exit_code === 0;
	const writerUnavailable = writerReceipt.reason_code === "CAPABILITY_MISSING";
	const outcome = succeeded
		? createOutcome({
				validatorVerdict: "PASS",
				workflowDisposition: "ADVANCE",
				reasonCode: "VALIDATION_PASSED",
				executionLifecycle: "SUCCEEDED",
			})
		: createOutcome({
				validatorVerdict: "NOT_EXECUTED",
				workflowDisposition: "WAIT",
				reasonCode: writerUnavailable ? "CAPABILITY_MISSING" : "EXECUTION_FAILED",
				executionLifecycle: writerUnavailable ? "NOT_STARTED" : "FAILED",
				diagnostics: [{ code: "MEMORY_WRITE_FAILED", message: "advisory memory writer did not complete" }],
			});
	output({
		ok: succeeded,
		command: "memory",
		operation,
		advisory: true,
		contract_path: contractPath,
		contract_digest: contractDigest,
		contract,
		runtime_digest: runtime.lock.runtime_digest,
		writer_receipt: writerReceipt,
		outcome,
	});
	return outcome.outcome;
}

/** Validate the host-neutral contract and optionally run its locked advisory writer. */
function memory(args: ParsedArgs): PublicOutcome {
	const contractValue = one(args, "contract") ?? one(args, "memory-contract");
	if (contractValue === undefined) throw new Error("--contract (or --memory-contract) is required.");
	const contractPath = absolute(contractValue, "--contract");
	assertRegularFile(contractPath, "Memory contract");
	const validation = validateMemoryContract(readJson(contractPath));
	const requestedDigest = one(args, "digest");
	const skill = one(args, "skill");
	const uriValue = one(args, "uri");
	const diagnostics: string[] = [...validation.errors];
	if (validation.valid && requestedDigest !== undefined) {
		if (!validDigestValue(requestedDigest) || !sameDigest(requestedDigest, validation.digest ?? "")) {
			diagnostics.push("memory contract digest does not match the supplied digest");
		}
	}
	const projection =
		validation.valid &&
		validation.contract &&
		typeof validation.contract.skill === "object" &&
		validation.contract.skill !== null
			? (validation.contract.skill as Record<string, unknown>)
			: undefined;
	if (validation.valid && skill !== undefined) {
		if (projection?.skill_id !== skill) diagnostics.push("memory contract skill projection does not match --skill");
		if (skill === "sure_approve" && projection?.enabled !== false)
			diagnostics.push("sure_approve memory must be disabled");
	}
	let parsedUri: ReturnType<typeof parseMemoryUri> | undefined;
	if (validation.valid && uriValue !== undefined) {
		try {
			parsedUri = parseMemoryUri(uriValue);
			if (skill !== undefined && parsedUri.skill !== skill)
				diagnostics.push("memory URI skill does not match --skill");
		} catch (error) {
			diagnostics.push(error instanceof Error ? error.message : String(error));
		}
	}
	if (diagnostics.length > 0 || !validation.valid) {
		const outcome = createOutcome({
			validatorVerdict: "NOT_EXECUTED",
			workflowDisposition: "BLOCK",
			reasonCode: diagnostics.some((message) => message.includes("digest")) ? "DIGEST_MISMATCH" : "INVALID_CONTRACT",
			diagnostics: diagnostics.map((message) => ({ code: "MEMORY_CONTRACT_REJECTED", message })),
		});
		output({
			ok: false,
			command: "memory",
			operation: "contract",
			contract_path: contractPath,
			contract_digest: validation.digest,
			errors: diagnostics,
			outcome,
		});
		return outcome.outcome;
	}
	const operationValue = one(args, "operation") ?? "contract";
	if (operationValue !== "contract") {
		if (operationValue !== "publish" && operationValue !== "index" && operationValue !== "promote") {
			const outcome = createOutcome({
				validatorVerdict: "NOT_EXECUTED",
				workflowDisposition: "BLOCK",
				reasonCode: "INVALID_CONTRACT",
				diagnostics: [
					{ code: "MEMORY_OPERATION_REJECTED", message: `unknown memory operation: ${operationValue}` },
				],
			});
			output({ ok: false, command: "memory", operation: operationValue, advisory: true, outcome });
			return outcome.outcome;
		}
		return memoryWriterOutcome(
			args,
			contractPath,
			validation.digest ?? canonicalJsonDigest(validation.contract as unknown as JsonValue),
			validation.contract as unknown as Record<string, unknown>,
			projection,
			operationValue,
		);
	}
	const outcome = createOutcome({
		validatorVerdict: "PASS",
		workflowDisposition: "ADVANCE",
		reasonCode: "VALIDATION_PASSED",
	});
	output({
		ok: true,
		command: "memory",
		operation: "contract",
		contract_path: contractPath,
		contract_digest: validation.digest,
		contract: validation.contract,
		...(parsedUri === undefined ? {} : { uri: parsedUri }),
		outcome,
	});
	return outcome.outcome;
}

function resume(args: ParsedArgs): void {
	const root = rootFor(args);
	const runId = required(args, "run-id");
	const suppliedPolicy = policySnapshotFor(args);
	const { store, run } = contextForRun(args, root, runId, { requireCurrentPolicy: true });
	const existingState = store.readState(run);
	const integrityError = stateIntegrityError(run, existingState);
	if (integrityError) throw new Error(integrityError);
	const loaded = currentDefinition(args, root, run.skillName);
	const registry = registryFor(loaded, args);
	const executorDigest = normalizeDigest(
		one(args, "executor-digest") ??
			run.executorDigest ??
			requiredValue(args, "executor-digest", "SURE_EXECUTOR_DIGEST"),
		"--executor-digest",
	);
	const policyDigest =
		suppliedPolicy === undefined
			? normalizeDigest(
					one(args, "policy-digest") ??
						run.policyDigest ??
						requiredValue(args, "policy-digest", "SURE_POLICY_DIGEST"),
					"--policy-digest",
				)
			: policyDigestFor(args, suppliedPolicy.snapshot);
	const bindingDigest = one(args, "binding-digest") ?? run.bindingDigest;
	const binding = {
		coreVersion: run.coreVersion ?? CORE_VERSION,
		workflowDigest: workflowDigest(loaded.definition),
		validatorDigest: registry.digest,
		executorDigest,
		policyDigest,
		...(suppliedPolicy === undefined ? {} : { policySnapshotDigest: suppliedPolicy.snapshot.snapshot_digest }),
		...(bindingDigest === undefined ? {} : { bindingDigest: normalizeDigest(bindingDigest, "--binding-digest") }),
	};
	const resumed = store.resumeRun(runId, binding);
	const state = store.readState(resumed);
	const checkpoint = checkpointFromState(loaded.definition, state);
	output({ ok: true, command: "resume", run: resumed, state, checkpoint, binding });
}

function executionKind(
	args: ParsedArgs,
	request: ExecutionRequest,
): "local" | "python" | "docker" | "remote" | "trusted" {
	const raw = one(args, "kind") ?? one(args, "executor");
	const runtimeRequirements =
		typeof request.runtime_requirements === "object" && request.runtime_requirements !== null
			? (request.runtime_requirements as Record<string, unknown>)
			: {};
	const candidate =
		raw ?? (typeof runtimeRequirements.executor_kind === "string" ? runtimeRequirements.executor_kind : "local");
	if (
		candidate !== "local" &&
		candidate !== "python" &&
		candidate !== "docker" &&
		candidate !== "remote" &&
		candidate !== "trusted"
	) {
		throw new Error(`Unsupported executor kind: ${candidate}`);
	}
	if (!executorDescriptor(candidate)) throw new Error(`Executor ${candidate} is not registered.`);
	return candidate;
}

function execute(args: ParsedArgs): PublicOutcome {
	const root = rootFor(args);
	const runId = required(args, "run-id");
	const { store, run, policySnapshot } = contextForRun(args, root, runId);
	const policyReferences = referenceRoots(args, policySnapshot);
	const existingState = store.readState(run);
	const integrityError = stateIntegrityError(run, existingState);
	if (integrityError) throw new Error(integrityError);
	const loaded = currentDefinition(args, root, run.skillName);
	const checkpoint = checkpointFromState(loaded.definition, existingState);
	const checkpointAudit = auditCheckpointState(loaded.definition, checkpoint);
	if (!checkpointAudit.ok) throw new Error(checkpointAudit.reason ?? "Invalid checkpoint.");
	const currentUnit = unitForCurrent(loaded.definition, checkpoint.data.currentUnit, checkpoint.branch_id);
	const executionGate = currentUnit.gate;
	let registeredOperationId = one(args, "operation");
	let dispatchInputContext: RegisteredInputContext | undefined;
	if (currentUnit.gate?.execution_dispatch !== undefined) {
		const firstCase = currentUnit.gate.execution_dispatch[0];
		if (firstCase === undefined) throw new Error(`Current unit ${currentUnit.id} has an empty execution dispatch.`);
		dispatchInputContext = registeredInputContext(
			store,
			run,
			firstCase.input_contract,
			policyReferences,
			loaded.root,
			validatorReferenceDigest(run, policyReferences),
		);
		const selectedCase = selectExecutionDispatch(currentUnit.gate.execution_dispatch, dispatchInputContext.context);
		if (registeredOperationId !== undefined && registeredOperationId !== selectedCase.operation_id) {
			throw new Error(
				`Requested operation ${registeredOperationId} does not match the context-selected dispatch operation ${selectedCase.operation_id}.`,
			);
		}
		registeredOperationId = selectedCase.operation_id;
	} else if (
		registeredOperationId !== undefined &&
		currentUnit.gate?.execution_operation_id !== registeredOperationId
	) {
		throw new Error(
			`Current unit ${currentUnit.id} does not authorize registered execution operation ${registeredOperationId}.`,
		);
	}
	if (registeredOperationId !== undefined) {
		const targetArtifactPath = admittedRunArtifactPath(
			store,
			run,
			join(run.runDir, "artifacts", currentUnit.produces),
		);
		const artifactPath = producerInputArtifactPath(
			store,
			run,
			loaded,
			checkpoint,
			currentUnit,
			one(args, "artifact"),
			targetArtifactPath,
		);
		const artifact = artifactRef(artifactPath, currentUnit.id, run.runDir);
		const runtimeBinding = loadSkillRuntimeBinding(loaded.root, {
			skill_id: loaded.definition.workflow_id,
			workflow_digest: run.workflowDigest ?? workflowDigest(loaded.definition),
			validator_registry_digest: run.validatorDigest ?? "",
			core_version: run.coreVersion ?? CORE_VERSION,
		});
		const runtimeValue = one(args, "semantic-runtime") ?? process.env.SURE_RUNTIME_SUPPORT_ROOT;
		const runtimeRoot = runtimeValue === undefined ? undefined : absolute(runtimeValue, "--semantic-runtime");
		let inputContext: RegisteredInputContext | undefined = dispatchInputContext;
		if (runtimeRoot !== undefined) {
			try {
				const verification = verifyPortableRuntime(runtimeRoot, {
					expected_runtime_digest: runtimeBinding.semantic_runtime_digest,
					expected_core_package_version: runtimeBinding.core_package_version,
					expected_semantic_backend_registry_digest: runtimeBinding.semantic_backend_registry_digest,
					expected_executor_registry_digest: runtimeBinding.executor_registry_digest,
				});
				const operation = resolveSemanticBackendOperation(verification.root, registeredOperationId, {
					manifestPath: join(verification.root, "semantic-backends.json"),
					expectedRegistryDigest: verification.lock.semantic_backend_registry_digest,
					environment: semanticRuntimeEnvironment(
						{ run, workspace_root: run.cwd, policy_digest: run.policyDigest ?? canonicalJsonDigest(null) },
						verification.root,
					),
				});
				if (operation.input_contract !== undefined) {
					inputContext = registeredInputContext(
						store,
						run,
						operation.input_contract,
						policyReferences,
						loaded.root,
						validatorReferenceDigest(run, policyReferences),
					);
				}
			} catch {
				// runRegisteredOperation performs the authoritative admission and
				// returns NOT_EXECUTED when this setup is unavailable.
			}
		}
		const artifactsRoot = admittedRunArtifactPath(store, run, join(run.runDir, "artifacts"));
		const artifactsAdmission = store.admitPath(artifactsRoot, [run.runDir]);
		const attempt = (checkpoint.data.retries[currentUnit.id] ?? 0) + 1;
		const invocationId = randomUUID().replaceAll("-", "").slice(0, 16);
		const invocationRoot = admittedRunArtifactPath(
			store,
			run,
			join(artifactsRoot, "execution", currentUnit.id, invocationId),
		);
		const result = runRegisteredOperation({
			runtime_root: runtimeRoot,
			runtime_binding: runtimeBinding,
			run,
			branch_id: checkpoint.branch_id,
			unit_id: currentUnit.id,
			attempt,
			operation_id: registeredOperationId,
			request_operation: executionGate?.execution_request_operation ?? "validation",
			script_args: [...(executionGate?.script_args ?? [])],
			artifact,
			...(inputContext === undefined
				? {}
				: {
						input_context: inputContext.context,
						input_context_digest: inputContext.context_digest,
						input_resolver: inputContext.resolver,
					}),
			output_path: targetArtifactPath,
			python_executable:
				one(args, "operation-python") ??
				one(args, "validator-python") ??
				process.env.HARNESS_PYTHON_BIN ??
				process.env.PYTHON ??
				"python3",
			package_dir: loaded.root,
			workspace_root: run.cwd,
			artifacts_root: artifactsRoot,
			artifacts_resolved_root: artifactsAdmission.resolvedPath,
			reference_snapshot_digest: validatorReferenceDigest(run, policyReferences),
			policy_digest: run.policyDigest ?? canonicalJsonDigest(null),
			forbidden_output_roots: policyReferences,
			created_at: new Date().toISOString(),
			persist_request(request) {
				const path = admittedRunArtifactPath(store, run, join(invocationRoot, "execution_request.json"));
				writeJsonImmutable(path, request);
				return { path, digest: digestFile(path) };
			},
			persist_receipt(receipt) {
				const path = admittedRunArtifactPath(store, run, join(invocationRoot, "execution_receipt.json"));
				writeJsonImmutable(path, receipt);
				return { path, digest: digestFile(path) };
			},
		});
		const nextState = {
			...(existingState ?? {}),
			last_execution: {
				...result.evidence,
				source: "registered_operation",
				branch_id: checkpoint.branch_id,
				unit_id: currentUnit.id,
				attempt,
				outcome: result.outcome,
			},
		};
		const updatedRun = store.writeState(
			runId,
			nextState,
			"execution_recorded",
			{ unit_id: currentUnit.id, operation_id: registeredOperationId, outcome: result.outcome },
			run.revision,
		);
		output({
			ok: result.evidence.verdict === "PASS",
			command: "execute",
			kind: "registered_operation",
			run: updatedRun,
			unit: currentUnit.id,
			operation_id: registeredOperationId,
			evidence: result.evidence,
			request: result.request,
			receipt: result.receipt,
			outcome: result.outcome,
		});
		return result.outcome.outcome;
	}
	const requestValue = one(args, "execution-request") ?? one(args, "request");
	if (!requestValue) throw new Error("--execution-request (or --request) is required.");
	const requestPath = admittedRunArtifactPath(store, run, absolute(requestValue, "--execution-request"));
	const request = recordObject(readJson(requestPath), "execution request") as unknown as ExecutionRequest;
	if (request.run_id !== runId) throw new Error("Execution request run_id does not match the selected run.");
	if (request.unit_id !== currentUnit.id)
		throw new Error(`Execution request unit_id ${request.unit_id} is not the current unit ${currentUnit.id}.`);
	if (!isAbsolute(request.output_root?.path ?? ""))
		throw new Error("Execution request output_root.path must be absolute.");
	const allowedOutputRoots = [run.runDir, ...(run.outputDir ? [run.outputDir] : [])];
	const admittedOutputRoot = store.admitPath(request.output_root.path, allowedOutputRoots);
	if (request.output_root.resolved_path !== admittedOutputRoot.resolvedPath) {
		throw new Error("Execution request output_root.resolved_path does not match the admitted path.");
	}
	if (request.entrypoint.working_directory !== undefined) {
		store.admitPath(request.entrypoint.working_directory, [run.cwd, run.runDir, ...allowedOutputRoots]);
	}
	const receiptValue = one(args, "execution-receipt") ?? one(args, "receipt");
	const receiptPath = admittedRunArtifactPath(
		store,
		run,
		receiptValue === undefined
			? join(run.runDir, "artifacts", "execution_receipt.json")
			: absolute(receiptValue, "--execution-receipt"),
	);
	const outputPaths = many(args, "output").map((value) => {
		const candidate = absolute(value, "--output");
		return store.admitPath(candidate, [request.output_root.path]).path;
	});
	const timeoutValue = one(args, "timeout-ms");
	const timeoutMs = timeoutValue === undefined ? 30 * 60 * 1000 : Number(timeoutValue);
	if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) throw new Error("--timeout-ms must be a positive integer.");
	const executorDigest = normalizeDigest(
		one(args, "executor-digest") ??
			run.executorDigest ??
			requiredValue(args, "executor-digest", "SURE_EXECUTOR_DIGEST"),
		"--executor-digest",
	);
	const result = executeRequest(request, {
		kind: executionKind(args, request),
		executor_digest: executorDigest,
		executor_version: CORE_VERSION,
		working_directory: run.cwd,
		allowed_output_roots: allowedOutputRoots,
		forbidden_output_roots: policyReferences,
		timeout_ms: timeoutMs,
		output_paths: outputPaths,
	});
	let updatedRun = run;
	if (result.receipt) {
		writeJsonAtomic(receiptPath, result.receipt);
		const state = store.readState(run) ?? {};
		updatedRun = store.writeState(
			runId,
			{
				...state,
				last_execution: {
					request_path: requestPath,
					request_digest: canonicalJsonDigest(request as unknown as JsonValue),
					receipt_path: receiptPath,
					receipt_digest: digestFile(receiptPath),
					outcome: result.outcome,
				},
			},
			"execution_recorded",
			{ unit_id: request.unit_id, outcome: result.outcome },
			run.revision,
		);
	}
	output({
		ok: result.outcome.outcome === "PASS",
		command: "execute",
		run: updatedRun,
		request_path: requestPath,
		receipt_path: result.receipt ? receiptPath : undefined,
		receipt: result.receipt,
		receipt_validation: result.receipt_validation,
		outcome: result.outcome,
	});
	return result.outcome.outcome;
}

function digestOrUndefined(value: unknown): string | undefined {
	return typeof value === "string" && value.trim() !== "" ? value.trim() : undefined;
}

function digestOrLegacy(value: string | undefined, label: string, missing: string[]): string {
	if (value !== undefined && value.trim() !== "") {
		try {
			return normalizeDigest(value.trim(), label);
		} catch {
			missing.push(label);
		}
	} else {
		missing.push(label);
	}
	return canonicalJsonDigest({ legacy_unverified: label } as unknown as JsonValue);
}

function freeze(args: ParsedArgs): PublicOutcome {
	const root = rootFor(args);
	const runId = required(args, "run-id");
	const { store, run, policySnapshot } = contextForRun(args, root, runId);
	const policyReferences = referenceRoots(args, policySnapshot);
	const state = store.readState(run);
	const integrityError = stateIntegrityError(run, state);
	if (integrityError) throw new Error(integrityError);
	const requestPath = admittedRunArtifactPath(
		store,
		run,
		absolute(requiredValue(args, "execution-request", "SURE_EXECUTION_REQUEST"), "--execution-request"),
	);
	const receiptPath = admittedRunArtifactPath(
		store,
		run,
		absolute(requiredValue(args, "execution-receipt", "SURE_EXECUTION_RECEIPT"), "--execution-receipt"),
	);
	assertRegularFile(requestPath, "Execution request");
	assertRegularFile(receiptPath, "Execution receipt");
	const request = recordObject(readJson(requestPath), "execution request") as unknown as ExecutionRequest;
	const receipt = recordObject(readJson(receiptPath), "execution receipt") as unknown as ExecutionReceipt;
	if (request.run_id !== runId || receipt.run_id !== runId)
		throw new Error("Frozen subject run_id does not match the selected run.");
	if (receipt.request_id !== request.request_id)
		throw new Error("Frozen subject receipt is not bound to the request.");
	const boundary = {
		allowed_output_roots: [run.runDir, ...(run.outputDir ? [run.outputDir] : [])],
		forbidden_output_roots: policyReferences,
	};
	const requestValidation = validateExecutionRequest(request, boundary);
	const receiptValidation = validateExecutionReceipt(request, receipt, boundary);
	const missing: string[] = [];
	if (!requestValidation.valid) missing.push("valid execution request");
	if (!receiptValidation.valid) missing.push("valid execution receipt");
	if (
		outputRootBindingError(store, run, request, policyReferences) !== undefined ||
		receiptOutputErrors(store, run, request, receipt).length > 0
	) {
		missing.push("admissible execution output files");
	}
	if (receipt.lifecycle !== "SUCCEEDED") missing.push("successful execution receipt");
	const loaded = currentDefinition(args, root, run.skillName);
	const registry = registryFor(loaded, args);
	const subjectInputPath = one(args, "subject-input");
	let subjectInputResolvedPath: string | undefined;
	const subjectInput = subjectInputPath
		? (() => {
				const admittedPath = admittedReadArtifactPath(
					store,
					run,
					absolute(subjectInputPath, "--subject-input"),
					policyReferences,
				);
				subjectInputResolvedPath = admittedPath;
				return recordObject(readJson(admittedPath), "subject input");
			})()
		: {};
	const requestSubject = request.subject;
	const field = (name: string): string | undefined => {
		const override = one(args, name);
		const fromFile = subjectInput[name];
		return override ?? (typeof fromFile === "string" ? fromFile : undefined);
	};
	const bundleDigest = digestOrLegacy(
		field("bundle-digest") ?? requestSubject.bundle_digest,
		"bundle_digest",
		missing,
	);
	const runtimeDigest = digestOrLegacy(
		field("runtime-digest") ?? requestSubject.runtime_identity_digest,
		"runtime_identity_digest",
		missing,
	);
	const inferenceDigest = digestOrLegacy(
		field("inference-protocol-digest") ?? requestSubject.inference_protocol_digest,
		"inference_protocol_digest",
		missing,
	);
	const datasetDigest = digestOrLegacy(
		field("dataset-digest") ?? requestSubject.dataset_identity_digest,
		"dataset_identity_digest",
		missing,
	);
	const scoringDigest = digestOrLegacy(
		field("scoring-digest") ?? requestSubject.scoring_protocol_digest,
		"scoring_protocol_digest",
		missing,
	);
	const workflow = digestOrLegacy(one(args, "workflow-digest") ?? run.workflowDigest, "workflow_digest", missing);
	const validator = digestOrLegacy(one(args, "validator-digest") ?? registry.digest, "validator_digest", missing);
	const executor = digestOrLegacy(one(args, "executor-digest") ?? receipt.executor.digest, "executor_digest", missing);
	const policy = digestOrLegacy(one(args, "policy-digest") ?? request.policy_digest, "policy_digest", missing);
	const snapshot = digestOrLegacy(
		one(args, "reference-snapshot-digest") ?? request.reference_snapshot_digest,
		"reference_snapshot_digest",
		missing,
	);
	let predictionPath = "/__sure__/legacy-unverified/predictions";
	const predictionArg = one(args, "prediction");
	if (predictionArg) {
		predictionPath = admittedReadArtifactPath(store, run, absolute(predictionArg, "--prediction"), policyReferences);
	} else {
		missing.push("prediction");
	}
	const predictionDigest = predictionArg
		? digestPath(predictionPath)
		: digestOrLegacy(undefined, "prediction_digest", missing);
	const enginePathArg = one(args, "engine-root");
	let evaluatorEngineDigest = digestOrLegacy(one(args, "engine-digest"), "evaluator_engine_digest", missing);
	if (enginePathArg && one(args, "engine-digest") === undefined) {
		const enginePath = absolute(enginePathArg, "--engine-root");
		const admittedEnginePath = admittedReadArtifactPath(store, run, enginePath, policyReferences);
		evaluatorEngineDigest = digestPath(admittedEnginePath);
		missing.splice(missing.indexOf("evaluator_engine_digest"), 1);
	}
	const routeArg = one(args, "route");
	let evaluatorRouteDigest: string;
	if (routeArg?.startsWith("/")) {
		const routePath = admittedReadArtifactPath(store, run, routeArg, policyReferences);
		evaluatorRouteDigest = digestPath(routePath);
	} else if (routeArg) {
		// A route id is data, not a path; normalize it into a stable identity.
		evaluatorRouteDigest = canonicalJsonDigest({ route_id: routeArg } as unknown as JsonValue);
	} else {
		evaluatorRouteDigest = digestOrLegacy(one(args, "route-digest"), "evaluator_route_digest", missing);
	}
	let approvalEventDigest: string | undefined;
	const approvalArg = one(args, "approval-event");
	if (approvalArg) {
		const approvalPath = admittedReadArtifactPath(
			store,
			run,
			absolute(approvalArg, "--approval-event"),
			policyReferences,
		);
		approvalEventDigest = digestPath(approvalPath);
	} else if (one(args, "approval-digest")) {
		approvalEventDigest = normalizeDigest(required(args, "approval-digest"), "--approval-digest");
	} else {
		missing.push("approval_event_digest");
	}
	const explicitLegacy = one(args, "legacy-unverified") === "true" || one(args, "legacy-unverified") === "1";
	const subject = createFrozenEvaluationSubject({
		subject_id: one(args, "subject-id") ?? `subject-${runId}-${request.unit_id}`,
		bundle_manifest_path: requestSubject.bundle_manifest_path,
		bundle_digest: bundleDigest,
		runtime_identity_digest: runtimeDigest,
		inference_protocol_digest: inferenceDigest,
		dataset_identity_digest: datasetDigest,
		scoring_protocol_digest: scoringDigest,
		prediction_path: predictionPath,
		prediction_digest: predictionDigest,
		execution_receipt_digest: digestFile(receiptPath),
		evaluator_engine_digest: evaluatorEngineDigest,
		evaluator_route_digest: evaluatorRouteDigest,
		workflow_digest: workflow,
		validator_digest: validator,
		executor_digest: executor,
		policy_digest: policy,
		reference_snapshot_digest: snapshot,
		assurance_profile: (one(args, "assurance-profile") ?? "cooperative") as AssuranceProfile,
		legacy_unverified: explicitLegacy || missing.length > 0,
		...(approvalEventDigest === undefined ? {} : { approval_event_digest: approvalEventDigest }),
		frozen_at: one(args, "frozen-at") ?? new Date().toISOString(),
	});
	const subjectErrors = validateFrozenEvaluationSubject(subject);
	if (subjectErrors.length > 0) throw new Error(`Invalid frozen evaluation subject: ${subjectErrors.join("; ")}`);
	const outputPath = admittedRunArtifactPath(
		store,
		run,
		absolute(one(args, "output") ?? join(run.runDir, "artifacts", "evaluation_subject.json"), "--output"),
	);
	writeJsonImmutable(outputPath, subject);
	output({
		ok: !subject.legacy_unverified,
		command: "freeze",
		subject,
		subject_path: outputPath,
		subject_manifest_digest: digestFile(outputPath),
		legacy_reasons: missing,
		...(subjectInputResolvedPath === undefined
			? {}
			: { subject_input_manifest_digest: digestFile(subjectInputResolvedPath) }),
	});
	return subject.legacy_unverified ? "NOT_EXECUTED" : "PASS";
}

function conformance(args: ParsedArgs): PublicOutcome {
	const root = rootFor(args);
	const runId = required(args, "run-id");
	const { store, run, policySnapshot } = contextForRun(args, root, runId);
	const policyReferences = referenceRoots(args, policySnapshot);
	const existingState = store.readState(run);
	const integrityError = stateIntegrityError(run, existingState);
	if (integrityError) throw new Error(integrityError);
	const requestPath = admittedRunArtifactPath(
		store,
		run,
		absolute(requiredValue(args, "execution-request", "SURE_EXECUTION_REQUEST"), "--execution-request"),
	);
	const receiptPath = admittedRunArtifactPath(
		store,
		run,
		absolute(requiredValue(args, "execution-receipt", "SURE_EXECUTION_RECEIPT"), "--execution-receipt"),
	);
	assertRegularFile(requestPath, "Execution request");
	assertRegularFile(receiptPath, "Execution receipt");
	const request = recordObject(readJson(requestPath), "execution request") as unknown as ExecutionRequest;
	const receipt = recordObject(readJson(receiptPath), "execution receipt") as unknown as ExecutionReceipt;
	const profileArgument = one(args, "assurance-profile");
	const requestedProfileValue = profileArgument ?? "cooperative";
	if (
		requestedProfileValue !== "cooperative" &&
		requestedProfileValue !== "pi_enforced" &&
		requestedProfileValue !== "trusted"
	)
		throw new Error(`Invalid --assurance-profile: ${requestedProfileValue}`);
	const boundary = {
		allowed_output_roots: [run.runDir, ...(run.outputDir ? [run.outputDir] : [])],
		forbidden_output_roots: policyReferences,
		require_attested_executor: requestedProfileValue === "trusted",
	};
	const outputRootError = outputRootBindingError(store, run, request, policyReferences);
	const baseReceiptValidation = validateExecutionReceipt(request, receipt, boundary);
	const receiptFileErrors = receiptOutputErrors(store, run, request, receipt);
	const receiptBoundaryErrors = [...(outputRootError === undefined ? [] : [outputRootError]), ...receiptFileErrors];
	const receiptValidation =
		receiptBoundaryErrors.length === 0
			? baseReceiptValidation
			: {
					...baseReceiptValidation,
					valid: false,
					errors: [...baseReceiptValidation.errors, ...receiptBoundaryErrors],
					outcome: createOutcome({
						validatorVerdict: "NOT_EXECUTED",
						workflowDisposition: "BLOCK",
						reasonCode: receiptBoundaryErrors.some((message) => /outside|root|symlink|reference/i.test(message))
							? "PATH_OUT_OF_SCOPE"
							: "INVALID_CONTRACT",
						diagnostics: receiptBoundaryErrors.map((message) => ({
							code: "EXECUTION_EVIDENCE_REJECTED",
							message,
						})),
					}),
				};
	const state = store.readState(run) ?? {};
	const lastValidation =
		typeof state.last_validation === "object" && state.last_validation !== null
			? (state.last_validation as Record<string, unknown>)
			: {};
	const lastOutcome =
		typeof lastValidation.outcome === "object" && lastValidation.outcome !== null
			? (lastValidation.outcome as Record<string, unknown>)
			: {};
	const formalDiagnostics: string[] = [];
	const formalBinding =
		request.operation === "formal_evaluation"
			? formalValidationBinding(store, run, state, request, receiptPath, policyReferences)
			: undefined;
	if (formalBinding !== undefined && formalBinding.diagnostics.length === 0) {
		const suppliedVerdict = one(args, "validator-verdict");
		if (suppliedVerdict !== undefined && suppliedVerdict !== formalBinding.validatorVerdict) {
			formalDiagnostics.push("validator verdict argument cannot override the persisted validation outcome");
		}
		const suppliedDisposition = one(args, "workflow-disposition");
		if (suppliedDisposition !== undefined && !["ADVANCE", "TERMINATE"].includes(suppliedDisposition)) {
			formalDiagnostics.push("workflow disposition argument cannot override the persisted validation outcome");
		}
	}
	const validatorVerdictValue =
		formalBinding?.validatorVerdict ?? one(args, "validator-verdict") ?? lastOutcome.validator_verdict;
	const workflowDispositionValue =
		formalBinding?.workflowDisposition ?? one(args, "workflow-disposition") ?? lastOutcome.workflow_disposition;
	const validatorVerdict =
		validatorVerdictValue === "PASS" || validatorVerdictValue === "FAIL" || validatorVerdictValue === "NOT_EXECUTED"
			? validatorVerdictValue
			: "NOT_EXECUTED";
	const workflowDisposition =
		workflowDispositionValue === "ADVANCE" ||
		workflowDispositionValue === "RETRY" ||
		workflowDispositionValue === "BLOCK" ||
		workflowDispositionValue === "TERMINATE" ||
		workflowDispositionValue === "WAIT"
			? workflowDispositionValue
			: "WAIT";
	const defaultSubjectPath = join(run.runDir, "artifacts", "evaluation_subject.json");
	const subjectInputValue = one(args, "subject") ?? (existsSync(defaultSubjectPath) ? defaultSubjectPath : undefined);
	let subjectManifestPath: string | undefined;
	const subjectInput =
		subjectInputValue === undefined
			? {}
			: (() => {
					const admittedPath = admittedReadArtifactPath(
						store,
						run,
						absolute(subjectInputValue, "--subject"),
						policyReferences,
					);
					subjectManifestPath = admittedPath;
					return recordObject(readJson(admittedPath), "subject");
				})();
	const frozenSubject =
		subjectInput.schema === "sure.evaluation_subject.v1"
			? (subjectInput as unknown as FrozenEvaluationSubject)
			: undefined;
	const formalOperation = request.operation === "formal_evaluation";
	const profileValue = formalOperation && frozenSubject ? frozenSubject.assurance_profile : requestedProfileValue;
	if (formalOperation && frozenSubject) {
		formalDiagnostics.push(...frozenSubjectFileErrors(store, run, frozenSubject, policyReferences));
	}
	if (formalOperation && frozenSubject && profileArgument !== undefined && profileArgument !== profileValue) {
		formalDiagnostics.push("assurance profile argument does not match the frozen evaluation subject");
	}
	if (formalOperation && frozenSubject) {
		for (const [argument, expected] of [
			["bundle-digest", frozenSubject.bundle_digest],
			["runtime-digest", frozenSubject.runtime_identity_digest],
			["inference-protocol-digest", frozenSubject.inference_protocol_digest],
			["dataset-digest", frozenSubject.dataset_identity_digest],
			["scoring-digest", frozenSubject.scoring_protocol_digest],
		] as const) {
			const supplied = one(args, argument);
			if (supplied !== undefined && (!validDigestValue(supplied) || !sameDigest(supplied, expected ?? ""))) {
				formalDiagnostics.push(`${argument} does not match the frozen evaluation subject`);
			}
		}
	}
	const subject: FrozenFormalSubject = {
		bundle_manifest_path:
			formalOperation && frozenSubject
				? frozenSubject.bundle_manifest_path
				: typeof subjectInput.bundle_manifest_path === "string"
					? subjectInput.bundle_manifest_path
					: request.subject.bundle_manifest_path,
		bundle_digest:
			formalOperation && frozenSubject
				? frozenSubject.bundle_digest
				: (digestOrUndefined(one(args, "bundle-digest")) ??
					(typeof subjectInput.bundle_digest === "string"
						? subjectInput.bundle_digest
						: request.subject.bundle_digest)),
		runtime_identity_digest:
			formalOperation && frozenSubject
				? frozenSubject.runtime_identity_digest
				: (digestOrUndefined(one(args, "runtime-digest")) ??
					(typeof subjectInput.runtime_identity_digest === "string"
						? subjectInput.runtime_identity_digest
						: request.subject.runtime_identity_digest)),
		inference_protocol_digest:
			formalOperation && frozenSubject
				? (frozenSubject.inference_protocol_digest ?? "")
				: (digestOrUndefined(one(args, "inference-protocol-digest")) ??
					(typeof subjectInput.inference_protocol_digest === "string"
						? subjectInput.inference_protocol_digest
						: request.subject.inference_protocol_digest)),
		dataset_identity_digest:
			formalOperation && frozenSubject
				? (frozenSubject.dataset_identity_digest ?? "")
				: (digestOrUndefined(one(args, "dataset-digest")) ??
					(typeof subjectInput.dataset_identity_digest === "string"
						? subjectInput.dataset_identity_digest
						: (request.subject.dataset_identity_digest ?? ""))),
		scoring_protocol_digest:
			formalOperation && frozenSubject
				? (frozenSubject.scoring_protocol_digest ?? "")
				: (digestOrUndefined(one(args, "scoring-digest")) ??
					(typeof subjectInput.scoring_protocol_digest === "string"
						? subjectInput.scoring_protocol_digest
						: (request.subject.scoring_protocol_digest ?? ""))),
	};
	const loaded = currentDefinition(args, root, run.skillName);
	const loadedRegistry = registryFor(loaded, args);
	const definitionWorkflowDigest = workflowDigestForFallback(loaded.definition);
	const boundWorkflowDigest = digestOrUndefined(run.workflowDigest) ?? definitionWorkflowDigest;
	const boundValidatorDigest = digestOrUndefined(run.validatorDigest) ?? loadedRegistry.digest;
	const boundExecutorDigest =
		digestOrUndefined(run.executorDigest) ?? digestOrUndefined(receipt.executor.digest) ?? "";
	const boundPolicyDigest = digestOrUndefined(run.policyDigest) ?? digestOrUndefined(request.policy_digest) ?? "";
	const boundReferenceSnapshotDigest = request.reference_snapshot_digest;
	if (formalOperation) {
		for (const [argument, expected] of [
			["workflow-digest", boundWorkflowDigest],
			["validator-digest", boundValidatorDigest],
			["executor-digest", boundExecutorDigest],
			["policy-digest", boundPolicyDigest],
			["reference-snapshot-digest", boundReferenceSnapshotDigest],
		] as const) {
			const supplied = one(args, argument);
			if (supplied !== undefined && (!validDigestValue(supplied) || !sameDigest(supplied, expected))) {
				formalDiagnostics.push(`${argument} does not match the run evidence binding`);
			}
		}
		if (run.workflowDigest !== undefined && !sameDigest(run.workflowDigest, definitionWorkflowDigest)) {
			formalDiagnostics.push("loaded workflow definition does not match the run workflow digest");
		}
		if (run.validatorDigest !== undefined && !sameDigest(run.validatorDigest, loadedRegistry.digest)) {
			formalDiagnostics.push("loaded validator registry does not match the run validator digest");
		}
	}
	const workflowDigest = formalOperation
		? boundWorkflowDigest
		: (digestOrUndefined(one(args, "workflow-digest")) ?? boundWorkflowDigest);
	const validatorDigest = formalOperation
		? boundValidatorDigest
		: (digestOrUndefined(one(args, "validator-digest")) ?? boundValidatorDigest);
	const executorDigest = formalOperation
		? boundExecutorDigest
		: (digestOrUndefined(one(args, "executor-digest")) ?? boundExecutorDigest);
	const policyDigest = formalOperation
		? boundPolicyDigest
		: (digestOrUndefined(one(args, "policy-digest")) ?? boundPolicyDigest);
	const referenceSnapshotDigest = formalOperation
		? boundReferenceSnapshotDigest
		: (digestOrUndefined(one(args, "reference-snapshot-digest")) ?? boundReferenceSnapshotDigest);
	const eligibility = assessFormalEligibility({
		request,
		receipt,
		receipt_validation: receiptValidation,
		assurance_profile: profileValue as AssuranceProfile,
		validator_verdict: validatorVerdict,
		workflow_disposition: workflowDisposition,
		subject,
		workflow_digest: workflowDigest,
		validator_digest: validatorDigest,
		executor_digest: executorDigest,
		policy_digest: policyDigest,
		reference_snapshot_digest: referenceSnapshotDigest,
		...(frozenSubject === undefined ? {} : { frozen_subject: frozenSubject }),
		receipt_digest: digestFile(receiptPath),
	});
	const authoritativeDiagnostics = [
		...formalDiagnostics,
		...(formalBinding === undefined ? [] : formalBinding.diagnostics),
	];
	const eligibilityWithEvidence =
		authoritativeDiagnostics.length === 0
			? eligibility
			: {
					...eligibility,
					eligible: false,
					outcome: createOutcome({
						validatorVerdict: "NOT_EXECUTED",
						workflowDisposition: authoritativeDiagnostics.some((message) => /requires|not PASS/i.test(message))
							? "WAIT"
							: "BLOCK",
						reasonCode: authoritativeDiagnostics.some((message) =>
							/digest|definition|registry|profile|match/i.test(message),
						)
							? "DIGEST_MISMATCH"
							: "VALIDATION_PENDING",
						diagnostics: authoritativeDiagnostics.map((message) => ({
							code: "FORMAL_EVIDENCE_REJECTED",
							message,
						})),
					}),
					diagnostics: [...authoritativeDiagnostics, ...eligibility.diagnostics],
				};
	const conformanceRecord = {
		schema: "sure.conformance.v1",
		conformance_id: one(args, "conformance-id") ?? `conformance-${randomUUID().slice(0, 12)}`,
		run_id: runId,
		unit_id: request.unit_id,
		attempt: request.attempt,
		request_digest: canonicalJsonDigest(request as unknown as JsonValue),
		receipt_digest: digestFile(receiptPath),
		subject_bundle_digest: subject.bundle_digest,
		...(subjectManifestPath === undefined ? {} : { subject_manifest_digest: digestFile(subjectManifestPath) }),
		...(frozenSubject === undefined
			? {}
			: {
					prediction_digest: frozenSubject.prediction_digest,
					evaluator_engine_digest: frozenSubject.evaluator_engine_digest,
					evaluator_route_digest: frozenSubject.evaluator_route_digest,
					...(frozenSubject.approval_event_digest === undefined
						? {}
						: { approval_event_digest: frozenSubject.approval_event_digest }),
					legacy_unverified: frozenSubject.legacy_unverified,
				}),
		runtime_identity_digest: subject.runtime_identity_digest,
		inference_protocol_digest: subject.inference_protocol_digest,
		dataset_identity_digest: subject.dataset_identity_digest,
		scoring_protocol_digest: subject.scoring_protocol_digest,
		workflow_digest: workflowDigest,
		validator_digest: validatorDigest,
		executor_digest: executorDigest,
		policy_digest: policyDigest,
		reference_snapshot_digest: referenceSnapshotDigest,
		output_root: request.output_root,
		validator_verdict: validatorVerdict,
		workflow_disposition: workflowDisposition,
		outcome: eligibilityWithEvidence.outcome.outcome,
		reason_code: eligibilityWithEvidence.outcome.reason_code,
		assurance_profile: profileValue,
		formal_evaluation_eligible: eligibilityWithEvidence.eligible,
		checked_at: new Date().toISOString(),
		evidence: [],
		diagnostics: eligibilityWithEvidence.diagnostics.map((message) => ({ message })),
	};
	const outputPath = admittedRunArtifactPath(
		store,
		run,
		absolute(one(args, "output") ?? join(run.runDir, "artifacts", "conformance.json"), "--output"),
	);
	writeJsonAtomic(outputPath, conformanceRecord);
	output({
		ok: eligibilityWithEvidence.eligible,
		command: "conformance",
		conformance: conformanceRecord,
		eligibility: eligibilityWithEvidence,
	});
	return eligibilityWithEvidence.outcome.outcome;
}

function workflowDigestForFallback(definition: WorkflowDefinition): string {
	return workflowDigest(definition);
}

function finalize(args: ParsedArgs): void {
	const root = rootFor(args);
	const runId = required(args, "run-id");
	const status = required(args, "status");
	if (!["success", "incomplete", "failed", "cancelled"].includes(status))
		throw new Error(`Invalid final status: ${status}`);
	const { store, run, policySnapshot } = contextForRun(args, root, runId);
	const policyReferences = referenceRoots(args, policySnapshot);
	const state = store.readState(run);
	const integrityError = stateIntegrityError(run, state);
	if (integrityError) throw new Error(integrityError);
	const artifacts = many(args, "artifact");
	let receiptPath: string | undefined;
	let successReceipt = false;
	let successReceiptDigest: string | undefined;
	if (status === "success") {
		const requestPath = admittedRunArtifactPath(
			store,
			run,
			absolute(required(args, "execution-request"), "--execution-request"),
		);
		receiptPath = admittedRunArtifactPath(
			store,
			run,
			absolute(one(args, "execution-receipt") ?? required(args, "receipt"), "--execution-receipt"),
		);
		assertRegularFile(requestPath, "Execution request");
		assertRegularFile(receiptPath, "Execution receipt");
		const request = recordObject(readJson(requestPath), "execution request") as unknown as ExecutionRequest;
		const receipt = recordObject(readJson(receiptPath), "execution receipt") as unknown as ExecutionReceipt;
		assertOutputRootBinding(store, run, request, policyReferences);
		const validation = validateExecutionReceipt(request, receipt, {
			allowed_output_roots: [run.runDir, ...(run.outputDir ? [run.outputDir] : [])],
			forbidden_output_roots: policyReferences,
		});
		if (!validation.valid || receipt.run_id !== runId || receipt.lifecycle !== "SUCCEEDED") {
			throw new Error(
				`Success execution receipt is not admissible: ${validation.errors.join("; ") || validation.outcome.reason_code}.`,
			);
		}
		if (run.executorDigest && !sameDigest(run.executorDigest, receipt.executor.digest)) {
			throw new Error("Success execution receipt executor digest does not match the run binding.");
		}
		if (run.policyDigest && !sameDigest(run.policyDigest, receipt.policy_digest)) {
			throw new Error("Success execution receipt policy digest does not match the run binding.");
		}
		const requestSurface =
			typeof request.runtime_requirements === "object" && request.runtime_requirements !== null
				? request.runtime_requirements.execution_surface
				: undefined;
		if (requestSurface === "vc" || requestSurface === "remote" || requestSurface === "trusted") {
			if (
				run.policySnapshotDigest === undefined ||
				!validDigestValue(request.policy_snapshot_digest) ||
				!sameDigest(request.policy_snapshot_digest, run.policySnapshotDigest) ||
				!validDigestValue(receipt.policy_snapshot_digest) ||
				!sameDigest(receipt.policy_snapshot_digest, run.policySnapshotDigest)
			) {
				throw new Error("Success external execution requires a receipt bound to the run policy snapshot.");
			}
		}
		assertReceiptOutputFiles(store, run, request, receipt);
		let loaded: LoadedDefinition | undefined;
		try {
			loaded = currentDefinition(args, root, run.skillName);
		} catch (error) {
			if (!run.legacyCompatibility) {
				throw new Error(
					`Success finalization requires the canonical workflow definition: ${error instanceof Error ? error.message : String(error)}`,
				);
			}
		}
		if (loaded !== undefined) {
			const checkpoint = checkpointFromState(loaded.definition, state);
			const checkpointAudit = auditCheckpointState(loaded.definition, checkpoint);
			if (!checkpointAudit.ok) throw new Error(checkpointAudit.reason ?? "Invalid checkpoint.");
			if (checkpoint.resumable) throw new Error("Success finalization requires a terminal checkpoint.");
			const lastOutcome = lastValidationOutcome(state);
			if (!run.legacyCompatibility && lastOutcome?.outcome !== "PASS") {
				throw new Error("Success finalization requires a persisted PASS validation for the terminal unit.");
			}
		}
		artifacts.push(requestPath);
		successReceipt = true;
		successReceiptDigest = digestFile(receiptPath);
		artifacts.push(receiptPath);
	}
	const finalized = store.finalizeRun(runId, status as "success" | "incomplete" | "failed" | "cancelled", {
		terminalCheckpoint: true,
		requiredArtifacts: artifacts,
		successReceipt,
		...(receiptPath === undefined ? {} : { successReceiptPath: receiptPath }),
		...(successReceiptDigest === undefined ? {} : { successReceiptDigest }),
	});
	output({ ok: true, command: "finalize", run: finalized, receipt_path: receiptPath });
}

function help(): void {
	output({
		commands: {
			start: "surectl start --skill <id> --run-id <id> --policy-digest <sha256> --executor-digest <sha256>",
			status: "surectl status --run-id <id>",
			validate:
				"surectl validate --run-id <id> [--artifact <path>] [--semantic-runtime <path> --validator-python <path>] [--evidence <compatibility-json>] [--execution-request <json> --execution-receipt <json>]",
			resume: "surectl resume --run-id <id> [--policy-digest <sha256> --executor-digest <sha256>]",
			execute:
				"surectl execute --run-id <id> (--operation <registered-id> --semantic-runtime <path> [--artifact <path>] | --execution-request <json> [--kind local|python|docker])",
			capabilities: "surectl capabilities [--skill <id>]",
			memory:
				"surectl memory --contract <memory-contract.json> [--skill <id>] [--uri memory://<skill>/<kind>/<slug>] [--operation publish|index|promote --semantic-runtime <dir> --memory-root <dir> --canonical-root <dir> --legacy-skills-root <dir>]",
			conformance: "surectl conformance --run-id <id> --execution-request <json> --execution-receipt <json>",
			freeze:
				"surectl freeze --run-id <id> --execution-request <json> --execution-receipt <json> --prediction <path> --engine-digest <sha256> --route-digest <sha256> --approval-digest <sha256>",
			finalize: "surectl finalize --run-id <id> --status <success|incomplete|failed|cancelled>",
		},
		exit_codes: SURECTL_EXIT_CODES,
		invariant:
			"Only Core validation results advance checkpoints; execute writes receipts only; missing capability is NOT_EXECUTED.",
	});
}

export function runSurectl(argv: readonly string[] = process.argv.slice(2)): number {
	try {
		const args = parseArgs(argv);
		let outcome: PublicOutcome | undefined;
		switch (args.command) {
			case "start":
				start(args);
				break;
			case "status":
				status(args);
				break;
			case "validate":
				outcome = validate(args);
				break;
			case "resume":
				resume(args);
				break;
			case "execute":
				outcome = execute(args);
				break;
			case "capabilities":
				outcome = capabilities(args);
				break;
			case "memory":
				outcome = memory(args);
				break;
			case "conformance":
				outcome = conformance(args);
				break;
			case "freeze":
				outcome = freeze(args);
				break;
			case "finalize":
				finalize(args);
				break;
			case "help":
			case "--help":
			case "-h":
				help();
				break;
			default:
				throw new Error(`Unknown surectl command: ${args.command}`);
		}
		return outcome === undefined ? SURECTL_EXIT_CODES.PASS : PUBLIC_OUTCOME_EXIT_CODES[outcome];
	} catch (error) {
		process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
		return SURECTL_EXIT_CODES.ERROR;
	}
}

if (import.meta.url === `file://${process.argv[1]}`) process.exitCode = runSurectl();
