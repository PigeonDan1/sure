import { randomUUID } from "node:crypto";
import { lstatSync, readFileSync, realpathSync } from "node:fs";
import { delimiter, isAbsolute, join, relative, resolve, sep } from "node:path";
import {
	type CapabilityEvidence,
	type CapabilityRequirement,
	canonicalJsonDigest,
	EXECUTOR_KINDS,
	EXECUTOR_TRUST_LEVELS,
	ExecutionProvenancePublisher,
	type ExecutionRequest,
	type ExecutionRequestDispatcher,
	type ExecutorIdentity,
	type JsonValue,
	validatePolicySnapshot,
} from "@earendil-works/sure-core";
import { NodeExecutionProvenancePublicationPort } from "@earendil-works/sure-core/node";
import type { SureHookContext, SureRunRecord } from "./types.ts";

const SAFE_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export interface PiExecutionProvenancePublisherOptions {
	run: Pick<SureRunRecord, "runDir" | "outputDir">;
	unit_id: string;
	invocation_id: string;
	forbidden_output_roots?: readonly string[];
}

export interface PiExecutionProvenancePublisher {
	root: string;
	publisher: ExecutionProvenancePublisher;
}

/**
 * The binding carried by a host-issued Pi session.  It is deliberately more
 * complete than the legacy operation evidence: a request cannot be created
 * without a generated skill lock, runtime identity, run identity and reference
 * snapshot.  `trust_level` remains cooperative in this slice.
 */
export interface PiExecutionProvenanceBinding {
	readonly skill_id: string;
	readonly branch_id: string;
	readonly workflow_digest: string;
	readonly validator_registry_digest: string;
	readonly semantic_runtime_digest: string;
	readonly semantic_backend_registry_digest: string;
	readonly executor_registry_digest: string;
	readonly core_package_version: string;
	readonly reference_snapshot_digest: string;
	readonly policy_digest: string;
	readonly policy_snapshot_digest?: string;
	readonly executor: ExecutorIdentity;
	readonly forbidden_output_roots: readonly string[];
}

export interface PiExecutionProvenanceSession extends PiExecutionProvenanceBinding {
	readonly invocation_id: string;
	readonly request_id: string;
	readonly receipt_id: string;
	readonly started_at: string;
	readonly run_id: string;
	readonly run_dir: string;
	readonly package_dir: string;
	readonly artifacts_root: string;
	readonly artifacts_resolved_root: string;
	readonly python_executable?: string;
	readonly publisher: ExecutionProvenancePublisher;
	readonly execution_dispatcher?: ExecutionRequestDispatcher;
	readonly execution_dispatcher_for_request?: (request: ExecutionRequest) => ExecutionRequestDispatcher | undefined;
	readonly capability_evidence_for?: (requirements: readonly CapabilityRequirement[]) => readonly CapabilityEvidence[];
}

/** Opaque host port exposed to a hook; issuance stays outside agent state. */
export interface PiExecutionProvenanceHost {
	readonly issue: (input: { unit_id: string; attempt: number; operation_id: string }) => PiExecutionProvenanceSession;
}

export interface PiExecutionProvenanceHostOptions {
	run: Pick<SureRunRecord, "runId" | "runDir" | "outputDir">;
	package_dir: string;
	skill_id: string;
	workflow_digest: string;
	validator_registry_digest: string;
	semantic_runtime_digest: string;
	semantic_backend_registry_digest: string;
	executor_registry_digest: string;
	core_package_version: string;
	reference_snapshot_digest: string;
	policy_digest: string;
	policy_snapshot_digest?: string;
	branch_id?: string;
	forbidden_output_roots?: readonly string[];
	python_executable?: string;
	executor?: ExecutorIdentity;
	now?: () => string;
	new_id?: () => string;
	execution_dispatcher?: ExecutionRequestDispatcher;
	execution_dispatcher_for_request?: (request: ExecutionRequest) => ExecutionRequestDispatcher | undefined;
	capability_evidence_for?: (requirements: readonly CapabilityRequirement[]) => readonly CapabilityEvidence[];
}

/** Optional host-only additions for a generated Pi provenance context. */
export interface PiExecutionProvenanceContextOptions {
	readonly execution_dispatcher?: ExecutionRequestDispatcher;
	readonly execution_dispatcher_for_request?: (request: ExecutionRequest) => ExecutionRequestDispatcher | undefined;
}

