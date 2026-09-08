import { randomUUID } from "node:crypto";
import { join, resolve } from "node:path";
import type {
	ArtifactRef,
	CoreOutcome,
	CoreRunRecord,
	ExecutionAdmissionTrace,
	ExecutionInputBindingResolver,
	ExecutionOperation,
	ExecutionProvenancePublisher,
	ExecutionReceipt,
	ExecutionRequest,
	OperationExecutionEvidence,
	PublishedExecutionProvenance,
} from "@earendil-works/sure-core";
import {
	createOperationExecutionEvidence,
	createOutcome,
	validateExecutionAdmissionReceiptBinding,
} from "@earendil-works/sure-core";
import {
	createRegisteredOperationRequest,
	resolveSemanticBackendOperation,
	verifyPortableRuntime,
} from "@earendil-works/sure-core/evaluation";

export type { RegisteredOperationSemanticBinding } from "@earendil-works/sure-core/evaluation";
export {
	registeredOperationCapabilityRequirements,
	registeredOperationCapabilityRequirementsDigest,
	registeredOperationSemanticDigest,
} from "@earendil-works/sure-core/evaluation";

import { type ExecutorRunResult, executeRequest, projectSurectlExecutionEvidence } from "./executor.ts";
import { type SkillRuntimeBinding, semanticRuntimeEnvironment } from "./registered-validator.ts";

/** Compatibility export for callers that imported the former CLI-local type. */
export type RegisteredOperationEvidence = OperationExecutionEvidence;

export interface RegisteredOperationResult {
	outcome: CoreOutcome;
	evidence: RegisteredOperationEvidence;
	request?: ExecutionRequest;
	receipt?: ExecutionReceipt;
	admission_trace?: ExecutionAdmissionTrace;
	execution?: ExecutorRunResult;
	provenance?: PublishedExecutionProvenance;
}

export interface RegisteredOperationOptions {
	runtime_root?: string;
	runtime_binding: SkillRuntimeBinding;
	run: CoreRunRecord;
	branch_id: string;
	unit_id: string;
	attempt: number;
	operation_id: string;
	request_operation: ExecutionOperation;
	script_args: readonly string[];
	artifact: ArtifactRef;
	/** Resolved immutable context used by operations with conditional inputs. */
	input_context?: Readonly<Record<string, unknown>>;
	input_context_digest?: string;
	input_resolver?: ExecutionInputBindingResolver;
	/** Optional output path for a producer; defaults to the contract's first output. */
	output_path?: string;
	python_executable: string;
	package_dir: string;
	workspace_root: string;
	artifacts_root: string;
	artifacts_resolved_root: string;
	reference_snapshot_digest: string;
	policy_digest: string;
	forbidden_output_roots: readonly string[];
	created_at: string;
	base_environment?: NodeJS.ProcessEnv;
	provenance: ExecutionProvenancePublisher;
}

function diagnostics(result: ExecutorRunResult): string[] {
	const messages = [...result.request_validation.errors, ...(result.receipt_validation?.errors ?? [])];
	for (const diagnostic of result.receipt?.diagnostics ?? []) {
		if (typeof diagnostic.message === "string") messages.push(diagnostic.message);
		else if (typeof diagnostic.text === "string") messages.push(diagnostic.text);
		else messages.push(JSON.stringify(diagnostic));
	}
	return [...new Set(messages.filter((message) => message.trim() !== ""))];
}

function unavailable(
	options: RegisteredOperationOptions,
	reason: string,
	runtimeDigest?: string,
): RegisteredOperationResult {
	const outcome = createOutcome({
		validatorVerdict: "NOT_EXECUTED",
		workflowDisposition: "WAIT",
		reasonCode: "CAPABILITY_MISSING",
	});
	return {
		outcome,
		evidence: createOperationExecutionEvidence({
			source: "surectl",
			operation_id: options.operation_id,
			verdict: "NOT_EXECUTED",
			reason_code: "CAPABILITY_MISSING",
			diagnostics: [reason],
			artifact_input_digest: options.artifact.sha256,
			...(runtimeDigest === undefined ? {} : { runtime_digest: runtimeDigest }),
		}),
	};
}

