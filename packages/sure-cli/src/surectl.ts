#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { isAbsolute, join, resolve } from "node:path";
import {
	applyValidation,
	type CapabilityEvidence,
	type CapabilityReport,
	canonicalJsonDigest,
	createOutcome,
	decodeLegacyCheckpoint,
	type ExecutionReceipt,
	type ExecutionRequest,
	encodeLegacyCheckpoint,
	evaluateCapabilityRequirements,
	initialCheckpoint,
	type JsonValue,
	type StateDocument,
	type StructuralValidationResult,
	validateExecutionReceipt,
	validateExecutionRequest,
	validateStructuralArtifact,
	type WorkflowCheckpoint,
	type WorkflowDefinition,
	type WorkflowUnit,
} from "@earendil-works/sure-core";
import { type LoadedDefinition, loadDefinition, unitForCurrent } from "./definition.ts";
import { NodeRunStore } from "./node-run-store.ts";

const CORE_VERSION = "0.80.3";
const SHA256 = /^[0-9a-f]{64}$/;

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

function absolute(value: string, label: string): string {
	if (!isAbsolute(value)) throw new Error(`${label} must be absolute: ${value}`);
	return resolve(value);
}

function json(value: unknown): string {
	return `${JSON.stringify(value, null, 2)}\n`;
}

function output(value: unknown): void {
	process.stdout.write(json(value));
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
		referenceRoots: many(args, "reference-root").map((value) => absolute(value, "--reference-root")),
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
		...metadata,
	};
}

function currentDefinition(args: ParsedArgs, root: string): LoadedDefinition {
	return loadDefinition(root, one(args, "definition"), one(args, "skill"));
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

function start(args: ParsedArgs): void {
	const root = absolute(one(args, "root") ?? process.cwd(), "--root");
	const loaded = currentDefinition(args, root);
	const registry = registryFor(loaded, args);
	const skillName = one(args, "skill") ?? loaded.definition.workflow_id;
	const runId =
		one(args, "run-id") ??
		`${new Date().toISOString().replace(/[-:.]/g, "").replace(/Z$/, "")}-${randomUUID().slice(0, 8)}`;
	const policyDigest = normalizeDigest(required(args, "policy-digest"), "--policy-digest");
	const executorDigest = normalizeDigest(required(args, "executor-digest"), "--executor-digest");
	const bindingDigest = one(args, "binding-digest");
	const store = storeFor(args, root);
	const record = store.createRun({
		runId,
		skillName,
		command: loaded.definition.workflow_id,
		cwd: root,
		packageDir: loaded.root,
		args: one(args, "args") ?? "",
		...(one(args, "output-dir") === undefined
			? {}
			: { outputDir: absolute(required(args, "output-dir"), "--output-dir") }),
		coreVersion: CORE_VERSION,
		workflowDigest: workflowDigest(loaded.definition),
		validatorDigest: registry.digest,
		executorDigest,
		policyDigest,
		...(bindingDigest === undefined ? {} : { bindingDigest: normalizeDigest(bindingDigest, "--binding-digest") }),
	});
	const checkpoint = initialCheckpoint(loaded.definition);
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
	const root = absolute(one(args, "root") ?? process.cwd(), "--root");
	const runId = required(args, "run-id");
	const store = storeFor(args, root);
	const run = store.readRun(runId);
	if (!run) throw new Error(`Run ${runId} does not exist.`);
	const state = store.readState(runId);
	let checkpoint: WorkflowCheckpoint | undefined;
	let nextUnit: string | undefined;
	try {
		const loaded = currentDefinition(args, root);
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

function validate(args: ParsedArgs): void {
	const root = absolute(one(args, "root") ?? process.cwd(), "--root");
	const runId = required(args, "run-id");
	const store = storeFor(args, root);
	const run = store.readRun(runId);
	if (!run) throw new Error(`Run ${runId} does not exist.`);
	const state = store.readState(runId);
	const loaded = currentDefinition(args, root);
	const registry = registryFor(loaded, args);
	if (run.validatorDigest !== registry.digest)
		throw new Error("Run validator digest does not match the registered validator set.");
	const checkpoint = checkpointFromState(loaded.definition, state);
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
			ok: execution.valid,
			command: "validate",
			kind: "execution",
			run: updated,
			unit: unit.id,
			outcome: execution.outcome,
			execution,
			checkpoint,
		});
		return;
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
	output({ ok: transition.accepted, command: "validate", run: updated, unit: unit.id, transition, outcome });
}

function capabilities(args: ParsedArgs): void {
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
		requirements = currentDefinition(args, absolute(one(args, "root") ?? process.cwd(), "--root")).capabilities.map(
			(entry) => ({ ...entry }),
		);
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
	output({ ok: true, command: "capabilities", report, admission });
}

function finalize(args: ParsedArgs): void {
	const root = absolute(one(args, "root") ?? process.cwd(), "--root");
	const runId = required(args, "run-id");
	const status = required(args, "status");
	if (!["success", "incomplete", "failed", "cancelled"].includes(status))
		throw new Error(`Invalid final status: ${status}`);
	const store = storeFor(args, root);
	const run = store.readRun(runId);
	if (!run) throw new Error(`Run ${runId} does not exist.`);
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
			capabilities: "surectl capabilities",
			finalize: "surectl finalize --run-id <id> --status <success|incomplete|failed|cancelled>",
		},
		invariant: "Only Core validation results advance checkpoints; missing capability is NOT_EXECUTED.",
	});
}

export function runSurectl(argv: readonly string[] = process.argv.slice(2)): number {
	try {
		const args = parseArgs(argv);
		switch (args.command) {
			case "start":
				start(args);
				break;
			case "status":
				status(args);
				break;
			case "validate":
				validate(args);
				break;
			case "capabilities":
				capabilities(args);
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
		return 0;
	} catch (error) {
		process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
		return 1;
	}
}

if (import.meta.url === `file://${process.argv[1]}`) process.exitCode = runSurectl();