const DIGEST = /^sha256:[0-9a-f]{64}$/;

function validDigest(value: string): boolean {
	return DIGEST.test(value);
}

function safeId(value: string, label: string): void {
	if (!SAFE_SEGMENT.test(value)) throw new Error(`Pi execution provenance ${label} is unsafe`);
}

function uniqueAbsolute(paths: readonly string[]): string[] {
	const normalized: string[] = [];
	for (const path of paths) {
		if (!isAbsolute(path)) throw new Error("provenance path roots must be absolute");
		normalized.push(resolve(path));
	}
	return [...new Set(normalized)];
}

function contained(root: string, candidate: string): boolean {
	const relation = relative(resolve(root), resolve(candidate));
	return relation === "" || (relation !== ".." && !relation.startsWith(`..${sep}`) && !isAbsolute(relation));
}

function isMissingError(error: unknown): boolean {
	return typeof error === "object" && error !== null && "code" in error && error.code === "ENOENT";
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertExecutorIdentity(value: unknown): asserts value is ExecutorIdentity {
	if (!isRecord(value)) throw new Error("Pi execution provenance executor must be an object");
	if (typeof value.executor_id !== "string" || value.executor_id.trim() === "") {
		throw new Error("Pi execution provenance executor_id is required");
	}
	if (typeof value.version !== "string" || value.version.trim() === "") {
		throw new Error("Pi execution provenance executor version is required");
	}
	if (!EXECUTOR_KINDS.includes(value.kind as (typeof EXECUTOR_KINDS)[number])) {
		throw new Error("Pi execution provenance executor kind is invalid");
	}
	if (!EXECUTOR_TRUST_LEVELS.includes(value.trust_level as (typeof EXECUTOR_TRUST_LEVELS)[number])) {
		throw new Error("Pi execution provenance executor trust level is invalid");
	}
	if (typeof value.digest !== "string" || !validDigest(value.digest)) {
		throw new Error("Pi execution provenance executor digest is invalid");
	}
}

function regularFileWithin(path: string, root: string): { lexical: string; resolved: string } {
	if (!isAbsolute(path)) throw new Error("bound file path must be absolute");
	const lexical = resolve(path);
	const lexicalRoot = resolve(root);
	if (!contained(lexicalRoot, lexical)) throw new Error("bound file path is outside its admitted root");
	const rootStat = lstatSync(lexicalRoot);
	if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) {
		throw new Error("bound file root is not a regular directory");
	}
	const stat = lstatSync(lexical);
	if (!stat.isFile() || stat.isSymbolicLink()) throw new Error("bound file is not a regular file");
	const resolvedRoot = resolve(realpathSync.native(lexicalRoot));
	const resolved = resolve(realpathSync.native(lexical));
	if (!contained(resolvedRoot, resolved)) throw new Error("bound file resolves outside its admitted root");
	return { lexical, resolved };
}

function defaultId(): string {
	return randomUUID().replaceAll("-", "").slice(0, 20);
}

function defaultExecutor(digest: string, version: string): ExecutorIdentity {
	return {
		executor_id: "sure.pi-hook",
		kind: "python",
		version,
		digest,
		trust_level: "cooperative",
	};
}

/**
 * Pi-owned location adapter for the shared publication protocol. This factory
 * grants no assurance by itself; issuer verification remains in the host-only
 * lifecycle boundary.
 */
export function createPiExecutionProvenancePublisher(
	options: PiExecutionProvenancePublisherOptions,
): PiExecutionProvenancePublisher {
	if (!isAbsolute(options.run.runDir)) throw new Error("Pi execution provenance requires an absolute runDir");
	if (!SAFE_SEGMENT.test(options.unit_id)) throw new Error("Pi execution provenance unit_id is unsafe");
	if (!SAFE_SEGMENT.test(options.invocation_id)) throw new Error("Pi execution provenance invocation_id is unsafe");
	const root = join(options.run.runDir, "artifacts", "execution", options.unit_id, options.invocation_id);
	return {
		root,
		publisher: new ExecutionProvenancePublisher(
			new NodeExecutionProvenancePublicationPort({
				root,
				allowed_roots: [options.run.runDir, ...(options.run.outputDir ? [options.run.outputDir] : [])],
				forbidden_roots: options.forbidden_output_roots ?? [],
			}),
		),
	};
}

