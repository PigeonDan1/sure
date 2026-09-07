import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import { evaluateCapabilityRequirements, validateCapabilityEvidenceList } from "../contracts/capability.ts";
import type {
	CapabilityEvaluation,
	CapabilityEvidence,
	CapabilityRequirement,
	ExecutionReceipt,
	ExecutionRequest,
	ExecutorIdentity,
	ExecutorKind,
	JsonValue,
} from "../contracts/index.ts";
import { type CoreOutcome, createOutcome } from "../workflow/outcome.ts";
import { executionAdapterRouteFor, parseExecutionAdapterRoute } from "./adapter.ts";
import { admitExternalAdapterRequest, type ExternalAdapterRequestAdmission } from "./adapter-policy.ts";
import { createExecutionAdmissionTrace } from "./admission.ts";
import { validateExecutionReceipt, validateExecutionRequest } from "./receipt.ts";
import type { ExecutorDescriptor, ExecutorRegistry } from "./registry.ts";
import type {
	ExecutionAdmissionTrace,
	ExecutionBoundaryOptions,
	ExecutionReceiptValidation,
	ExecutionRequestValidation,
	ExecutorPort,
} from "./types.ts";

/** Options shared by every host adapter dispatch. */
export interface ExecutorDispatchOptions extends ExecutionBoundaryOptions {
	/** A clock is injected by tests and deterministic host adapters. */
	now?: () => string;
}

/**
 * Result of dispatching one request through a registered executor port.
 * Dispatch is deliberately separate from workflow mutation: callers still
 * have to submit the returned receipt/evidence to the Core workflow engine.
 */
export interface ExecutorDispatchResult {
	descriptor?: ExecutorDescriptor;
	registered: boolean;
	admission_trace: ExecutionAdmissionTrace;
	request_validation: ExecutionRequestValidation;
	receipt?: ExecutionReceipt;
	receipt_validation?: ExecutionReceiptValidation;
	adapter_admission?: ExternalAdapterRequestAdmission;
	capability: CapabilityEvaluation;
	outcome: CoreOutcome;
}

function attachAdmissionTrace(
	result: Omit<ExecutorDispatchResult, "admission_trace">,
	request: ExecutionRequest,
	observedAt: string,
	probeInvoked: boolean,
	executeInvoked: boolean,
): ExecutorDispatchResult {
	return {
		...result,
		admission_trace: createExecutionAdmissionTrace(request, observedAt, result.outcome, {
			probe_invoked: probeInvoked,
			execute_invoked: executeInvoked,
			receipt: result.receipt,
			receipt_valid: result.receipt_validation?.valid === true,
		}),
	};
}

function emptyCapability(): CapabilityEvaluation {
	return evaluateCapabilityRequirements([], []);
}

function failure(
	validatorVerdict: "NOT_EXECUTED" | "FAIL",
	workflowDisposition: "BLOCK" | "RETRY",
	reasonCode: "CAPABILITY_MISSING" | "INVALID_CONTRACT" | "EXECUTION_FAILED",
	message: string,
	executionLifecycle?: ExecutionReceipt["lifecycle"],
): CoreOutcome {
	return createOutcome({
		validatorVerdict,
		workflowDisposition,
		reasonCode,
		...(executionLifecycle === undefined ? {} : { executionLifecycle }),
		diagnostics: [{ code: reasonCode, message }],
	});
}

function effectiveRequirements(request: ExecutionRequest, descriptor: ExecutorDescriptor): CapabilityRequirement[] {
	const requirements = new Map<string, CapabilityRequirement>();
	for (const requirement of request.capability_requirements ?? []) {
		requirements.set(requirement.capability_id, { ...requirement });
	}
	// An adapter's registry declaration is an admission prerequisite even when
	// a legacy request forgot to list the corresponding capability explicitly.
	for (const capabilityId of descriptor.capability_ids) {
		const existing = requirements.get(capabilityId);
		requirements.set(capabilityId, {
			...(existing ?? {
				capability_id: capabilityId,
				capability_class: "execution_capability",
			}),
			required: true,
		});
	}
	const route = executionAdapterRouteFor(request.runtime_requirements);
	if (route !== undefined) {
		const existing = requirements.get(route.capability_id);
		requirements.set(route.capability_id, {
			...(existing ?? {
				capability_id: route.capability_id,
				capability_class: "execution_capability",
			}),
			required: true,
		});
	}
	return [...requirements.values()];
}

