import { join, resolve } from "node:path";
import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type {
	ArtifactRef,
	CapabilityRequirement,
	ExecutionArtifactMode,
	ExecutionInputBinding,
	ExecutionOperation,
	ExecutionRequest,
	JsonValue,
} from "../contracts/types.ts";
import { bindExecutionInputs, type ExecutionInputBindingResolver } from "../execution/input-contract.ts";
import { executionOutputContractDigest } from "../execution/output-contract.ts";
import type { ResolvedSemanticBackend } from "./semantic-backend.ts";

export interface RegisteredOperationSemanticBinding {
	run_id: string;
	branch_id: string;
	unit_id: string;
	attempt: number;
	operation_id: string;
	request_operation: ExecutionOperation;
	/** Required for current requests; omitted only when validating legacy-v1 bytes. */
	artifact_mode?: ExecutionArtifactMode;
	artifact_input_digest: string;
	artifact_input_path?: string;
	workflow_digest?: string;
	runtime_digest: string;
	backend_registry_digest: string;
	backend_bundle_digest?: string;
	backend_resource_digest: string;
	reference_snapshot_digest: string;
	script_args: readonly string[];
	policy_digest: string;
	policy_snapshot_digest?: string;
	artifact_output_path?: string;
	output_contract_digest?: string;
	capability_requirements_digest?: string;
	input_contract_digest?: string;
	input_selector_id?: string;
	input_context_digest?: string;
	input_binding_digest?: string;
}

export interface RegisteredOperationRequestOptions {
	request_id: string;
	run_id: string;
	run_dir: string;
	workflow_digest?: string;
	policy_snapshot_digest?: string;
	branch_id: string;
	unit_id: string;
	attempt: number;
	request_operation: ExecutionOperation;
	backend: ResolvedSemanticBackend;
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
	artifacts_root: string;
	artifacts_resolved_root: string;
	semantic_runtime_digest: string;
	reference_snapshot_digest: string;
	policy_digest: string;
	created_at: string;
}

export function registeredOperationSemanticDigest(binding: RegisteredOperationSemanticBinding): string {
	return canonicalJsonDigest({ schema: "sure.semantic.operation.request.v1", ...binding } as unknown as JsonValue);
}

/** Return the canonical capability set required by a registered operation. */
export function registeredOperationCapabilityRequirements(
	operation: ResolvedSemanticBackend,
): readonly CapabilityRequirement[] {
	const requirements = new Map<string, CapabilityRequirement>();
	// Registered operations always execute through the SURE Python bridge.
	requirements.set("sure.execution.harness-python", {
		capability_id: "sure.execution.harness-python",
		capability_class: "execution_capability",
		required: true,
	});
	for (const requirement of operation.capability_requirements ?? []) {
		if (requirement.capability_id === "sure.execution.harness-python") continue;
		requirements.set(requirement.capability_id, { ...requirement });
	}
	return [...requirements.values()];
}

export function registeredOperationCapabilityRequirementsDigest(
	operation: ResolvedSemanticBackend,
): string | undefined {
	return operation.capability_requirements === undefined
		? undefined
		: canonicalJsonDigest(registeredOperationCapabilityRequirements(operation) as unknown as JsonValue);
}

/**
 * Build the one canonical request for a resolved registered operation. Hosts
 * own identity allocation and path admission; Core owns the semantic binding
 * and wire shape shared by portable and Pi execution surfaces.
 */
