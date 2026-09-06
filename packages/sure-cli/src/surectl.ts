#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import {
	existsSync,
	linkSync,
	lstatSync,
	mkdirSync,
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
	applyValidation,
	assessFormalEligibility,
	auditCheckpointState,
	type CapabilityEvidence,
	type CapabilityReport,
	canonicalJsonDigest,
	createOutcome,
	decodeLegacyCheckpoint,
	type ExecutionReceipt,
	type ExecutionRequest,
	encodeLegacyCheckpoint,
	evaluateCapabilityRequirements,
	createFrozenEvaluationSubject,
	type FrozenFormalSubject,
	type FrozenEvaluationSubject,
	initialCheckpoint,
	type JsonValue,
	type PublicOutcome,
	type StateDocument,
	type StructuralValidationResult,
	validateExecutionReceipt,
	validateFrozenEvaluationSubject,
	validateExecutionRequest,
	validateStructuralArtifact,
	type WorkflowCheckpoint,
	type WorkflowDefinition,
	type WorkflowUnit,
} from "@earendil-works/sure-core";
import { type LoadedDefinition, loadDefinition, unitForCurrent } from "./definition.ts";
import { executeRequest } from "./executor.ts";
import { NodeRunStore } from "./node-run-store.ts";

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

function referenceRoots(args: ParsedArgs): string[] {
	return [
		...many(args, "reference-root").map((value) => absolute(value, "--reference-root")),
		...envPaths("SURE_REFERENCE_ROOT", "REFERENCE_ROOT").map((value) => absolute(value, "reference root")),
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

function writeJsonAtomic(path: string, value: unknown): void {
	mkdirSync(dirname(path), { recursive: true });
	const temporary = `${path}.sure-tmp-${process.pid}-${randomUUID()}`;
	writeFileSync(temporary, json(value), { encoding: "utf8", flag: "wx" });
	try {
		renameSync(temporary, path);
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
	writeFileSync(temporary, content, { encoding: "utf8", flag: "wx" });
	try {
		// A hard-link publish is no-clobber on POSIX filesystems, unlike rename().
		linkSync(temporary, path);
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

function sameDigest(left: string, right: string): boolean {
	return left.replace(/^sha256:/, "").toLowerCase() === right.replace(/^sha256:/, "").toLowerCase();
}

function storeFor(args: ParsedArgs, root: string, digestOverrides: Partial<Record<string, string>> = {}): NodeRunStore {
	return new NodeRunStore({
		rootDir: root,
		coreVersion: CORE_VERSION,
		referenceRoots: referenceRoots(args),
		writeRoots: many(args, "write-root").map((value) => absolute(value, "--write-root")),
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

function start(args: ParsedArgs): void {
	const root = rootFor(args);
	const loaded = currentDefinition(args, root);
	const registry = registryFor(loaded, args);
	const skillName = one(args, "skill") ?? loaded.definition.workflow_id;
	const runId =
		one(args, "run-id") ??
		`${new Date().toISOString().replace(/[-:.]/g, "").replace(/Z$/, "")}-${randomUUID().slice(0, 8)}`;
	const policyDigest = normalizeDigest(requiredValue(args, "policy-digest", "SURE_POLICY_DIGEST"), "--policy-digest");
	const executorDigest = normalizeDigest(
		requiredValue(args, "executor-digest", "SURE_EXECUTOR_DIGEST"),
		"--executor-digest",
	);
	const bindingDigest = one(args, "binding-digest");
	const store = storeFor(args, root);
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
		...(bindingDigest === undefined ? {} : { bindingDigest: normalizeDigest(bindingDigest, "--binding-digest") }),
	});
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
	const store = storeFor(args, root);
	const run = store.readRun(runId);
	if (!run) throw new Error(`Run ${runId} does not exist.`);
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

function validate(args: ParsedArgs): PublicOutcome {
	const root = rootFor(args);
	const runId = required(args, "run-id");
	const store = storeFor(args, root);
	const run = store.readRun(runId);
	if (!run) throw new Error(`Run ${runId} does not exist.`);
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
		const request = recordObject(readJson(requestPath), "execution request") as unknown as ExecutionRequest;
		if (request.run_id !== runId) throw new Error("Execution request run_id does not match the selected run.");
		if (request.unit_id !== unit.id)
			throw new Error(`Execution request unit_id ${request.unit_id} is not the current unit ${unit.id}.`);
		const allowedOutputRoots = [run.runDir, ...(run.outputDir ? [run.outputDir] : [])];
		const boundaryOptions = {
			allowed_output_roots: allowedOutputRoots,
			forbidden_output_roots: many(args, "reference-root").map((value) => absolute(value, "--reference-root")),
		};
		const requestValidation = validateExecutionRequest(request, boundaryOptions);
		let execution = requestValidation;
		let receiptPath: string | undefined;
		let receipt: ExecutionReceipt | undefined;
		if (executionReceiptPath) {
			receiptPath = admittedRunArtifactPath(store, run, absolute(executionReceiptPath, "--execution-receipt"));
			receipt = recordObject(readJson(receiptPath), "execution receipt") as unknown as ExecutionReceipt;
			const receiptValidation = validateExecutionReceipt(request, receipt, boundaryOptions);
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
	if (structural.ok && unit.kind === "gate") {
		const evidenceValue = one(args, "evidence");
		const evidencePath =
			evidenceValue === undefined
				? undefined
				: admittedRunArtifactPath(store, run, absolute(evidenceValue, "--evidence"));
		const gate = validateGateEvidence(evidencePath, unit, checkpoint.branch_id, registry, artifactDigest ?? "");
		validatorVerdict = gate.verdict;
		reason = gate.reason ?? "registered validators passed";
		evidence = gate.evidence;
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
			: transition.action === "retry"
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
				? reason.includes("capability") || reason.includes("validator")
					? "CAPABILITY_MISSING"
					: "VALIDATION_PENDING"
				: validatorVerdict === "FAIL"
					? transition.action === "exhausted"
						? "RETRY_EXHAUSTED"
						: "VALIDATION_FAILED"
					: "VALIDATION_PASSED",
	});
	const nextState = stateWithCheckpoint(state, transition.checkpoint, {
		last_validation: {
			unit_id: unit.id,
			artifact_path: artifactPath,
			artifact_digest: artifactDigest,
			outcome,
			evidence,
		},
	});
	const updated = store.writeState(runId, nextState, "validated", { unit_id: unit.id, outcome }, run.revision);
	output({ ok: outcome.outcome === "PASS", command: "validate", run: updated, unit: unit.id, transition, outcome });
	return outcome.outcome;
}

function capabilities(args: ParsedArgs): PublicOutcome {
	const now = new Date().toISOString();
	const evidence: CapabilityEvidence[] = [];
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
	output({ ok: outcome.outcome === "PASS", command: "capabilities", report, admission, outcome });
	return outcome.outcome;
}

function resume(args: ParsedArgs): void {
	const root = rootFor(args);
	const runId = required(args, "run-id");
	const store = storeFor(args, root);
	const run = store.readRun(runId);
	if (!run) throw new Error(`Run ${runId} does not exist.`);
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
	const policyDigest = normalizeDigest(
		one(args, "policy-digest") ?? run.policyDigest ?? requiredValue(args, "policy-digest", "SURE_POLICY_DIGEST"),
		"--policy-digest",
	);
	const bindingDigest = one(args, "binding-digest") ?? run.bindingDigest;
	const binding = {
		coreVersion: run.coreVersion ?? CORE_VERSION,
		workflowDigest: workflowDigest(loaded.definition),
		validatorDigest: registry.digest,
		executorDigest,
		policyDigest,
		...(bindingDigest === undefined ? {} : { bindingDigest: normalizeDigest(bindingDigest, "--binding-digest") }),
	};
	const resumed = store.resumeRun(runId, binding);
	const state = store.readState(resumed);
	const checkpoint = checkpointFromState(loaded.definition, state);
	output({ ok: true, command: "resume", run: resumed, state, checkpoint, binding });
}

function executionKind(args: ParsedArgs, request: ExecutionRequest): "local" | "python" | "docker" {
	const raw = one(args, "kind") ?? one(args, "executor");
	const runtimeRequirements =
		typeof request.runtime_requirements === "object" && request.runtime_requirements !== null
			? (request.runtime_requirements as Record<string, unknown>)
			: {};
	const candidate =
		raw ?? (typeof runtimeRequirements.executor_kind === "string" ? runtimeRequirements.executor_kind : "local");
	if (candidate !== "local" && candidate !== "python" && candidate !== "docker") {
		throw new Error(`Unsupported cooperative executor kind: ${candidate}`);
	}
	return candidate;
}

function execute(args: ParsedArgs): PublicOutcome {
	const root = rootFor(args);
	const runId = required(args, "run-id");
	const store = storeFor(args, root);
	const run = store.readRun(runId);
	if (!run) throw new Error(`Run ${runId} does not exist.`);
	const existingState = store.readState(run);
	const integrityError = stateIntegrityError(run, existingState);
	if (integrityError) throw new Error(integrityError);
	const loaded = currentDefinition(args, root, run.skillName);
	const checkpoint = checkpointFromState(loaded.definition, existingState);
	const checkpointAudit = auditCheckpointState(loaded.definition, checkpoint);
	if (!checkpointAudit.ok) throw new Error(checkpointAudit.reason ?? "Invalid checkpoint.");
	const currentUnit = unitForCurrent(loaded.definition, checkpoint.data.currentUnit, checkpoint.branch_id);
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
		forbidden_output_roots: referenceRoots(args),
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
	const store = storeFor(args, root);
	const run = store.readRun(runId);
	if (!run) throw new Error(`Run ${runId} does not exist.`);
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
	const request = recordObject(readJson(requestPath), "execution request") as unknown as ExecutionRequest;
	const receipt = recordObject(readJson(receiptPath), "execution receipt") as unknown as ExecutionReceipt;
	if (request.run_id !== runId || receipt.run_id !== runId)
		throw new Error("Frozen subject run_id does not match the selected run.");
	if (receipt.request_id !== request.request_id)
		throw new Error("Frozen subject receipt is not bound to the request.");
	const boundary = {
		allowed_output_roots: [run.runDir, ...(run.outputDir ? [run.outputDir] : [])],
		forbidden_output_roots: referenceRoots(args),
	};
	const requestValidation = validateExecutionRequest(request, boundary);
	const receiptValidation = validateExecutionReceipt(request, receipt, boundary);
	const missing: string[] = [];
	if (!requestValidation.valid) missing.push("valid execution request");
	if (!receiptValidation.valid) missing.push("valid execution receipt");
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
					referenceRoots(args),
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
		predictionPath = admittedReadArtifactPath(
			store,
			run,
			absolute(predictionArg, "--prediction"),
			referenceRoots(args),
		);
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
		const admittedEnginePath = admittedReadArtifactPath(store, run, enginePath, referenceRoots(args));
		evaluatorEngineDigest = digestPath(admittedEnginePath);
		missing.splice(missing.indexOf("evaluator_engine_digest"), 1);
	}
	const routeArg = one(args, "route");
	let evaluatorRouteDigest: string;
	if (routeArg && routeArg.startsWith("/")) {
		const routePath = admittedReadArtifactPath(store, run, routeArg, referenceRoots(args));
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
			referenceRoots(args),
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
	const store = storeFor(args, root);
	const run = store.readRun(runId);
	if (!run) throw new Error(`Run ${runId} does not exist.`);
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
	const request = recordObject(readJson(requestPath), "execution request") as unknown as ExecutionRequest;
	const receipt = recordObject(readJson(receiptPath), "execution receipt") as unknown as ExecutionReceipt;
	const profileValue = one(args, "assurance-profile") ?? "cooperative";
	if (profileValue !== "cooperative" && profileValue !== "pi_enforced" && profileValue !== "trusted")
		throw new Error(`Invalid --assurance-profile: ${profileValue}`);
	const boundary = {
		allowed_output_roots: [run.runDir, ...(run.outputDir ? [run.outputDir] : [])],
		forbidden_output_roots: referenceRoots(args),
		require_attested_executor: profileValue === "trusted",
	};
	const receiptValidation = validateExecutionReceipt(request, receipt, boundary);
	const state = store.readState(run) ?? {};
	const lastValidation =
		typeof state.last_validation === "object" && state.last_validation !== null
			? (state.last_validation as Record<string, unknown>)
			: {};
	const lastOutcome =
		typeof lastValidation.outcome === "object" && lastValidation.outcome !== null
			? (lastValidation.outcome as Record<string, unknown>)
			: {};
	const validatorVerdictValue = one(args, "validator-verdict") ?? lastOutcome.validator_verdict;
	const workflowDispositionValue = one(args, "workflow-disposition") ?? lastOutcome.workflow_disposition;
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
						referenceRoots(args),
					);
					subjectManifestPath = admittedPath;
					return recordObject(readJson(admittedPath), "subject");
				})();
	const frozenSubject =
		subjectInput.schema === "sure.evaluation_subject.v1"
			? (subjectInput as unknown as FrozenEvaluationSubject)
			: undefined;
	const subject: FrozenFormalSubject = {
		bundle_manifest_path:
			typeof subjectInput.bundle_manifest_path === "string"
				? subjectInput.bundle_manifest_path
				: request.subject.bundle_manifest_path,
		bundle_digest:
			digestOrUndefined(one(args, "bundle-digest")) ??
			(typeof subjectInput.bundle_digest === "string" ? subjectInput.bundle_digest : request.subject.bundle_digest),
		runtime_identity_digest:
			digestOrUndefined(one(args, "runtime-digest")) ??
			(typeof subjectInput.runtime_identity_digest === "string"
				? subjectInput.runtime_identity_digest
				: request.subject.runtime_identity_digest),
		inference_protocol_digest:
			digestOrUndefined(one(args, "inference-protocol-digest")) ??
			(typeof subjectInput.inference_protocol_digest === "string"
				? subjectInput.inference_protocol_digest
				: request.subject.inference_protocol_digest),
		dataset_identity_digest:
			digestOrUndefined(one(args, "dataset-digest")) ??
			(typeof subjectInput.dataset_identity_digest === "string"
				? subjectInput.dataset_identity_digest
				: (request.subject.dataset_identity_digest ?? "")),
		scoring_protocol_digest:
			digestOrUndefined(one(args, "scoring-digest")) ??
			(typeof subjectInput.scoring_protocol_digest === "string"
				? subjectInput.scoring_protocol_digest
				: (request.subject.scoring_protocol_digest ?? "")),
	};
	const loaded = currentDefinition(args, root, run.skillName);
	const workflowDigest =
		digestOrUndefined(one(args, "workflow-digest")) ??
		digestOrUndefined(run.workflowDigest) ??
		workflowDigestForFallback(loaded.definition);
	const validatorDigest =
		digestOrUndefined(one(args, "validator-digest")) ??
		digestOrUndefined(run.validatorDigest) ??
		registryFor(loaded, args).digest;
	const executorDigest =
		digestOrUndefined(one(args, "executor-digest")) ??
		digestOrUndefined(run.executorDigest) ??
		digestOrUndefined(receipt.executor.digest) ??
		"";
	const policyDigest =
		digestOrUndefined(one(args, "policy-digest")) ??
		digestOrUndefined(run.policyDigest) ??
		digestOrUndefined(request.policy_digest) ??
		"";
	const referenceSnapshotDigest =
		digestOrUndefined(one(args, "reference-snapshot-digest")) ?? request.reference_snapshot_digest;
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
		outcome: eligibility.outcome.outcome,
		reason_code: eligibility.outcome.reason_code,
		assurance_profile: profileValue,
		formal_evaluation_eligible: eligibility.eligible,
		checked_at: new Date().toISOString(),
		evidence: [],
		diagnostics: eligibility.diagnostics.map((message) => ({ message })),
	};
	const outputPath = admittedRunArtifactPath(
		store,
		run,
		absolute(one(args, "output") ?? join(run.runDir, "artifacts", "conformance.json"), "--output"),
	);
	writeJsonAtomic(outputPath, conformanceRecord);
	output({ ok: eligibility.eligible, command: "conformance", conformance: conformanceRecord, eligibility });
	return eligibility.outcome.outcome;
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
	const store = storeFor(args, root);
	const run = store.readRun(runId);
	if (!run) throw new Error(`Run ${runId} does not exist.`);
	const state = store.readState(run);
	const integrityError = stateIntegrityError(run, state);
	if (integrityError) throw new Error(integrityError);
	if (status === "success" && (one(args, "definition") !== undefined || one(args, "skill") !== undefined)) {
		const loaded = currentDefinition(args, root, run.skillName);
		const checkpoint = checkpointFromState(loaded.definition, state);
		const checkpointAudit = auditCheckpointState(loaded.definition, checkpoint);
		if (!checkpointAudit.ok) throw new Error(checkpointAudit.reason ?? "Invalid checkpoint.");
	}
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
		const request = recordObject(readJson(requestPath), "execution request") as unknown as ExecutionRequest;
		const receipt = recordObject(readJson(receiptPath), "execution receipt") as unknown as ExecutionReceipt;
		const validation = validateExecutionReceipt(request, receipt, {
			allowed_output_roots: [run.runDir, ...(run.outputDir ? [run.outputDir] : [])],
			forbidden_output_roots: many(args, "reference-root").map((value) => absolute(value, "--reference-root")),
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
		artifacts.push(requestPath);
		successReceipt = true;
		successReceiptDigest = digestFile(receiptPath);
		artifacts.push(receiptPath);
	}
	const finalized = store.finalizeRun(runId, status as "success" | "incomplete" | "failed" | "cancelled", {
		terminalCheckpoint: true,
		requiredArtifacts: artifacts,
		successReceipt,
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
				"surectl validate --run-id <id> [--artifact <path>] [--evidence <json>] [--execution-request <json> --execution-receipt <json>]",
			resume: "surectl resume --run-id <id> [--policy-digest <sha256> --executor-digest <sha256>]",
			execute: "surectl execute --run-id <id> --execution-request <json> [--kind local|python|docker]",
			capabilities: "surectl capabilities [--skill <id>]",
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
