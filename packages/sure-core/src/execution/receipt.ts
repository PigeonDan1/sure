import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import { evaluateCapabilityRequirements } from "../contracts/capability.ts";
import { evaluatePathBoundary } from "../contracts/path-boundary.ts";
import type {
	ArtifactRef,
	CapabilityEvidence,
	ExecutionReceipt,
	ExecutionRequest,
	ExecutorIdentity,
	JsonValue,
} from "../contracts/types.ts";
import { type CoreOutcome, createOutcome, outcomeFromExecutionLifecycle } from "../workflow/outcome.ts";
import { parseExecutionAdapterRoute } from "./adapter.ts";
import { dockerImageDigest, parseDockerRuntimeRequirements } from "./docker.ts";
import { validateExecutionInputBinding } from "./input-contract.ts";
import { validateExecutionOutputBinding, validateExecutionOutputContract } from "./output-contract.ts";
import type { ExecutionBoundaryOptions, ExecutionReceiptValidation, ExecutionRequestValidation } from "./types.ts";

const DIGEST = /^(?:sha256:)?[0-9a-f]{64}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

function sameDigest(left: string, right: string): boolean {
	return left.replace(/^sha256:/, "").toLowerCase() === right.replace(/^sha256:/, "").toLowerCase();
}

function validDigest(value: unknown): value is string {
	return typeof value === "string" && DIGEST.test(value);
}

function validId(value: unknown): value is string {
	return typeof value === "string" && ID.test(value);
}

