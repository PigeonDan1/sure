import { spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname } from "node:path";
import type {
	ArtifactRef,
	CapabilityEvidence,
	CoreOutcome,
	ExecutionReceipt,
	ExecutionRequest,
	ExecutorKind,
	JsonValue,
} from "@earendil-works/sure-core";
import {
	canonicalJsonDigest,
	type ExecutionBoundaryOptions,
	type ExecutionReceiptValidation,
	type ExecutionRequestValidation,
	evaluateCapabilityRequirements,
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
]);

const MAX_CAPTURED_OUTPUT = 8192;

export interface ExecutorRunOptions {
	kind: Extract<ExecutorKind, "local" | "python" | "docker">;
	executor_digest: string;
	executor_version: string;
	working_directory: string;
	allowed_output_roots: readonly string[];
	forbidden_output_roots: readonly string[];
	timeout_ms: number;
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

function digestFile(path: string): string {
	return `sha256:${createHash("sha256").update(readFileSync(path)).digest("hex")}`;
}

function truncate(value: string): string {
	return value.length <= MAX_CAPTURED_OUTPUT ? value : `${value.slice(0, MAX_CAPTURED_OUTPUT)}\n...[truncated]`;
}

function runtimeExecutable(request: ExecutionRequest, key: string): string | undefined {
	const value = request.runtime_requirements?.[key];
	return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

function probeTarget(capabilityId: string, request: ExecutionRequest, options: ExecutorRunOptions): string | undefined {
	switch (capabilityId) {
		case "sure.execution.docker":
		case "sure.execution.docker-optional":
			return runtimeExecutable(request, "docker_executable") ?? process.env.DOCKER_BIN ?? "docker";
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
		default:
			return undefined;
	}
}

function probeCapabilities(
	request: ExecutionRequest,
	options: ExecutorRunOptions,
): Map<string, { executable?: string; result?: ReturnType<typeof spawnSync> }> {
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
	const results = new Map<string, { executable?: string; result?: ReturnType<typeof spawnSync> }>();
	for (const [capabilityId, executable] of targets) {
		try {
			results.set(capabilityId, {
				executable,
				result: spawnSync(executable, ["--version"], {
					cwd: options.working_directory,
					encoding: "utf8",
					timeout: Math.min(options.timeout_ms, 5000),
					maxBuffer: MAX_CAPTURED_OUTPUT,
				}),
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
		const probeSupported =
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
		const available = probeSupported && probe?.result?.status === 0;
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

function outputArtifact(path: string, index: number): ArtifactRef | undefined {
	if (!existsSync(path)) return undefined;
	let stat: ReturnType<typeof statSync>;
	try {
		stat = statSync(path);
	} catch {
		return undefined;
	}
	if (!stat.isFile()) return undefined;
	return {
		artifact_id: `output-${index + 1}`,
		path,
		resolved_path: path,
		sha256: digestFile(path),
		size: stat.size,
		media_type: "application/octet-stream",
		origin: "generated",
		source_root: dirname(path),
	};
}

function executorIdentity(options: ExecutorRunOptions) {
	return {
		executor_id: `surectl.${options.kind}`,
		kind: options.kind,
		version: options.executor_version,
		digest: options.executor_digest,
		// A portable CLI can prove which adapter ran, but it cannot enforce the
		// Pi lifecycle or provide a trusted attestation.
		trust_level: "cooperative" as const,
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
		reference_snapshot_digest: request.reference_snapshot_digest,
		output_root: request.output_root,
		policy_digest: request.policy_digest,
		started_at: startedAt,
		finished_at: finishedAt,
	};
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
		processResult = spawnSync(request.entrypoint.executable, request.entrypoint.argv, {
			cwd: request.entrypoint.working_directory ?? options.working_directory,
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
	const outputPaths = options.output_paths ?? [];
	const outputs: ArtifactRef[] = [];
	const missingOutputs: string[] = [];
	for (const [index, path] of outputPaths.entries()) {
		const artifact = outputArtifact(path, index);
		if (artifact) outputs.push(artifact);
		else missingOutputs.push(path);
	}
	receipt.outputs = outputs;
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