function identityMismatch(expected: ExecutorIdentity, actual: ExecutorIdentity | undefined): string[] {
	if (actual === undefined || typeof actual !== "object") return ["receipt.executor is missing"];
	const errors: string[] = [];
	for (const field of ["executor_id", "kind", "version", "digest", "trust_level"] as const) {
		if (actual[field] !== expected[field])
			errors.push(`receipt.executor.${field} does not match registered executor`);
	}
	return errors;
}

function registeredPortIdentityMismatch(expected: ExecutorIdentity, actual: ExecutorIdentity | undefined): string[] {
	if (actual === undefined || typeof actual !== "object") return ["registered port.identity is missing"];
	const errors: string[] = [];
	for (const field of ["executor_id", "kind", "version", "digest", "trust_level"] as const) {
		if (actual[field] !== expected[field])
			errors.push(`registered port.identity.${field} does not match its pinned executor identity`);
	}
	return errors;
}

function invalidReceiptResult(
	descriptor: ExecutorDescriptor,
	requestValidation: ExecutionRequestValidation,
	capability: CapabilityEvaluation,
	receipt: ExecutionReceipt,
	validation: ExecutionReceiptValidation,
	identityErrors: readonly string[],
	adapterAdmission?: ExternalAdapterRequestAdmission,
): Omit<ExecutorDispatchResult, "admission_trace"> {
	const diagnostics = [...identityErrors, ...validation.errors];
	const outcome =
		identityErrors.length > 0
			? failure("NOT_EXECUTED", "BLOCK", "INVALID_CONTRACT", diagnostics.join("; "))
			: validation.outcome;
	return {
		descriptor,
		registered: true,
		...(adapterAdmission === undefined ? {} : { adapter_admission: adapterAdmission }),
		receipt,
		receipt_validation: {
			...validation,
			valid: false,
			errors: diagnostics,
			outcome,
		},
		request_validation: requestValidation,
		capability,
		outcome,
	};
}

function probeFailure(
	descriptor: ExecutorDescriptor,
	requestValidation: ExecutionRequestValidation,
	requirements: readonly CapabilityRequirement[],
	message: string,
	registered: boolean,
	observedAt: string,
	adapterAdmission?: ExternalAdapterRequestAdmission,
): Omit<ExecutorDispatchResult, "admission_trace"> {
	const evidence: CapabilityEvidence[] = descriptor.capability_ids.map((capabilityId) => {
		const base = {
			capability_id: capabilityId,
			capability_class: "execution_capability" as const,
			status: "MISSING" as const,
			source: "executor" as const,
			observed_at: observedAt,
			details: { implementation: descriptor.implementation, executor_kind: descriptor.kind },
		};
		return { ...base, evidence_digest: canonicalJsonDigest(base as unknown as JsonValue) };
	});
	const capability = evaluateCapabilityRequirements(requirements, evidence);
	return {
		descriptor,
		registered,
		...(adapterAdmission === undefined ? {} : { adapter_admission: adapterAdmission }),
		request_validation: requestValidation,
		capability,
		outcome: failure("NOT_EXECUTED", "BLOCK", "CAPABILITY_MISSING", message, "NOT_STARTED"),
	};
}

/**
 * Dispatch a request through a deployment-provided executor port.
 *
 * This is the single Core admission path for remote, VC-backed, and trusted
 * execution. It never falls back to a local process. A port must first prove
 * the declared capabilities, then return a receipt bound to both the request
 * and its registered identity. A malformed receipt is retained for diagnosis
 * but can never become an execution success.
 */