function object(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function declaredContractDiagnostics(receipt: ExecutionReceipt): string[] {
	if (receipt.lifecycle !== "NOT_STARTED" || !Array.isArray(receipt.diagnostics)) return [];
	return receipt.diagnostics.flatMap((diagnostic) => {
		if (!object(diagnostic) || diagnostic.code !== "INVALID_CONTRACT" || typeof diagnostic.message !== "string")
			return [];
		return [diagnostic.message];
	});
}

function invalidOutcome(errors: readonly string[], code: "INVALID_CONTRACT" | "PATH_OUT_OF_SCOPE"): CoreOutcome {
	return createOutcome({
		validatorVerdict: "NOT_EXECUTED",
		workflowDisposition: "BLOCK",
		reasonCode: code,
		diagnostics: errors.map((message) => ({ code, message })),
	});
}

function requiredString(value: unknown, field: string, errors: string[]): void {
	if (typeof value !== "string" || value.length === 0) errors.push(`${field} must be a non-empty string`);
}

function validateArtifactRef(
	artifact: ArtifactRef,
	field: string,
	errors: string[],
	requireOutputOrigin = false,
): void {
	if (!object(artifact)) {
		errors.push(`${field} must be an object`);
		return;
	}
	for (const key of ["artifact_id", "path", "resolved_path", "media_type", "source_root"] as const) {
		requiredString(artifact[key], `${field}.${key}`, errors);
	}
	if (!validId(artifact.artifact_id)) errors.push(`${field}.artifact_id is not a valid id`);
	if (!validDigest(artifact.sha256)) errors.push(`${field}.sha256 must be a SHA-256 digest`);
	if (!Number.isSafeInteger(artifact.size) || artifact.size < 0)
		errors.push(`${field}.size must be a non-negative integer`);
	if (artifact.origin === "read_only_reference" && !validDigest(artifact.reference_snapshot_digest)) {
		errors.push(`${field}.reference_snapshot_digest is required for read-only references`);
	}
	const kind = artifact.kind ?? "file";
	if (kind !== "file" && kind !== "directory") errors.push(`${field}.kind is invalid`);
	const digestKind = artifact.digest_kind ?? "file_sha256";
	if (digestKind !== "file_sha256" && digestKind !== "tree_sha256") errors.push(`${field}.digest_kind is invalid`);
	if (kind === "directory" && digestKind !== "tree_sha256") {
		errors.push(`${field}.digest_kind must be tree_sha256 for a directory artifact`);
	}
	if (kind === "directory" && artifact.media_type !== "inode/directory") {
		errors.push(`${field}.media_type must be inode/directory for a directory artifact`);
	}
	if (kind === "file" && digestKind === "tree_sha256") {
		errors.push(`${field}.digest_kind cannot be tree_sha256 for a file artifact`);
	}
	if (requireOutputOrigin && (artifact.origin === "read_only_reference" || artifact.origin === "external")) {
		errors.push(`${field}.origin ${String(artifact.origin)} cannot be an executor output`);
	}
}

function validateExecutor(identity: ExecutorIdentity, errors: string[]): void {
	if (!object(identity)) {
		errors.push("executor must be an object");
		return;
	}
	requiredString(identity.executor_id, "executor.executor_id", errors);
	requiredString(identity.version, "executor.version", errors);
	if (!validId(identity.executor_id)) errors.push("executor.executor_id is not a valid id");
	if (!validDigest(identity.digest)) errors.push("executor.digest must be a SHA-256 digest");
	if (!["local", "python", "docker", "remote", "trusted"].includes(identity.kind))
		errors.push("executor.kind is invalid");
	if (!["cooperative", "host_enforced", "attested"].includes(identity.trust_level))
		errors.push("executor.trust_level is invalid");
}

function validateOutputRoot(
	root: { path: string; resolved_path: string; scope_id: string; policy_digest: string; writable: true },
	field: string,
	errors: string[],
): void {
	if (!object(root)) {
		errors.push(`${field} must be an object`);
		return;
	}
	for (const key of ["path", "resolved_path", "scope_id", "policy_digest"] as const)
		requiredString(root[key], `${field}.${key}`, errors);
	if (!validId(root.scope_id)) errors.push(`${field}.scope_id is not a valid id`);
	if (!validDigest(root.policy_digest)) errors.push(`${field}.policy_digest must be a SHA-256 digest`);
	if (root.writable !== true) errors.push(`${field}.writable must be true`);
}

function compareOutputRoot(request: ExecutionRequest, receipt: ExecutionReceipt, errors: string[]): void {
	if (!object(request.output_root) || !object(receipt.output_root)) return;
	for (const key of ["path", "resolved_path", "scope_id", "policy_digest", "writable"] as const) {
		if (request.output_root[key] !== receipt.output_root[key]) {
			errors.push(`receipt.output_root.${key} does not match request.output_root.${key}`);
		}
	}
}

function pathRoots(values: readonly string[] | undefined): Array<{ path: string; resolved_path: string }> {
	return (values ?? []).map((path) => ({ path, resolved_path: path }));
}

function validateRequestShape(request: ExecutionRequest, options: ExecutionBoundaryOptions): string[] {
	const errors: string[] = [];
	if (!object(request)) return ["execution request must be an object"];
	if (request.schema !== "sure.execution_request.v1") errors.push("request.schema is unsupported");
	for (const key of [
		"request_id",
		"run_id",
		"unit_id",
		"created_at",
		"reference_snapshot_digest",
		"policy_digest",
	] as const) {
		requiredString(request[key], `request.${key}`, errors);
	}
	if (!validId(request.request_id) || !validId(request.run_id) || !validId(request.unit_id))
		errors.push("request identity contains an invalid id");
	if (!validDigest(request.semantic_request_digest))
		errors.push("request.semantic_request_digest must be a SHA-256 digest");
	if (!validDigest(request.reference_snapshot_digest))
		errors.push("request.reference_snapshot_digest must be a SHA-256 digest");
	if (!validDigest(request.policy_digest)) errors.push("request.policy_digest must be a SHA-256 digest");
	if (!Number.isSafeInteger(request.attempt) || request.attempt < 1)
		errors.push("request.attempt must be a positive integer");
	if (!["validation", "inference", "formal_evaluation", "package", "publication"].includes(request.operation))
		errors.push("request.operation is invalid");
	if (!object(request.subject)) errors.push("request.subject must be an object");
	else {
		for (const key of ["bundle_manifest_path", "bundle_digest", "runtime_identity_digest"] as const)
			requiredString(request.subject[key], `request.subject.${key}`, errors);
		if (!validDigest(request.subject.bundle_digest))
			errors.push("request.subject.bundle_digest must be a SHA-256 digest");
		if (!validDigest(request.subject.runtime_identity_digest))
			errors.push("request.subject.runtime_identity_digest must be a SHA-256 digest");
		if (
			request.subject.inference_protocol_digest !== undefined &&
			!validDigest(request.subject.inference_protocol_digest)
		)
			errors.push("request.subject.inference_protocol_digest must be a SHA-256 digest");
		if (
			request.subject.dataset_identity_digest !== undefined &&
			!validDigest(request.subject.dataset_identity_digest)
		)
			errors.push("request.subject.dataset_identity_digest must be a SHA-256 digest");
		if (
			request.subject.scoring_protocol_digest !== undefined &&
			!validDigest(request.subject.scoring_protocol_digest)
		)
			errors.push("request.subject.scoring_protocol_digest must be a SHA-256 digest");
		if (request.operation === "formal_evaluation") {
			for (const key of [
				"inference_protocol_digest",
				"dataset_identity_digest",
				"scoring_protocol_digest",
			] as const) {
				if (!validDigest(request.subject[key])) errors.push(`formal evaluation requires request.subject.${key}`);
			}
		}
	}
	if (!object(request.entrypoint)) errors.push("request.entrypoint must be an object");
	else {
		requiredString(request.entrypoint.executable, "request.entrypoint.executable", errors);
		if (!Array.isArray(request.entrypoint.argv) || request.entrypoint.argv.some((value) => typeof value !== "string"))
			errors.push("request.entrypoint.argv must be a string array");
	}
	if (!Array.isArray(request.inputs)) errors.push("request.inputs must be an array");
	else
		request.inputs.forEach((artifact, index) => {
			validateArtifactRef(artifact, `request.inputs[${index}]`, errors);
		});
	if (!object(request.runtime_requirements)) {
		errors.push("request.runtime_requirements must be an object");
	} else {
		const route = parseExecutionAdapterRoute(request.runtime_requirements);
		errors.push(...route.errors);
		if (request.runtime_requirements.executor_kind === "docker") {
			const docker = parseDockerRuntimeRequirements(request.runtime_requirements);
			errors.push(...docker.errors);
			if (request.operation === "formal_evaluation") {
				const image = request.runtime_requirements.docker_image;
				if (
					typeof image !== "string" ||
					dockerImageDigest(image) === undefined ||
					docker.spec?.image_digest === undefined
				) {
					errors.push("formal Docker execution requires a digest-pinned docker_image");
				}
			}
		}
	}
	if (request.input_binding !== undefined) {
		errors.push(...validateExecutionInputBinding(request.input_binding, request.inputs).errors);
	}
	if (!Array.isArray(request.capability_requirements)) errors.push("request.capability_requirements must be an array");
	else {
		for (const [index, requirement] of request.capability_requirements.entries()) {
			if (!object(requirement)) {
				errors.push(`request.capability_requirements[${index}] must be an object`);
				continue;
			}
			if (!validId(requirement.capability_id))
				errors.push(`request.capability_requirements[${index}].capability_id is invalid`);
			if (!["agent_capability", "execution_capability"].includes(requirement.capability_class))
				errors.push(`request.capability_requirements[${index}].capability_class is invalid`);
			if (typeof requirement.required !== "boolean")
				errors.push(`request.capability_requirements[${index}].required must be boolean`);
		}
	}
	validateOutputRoot(request.output_root, "request.output_root", errors);
	if (request.output_contract !== undefined) {
		errors.push(...validateExecutionOutputContract(request.output_contract).errors);
	}
	if (options.allowed_output_roots && options.allowed_output_roots.length > 0) {
		const boundary = evaluatePathBoundary({
			candidate_path: request.output_root.path,
			candidate_resolved_path: request.output_root.resolved_path,
			allowed_roots: pathRoots(options.allowed_output_roots),
			forbidden_roots: pathRoots(options.forbidden_output_roots),
		});
		if (!boundary.admitted)
			errors.push(boundary.blocking_outcome?.diagnostics[0]?.message ?? "output root is outside policy");
	}
	return errors;
}

export function validateExecutionRequest(
	request: ExecutionRequest,
	options: ExecutionBoundaryOptions = {},
): ExecutionRequestValidation {
	const errors = validateRequestShape(request, options);
	return {
		valid: errors.length === 0,
		errors,
		outcome:
			errors.length === 0
				? createOutcome({
						validatorVerdict: "NOT_EXECUTED",
						workflowDisposition: "WAIT",
						reasonCode: "AWAITING_EXECUTION",
					})
				: invalidOutcome(
						errors,
						errors.some(
							(error) => error.includes("outside policy") || error.includes("outside every allowed root"),
						)
							? "PATH_OUT_OF_SCOPE"
							: "INVALID_CONTRACT",
					),
	};
}

function capabilityOutcome(
	evidence: readonly CapabilityEvidence[],
	request: ExecutionRequest,
	options: ExecutionBoundaryOptions,
): { capability: ReturnType<typeof evaluateCapabilityRequirements>; outcome?: CoreOutcome } {
	const capability = evaluateCapabilityRequirements(
		Array.isArray(request.capability_requirements) ? request.capability_requirements : [],
		evidence,
		options.known_capability_ids,
	);
	return {
		capability,
		...(capability.blocking_outcome === undefined ? {} : { outcome: capability.blocking_outcome }),
	};
}

export function validateExecutionReceipt(
	request: ExecutionRequest,
	receipt: ExecutionReceipt,
	options: ExecutionBoundaryOptions = {},
): ExecutionReceiptValidation {
	const requestValidation = validateExecutionRequest(request, options);
	if (!object(request)) {
		return {
			valid: false,
			errors: requestValidation.errors,
			outcome: requestValidation.outcome,
			capability: evaluateCapabilityRequirements([], [], options.known_capability_ids),
		};
	}
	const errors = [...requestValidation.errors];
	if (!object(receipt)) errors.push("execution receipt must be an object");
	else {
		if (receipt.schema !== "sure.execution_receipt.v1") errors.push("receipt.schema is unsupported");
		for (const key of ["receipt_id", "request_id", "run_id", "unit_id", "started_at"] as const)
			requiredString(receipt[key], `receipt.${key}`, errors);
		if (
			!validId(receipt.receipt_id) ||
			!validId(receipt.request_id) ||
			!validId(receipt.run_id) ||
			!validId(receipt.unit_id)
		)
			errors.push("receipt identity contains an invalid id");
		if (!validDigest(receipt.request_digest) || !validDigest(receipt.semantic_request_digest))
			errors.push("receipt request digests must be SHA-256 digests");
		if (!Number.isSafeInteger(receipt.attempt) || receipt.attempt < 1)
			errors.push("receipt.attempt must be a positive integer");
		if (
			!["NOT_STARTED", "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "PARTIAL", "CANCELLED"].includes(
				receipt.lifecycle,
			)
		)
			errors.push("receipt.lifecycle is invalid");
		validateExecutor(receipt.executor, errors);
		validateOutputRoot(receipt.output_root, "receipt.output_root", errors);
		if (!validDigest(receipt.reference_snapshot_digest))
			errors.push("receipt.reference_snapshot_digest must be a SHA-256 digest");
		if (!validDigest(receipt.policy_digest)) errors.push("receipt.policy_digest must be a SHA-256 digest");
		if (!Array.isArray(receipt.capability_evidence)) errors.push("receipt.capability_evidence must be an array");
		if (!Array.isArray(receipt.outputs)) errors.push("receipt.outputs must be an array");
		else
			receipt.outputs.forEach((artifact, index) => {
				validateArtifactRef(artifact, `receipt.outputs[${index}]`, errors, true);
			});
		if (request.input_binding === undefined) {
			if (receipt.input_binding_digest !== undefined)
				errors.push("receipt.input_binding_digest is not allowed without request.input_binding");
		} else if (!validDigest(receipt.input_binding_digest)) {
			errors.push("receipt.input_binding_digest is required when request.input_binding is present");
		} else if (!sameDigest(receipt.input_binding_digest, request.input_binding.binding_digest)) {
			errors.push("receipt.input_binding_digest does not match request.input_binding");
		}
		if (
			["SUCCEEDED", "FAILED", "PARTIAL", "CANCELLED"].includes(receipt.lifecycle) &&
			typeof receipt.finished_at !== "string"
		)
			errors.push("terminal receipt must include finished_at");
		if (receipt.lifecycle === "SUCCEEDED" && receipt.exit_code !== 0)
			errors.push("SUCCEEDED receipt must have exit_code 0");
		if (
			receipt.lifecycle === "SUCCEEDED" &&
			object(receipt.executor) &&
			receipt.executor.trust_level === "cooperative" &&
			options.require_attested_executor
		)
			errors.push("formal execution requires an attested executor");
		if (receipt.request_id !== request.request_id) errors.push("receipt.request_id does not match request");
		if (receipt.run_id !== request.run_id) errors.push("receipt.run_id does not match request");
		if (receipt.unit_id !== request.unit_id) errors.push("receipt.unit_id does not match request");
		if (receipt.attempt !== request.attempt) errors.push("receipt.attempt does not match request");
		if (validDigest(receipt.request_digest)) {
			const expectedRequestDigest = canonicalJsonDigest(request as unknown as JsonValue);
			if (!sameDigest(receipt.request_digest, expectedRequestDigest))
				errors.push("receipt.request_digest does not match canonical request digest");
		}
		if (
			validDigest(receipt.semantic_request_digest) &&
			validDigest(request.semantic_request_digest) &&
			!sameDigest(receipt.semantic_request_digest, request.semantic_request_digest)
		)
			errors.push("receipt.semantic_request_digest does not match request");
		if (
			validDigest(receipt.reference_snapshot_digest) &&
			validDigest(request.reference_snapshot_digest) &&
			!sameDigest(receipt.reference_snapshot_digest, request.reference_snapshot_digest)
		)
			errors.push("receipt.reference_snapshot_digest does not match request");
		if (
			validDigest(receipt.policy_digest) &&
			validDigest(request.policy_digest) &&
			!sameDigest(receipt.policy_digest, request.policy_digest)
		)
			errors.push("receipt.policy_digest does not match request");
		compareOutputRoot(request, receipt, errors);
		errors.push(...validateExecutionOutputBinding(request, receipt));
		for (const output of Array.isArray(receipt.outputs) ? receipt.outputs : []) {
			if (!object(output) || typeof output.path !== "string" || typeof output.resolved_path !== "string") continue;
			const boundary = evaluatePathBoundary({
				candidate_path: output.path,
				candidate_resolved_path: output.resolved_path,
				allowed_roots: [{ path: request.output_root.path, resolved_path: request.output_root.resolved_path }],
				forbidden_roots: pathRoots(options.forbidden_output_roots),
			});
			if (!boundary.admitted)
				errors.push(
					boundary.blocking_outcome?.diagnostics[0]?.message ??
						`output ${output.artifact_id} is outside output root`,
				);
		}
	}
	const evidence = Array.isArray(receipt.capability_evidence) ? receipt.capability_evidence : [];
	const capabilityResult = capabilityOutcome(evidence, request, options);
	const declaredContractErrors = object(receipt) ? declaredContractDiagnostics(receipt) : [];
	if (declaredContractErrors.length > 0) {
		return {
			valid: false,
			errors: [...errors, ...declaredContractErrors],
			outcome: invalidOutcome([...errors, ...declaredContractErrors], "INVALID_CONTRACT"),
			capability: capabilityResult.capability,
		};
	}
	if (capabilityResult.outcome) {
		return {
			valid: false,
			errors: [...errors, ...capabilityResult.outcome.diagnostics.map((diagnostic) => diagnostic.message)],
			outcome: capabilityResult.outcome,
			capability: capabilityResult.capability,
		};
	}
	if (errors.length > 0) {
		return {
			valid: false,
			errors,
			outcome: invalidOutcome(
				errors,
				errors.some((error) => error.includes("outside") || error.includes("root"))
					? "PATH_OUT_OF_SCOPE"
					: "INVALID_CONTRACT",
			),
			capability: capabilityResult.capability,
		};
	}
	return {
		valid: true,
		errors: [],
		outcome: outcomeFromExecutionLifecycle(receipt.lifecycle),
		capability: capabilityResult.capability,
	};
}