/**
 * Create one host-owned issuance port.  The caller supplies already verified
 * identity fields; this constructor only checks their shape and allocates
 * per-invocation IDs.  It never grants formal assurance or advances workflow.
 */
export function createPiExecutionProvenanceHost(options: PiExecutionProvenanceHostOptions): PiExecutionProvenanceHost {
	if (!isAbsolute(options.run.runDir)) throw new Error("Pi execution provenance requires an absolute runDir");
	if (!isAbsolute(options.package_dir)) throw new Error("Pi execution provenance requires an absolute package_dir");
	if (!SAFE_SEGMENT.test(options.skill_id)) throw new Error("Pi execution provenance skill_id is unsafe");
	if (!SAFE_SEGMENT.test(options.run.runId)) throw new Error("Pi execution provenance runId is unsafe");
	for (const [label, value] of [
		["workflow_digest", options.workflow_digest],
		["validator_registry_digest", options.validator_registry_digest],
		["semantic_runtime_digest", options.semantic_runtime_digest],
		["semantic_backend_registry_digest", options.semantic_backend_registry_digest],
		["executor_registry_digest", options.executor_registry_digest],
		["reference_snapshot_digest", options.reference_snapshot_digest],
		["policy_digest", options.policy_digest],
	] as const) {
		if (!validDigest(value)) throw new Error(`Pi execution provenance ${label} is not a SHA-256 digest`);
	}
	if (options.policy_snapshot_digest !== undefined && !validDigest(options.policy_snapshot_digest)) {
		throw new Error("Pi execution provenance policy_snapshot_digest is not a SHA-256 digest");
	}
	const branchId = options.branch_id ?? "main";
	safeId(branchId, "branch_id");
	const forbiddenRoots = uniqueAbsolute(options.forbidden_output_roots ?? []);
	const executor = options.executor ?? defaultExecutor(options.executor_registry_digest, options.core_package_version);
	assertExecutorIdentity(executor);
	if (options.run.outputDir !== undefined && !isAbsolute(options.run.outputDir)) {
		throw new Error("Pi execution provenance outputDir must be absolute");
	}
	const nextId = options.new_id ?? defaultId;
	const now = options.now ?? (() => new Date().toISOString());
	const issued = new Set<string>();

	return Object.freeze({
		issue(input: { unit_id: string; attempt: number; operation_id: string }): PiExecutionProvenanceSession {
			safeId(input.unit_id, "unit_id");
			safeId(input.operation_id, "operation_id");
			if (!Number.isSafeInteger(input.attempt) || input.attempt < 1) {
				throw new Error("Pi execution provenance attempt must be a positive integer");
			}
			const invocationSeed = nextId();
			const requestSeed = nextId();
			const receiptSeed = nextId();
			if (typeof invocationSeed !== "string" || typeof requestSeed !== "string" || typeof receiptSeed !== "string") {
				throw new Error("Pi execution provenance host allocated a non-string identifier");
			}
			const invocationId = invocationSeed;
			const requestId = `operation-${requestSeed}`;
			const receiptId = `receipt-${receiptSeed}`;
			if (!SAFE_SEGMENT.test(invocationId) || !SAFE_SEGMENT.test(requestId) || !SAFE_SEGMENT.test(receiptId)) {
				throw new Error("Pi execution provenance host allocated an unsafe identifier");
			}
			for (const id of [invocationId, requestId, receiptId]) {
				if (issued.has(id)) throw new Error(`Pi execution provenance identifier collision: ${id}`);
				issued.add(id);
			}
			const publication = createPiExecutionProvenancePublisher({
				run: options.run,
				unit_id: input.unit_id,
				invocation_id: invocationId,
				forbidden_output_roots: forbiddenRoots,
			});
			const artifactsRoot = join(options.run.runDir, "artifacts");
			const artifactsStat = lstatSync(artifactsRoot);
			if (!artifactsStat.isDirectory() || artifactsStat.isSymbolicLink()) {
				throw new Error("Pi execution provenance run artifacts root is not a regular directory");
			}
			const artifactsResolvedRoot = resolve(realpathSync.native(artifactsRoot));
			const publisher = publication.publisher;
			Object.freeze(publisher);
			const session: PiExecutionProvenanceSession = {
				skill_id: options.skill_id,
				branch_id: branchId,
				workflow_digest: options.workflow_digest,
				validator_registry_digest: options.validator_registry_digest,
				semantic_runtime_digest: options.semantic_runtime_digest,
				semantic_backend_registry_digest: options.semantic_backend_registry_digest,
				executor_registry_digest: options.executor_registry_digest,
				core_package_version: options.core_package_version,
				reference_snapshot_digest: options.reference_snapshot_digest,
				policy_digest: options.policy_digest,
				...(options.policy_snapshot_digest === undefined
					? {}
					: { policy_snapshot_digest: options.policy_snapshot_digest }),
				executor: Object.freeze({ ...executor }),
				forbidden_output_roots: Object.freeze([...forbiddenRoots]),
				invocation_id: invocationId,
				request_id: requestId,
				receipt_id: receiptId,
				started_at: now(),
				run_id: options.run.runId,
				run_dir: resolve(options.run.runDir),
				package_dir: resolve(options.package_dir),
				artifacts_root: resolve(artifactsRoot),
				artifacts_resolved_root: artifactsResolvedRoot,
				publisher,
				...(options.python_executable === undefined ? {} : { python_executable: options.python_executable }),
				...(options.execution_dispatcher === undefined
					? {}
					: { execution_dispatcher: Object.freeze(options.execution_dispatcher) }),
				...(options.execution_dispatcher_for_request === undefined
					? {}
					: { execution_dispatcher_for_request: options.execution_dispatcher_for_request }),
				...(options.capability_evidence_for === undefined
					? {}
					: { capability_evidence_for: options.capability_evidence_for }),
			};
			return Object.freeze(session);
		},
	});
}