function requestFor(
	options: RegisteredOperationOptions,
	operation: ReturnType<typeof resolveSemanticBackendOperation>,
): ExecutionRequest {
	return createRegisteredOperationRequest({
		request_id: `operation-${randomUUID().replaceAll("-", "").slice(0, 20)}`,
		run_id: options.run.runId,
		run_dir: options.run.runDir,
		workflow_digest: options.run.workflowDigest,
		policy_snapshot_digest: options.run.policySnapshotDigest,
		branch_id: options.branch_id,
		unit_id: options.unit_id,
		attempt: options.attempt,
		request_operation: options.request_operation,
		backend: operation,
		script_args: options.script_args,
		artifact: options.artifact,
		input_context: options.input_context,
		input_context_digest: options.input_context_digest,
		input_resolver: options.input_resolver,
		output_path: options.output_path,
		python_executable: options.python_executable,
		package_dir: options.package_dir,
		artifacts_root: options.artifacts_root,
		artifacts_resolved_root: options.artifacts_resolved_root,
		semantic_runtime_digest: options.runtime_binding.semantic_runtime_digest,
		reference_snapshot_digest: options.reference_snapshot_digest,
		policy_digest: options.policy_digest,
		created_at: options.created_at,
	});
}

export function runRegisteredOperation(options: RegisteredOperationOptions): RegisteredOperationResult {
	if (!options.runtime_root) return unavailable(options, "portable semantic operation runtime was not provided");
	let verification: ReturnType<typeof verifyPortableRuntime>;
	try {
		verification = verifyPortableRuntime(options.runtime_root, {
			expected_runtime_digest: options.runtime_binding.semantic_runtime_digest,
			expected_core_package_version: options.runtime_binding.core_package_version,
			expected_semantic_backend_registry_digest: options.runtime_binding.semantic_backend_registry_digest,
			expected_executor_registry_digest: options.runtime_binding.executor_registry_digest,
		});
	} catch (error) {
		return unavailable(options, error instanceof Error ? error.message : String(error));
	}
	const environment = semanticRuntimeEnvironment(options, verification.root);
	let operation: ReturnType<typeof resolveSemanticBackendOperation>;
	try {
		operation = resolveSemanticBackendOperation(verification.root, options.operation_id, {
			manifestPath: join(verification.root, "semantic-backends.json"),
			expectedRegistryDigest: verification.lock.semantic_backend_registry_digest,
			environment,
		});
		if (operation.kind !== "execute") throw new Error(`${operation.operation_id} is not an execute operation`);
		if (!operation.consumer_skill_ids.includes(options.runtime_binding.skill_id)) {
			throw new Error(
				`${options.runtime_binding.skill_id} is not an admitted consumer of ${operation.operation_id}`,
			);
		}
		if (operation.artifact_mode === undefined) {
			throw new Error(`${operation.operation_id} does not declare an artifact_mode`);
		}
		if (operation.artifact_mode === "producing" && operation.output_contract === undefined) {
			throw new Error(
				`${operation.operation_id} declares a producing artifact; an explicit output contract is required before gate binding`,
			);
		}
	} catch (error) {
		return unavailable(
			options,
			error instanceof Error ? error.message : String(error),
			verification.lock.runtime_digest,
		);
	}
	if (
		operation.requires_policy_snapshot &&
		(options.run.policySnapshotPath === undefined || options.run.policySnapshotDigest === undefined)
	) {
		return unavailable(
			options,
			`${operation.operation_id} requires an immutable site-policy snapshot bound to the run`,
			verification.lock.runtime_digest,
		);
	}
	let request: ExecutionRequest;
	try {
		request = requestFor(options, operation);
	} catch (error) {
		return unavailable(
			options,
			error instanceof Error ? error.message : String(error),
			verification.lock.runtime_digest,
		);
	}
	options.provenance.publishRequest(request);
	const execution = executeRequest(request, {
		kind: "python",
		executor_digest: options.run.executorDigest ?? options.runtime_binding.executor_registry_digest,
		executor_version: options.runtime_binding.core_package_version,
		working_directory: options.package_dir,
		allowed_output_roots: [options.run.runDir, ...(options.run.outputDir ? [options.run.outputDir] : [])],
		forbidden_output_roots: options.forbidden_output_roots,
		timeout_ms: operation.timeout_ms,
		environment,
		output_paths: [
			operation.output_contract === undefined
				? options.artifact.path
				: (options.output_path ?? join(options.artifacts_root, operation.output_contract.outputs[0]?.path ?? "")),
		],
	});
	const provenance = options.provenance.publishCompletion({
		request,
		...(execution.receipt === undefined ? {} : { receipt: execution.receipt }),
		admission: execution.admission_trace,
		validation_options: {
			require_receipt: true,
			allowed_output_roots: [options.run.runDir, ...(options.run.outputDir ? [options.run.outputDir] : [])],
			forbidden_output_roots: options.forbidden_output_roots,
		},
	});
	const admissionErrors = validateExecutionAdmissionReceiptBinding(request, execution.admission_trace, {
		receipt: execution.receipt,
		receipt_valid: execution.receipt_validation?.valid,
		capability_admitted: execution.capability.admitted,
	});
	const outputPath =
		operation.output_contract === undefined
			? options.artifact.path
			: (options.output_path ?? resolve(options.artifacts_root, operation.output_contract.outputs[0]?.path ?? ""));
	const outputArtifact = execution.receipt?.outputs.find(
		(candidate) => resolve(candidate.path) === resolve(outputPath),
	);
	const validReceipt = execution.receipt !== undefined && execution.receipt_validation?.valid === true;
	const missingSuccessfulOutput = execution.receipt?.lifecycle === "SUCCEEDED" && outputArtifact === undefined;
	const evidenceProjection =
		admissionErrors.length > 0
			? { verdict: "NOT_EXECUTED" as const, reason_code: "INVALID_CONTRACT" as const }
			: projectSurectlExecutionEvidence({
					lifecycle: execution.receipt?.lifecycle,
					receipt_valid: validReceipt,
					capability_admitted: execution.capability.admitted,
					missing_output: missingSuccessfulOutput,
					outcome_reason_code: execution.outcome.reason_code,
				});
	const outcome =
		admissionErrors.length > 0
			? createOutcome({
					validatorVerdict: "NOT_EXECUTED",
					workflowDisposition: "BLOCK",
					reasonCode: "INVALID_CONTRACT",
					diagnostics: admissionErrors.map((message) => ({
						code: "EXECUTION_ADMISSION_REJECTED",
						message,
					})),
				})
			: execution.outcome;
	return {
		outcome,
		request,
		receipt: execution.receipt,
		admission_trace: execution.admission_trace,
		execution,
		provenance,
		evidence: createOperationExecutionEvidence({
			source: "surectl",
			operation_id: operation.operation_id,
			artifact_mode: operation.artifact_mode,
			verdict: evidenceProjection.verdict,
			reason_code: evidenceProjection.reason_code,
			diagnostics: [
				...diagnostics(execution),
				...admissionErrors,
				...(missingSuccessfulOutput ? [`${operation.operation_id} did not bind the gate artifact output`] : []),
			],
			artifact_input_digest: options.artifact.sha256,
			...(operation.output_contract === undefined ? {} : { artifact_input_path: options.artifact.path }),
			...(operation.output_contract === undefined ? {} : { artifact_output_path: outputPath }),
			...(outputArtifact === undefined ? {} : { artifact_output_digest: outputArtifact.sha256 }),
			runtime_digest: verification.lock.runtime_digest,
			backend_registry_digest: operation.registry_digest,
			...(operation.bundle_digest === undefined ? {} : { backend_bundle_digest: operation.bundle_digest }),
			backend_resource_digest: operation.resource_digest,
			...(request.input_binding === undefined
				? {}
				: {
						input_contract_digest: request.input_binding.contract_digest,
						input_selector_id: request.input_binding.selector_id,
						input_context_digest: request.input_binding.context_digest,
						input_binding_digest: request.input_binding.binding_digest,
					}),
			request_path: provenance.documents.request.latest.path,
			request_digest: provenance.documents.request.latest.digest,
			admission_path: provenance.documents.admission.latest.path,
			admission_digest: provenance.documents.admission.latest.digest,
			...(provenance.documents.receipt === undefined
				? {}
				: {
						receipt_path: provenance.documents.receipt.latest.path,
						receipt_digest: provenance.documents.receipt.latest.digest,
					}),
			contract_path: provenance.documents.contract.latest.path,
			contract_digest: provenance.documents.contract.latest.digest,
			execution_history_digest: provenance.history_digest,
		}),
	};
}
