import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import { evaluateCapabilityRequirements } from "../contracts/capability.ts";
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
import { validateExecutionReceipt, validateExecutionRequest } from "./receipt.ts";
import type { ExecutorDescriptor, ExecutorRegistry } from "./registry.ts";
import type { ExecutionBoundaryOptions, ExecutionReceiptValidation, ExecutionRequestValidation } from "./types.ts";

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
	request_validation: ExecutionRequestValidation;
	receipt?: ExecutionReceipt;
	receipt_validation?: ExecutionReceiptValidation;
	capability: CapabilityEvaluation;
	outcome: CoreOutcome;
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

function invalidReceiptResult(
	requestValidation: ExecutionRequestValidation,
	capability: CapabilityEvaluation,
	receipt: ExecutionReceipt,
	validation: ExecutionReceiptValidation,
	identityErrors: readonly string[],
): ExecutorDispatchResult {
	const diagnostics = [...identityErrors, ...validation.errors];
	const outcome =
		identityErrors.length > 0
			? failure("NOT_EXECUTED", "BLOCK", "INVALID_CONTRACT", diagnostics.join("; "))
			: validation.outcome;
	return {
		registered: true,
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
): ExecutorDispatchResult {
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
	const requestValidation = validateExecutionRequest(request, options);
	if (!requestValidation.valid) {
		return {
			registered: false,
			request_validation: requestValidation,
			capability: emptyCapability(),
			outcome: requestValidation.outcome,
		};
	}
	const routeValidation = parseExecutionAdapterRoute(request.runtime_requirements);
	if (!routeValidation.valid) {
		return {
			registered: false,
			request_validation: {
				...requestValidation,
				valid: false,
				errors: routeValidation.errors,
				outcome: failure("NOT_EXECUTED", "BLOCK", "INVALID_CONTRACT", routeValidation.errors.join("; ")),
			},
			capability: emptyCapability(),
			outcome: failure("NOT_EXECUTED", "BLOCK", "INVALID_CONTRACT", routeValidation.errors.join("; ")),
		};
	}
	const kind = request.runtime_requirements?.executor_kind;
	if (typeof kind !== "string") {
		return {
			registered: false,
			request_validation: requestValidation,
			capability: emptyCapability(),
			outcome: failure(
				"NOT_EXECUTED",
				"BLOCK",
				"INVALID_CONTRACT",
				"execution request must declare runtime_requirements.executor_kind before adapter dispatch",
			),
		};
	}
	// The runtime field is intentionally generic in v1. Resolve through the
	// registry only after checking that it names one of the declared wire kinds.
	const wireKinds = ["local", "python", "docker", "remote", "trusted"] as const;
	const resolvedDescriptor = wireKinds.includes(kind as (typeof wireKinds)[number])
		? registry.descriptor(kind as ExecutorKind)
		: undefined;
	if (resolvedDescriptor === undefined) {
		return {
			registered: false,
			request_validation: requestValidation,
			capability: emptyCapability(),
			outcome: failure("NOT_EXECUTED", "BLOCK", "INVALID_CONTRACT", `unknown executor kind: ${kind}`),
		};
	}
	const requirements = effectiveRequirements(request, resolvedDescriptor);
	const port = registry.resolve(resolvedDescriptor.kind);
	if (port === undefined) {
		return probeFailure(
			resolvedDescriptor,
			requestValidation,
			requirements,
			`Executor ${resolvedDescriptor.kind} requires a registered adapter; no local fallback is permitted.`,
			false,
			observedAt,
		);
	}

	let evidence: readonly CapabilityEvidence[];
	try {
		evidence = port.probe(requirements);
	} catch (error) {
		return probeFailure(
			resolvedDescriptor,
			requestValidation,
			requirements,
			`Executor ${resolvedDescriptor.kind} capability probe failed: ${error instanceof Error ? error.message : String(error)}`,
			true,
			observedAt,
		);
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
		return {
			descriptor: resolvedDescriptor,
			registered: true,
			request_validation: requestValidation,
			capability: emptyCapability(),
			outcome: failure(
				"NOT_EXECUTED",
				"BLOCK",
				"INVALID_CONTRACT",
				`Executor ${resolvedDescriptor.kind} returned malformed capability evidence.`,
			),
		};
	}
	const capability = evaluateCapabilityRequirements(requirements, evidence);
	if (!capability.admitted) {
		return {
			descriptor: resolvedDescriptor,
			registered: true,
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
		};
	}

	let receipt: ExecutionReceipt;
	try {
		receipt = await port.execute(request);
	} catch (error) {
		return {
			descriptor: resolvedDescriptor,
			registered: true,
			request_validation: requestValidation,
			capability,
			outcome: failure(
				"FAIL",
				"RETRY",
				"EXECUTION_FAILED",
				`Executor ${resolvedDescriptor.kind} failed before producing a receipt: ${error instanceof Error ? error.message : String(error)}`,
				"FAILED",
			),
		};
	}
	const validation = validateExecutionReceipt(request, receipt, options);
	const identityErrors = identityMismatch(port.identity, receipt.executor);
	if (identityErrors.length > 0 || !validation.valid) {
		return invalidReceiptResult(requestValidation, capability, receipt, validation, identityErrors);
	}
	return {
		descriptor: resolvedDescriptor,
		registered: true,
		request_validation: requestValidation,
		receipt,
		receipt_validation: validation,
		capability,
		outcome: validation.outcome,
	};
}