export interface PiGenerationBinding {
	skill_id: string;
	definition_digest: string;
	workflow_digest: string;
	validator_registry_digest: string;
	semantic_runtime_digest: string;
	semantic_backend_registry_digest: string;
	executor_registry_digest: string;
	core_package_version: string;
}

interface GenerationBindingRead {
	present: boolean;
	binding?: PiGenerationBinding;
}

function readGenerationBinding(context: Omit<SureHookContext, "point">): GenerationBindingRead {
	const path = join(context.packageDir, "generation.lock.json");
	let stat: ReturnType<typeof lstatSync>;
	try {
		stat = lstatSync(path);
	} catch (error) {
		return isMissingError(error) ? { present: false } : { present: true };
	}
	if (!stat.isFile() || stat.isSymbolicLink()) return { present: true };
	try {
		const value = JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
		if (value.schema !== "sure.skill.generation.lock.v1" || value.host !== "pi") return { present: true };
		if (value.skill_id !== context.skill.name) return { present: true };
		const required = [
			"definition_digest",
			"workflow_digest",
			"validator_registry_digest",
			"semantic_runtime_digest",
			"semantic_backend_registry_digest",
			"executor_registry_digest",
		] as const;
		for (const field of required) {
			if (typeof value[field] !== "string" || !validDigest(value[field])) return { present: true };
		}
		if (typeof value.core_package_version !== "string" || value.core_package_version.trim() === "")
			return { present: true };
		return {
			present: true,
			binding: {
				skill_id: value.skill_id as string,
				definition_digest: value.definition_digest as string,
				workflow_digest: value.workflow_digest as string,
				validator_registry_digest: value.validator_registry_digest as string,
				semantic_runtime_digest: value.semantic_runtime_digest as string,
				semantic_backend_registry_digest: value.semantic_backend_registry_digest as string,
				executor_registry_digest: value.executor_registry_digest as string,
				core_package_version: value.core_package_version as string,
			},
		};
	} catch {
		return { present: true };
	}
}

function readPackageJson(packageDir: string, name: string): Record<string, unknown> {
	const path = join(packageDir, name);
	const file = regularFileWithin(path, packageDir);
	const value = JSON.parse(readFileSync(file.lexical, "utf8")) as unknown;
	if (!isRecord(value)) throw new Error(`generated Pi ${name} must contain a JSON object`);
	return value;
}

