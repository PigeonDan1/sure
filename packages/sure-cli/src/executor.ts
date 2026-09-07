import { spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { closeSync, constants, fstatSync, lstatSync, openSync, readFileSync, realpathSync } from "node:fs";
import { dirname, resolve } from "node:path";
import type {
	ArtifactRef,
	CapabilityEvidence,
	CoreOutcome,
	ExecutionOutputKind,
	ExecutionOutputResidual,
	ExecutionReceipt,
	ExecutionRequest,
	ExecutorKind,
	JsonValue,
} from "@earendil-works/sure-core";
import {
	canonicalJsonDigest,
	createOutcome,
	dockerImageDigest,
	type ExecutionBoundaryOptions,
	type ExecutionReceiptValidation,
	type ExecutionRequestValidation,
	evaluateCapabilityRequirements,
	evaluatePathBoundary,
	executionOutputContractDigest,
	executionOutputSetDigest,
	executorDescriptor,
	inspectExecutionArtifact,
	parseDockerRuntimeRequirements,
	validateExecutionReceipt,
	validateExecutionRequest,
} from "@earendil-works/sure-core";

const CAPABILITY_PROBE_IDS = new Set([
	"sure.execution.docker",
	"sure.execution.docker-optional",
	"sure.execution.evaluation-runtime",
	"sure.execution.harness-python",
	"sure.execution.local-python",
	"sure.execution.model-runtime",
	"sure.execution.source-runtime",
	"sure.execution.uv",
	"sure.execution.vc",
	"sure.execution.gpu",
]);

const MAX_CAPTURED_OUTPUT = 8192;

interface CapabilityProbeResult {
	executable?: string;
	result?: ReturnType<typeof spawnSync>;
	image_ref?: string;
	image_digest?: string;
	image_verified?: boolean;
}

export interface ExecutorRunOptions {
	kind: ExecutorKind;
	executor_digest: string;
	executor_version: string;
	working_directory: string;
	allowed_output_roots: readonly string[];
	forbidden_output_roots: readonly string[];
	timeout_ms: number;
	/** Exact environment used for capability probes and the executed process. */
	environment?: NodeJS.ProcessEnv;
	output_paths?: readonly string[];
	now?: () => string;
	receipt_id?: string;
}

export interface ExecutorRunResult {
	request_validation: ExecutionRequestValidation;
	receipt?: ExecutionReceipt;
	receipt_validation?: ExecutionReceiptValidation;
	outcome: CoreOutcome;
	capability: ReturnType<typeof evaluateCapabilityRequirements>;
}

function now(options: ExecutorRunOptions): string {
	return options.now?.() ?? new Date().toISOString();
}

function truncate(value: string): string {
	return value.length <= MAX_CAPTURED_OUTPUT ? value : `${value.slice(0, MAX_CAPTURED_OUTPUT)}\n...[truncated]`;
}

function runtimeExecutable(request: ExecutionRequest, key: string): string | undefined {
	const value = request.runtime_requirements?.[key];
	return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

function probeTarget(capabilityId: string, request: ExecutionRequest, options: ExecutorRunOptions): string | undefined {
	const environment = options.environment ?? process.env;
	switch (capabilityId) {
		case "sure.execution.docker":
		case "sure.execution.docker-optional":
			return runtimeExecutable(request, "docker_executable") ?? environment.DOCKER_BIN ?? "docker";
		case "sure.execution.local-python":
			return (
				runtimeExecutable(request, "python_executable") ??
				(options.kind === "local" || options.kind === "python" ? request.entrypoint.executable : undefined)
			);
		case "sure.execution.harness-python":
			return (
				runtimeExecutable(request, "harness_python_executable") ?? runtimeExecutable(request, "python_executable")
			);
		case "sure.execution.model-runtime":
			return runtimeExecutable(request, "model_runtime_executable");
		case "sure.execution.source-runtime":
			return runtimeExecutable(request, "source_runtime_executable");
		case "sure.execution.evaluation-runtime":
			return runtimeExecutable(request, "evaluation_runtime_executable");
		case "sure.execution.uv":
			return runtimeExecutable(request, "uv_executable") ?? environment.UV_BIN ?? environment.SURE_UV_BIN ?? "uv";
		case "sure.execution.vc":
			return runtimeExecutable(request, "vc_executable") ?? environment.VC_BIN ?? "vc";
		case "sure.execution.gpu":
			return runtimeExecutable(request, "gpu_probe_executable") ?? environment.NVIDIA_SMI_BIN ?? "nvidia-smi";
		default:
			return undefined;
	}
}

function probeArguments(capabilityId: string): readonly string[] {
	// The TRANS adapter uses `vc info` as its login-side health check; retain
	// that check here so the outer receipt and the adapter agree on admission.
	return capabilityId === "sure.execution.vc" ? ["info"] : ["--version"];
}

function dockerImageProbe(
	executable: string,
	request: ExecutionRequest,
	options: ExecutorRunOptions,
): Pick<CapabilityProbeResult, "image_ref" | "image_digest" | "image_verified"> {
	const parsed = parseDockerRuntimeRequirements(request.runtime_requirements);
	if (!parsed.valid || parsed.spec?.image_digest === undefined) return {};
	let result: ReturnType<typeof spawnSync>;
	try {
		result = spawnSync(executable, ["image", "inspect", "--format", "{{json .RepoDigests}}", parsed.spec.image], {
			cwd: options.working_directory,
			env: options.environment,
			encoding: "utf8",
			timeout: Math.min(options.timeout_ms, 5000),
			maxBuffer: MAX_CAPTURED_OUTPUT,
		});
	} catch {
		return { image_ref: parsed.spec.image, image_verified: false };
	}
	if (result.status !== 0 || typeof result.stdout !== "string") {
		return { image_ref: parsed.spec.image, image_verified: false };
	}
	try {
		const refs = JSON.parse(result.stdout.trim()) as unknown;
		const digests = Array.isArray(refs)
			? refs.flatMap((value) =>
					typeof value === "string" && dockerImageDigest(value) ? [dockerImageDigest(value)!] : [],
				)
			: [];
		const imageDigest = digests.find(
			(candidate) =>
				candidate.replace(/^sha256:/, "") === parsed.spec?.image_digest?.replace(/^sha256:/, "").toLowerCase(),
		);
		return {
			image_ref: parsed.spec.image,
			...(imageDigest === undefined ? {} : { image_digest: imageDigest }),
			image_verified: imageDigest !== undefined,
		};
	} catch {
		return { image_ref: parsed.spec.image, image_verified: false };
	}
}

function probeCapabilities(request: ExecutionRequest, options: ExecutorRunOptions): Map<string, CapabilityProbeResult> {
	const targets = new Map<string, string>();
	for (const requirement of request.capability_requirements ?? []) {
		if (
			requirement.capability_class !== "execution_capability" ||
			!CAPABILITY_PROBE_IDS.has(requirement.capability_id)
		)
			continue;
		const target = probeTarget(requirement.capability_id, request, options);
		if (target !== undefined) targets.set(requirement.capability_id, target);
	}
	const results = new Map<string, CapabilityProbeResult>();
	for (const [capabilityId, executable] of targets) {
		try {
			const result = spawnSync(executable, [...probeArguments(capabilityId)], {
				cwd: options.working_directory,
				env: options.environment,
				encoding: "utf8",
				timeout: Math.min(options.timeout_ms, 5000),
				maxBuffer: MAX_CAPTURED_OUTPUT,
			});
			results.set(capabilityId, {
				executable,
				result,
				...(capabilityId === "sure.execution.docker" && result.status === 0
					? dockerImageProbe(executable, request, options)
					: {}),
			});
		} catch {
			results.set(capabilityId, { executable });
		}
	}
	return results;
}

function capabilityEvidence(
	request: ExecutionRequest,
	options: ExecutorRunOptions,
): { evidence: CapabilityEvidence[]; evaluation: ReturnType<typeof evaluateCapabilityRequirements> } {
	const requirements = request.capability_requirements ?? [];
	const observedAt = now(options);
	const evidence: CapabilityEvidence[] = [];
	const probes = probeCapabilities(request, options);
	for (const requirement of requirements) {
		if (requirement.capability_id === "sure.core" && requirement.capability_class === "agent_capability") {
			const base = {
				capability_id: requirement.capability_id,
				capability_class: requirement.capability_class,
				status: "AVAILABLE" as const,
				source: "executor" as const,
				observed_at: observedAt,
				details: { control_plane: "surectl" },
			};
			evidence.push({ ...base, evidence_digest: canonicalJsonDigest(base as unknown as JsonValue) });
			continue;
		}
		if (requirement.capability_class !== "execution_capability") continue;
		const probe = probes.get(requirement.capability_id);
		const kindBoundProbeSupported =
			(requirement.capability_id === "sure.execution.local-python" &&
				(options.kind === "python" || options.kind === "local")) ||
			(requirement.capability_id === "sure.execution.harness-python" && options.kind === "python") ||
			(requirement.capability_id === "sure.execution.docker" && options.kind === "docker") ||
			(requirement.capability_id === "sure.execution.docker-optional" && options.kind === "docker") ||
			(requirement.capability_id === "sure.execution.source-runtime" &&
				(options.kind === "docker" || options.kind === "python")) ||
			(requirement.capability_id === "sure.execution.evaluation-runtime" && options.kind === "python") ||
			(requirement.capability_id === "sure.execution.model-runtime" &&
				(options.kind === "local" || options.kind === "python"));
		const hostProbeSupported =
			(requirement.capability_id === "sure.execution.uv" ||
				requirement.capability_id === "sure.execution.vc" ||
				requirement.capability_id === "sure.execution.gpu") &&
			probe !== undefined;
		const probeSupported = kindBoundProbeSupported || hostProbeSupported;
		const imageProbeSatisfied =
			requirement.capability_id !== "sure.execution.docker" ||
			!parseDockerRuntimeRequirements(request.runtime_requirements).spec?.image_digest ||
			probe?.image_verified === true;
		const available = probeSupported && probe?.result?.status === 0 && imageProbeSatisfied;
		const status = available ? ("AVAILABLE" as const) : ("MISSING" as const);
		const base = {
			capability_id: requirement.capability_id,
			capability_class: requirement.capability_class,
			status,
			source: "executor" as const,
			observed_at: observedAt,
			details: {
				kind: options.kind,
				...(probe?.executable === undefined ? {} : { executable: probe.executable }),
				...(probe?.image_ref === undefined ? {} : { image_ref: probe.image_ref }),
				...(probe?.image_digest === undefined ? {} : { image_digest: probe.image_digest }),
				...(probe?.image_verified === undefined ? {} : { image_verified: probe.image_verified }),
				...(probe?.result?.status === 0
					? { version: String(probe.result.stdout || probe.result.stderr || "").trim() }
					: {}),
			},
		};
		evidence.push({ ...base, evidence_digest: canonicalJsonDigest(base as unknown as JsonValue) });
	}
	return {
		evidence,
		evaluation: evaluateCapabilityRequirements(requirements, evidence),
	};
}

interface OutputInspection {
	artifact?: ArtifactRef;
	reason?: string;
}

interface ResidualInspection {
	residual?: ExecutionOutputResidual;
	reason?: string;
}

interface SpawnInvocation {
	executable: string;
	argv: readonly string[];
	working_directory?: string;
}

function resolvedRoot(path: string): { path: string; resolved_path: string } {
	const lexical = resolve(path);
	let resolvedPath = lexical;
	try {
		resolvedPath = realpathSync.native(lexical);
	} catch {
		// A root may be created by the child process. In that case its lexical
		// identity is the only stable value available to the boundary check.
	}
	return { path: lexical, resolved_path: resolve(resolvedPath) };
}

function outputArtifact(
	path: string,
	index: number,
	allowedRoots: readonly string[],
	forbiddenRoots: readonly string[],
	artifactId?: string,
	expectedKind: ExecutionOutputKind = "file",
): OutputInspection {
	const lexical = resolve(path);
	let stat: ReturnType<typeof lstatSync>;
	try {
		stat = lstatSync(lexical);
	} catch {
		return {};
	}
	if (stat.isSymbolicLink()) return { reason: "OUTPUT_SYMLINK" };
	if (expectedKind === "directory" ? !stat.isDirectory() : !stat.isFile()) return { reason: "OUTPUT_NOT_REGULAR" };
	let resolvedPath: string;
	try {
		resolvedPath = resolve(realpathSync.native(lexical));
	} catch {
		return { reason: "OUTPUT_UNRESOLVED" };
	}
	const boundary = evaluatePathBoundary({
		candidate_path: lexical,
		candidate_resolved_path: resolvedPath,
		allowed_roots: allowedRoots.map(resolvedRoot),
		forbidden_roots: forbiddenRoots.map(resolvedRoot),
	});
	if (!boundary.admitted) return { reason: boundary.reason_code ?? "OUTPUT_OUT_OF_SCOPE" };
	if (expectedKind === "directory") {
		try {
			const inspected = inspectExecutionArtifact(lexical);
			return {
				artifact: {
					artifact_id: artifactId ?? `output-${index + 1}`,
					path: lexical,
					resolved_path: resolvedPath,
					...inspected,
					origin: "generated",
					source_root: dirname(lexical),
				},
			};
		} catch (error) {
			return { reason: error instanceof Error ? "OUTPUT_TREE_INVALID" : "OUTPUT_READ_FAILED" };
		}
	}
	// O_NOFOLLOW prevents a replacement race between lstat and the digest read
	// from turning a reference path into an apparently generated artifact.
	let descriptor: number | undefined;
	try {
		descriptor = openSync(lexical, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
		const opened = fstatSync(descriptor);
		if (!opened.isFile()) return { reason: "OUTPUT_NOT_REGULAR" };
		const bytes = readFileSync(descriptor);
		return {
			artifact: {
				artifact_id: artifactId ?? `output-${index + 1}`,
				path: lexical,
				resolved_path: resolvedPath,
				sha256: `sha256:${createHash("sha256").update(bytes).digest("hex")}`,
				size: opened.size,
				media_type: "application/octet-stream",
				origin: "generated",
				source_root: dirname(lexical),
				// Keep the legacy file artifact shape stable. The Core validator
				// defaults omitted metadata to file/file_sha256; directory outputs
				// carry explicit kind/digest_kind fields below.
			},
		};
	} catch {
		return { reason: "OUTPUT_READ_FAILED" };
	} finally {
		if (descriptor !== undefined) closeSync(descriptor);
	}
}

function executorIdentity(options: ExecutorRunOptions) {
	const descriptor = executorDescriptor(options.kind);
	return {
		executor_id: descriptor?.executor_id ?? `surectl.${options.kind}`,
		kind: options.kind,
		version: options.executor_version,
		digest: options.executor_digest,
		trust_level: descriptor?.minimum_trust_level ?? ("cooperative" as const),
	};
}

function dockerMountRoots(
	request: ExecutionRequest,
	options: ExecutorRunOptions,
): { writable: string[]; read_only: string[] } {
	const writable = new Set<string>();
	for (const root of options.allowed_output_roots) writable.add(resolve(root));
	const readOnly = new Set<string>();
	for (const input of request.inputs) readOnly.add(resolve(input.source_root));
	const manifest = request.subject.bundle_manifest_path;
	if (typeof manifest === "string" && manifest.length > 0) readOnly.add(dirname(resolve(manifest)));
	return { writable: [...writable], read_only: [...readOnly].filter((root) => !writable.has(root)) };
}

function validateDockerMounts(
	request: ExecutionRequest,
	options: ExecutorRunOptions,
	spec: NonNullable<ReturnType<typeof parseDockerRuntimeRequirements>["spec"]>,
): string[] {
	const errors: string[] = [];
	const mountRoots = dockerMountRoots(request, options);
	const seenTargets = new Set<string>();
	for (const [index, mount] of spec.mounts.entries()) {
		const field = `runtime_requirements.docker_mounts[${index}]`;
		if (!mount.source.startsWith("/")) {
			errors.push(`${field}.source must be an absolute host path`);
			continue;
		}
		if (mount.source.includes(",") || mount.target.includes(",")) {
			errors.push(`${field} cannot contain commas in host or container paths`);
			continue;
		}
		const target = resolve(mount.target);
		if (seenTargets.has(target)) errors.push(`${field}.target is duplicated`);
		seenTargets.add(target);
		let stat: ReturnType<typeof lstatSync>;
		try {
			stat = lstatSync(mount.source);
		} catch {
			errors.push(`${field}.source does not exist: ${mount.source}`);
			continue;
		}
		if (stat.isSymbolicLink()) {
			errors.push(`${field}.source must not be a symlink: ${mount.source}`);
			continue;
		}
		if (!stat.isFile() && !stat.isDirectory()) {
			errors.push(`${field}.source must be a regular file or directory: ${mount.source}`);
			continue;
		}
		let resolvedSource: string;
		try {
			resolvedSource = resolve(realpathSync.native(mount.source));
		} catch {
			errors.push(`${field}.source could not be resolved: ${mount.source}`);
			continue;
		}
		const allowedRoots = [...mountRoots.writable, ...(mount.read_only ? mountRoots.read_only : [])].map(resolvedRoot);
		const boundary = evaluatePathBoundary({
			candidate_path: resolve(mount.source),
			candidate_resolved_path: resolvedSource,
			allowed_roots: allowedRoots,
			forbidden_roots: mount.read_only ? [] : options.forbidden_output_roots.map(resolvedRoot),
		});
		if (!boundary.admitted) {
			errors.push(
				`${field}.source is outside the admitted mount boundary: ${boundary.reason_code ?? "INVALID_CONTRACT"}`,
			);
		}
	}
	return errors;
}

function dockerInvocation(
	request: ExecutionRequest,
	options: ExecutorRunOptions,
): { invocation?: SpawnInvocation; errors: readonly string[] } {
	const requirements = request.runtime_requirements;
	const dockerRequirement = request.capability_requirements.find(
		(requirement) =>
			requirement.required &&
			requirement.capability_class === "execution_capability" &&
			requirement.capability_id === "sure.execution.docker",
	);
	if (dockerRequirement === undefined) {
		return {
			errors: ["docker executor requests must declare required capability sure.execution.docker"],
		};
	}
	const parsed = parseDockerRuntimeRequirements(requirements);
	if (!parsed.valid || parsed.spec === undefined) return { errors: parsed.errors };
	const mountErrors = validateDockerMounts(request, options, parsed.spec);
	if (mountErrors.length > 0) return { errors: mountErrors };
	const environment = options.environment ?? process.env;
	const executable = runtimeExecutable(request, "docker_executable") ?? environment.DOCKER_BIN ?? "docker";
	const argv: string[] = ["run", "--rm"];
	if (parsed.spec.network !== undefined) argv.push("--network", parsed.spec.network);
	for (const mount of parsed.spec.mounts) {
		argv.push("--mount", `type=bind,src=${mount.source},dst=${mount.target}${mount.read_only ? ",readonly" : ""}`);
	}
	for (const key of Object.keys(parsed.spec.environment).sort()) {
		argv.push("--env", `${key}=${parsed.spec.environment[key]}`);
	}
	if (parsed.spec.working_directory !== undefined) argv.push("--workdir", parsed.spec.working_directory);
	argv.push("--entrypoint", request.entrypoint.executable, parsed.spec.image, ...request.entrypoint.argv);
	return { invocation: { executable, argv, working_directory: options.working_directory }, errors: [] };
}

function contractFailure(
	requestValidation: ExecutionRequestValidation,
	request: ExecutionRequest,
	options: ExecutorRunOptions,
	errors: readonly string[],
): ExecutorRunResult {
	const startedAt = now(options);
	const outcome = createOutcome({
		validatorVerdict: "NOT_EXECUTED",
		workflowDisposition: "BLOCK",
		reasonCode: "INVALID_CONTRACT",
		executionLifecycle: "NOT_STARTED",
		diagnostics: errors.map((message) => ({ code: "INVALID_CONTRACT", message })),
	});
	const receipt = baseReceipt(request, options, [], "NOT_STARTED", startedAt, now(options));
	receipt.diagnostics = outcome.diagnostics.map(({ code, message }) => ({ code, message }));
	const receiptValidation = validateExecutionReceipt(request, receipt, {
		allowed_output_roots: options.allowed_output_roots,
		forbidden_output_roots: options.forbidden_output_roots,
	});
	return {
		request_validation: requestValidation,
		receipt,
		receipt_validation: receiptValidation,
		outcome,
		capability: evaluateCapabilityRequirements(request.capability_requirements, []),
	};
}

function baseReceipt(
	request: ExecutionRequest,
	options: ExecutorRunOptions,
	evidence: CapabilityEvidence[],
	lifecycle: ExecutionReceipt["lifecycle"],
	startedAt: string,
	finishedAt: string,
): ExecutionReceipt {
	const outputContract = request.output_contract;
	return {
		schema: "sure.execution_receipt.v1",
		receipt_id: options.receipt_id ?? `receipt-${randomUUID().slice(0, 12)}`,
		request_id: request.request_id,
		request_digest: canonicalJsonDigest(request as unknown as JsonValue),
		semantic_request_digest: request.semantic_request_digest,
		run_id: request.run_id,
		unit_id: request.unit_id,
		attempt: request.attempt,
		executor: executorIdentity(options),
		lifecycle,
		capability_evidence: evidence,
		outputs: [],
		...(request.input_binding === undefined ? {} : { input_binding_digest: request.input_binding.binding_digest }),
		reference_snapshot_digest: request.reference_snapshot_digest,
		output_root: request.output_root,
		policy_digest: request.policy_digest,
		started_at: startedAt,
		finished_at: finishedAt,
		...(outputContract === undefined
			? {}
			: {
					output_contract_digest: executionOutputContractDigest(outputContract),
					output_set_digest: executionOutputSetDigest([], []),
					residuals: [],
				}),
	};
}

function contractOutputDefinitions(
	request: ExecutionRequest,
): Map<string, { artifactId: string; kind: ExecutionOutputKind }> {
	const definitions = new Map<string, { artifactId: string; kind: ExecutionOutputKind }>();
	for (const spec of request.output_contract?.outputs ?? []) {
		definitions.set(resolve(request.output_root.resolved_path, ...spec.path.split("/")), {
			artifactId: spec.artifact_id,
			kind: spec.kind,
		});
	}
	return definitions;
}

function inspectResidual(
	path: string,
	allowedRoots: readonly string[],
	forbiddenRoots: readonly string[],
): ResidualInspection {
	const lexical = resolve(path);
	let stat: ReturnType<typeof lstatSync>;
	try {
		stat = lstatSync(lexical);
	} catch (error) {
		if (error instanceof Error && "code" in error && error.code === "ENOENT") return {};
		return { reason: "OUTPUT_RESIDUAL_STAT_FAILED" };
	}
	if (stat.isSymbolicLink()) return { reason: "OUTPUT_RESIDUAL_SYMLINK" };
	let resolvedPath: string;
	try {
		resolvedPath = resolve(realpathSync.native(lexical));
	} catch {
		return { reason: "OUTPUT_RESIDUAL_UNRESOLVED" };
	}
	const boundary = evaluatePathBoundary({
		candidate_path: lexical,
		candidate_resolved_path: resolvedPath,
		allowed_roots: allowedRoots.map(resolvedRoot),
		forbidden_roots: forbiddenRoots.map(resolvedRoot),
	});
	if (!boundary.admitted) return { reason: boundary.reason_code ?? "OUTPUT_RESIDUAL_OUT_OF_SCOPE" };
	try {
		const kind: ExecutionOutputKind = stat.isDirectory() ? "directory" : "file";
		const inspected = inspectExecutionArtifact(lexical);
		return {
			residual: {
				path: lexical,
				resolved_path: resolvedPath,
				kind,
				status: "present",
				sha256: inspected.sha256,
				digest_kind: inspected.digest_kind,
				size: inspected.size,
			},
		};
	} catch (error) {
		return { reason: error instanceof Error ? "OUTPUT_RESIDUAL_INVALID" : "OUTPUT_RESIDUAL_READ_FAILED" };
	}
}

/**
 * Execute one already-declared request. This adapter never reads or writes a
 * workflow checkpoint; callers must submit its receipt to `surectl validate`.
 */
export function executeRequest(request: ExecutionRequest, options: ExecutorRunOptions): ExecutorRunResult {
	const boundary: ExecutionBoundaryOptions = {
		allowed_output_roots: options.allowed_output_roots,
		forbidden_output_roots: options.forbidden_output_roots,
	};
	const requestValidation = validateExecutionRequest(request, boundary);
	if (!requestValidation.valid) {
		return {
			request_validation: requestValidation,
			outcome: requestValidation.outcome,
			capability: evaluateCapabilityRequirements([], []),
		};
	}
	let invocation: SpawnInvocation = {
		executable: request.entrypoint.executable,
		argv: request.entrypoint.argv,
		...(request.entrypoint.working_directory === undefined
			? {}
			: { working_directory: request.entrypoint.working_directory }),
	};
	if (options.kind === "docker") {
		const declaredKind = request.runtime_requirements.executor_kind;
		if (declaredKind !== undefined && declaredKind !== "docker") {
			return contractFailure(requestValidation, request, options, [
				`runtime_requirements.executor_kind must be docker for a docker executor (received ${String(declaredKind)})`,
			]);
		}
		const docker = dockerInvocation(request, options);
		if (docker.errors.length > 0 || docker.invocation === undefined) {
			return contractFailure(requestValidation, request, options, docker.errors);
		}
		invocation = docker.invocation;
	}
	const descriptor = executorDescriptor(options.kind);
	if (!descriptor || descriptor.implementation !== "builtin") {
		const observedAt = now(options);
		const capabilityIds = descriptor?.capability_ids ?? [`sure.execution.${options.kind}`];
		const evidence: CapabilityEvidence[] = capabilityIds.map((capabilityId) => {
			const base = {
				capability_id: capabilityId,
				capability_class: "execution_capability" as const,
				status: "MISSING" as const,
				source: "executor" as const,
				observed_at: observedAt,
				details: { kind: options.kind, implementation: "external_registration_required" },
			};
			return { ...base, evidence_digest: canonicalJsonDigest(base as unknown as JsonValue) };
		});
		const outcome = createOutcome({
			validatorVerdict: "NOT_EXECUTED",
			workflowDisposition: "BLOCK",
			reasonCode: "CAPABILITY_MISSING",
			executionLifecycle: "NOT_STARTED",
			diagnostics: [
				{
					code: "CAPABILITY_MISSING",
					message: `Executor ${options.kind} requires an external adapter that is not installed.`,
				},
			],
		});
		const receipt = baseReceipt(request, options, evidence, "NOT_STARTED", observedAt, now(options));
		receipt.diagnostics = outcome.diagnostics.map(({ code, message }) => ({ code, message }));
		const receiptValidation = validateExecutionReceipt(request, receipt, boundary);
		return {
			request_validation: requestValidation,
			receipt,
			receipt_validation: receiptValidation,
			outcome,
			capability: {
				admitted: false,
				unknown: [],
				missing: [...capabilityIds],
				denied: [],
				invalid_evidence: [],
				blocking_outcome: outcome,
			},
		};
	}
	const capabilities = capabilityEvidence(request, options);
	const startedAt = now(options);
	if (!capabilities.evaluation.admitted) {
		const receipt = baseReceipt(request, options, capabilities.evidence, "NOT_STARTED", startedAt, now(options));
		receipt.diagnostics = [
			{
				code: "CAPABILITY_MISSING",
				message: [
					...capabilities.evaluation.missing,
					...capabilities.evaluation.unknown,
					...capabilities.evaluation.denied,
				].join(", "),
			},
		];
		const receiptValidation = validateExecutionReceipt(request, receipt, boundary);
		return {
			request_validation: requestValidation,
			receipt,
			receipt_validation: receiptValidation,
			outcome: receiptValidation.outcome,
			capability: capabilities.evaluation,
		};
	}

	let processResult: ReturnType<typeof spawnSync>;
	try {
		processResult = spawnSync(invocation.executable, [...invocation.argv], {
			cwd: invocation.working_directory ?? options.working_directory,
			env: options.environment,
			encoding: "utf8",
			timeout: options.timeout_ms,
			maxBuffer: MAX_CAPTURED_OUTPUT * 4,
		});
	} catch (error) {
		const receipt = baseReceipt(request, options, capabilities.evidence, "FAILED", startedAt, now(options));
		receipt.exit_code = undefined;
		receipt.diagnostics = [
			{ code: "EXECUTOR_SPAWN_FAILED", message: error instanceof Error ? error.message : String(error) },
		];
		const receiptValidation = validateExecutionReceipt(request, receipt, boundary);
		return {
			request_validation: requestValidation,
			receipt,
			receipt_validation: receiptValidation,
			outcome: receiptValidation.outcome,
			capability: capabilities.evaluation,
		};
	}

	const timedOut =
		(typeof processResult.error === "object" &&
			processResult.error !== null &&
			"code" in processResult.error &&
			processResult.error.code === "ETIMEDOUT") ||
		processResult.signal !== null;
	const lifecycle: ExecutionReceipt["lifecycle"] = timedOut
		? "CANCELLED"
		: processResult.status === 0
			? "SUCCEEDED"
			: "FAILED";
	const receipt = baseReceipt(request, options, capabilities.evidence, lifecycle, startedAt, now(options));
	if (processResult.status !== null) receipt.exit_code = processResult.status;
	const definitions = contractOutputDefinitions(request);
	const outputPaths = [...definitions.keys(), ...(options.output_paths ?? [])].filter(
		(path, index, paths) => paths.indexOf(path) === index,
	);
	const outputs: ArtifactRef[] = [];
	const missingOutputs: string[] = [];
	const rejectedOutputs: Array<{ path: string; reason: string }> = [];
	for (const [index, path] of outputPaths.entries()) {
		const definition = definitions.get(resolve(path));
		const inspection = outputArtifact(
			path,
			index,
			options.allowed_output_roots,
			options.forbidden_output_roots,
			definition?.artifactId,
			definition?.kind ?? "file",
		);
		if (inspection.artifact) outputs.push(inspection.artifact);
		else if (inspection.reason) rejectedOutputs.push({ path, reason: inspection.reason });
		else missingOutputs.push(path);
	}
	receipt.outputs = outputs;
	const residualRejections: Array<{ path: string; reason: string }> = [];
	if (request.output_contract !== undefined) {
		const residualInspections = request.output_contract.temporary_paths.map((path) => ({
			path,
			inspection: inspectResidual(
				resolve(request.output_root.resolved_path, ...path.split("/")),
				options.allowed_output_roots,
				options.forbidden_output_roots,
			),
		}));
		const residuals = residualInspections.flatMap(({ path, inspection }) => {
			if (inspection.reason !== undefined) {
				residualRejections.push({ path, reason: inspection.reason });
				return [];
			}
			return inspection.residual === undefined ? [] : [inspection.residual];
		});
		receipt.residuals = residuals;
		receipt.output_set_digest = executionOutputSetDigest(outputs, residuals);
	}
	const diagnostics: Record<string, JsonValue>[] = [];
	if (processResult.error) {
		diagnostics.push({
			code: "EXECUTOR_PROCESS_ERROR",
			message: processResult.error.message,
		});
	}
	if (typeof processResult.stdout === "string" && processResult.stdout.trim() !== "")
		diagnostics.push({ code: "STDOUT", text: truncate(processResult.stdout) });
	if (typeof processResult.stderr === "string" && processResult.stderr.trim() !== "")
		diagnostics.push({ code: "STDERR", text: truncate(processResult.stderr) });
	if (timedOut) diagnostics.push({ code: "EXECUTOR_TIMEOUT", message: `Execution exceeded ${options.timeout_ms}ms.` });
	if (missingOutputs.length > 0) {
		diagnostics.push({ code: "OUTPUT_MISSING", paths: missingOutputs });
		if (receipt.lifecycle === "SUCCEEDED") {
			receipt.lifecycle = "PARTIAL";
			receipt.exit_code = 1;
		}
	}
	if (rejectedOutputs.length > 0) {
		diagnostics.push({ code: "OUTPUT_REJECTED", outputs: rejectedOutputs });
		if (receipt.lifecycle === "SUCCEEDED") {
			receipt.lifecycle = "PARTIAL";
			receipt.exit_code = 1;
		}
	}
	if (residualRejections.length > 0) {
		diagnostics.push({ code: "OUTPUT_RESIDUAL_REJECTED", outputs: residualRejections });
		if (receipt.lifecycle === "SUCCEEDED") {
			receipt.lifecycle = "PARTIAL";
			receipt.exit_code = 1;
		}
	}
	if (diagnostics.length > 0) receipt.diagnostics = diagnostics;
	const receiptValidation = validateExecutionReceipt(request, receipt, boundary);
	return {
		request_validation: requestValidation,
		receipt,
		receipt_validation: receiptValidation,
		outcome: receiptValidation.outcome,
		capability: capabilities.evaluation,
	};
}
