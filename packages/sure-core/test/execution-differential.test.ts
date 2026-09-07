import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { type ExecutionLifecycle, outcomeFromExecutionLifecycle, projectExecutionEvidence } from "../src/index.ts";

interface Projection {
	evidence_verdict: "PASS" | "FAIL" | "NOT_EXECUTED";
	evidence_reason_code: string;
	receipt_lifecycle?: ExecutionLifecycle;
	receipt_valid?: boolean;
	capability_admitted?: boolean;
	request_receipt: "present" | "absent";
	assurance_profile: "cooperative" | "hook_enforced";
}

interface DifferentialCase {
	id: string;
	class: string;
	canonical: {
		receipt_lifecycle: ExecutionLifecycle;
		validator_verdict: "PASS" | "FAIL" | "NOT_EXECUTED";
		workflow_disposition: "ADVANCE" | "RETRY" | "BLOCK" | "TERMINATE" | "WAIT";
		outcome: "PASS" | "FAIL" | "BLOCKED" | "RETRY" | "NOT_EXECUTED";
		reason_code: string;
	};
	surectl: Projection;
	pi: Projection;
	relation: string;
}

interface DifferentialFixture {
	schema: string;
	version: number;
	rules: {
		execution_success_requires_independent_validation: boolean;
		pi_facade_emits_request_receipt: boolean;
		capability_missing_must_not_pass: boolean;
	};
	cases: DifferentialCase[];
}

const FIXTURE = JSON.parse(
	readFileSync(
		new URL("../../../sure/canonical/fixtures/execution-differential-traces.json", import.meta.url),
		"utf8",
	),
) as DifferentialFixture;

describe("execution differential traces", () => {
	it("defines one canonical outcome and an explicit host assurance projection", () => {
		expect(FIXTURE.schema).toBe("sure.execution.differential_traces.v1");
		expect(FIXTURE.version).toBe(1);
		expect(FIXTURE.rules.execution_success_requires_independent_validation).toBe(true);
		expect(FIXTURE.rules.pi_facade_emits_request_receipt).toBe(false);
		expect(FIXTURE.cases).toHaveLength(5);

		for (const item of FIXTURE.cases) {
			if (item.class !== "structural_error" && item.class !== "capability_absent") {
				const canonical = outcomeFromExecutionLifecycle(item.canonical.receipt_lifecycle);
				expect(canonical.validator_verdict, item.id).toBe(item.canonical.validator_verdict);
				expect(canonical.workflow_disposition, item.id).toBe(item.canonical.workflow_disposition);
				expect(canonical.outcome, item.id).toBe(item.canonical.outcome);
				expect(canonical.reason_code, item.id).toBe(item.canonical.reason_code);
			} else {
				expect(item.canonical.outcome, item.id).toBe("NOT_EXECUTED");
				expect(item.canonical.validator_verdict, item.id).toBe("NOT_EXECUTED");
				expect(item.canonical.workflow_disposition, item.id).toBe("BLOCK");
			}

			const portable = projectExecutionEvidence({
				lifecycle: item.surectl.receipt_lifecycle,
				receipt_valid: item.surectl.receipt_valid === true,
				capability_admitted: item.surectl.capability_admitted === true,
				missing_output: item.id === "invalid_contract" ? false : undefined,
				outcome_reason_code: item.canonical.reason_code,
			});
			expect(portable, item.id).toEqual({
				verdict: item.surectl.evidence_verdict,
				reason_code: item.surectl.evidence_reason_code,
			});

			if (item.canonical.receipt_lifecycle !== "SUCCEEDED") {
				expect(item.surectl.evidence_verdict, item.id).not.toBe("PASS");
			}
			if (item.class === "capability_absent") {
				expect(item.surectl.evidence_verdict, item.id).toBe("NOT_EXECUTED");
				expect(item.pi.evidence_verdict, item.id).toBe("NOT_EXECUTED");
			}
			expect(item.pi.request_receipt).toBe("absent");
			expect(item.surectl.assurance_profile).toBe("cooperative");
			expect(item.pi.assurance_profile).toBe("hook_enforced");
		}
	});

	it("keeps executor success below validator PASS", () => {
		const item = FIXTURE.cases.find((candidate) => candidate.id === "execution_succeeded_validation_pending");
		if (!item) throw new Error("success trace is missing");
		const outcome = outcomeFromExecutionLifecycle(item.canonical.receipt_lifecycle);
		expect(outcome).toMatchObject({
			outcome: "NOT_EXECUTED",
			validator_verdict: "NOT_EXECUTED",
			workflow_disposition: "WAIT",
			reason_code: "VALIDATION_PENDING",
		});
		expect(item.surectl.evidence_verdict).toBe("PASS");
		expect(item.surectl.evidence_reason_code).toBe("EXECUTION_SUCCEEDED");
	});
});