/**
 * A generation lock is useful only when the generated package can prove that
 * its local definition and registries are the objects the lock describes.
 * This is an integrity/consistency check, not a cryptographic trust root.
 */
function verifyGeneratedPackageBinding(context: Omit<SureHookContext, "point">, generation: PiGenerationBinding): void {
	const definition = readPackageJson(context.packageDir, "canonical-definition.json");
	if (definition.schema !== "sure.canonical.skill.v1" || definition.skill_id !== generation.skill_id) {
		throw new Error("generated Pi canonical definition is invalid");
	}
	if (canonicalJsonDigest(definition as unknown as JsonValue) !== generation.definition_digest) {
		throw new Error("generated Pi canonical definition digest does not match its generation lock");
	}
	if (
		!isRecord(definition.workflow) ||
		canonicalJsonDigest(definition.workflow as JsonValue) !== generation.workflow_digest
	) {
		throw new Error("generated Pi workflow digest does not match its generation lock");
	}
	const validators = readPackageJson(context.packageDir, "validator-registry.json");
	if (
		validators.schema !== "sure.validator.registry.v1" ||
		validators.digest !== generation.validator_registry_digest
	) {
		throw new Error("generated Pi validator registry does not match its generation lock");
	}
	const backends = readPackageJson(context.packageDir, "semantic-backends.json");
	if (
		backends.schema !== "sure.semantic.backends.v1" ||
		backends.registry_digest !== generation.semantic_backend_registry_digest ||
		backends.runtime_distribution_digest !== generation.semantic_runtime_digest
	) {
		throw new Error("generated Pi semantic backend registry does not match its generation lock");
	}
	const executors = readPackageJson(context.packageDir, "executor-registry.json");
	if (
		executors.schema !== "sure.executor.registry.v1" ||
		executors.registry_digest !== generation.executor_registry_digest
	) {
		throw new Error("generated Pi executor registry does not match its generation lock");
	}
}

/** Verify generated Pi lock and registry bytes before constructing a host adapter. */
export function verifyPiGeneratedPackageBinding(
	context: Omit<SureHookContext, "point">,
): PiGenerationBinding | undefined {
	const generation = readGenerationBinding(context);
	if (!generation.present) return undefined;
	if (generation.binding === undefined) throw new Error("generated Pi generation lock is malformed or mismatched");
	verifyGeneratedPackageBinding(context, generation.binding);
	return generation.binding;
}

function rejectingProvenanceHost(message: string): PiExecutionProvenanceHost {
	return Object.freeze({
		issue(): PiExecutionProvenanceSession {
			throw new Error(`Pi execution provenance binding rejected: ${message}`);
		},
	});
}

function environmentReferenceRoots(): string[] {
	const roots: string[] = [];
	for (const value of [process.env.SURE_REFERENCE_ROOT, process.env.REFERENCE_ROOT]) {
		for (const raw of value ? value.split(delimiter) : []) {
			const candidate = raw.trim();
			if (candidate === "") continue;
			if (!isAbsolute(candidate)) throw new Error("configured reference root must be absolute");
			roots.push(candidate);
		}
	}
	return uniqueAbsolute(roots);
}

function snapshotBinding(
	context: Omit<SureHookContext, "point">,
	policyDigest: string,
):
	| { reference_snapshot_digest: string; policy_snapshot_digest?: string; forbidden_output_roots: string[] }
	| undefined {
	const snapshotPath = context.run.policySnapshotPath;
	const snapshotDigest = context.run.policySnapshotDigest;
	if ((snapshotPath === undefined) !== (snapshotDigest === undefined)) return undefined;
	if (snapshotPath === undefined || snapshotDigest === undefined) {
		const roots = environmentReferenceRoots();
		return {
			reference_snapshot_digest: canonicalJsonDigest({
				schema: "sure.reference.binding.v1",
				policy_digest: policyDigest,
				roots: [...roots].sort(),
			} as unknown as JsonValue),
			forbidden_output_roots: roots,
		};
	}
	try {
		const snapshotFile = regularFileWithin(snapshotPath, join(context.runDir, "artifacts"));
		const snapshot = validatePolicySnapshot(JSON.parse(readFileSync(snapshotFile.lexical, "utf8")) as unknown);
		if (snapshot.snapshot_digest !== snapshotDigest || snapshot.policy_digest !== policyDigest) return undefined;
		const roots = uniqueAbsolute([
			...environmentReferenceRoots(),
			...snapshot.path_bindings
				.filter((binding) => ["read_only_reference", "dataset_source", "forbidden_output"].includes(binding.role))
				.flatMap((binding) => [binding.path, binding.resolved_path]),
		]);
		return {
			reference_snapshot_digest: snapshot.snapshot_digest,
			policy_snapshot_digest: snapshot.snapshot_digest,
			forbidden_output_roots: roots,
		};
	} catch {
		return undefined;
	}
}

