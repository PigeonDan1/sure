export const EXECUTION_LIFECYCLES = [
	"NOT_STARTED",
	"QUEUED",
	"RUNNING",
	"SUCCEEDED",
	"FAILED",
	"PARTIAL",
	"CANCELLED",
] as const;
export type ExecutionLifecycle = (typeof EXECUTION_LIFECYCLES)[number];

export const VALIDATOR_VERDICTS = ["PASS", "FAIL", "NOT_EXECUTED"] as const;
export type ValidatorVerdict = (typeof VALIDATOR_VERDICTS)[number];

export const WORKFLOW_DISPOSITIONS = ["ADVANCE", "RETRY", "BLOCK", "TERMINATE", "WAIT"] as const;
export type WorkflowDisposition = (typeof WORKFLOW_DISPOSITIONS)[number];

export const PUBLIC_OUTCOMES = ["PASS", "FAIL", "BLOCKED", "RETRY", "NOT_EXECUTED"] as const;
export type PublicOutcome = (typeof PUBLIC_OUTCOMES)[number];

export const REASON_CODES = [
	"VALIDATION_PASSED",
	"VALIDATION_PENDING",
	"VALIDATION_FAILED",
	"MISSING_INPUT",
	"RETRY_EXHAUSTED",
	"AWAITING_EXECUTION",
	"AWAITING_HUMAN",
	"CAPABILITY_MISSING",
	"UNKNOWN_CAPABILITY",
	"POLICY_DENIED",
	"EXECUTION_FAILED",
	"EXECUTION_PARTIAL",
	"EXECUTION_CANCELLED",
	"PATH_OUT_OF_SCOPE",
	"SYMLINK_ESCAPE",
	"READ_ONLY_REFERENCE",
	"DIGEST_MISMATCH",
	"INVALID_CONTRACT",
	"UPGRADE_REQUIRED",
] as const;
export type ReasonCode = (typeof REASON_CODES)[number];

export interface OutcomeDiagnostic {
	code: string;
	message: string;
	repair?: string;
}

export interface CoreOutcome {
	validator_verdict: ValidatorVerdict;
	workflow_disposition: WorkflowDisposition;
	outcome: PublicOutcome;
	reason_code: ReasonCode;
	retryable: boolean;
	execution_lifecycle?: ExecutionLifecycle;
	diagnostics: OutcomeDiagnostic[];
	evidence: string[];
}

export interface CreateOutcomeInput {
	validatorVerdict: ValidatorVerdict;
	workflowDisposition: WorkflowDisposition;
	reasonCode: ReasonCode;
	executionLifecycle?: ExecutionLifecycle;
	diagnostics?: OutcomeDiagnostic[];
	evidence?: string[];
}

export function derivePublicOutcome(
	validatorVerdict: ValidatorVerdict,
	workflowDisposition: WorkflowDisposition,
): PublicOutcome {
	if (validatorVerdict === "NOT_EXECUTED" || workflowDisposition === "WAIT") return "NOT_EXECUTED";
	if (workflowDisposition === "RETRY") return "RETRY";
	if (workflowDisposition === "BLOCK") return "BLOCKED";
	if (validatorVerdict === "FAIL") return "FAIL";
	return "PASS";
}

export function createOutcome(input: CreateOutcomeInput): CoreOutcome {
	const outcome = derivePublicOutcome(input.validatorVerdict, input.workflowDisposition);
	const executionCannotPass =
		input.executionLifecycle !== undefined && input.executionLifecycle !== "SUCCEEDED" && outcome === "PASS";
	if (executionCannotPass) {
		throw new Error(`Execution lifecycle ${input.executionLifecycle} cannot produce PASS`);
	}
	if (outcome === "PASS" && !["ADVANCE", "TERMINATE"].includes(input.workflowDisposition)) {
		throw new Error(`Workflow disposition ${input.workflowDisposition} cannot produce PASS`);
	}
	if (input.reasonCode === "CAPABILITY_MISSING" && input.validatorVerdict !== "NOT_EXECUTED") {
		throw new Error("CAPABILITY_MISSING must use validator verdict NOT_EXECUTED");
	}
	return {
		validator_verdict: input.validatorVerdict,
		workflow_disposition: input.workflowDisposition,
		outcome,
		reason_code: input.reasonCode,
		retryable: input.workflowDisposition === "RETRY",
		...(input.executionLifecycle === undefined ? {} : { execution_lifecycle: input.executionLifecycle }),
		diagnostics: input.diagnostics ?? [],
		evidence: input.evidence ?? [],
	};
}

export function outcomeFromExecutionLifecycle(lifecycle: ExecutionLifecycle): CoreOutcome {
	switch (lifecycle) {
		case "SUCCEEDED":
			return createOutcome({
				validatorVerdict: "NOT_EXECUTED",
				workflowDisposition: "WAIT",
				reasonCode: "VALIDATION_PENDING",
				executionLifecycle: lifecycle,
			});
		case "FAILED":
			return createOutcome({
				validatorVerdict: "FAIL",
				workflowDisposition: "RETRY",
				reasonCode: "EXECUTION_FAILED",
				executionLifecycle: lifecycle,
			});
		case "PARTIAL":
			return createOutcome({
				validatorVerdict: "FAIL",
				workflowDisposition: "BLOCK",
				reasonCode: "EXECUTION_PARTIAL",
				executionLifecycle: lifecycle,
			});
		case "CANCELLED":
			return createOutcome({
				validatorVerdict: "NOT_EXECUTED",
				workflowDisposition: "BLOCK",
				reasonCode: "EXECUTION_CANCELLED",
				executionLifecycle: lifecycle,
			});
		case "NOT_STARTED":
		case "QUEUED":
		case "RUNNING":
			return createOutcome({
				validatorVerdict: "NOT_EXECUTED",
				workflowDisposition: "WAIT",
				reasonCode: "AWAITING_EXECUTION",
				executionLifecycle: lifecycle,
			});
	}
}
