import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { outcomeFromExecutionLifecycle } from "../../sure-core/src/index.ts";
import { projectSurectlExecutionEvidence } from "../src/executor.ts";

interface TraceCase {
	id: string;
	canonical: {
		receipt_lifecycle: "NOT_STARTED" | "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "PARTIAL" | "CANCELLED";
		validator_verdict: "PASS" | "FAIL" | "NOT_EXECUTED";
		workflow_disposition: "ADVANCE" | "RETRY" | "BLOCK" | "TERMINATE" | "WAIT";
		outcome: "PASS" | "FAIL" | "BLOCKED" | "RETRY" | "NOT_EXECUTED";
		reason_code: string;
	};
	surectl: {
		evidence_verdict: "PASS" | "FAIL" | "NOT_EXECUTED";
		evidence_reason_code: string;
		receipt_lifecycle: "NOT_STARTED" | "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "PARTIAL" | "CANCELLED";
		receipt_valid: boolean;
		capability_admitted: boolean;
	};
}

const FIXTURE = JSON.parse(
	readFileSync(
		new URL("../../../sure/canonical/fixtures/external-adapter-differential-traces.v1.json", import.meta.url),
		"utf8",
	),
) as { schema: string; cases: TraceCase[] };

describe("surectl external execution projection", () => {
	it("delegates all external trace outcomes to Core without local state transitions", () => {
		expect(FIXTURE.schema).toBe("sure.execution.external_adapter_differential_traces.v1");
		for (const item of FIXTURE.cases) {
			const projection = projectSurectlExecutionEvidence({
				lifecycle: item.surectl.receipt_lifecycle,
				receipt_valid: item.surectl.receipt_valid,
				capability_admitted: item.surectl.capability_admitted,
				outcome_reason_code: item.canonical.reason_code,
			});
			expect(projection, item.id).toEqual({
				verdict: item.surectl.evidence_verdict,
				reason_code: item.surectl.evidence_reason_code,
			});

			if (item.id !== "receipt_tamper") {
				const outcome =
					item.id === "missing_registration" || item.id === "policy_drift"
						? undefined
						: outcomeFromExecutionLifecycle(item.canonical.receipt_lifecycle);
				if (outcome !== undefined) {
					expect(outcome).toMatchObject({
						validator_verdict: item.canonical.validator_verdict,
						workflow_disposition: item.canonical.workflow_disposition,
						outcome: item.canonical.outcome,
						reason_code: item.canonical.reason_code,
					});
				}
			}
		}
	});
});