/**
 * Host boundary used by the Pi controller. A package without a generation lock
 * remains on the legacy facade. Once a Pi generation lock is present, malformed
 * or drifted run/policy bindings return a rejecting port rather than silently
 * falling back to an unbound execution.
 */
export function createPiExecutionProvenanceHostForContext(
	context: Omit<SureHookContext, "point">,
	contextOptions: PiExecutionProvenanceContextOptions = {},
): PiExecutionProvenanceHost | undefined {
	let generation: PiGenerationBinding | undefined;
	try {
		generation = verifyPiGeneratedPackageBinding(context);
	} catch (error) {
		return rejectingProvenanceHost(error instanceof Error ? error.message : String(error));
	}
	if (generation === undefined) return undefined;
	const run = context.run;
	for (const [actual, expected] of [
		[run.workflowDigest, generation.workflow_digest],
		[run.validatorDigest, generation.validator_registry_digest],
		[run.executorDigest, generation.executor_registry_digest],
		[run.coreVersion, generation.core_package_version],
	] as const) {
		if (actual === undefined || actual !== expected)
			return rejectingProvenanceHost("run binding does not match the generated Pi distribution");
	}
	if (run.policyDigest === undefined)
		return rejectingProvenanceHost("run policy digest is missing from the generated Pi binding");
	const policyDigest = run.policyDigest;
	if (!validDigest(policyDigest)) return rejectingProvenanceHost("run policy digest is invalid");
	let snapshot: ReturnType<typeof snapshotBinding>;
	try {
		snapshot = snapshotBinding(context, policyDigest);
	} catch (error) {
		return rejectingProvenanceHost(error instanceof Error ? error.message : String(error));
	}
	if (snapshot === undefined) return rejectingProvenanceHost("site-policy/reference binding is incomplete");
	try {
		const artifactRoot = join(run.runDir, "artifacts");
		const artifactStat = lstatSync(artifactRoot);
		if (!artifactStat.isDirectory() || artifactStat.isSymbolicLink()) {
			return rejectingProvenanceHost("run artifact root is missing or not a regular directory");
		}
		return createPiExecutionProvenanceHost({
			run: {
				runId: run.runId,
				runDir: run.runDir,
				...(run.outputDir === undefined ? {} : { outputDir: run.outputDir }),
			},
			package_dir: context.packageDir,
			skill_id: generation.skill_id,
			workflow_digest: generation.workflow_digest,
			validator_registry_digest: generation.validator_registry_digest,
			semantic_runtime_digest: generation.semantic_runtime_digest,
			semantic_backend_registry_digest: generation.semantic_backend_registry_digest,
			executor_registry_digest: generation.executor_registry_digest,
			core_package_version: generation.core_package_version,
			reference_snapshot_digest: snapshot.reference_snapshot_digest,
			policy_digest: policyDigest,
			...(snapshot.policy_snapshot_digest === undefined
				? {}
				: { policy_snapshot_digest: snapshot.policy_snapshot_digest }),
			forbidden_output_roots: snapshot.forbidden_output_roots,
			...(contextOptions.execution_dispatcher === undefined
				? {}
				: { execution_dispatcher: contextOptions.execution_dispatcher }),
			...(contextOptions.execution_dispatcher_for_request === undefined
				? {}
				: { execution_dispatcher_for_request: contextOptions.execution_dispatcher_for_request }),
		});
	} catch (error) {
		return rejectingProvenanceHost(error instanceof Error ? error.message : String(error));
	}
}