export async function dispatchExecutor(
	registry: ExecutorRegistry,
	request: ExecutionRequest,
	options: ExecutorDispatchOptions = {},
): Promise<ExecutorDispatchResult> {
	const observedAt = options.now?.() ?? new Date().toISOString();
	let probeInvoked = false;
	let executeInvoked = false;
	const finish = (result: Omit<ExecutorDispatchResult, "admission_trace">): ExecutorDispatchResult =>
		attachAdmissionTrace(result, request, observedAt, probeInvoked, executeInvoked);
	const requestValidation = validateExecutionRequest(request, options);
	if (!requestValidation.valid) {
		return finish({
			registered: false,
			request_validation: requestValidation,
			capability: emptyCapability(),
			outcome: requestValidation.outcome,
		});
	}
	const routeValidation = parseExecutionAdapterRoute(request.runtime_requirements);
	if (!routeValidation.valid) {
		return finish({
			registered: false,
			request_validation: {
				...requestValidation,
				valid: false,
				errors: routeValidation.errors,
				outcome: failure("NOT_EXECUTED", "BLOCK", "INVALID_CONTRACT", routeValidation.errors.join("; ")),
			},
			capability: emptyCapability(),
			outcome: failure("NOT_EXECUTED", "BLOCK", "INVALID_CONTRACT", routeValidation.errors.join("; ")),
		});
	}
	const kind = request.runtime_requirements?.executor_kind;
	if (typeof kind !== "string") {
		return finish({
			registered: false,
			request_validation: requestValidation,
			capability: emptyCapability(),
			outcome: failure(
				"NOT_EXECUTED",
				"BLOCK",
				"INVALID_CONTRACT",
				"execution request must declare runtime_requirements.executor_kind before adapter dispatch",
			),
		});
	}
	// The runtime field is intentionally generic in v1. Resolve through the
	// registry only after checking that it names one of the declared wire kinds.
	const wireKinds = ["local", "python", "docker", "remote", "trusted"] as const;
	const resolvedDescriptor = wireKinds.includes(kind as (typeof wireKinds)[number])
		? registry.descriptor(kind as ExecutorKind)
		: undefined;
	if (resolvedDescriptor === undefined) {
		return finish({
			registered: false,
			request_validation: requestValidation,
			capability: emptyCapability(),
			outcome: failure("NOT_EXECUTED", "BLOCK", "INVALID_CONTRACT", `unknown executor kind: ${kind}`),
		});
	}
	const requirements = effectiveRequirements(request, resolvedDescriptor);
	let adapterAdmission: ExternalAdapterRequestAdmission | undefined;
	let port: ExecutorPort | undefined;
	let expectedIdentity: ExecutorIdentity | undefined;
	if (routeValidation.route !== undefined) {
		const registration = registry.resolveExternal(resolvedDescriptor.kind, request.adapter_manifest_digest as string);
		if (registration === undefined) {
			return finish(
				probeFailure(
					resolvedDescriptor,
					requestValidation,
					requirements,
					`External adapter manifest ${String(request.adapter_manifest_digest)} is not registered for executor ${resolvedDescriptor.kind}; no local fallback is permitted.`,
					false,
					observedAt,
				),
			);
		}
		const portIdentityErrors = registeredPortIdentityMismatch(registration.identity, registration.port.identity);
		if (portIdentityErrors.length > 0) {
			return finish({
				descriptor: resolvedDescriptor,
				registered: true,
				request_validation: requestValidation,
				capability: emptyCapability(),
				outcome: failure("NOT_EXECUTED", "BLOCK", "INVALID_CONTRACT", portIdentityErrors.join("; ")),
			});
		}
		adapterAdmission = admitExternalAdapterRequest(registration.identity, registration.binding, request);
		if (!adapterAdmission.valid) {
			return finish({
				descriptor: resolvedDescriptor,
				registered: true,
				adapter_admission: adapterAdmission,
				request_validation: requestValidation,
				capability: emptyCapability(),
				outcome: failure(
					"NOT_EXECUTED",
					"BLOCK",
					"INVALID_CONTRACT",
					`External adapter admission failed: ${adapterAdmission.errors.join("; ")}`,
				),
			});
		}
		port = registration.port;
		expectedIdentity = registration.identity;
	} else {
		port = registry.resolve(resolvedDescriptor.kind);
		expectedIdentity = port?.identity;
	}
	if (port === undefined) {
		return finish(
			probeFailure(
				resolvedDescriptor,
				requestValidation,
				requirements,
				`Executor ${resolvedDescriptor.kind} requires a registered adapter; no local fallback is permitted.`,
				false,
				observedAt,
				adapterAdmission,
			),
		);
	}

	let evidence: readonly CapabilityEvidence[];
	try {
		probeInvoked = true;
		evidence = port.probe(requirements);
	} catch (error) {
		return finish(
			probeFailure(
				resolvedDescriptor,
				requestValidation,
				requirements,
				`Executor ${resolvedDescriptor.kind} capability probe failed: ${error instanceof Error ? error.message : String(error)}`,
				true,
				observedAt,
				adapterAdmission,
			),
		);
	}
	const evidenceErrors = validateCapabilityEvidenceList(evidence);
	if (evidenceErrors.length > 0) {
		return finish({
			descriptor: resolvedDescriptor,
			registered: true,
			...(adapterAdmission === undefined ? {} : { adapter_admission: adapterAdmission }),
			request_validation: requestValidation,
			capability: evaluateCapabilityRequirements(requirements, evidence),
			outcome: failure(
				"NOT_EXECUTED",
				"BLOCK",
				"INVALID_CONTRACT",
				`Executor ${resolvedDescriptor.kind} returned malformed capability evidence: ${evidenceErrors.join("; ")}`,
			),
		});
	}
	if (
		!Array.isArray(evidence) ||
		evidence.some(
			(entry) =>
				typeof entry !== "object" ||
				entry === null ||
				typeof entry.capability_id !== "string" ||
				!(["agent_capability", "execution_capability"] as readonly string[]).includes(entry.capability_class) ||
				!(["AVAILABLE", "MISSING", "UNKNOWN", "DENIED"] as readonly string[]).includes(entry.status) ||
				typeof entry.observed_at !== "string" ||
				(entry.status === "AVAILABLE" && typeof entry.evidence_digest !== "string") ||
				(entry.capability_class === "execution_capability" && entry.source === "agent"),
		)
	) {
		return finish({
			descriptor: resolvedDescriptor,
			registered: true,
			...(adapterAdmission === undefined ? {} : { adapter_admission: adapterAdmission }),
			request_validation: requestValidation,
			capability: emptyCapability(),
			outcome: failure(
				"NOT_EXECUTED",
				"BLOCK",
				"INVALID_CONTRACT",
				`Executor ${resolvedDescriptor.kind} returned malformed capability evidence.`,
			),
		});
	}
	const capability = evaluateCapabilityRequirements(requirements, evidence);
	if (!capability.admitted) {
		return finish({
			descriptor: resolvedDescriptor,
			registered: true,
			...(adapterAdmission === undefined ? {} : { adapter_admission: adapterAdmission }),
			request_validation: requestValidation,
			capability,
			outcome:
				capability.blocking_outcome ??
				failure(
					"NOT_EXECUTED",
					"BLOCK",
					"CAPABILITY_MISSING",
					`Executor ${resolvedDescriptor.kind} did not admit the required capabilities.`,
				),
		});
	}

	let receipt: ExecutionReceipt;
	try {
		executeInvoked = true;
		receipt = await port.execute(request);
	} catch (error) {
		return finish({
			descriptor: resolvedDescriptor,
			registered: true,
			...(adapterAdmission === undefined ? {} : { adapter_admission: adapterAdmission }),
			request_validation: requestValidation,
			capability,
			outcome: failure(
				"FAIL",
				"RETRY",
				"EXECUTION_FAILED",
				`Executor ${resolvedDescriptor.kind} failed before producing a receipt: ${error instanceof Error ? error.message : String(error)}`,
				"FAILED",
			),
		});
	}
	const validation = validateExecutionReceipt(request, receipt, options);
	const identityErrors = identityMismatch(expectedIdentity ?? port.identity, receipt.executor);
	if (identityErrors.length > 0 || !validation.valid) {
		return finish(
			invalidReceiptResult(
				resolvedDescriptor,
				requestValidation,
				capability,
				receipt,
				validation,
				identityErrors,
				adapterAdmission,
			),
		);
	}
	return finish({
		descriptor: resolvedDescriptor,
		registered: true,
		...(adapterAdmission === undefined ? {} : { adapter_admission: adapterAdmission }),
		request_validation: requestValidation,
		receipt,
		receipt_validation: validation,
		capability,
		outcome: validation.outcome,
	});
}