export function createRegisteredOperationRequest(options: RegisteredOperationRequestOptions): ExecutionRequest {
	const operation = options.backend;
	if (operation.artifact_mode === undefined)
		throw new Error(`${operation.operation_id} does not declare an artifact_mode`);
	const contract = operation.output_contract;
	let inputBinding: ExecutionInputBinding | undefined;
	if (operation.input_contract !== undefined) {
		if (
			options.input_context === undefined ||
			options.input_context_digest === undefined ||
			options.input_resolver === undefined
		) {
			throw new Error(`${operation.operation_id} requires a resolved input context and input resolver`);
		}
		inputBinding = bindExecutionInputs({
			contract: operation.input_contract,
			context: options.input_context,
			context_digest: options.input_context_digest,
			resolver: options.input_resolver,
		});
	}
	if (contract !== undefined && contract.outputs.length !== 1) {
		throw new Error(`${operation.operation_id} currently requires exactly one declared output`);
	}
	const declaredOutputPath =
		contract === undefined ? undefined : join(options.artifacts_root, contract.outputs[0]?.path ?? "");
	if (
		contract !== undefined &&
		options.output_path !== undefined &&
		declaredOutputPath !== undefined &&
		resolve(options.output_path) !== resolve(declaredOutputPath)
	) {
		throw new Error(`${operation.operation_id} output_path does not match its output contract`);
	}
	const outputPath = contract === undefined ? options.artifact.path : (options.output_path ?? declaredOutputPath!);
	const contractDigest = contract === undefined ? undefined : executionOutputContractDigest(contract);
	const declaredCapabilities = registeredOperationCapabilityRequirements(operation);
	const capabilityRequirementsDigest = registeredOperationCapabilityRequirementsDigest(operation);
	const binding: RegisteredOperationSemanticBinding = {
		run_id: options.run_id,
		branch_id: options.branch_id,
		unit_id: options.unit_id,
		attempt: options.attempt,
		operation_id: operation.operation_id,
		request_operation: options.request_operation,
		artifact_mode: operation.artifact_mode,
		artifact_input_digest: options.artifact.sha256,
		workflow_digest: options.workflow_digest,
		runtime_digest: options.semantic_runtime_digest,
		backend_registry_digest: operation.registry_digest,
		...(operation.bundle_digest === undefined ? {} : { backend_bundle_digest: operation.bundle_digest }),
		backend_resource_digest: operation.resource_digest,
		reference_snapshot_digest: options.reference_snapshot_digest,
		script_args: [...options.script_args],
		policy_digest: options.policy_digest,
		...(options.policy_snapshot_digest === undefined
			? {}
			: { policy_snapshot_digest: options.policy_snapshot_digest }),
		...(contract === undefined ? {} : { artifact_output_path: outputPath, output_contract_digest: contractDigest }),
		...(contract === undefined ? {} : { artifact_input_path: options.artifact.path }),
		...(capabilityRequirementsDigest === undefined
			? {}
			: { capability_requirements_digest: capabilityRequirementsDigest }),
		...(inputBinding === undefined
			? {}
			: {
					input_contract_digest: inputBinding.contract_digest,
					input_selector_id: inputBinding.selector_id,
					input_context_digest: inputBinding.context_digest,
					input_binding_digest: inputBinding.binding_digest,
				}),
	};
	return {
		schema: "sure.execution_request.v1",
		request_id: options.request_id,
		semantic_request_digest: registeredOperationSemanticDigest(binding),
		run_id: options.run_id,
		unit_id: options.unit_id,
		attempt: options.attempt,
		operation: options.request_operation,
		subject: {
			bundle_manifest_path: options.artifact.path,
			bundle_digest: options.artifact.sha256,
			runtime_identity_digest: options.semantic_runtime_digest,
		},
		inputs: inputBinding?.inputs.map((entry) => entry.artifact) ?? [options.artifact],
		...(inputBinding === undefined ? {} : { input_binding: inputBinding }),
		entrypoint: {
			executable: options.python_executable,
			argv: [operation.path, "--run-dir", options.run_dir, "--produces", outputPath, ...options.script_args],
			working_directory: options.package_dir,
		},
		runtime_requirements: {
			executor_kind: "python",
			harness_python_executable: options.python_executable,
			semantic_backend_operation_id: operation.operation_id,
			semantic_backend_registry_digest: operation.registry_digest,
			...(operation.bundle_digest === undefined ? {} : { semantic_backend_bundle_digest: operation.bundle_digest }),
			semantic_backend_resource_digest: operation.resource_digest,
			portable_runtime_digest: options.semantic_runtime_digest,
			workflow_digest: options.workflow_digest ?? "",
			branch_id: options.branch_id,
			artifact_mode: operation.artifact_mode,
			script_args: [...options.script_args],
			artifact_input_digest: options.artifact.sha256,
			...(contract === undefined ? {} : { artifact_input_path: options.artifact.path }),
			...(contract === undefined
				? {}
				: { artifact_output_path: outputPath, output_contract_digest: contractDigest }),
			...(capabilityRequirementsDigest === undefined
				? {}
				: { capability_requirements_digest: capabilityRequirementsDigest }),
			...(inputBinding === undefined
				? {}
				: {
						input_contract_digest: inputBinding.contract_digest,
						input_selector_id: inputBinding.selector_id,
						input_context_digest: inputBinding.context_digest,
						input_binding_digest: inputBinding.binding_digest,
					}),
		},
		capability_requirements: [...declaredCapabilities],
		reference_snapshot_digest: options.reference_snapshot_digest,
		output_root: {
			path: options.artifacts_root,
			resolved_path: options.artifacts_resolved_root,
			scope_id: options.run_id,
			policy_digest: options.policy_digest,
			writable: true,
		},
		policy_digest: options.policy_digest,
		...(options.policy_snapshot_digest === undefined
			? {}
			: { policy_snapshot_digest: options.policy_snapshot_digest }),
		created_at: options.created_at,
		...(contract === undefined ? {} : { output_contract: contract }),
	};
}
