import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
	dispatchExecutor,
	type ExecutionLifecycle,
	type ExecutionReceipt,
	ExecutorRegistry,
	outcomeFromExecutionLifecycle,
	projectExecutionEvidence,
} from "../src/index.ts";
import { externalPort, FIXTURE_DIGEST_B, FIXTURE_NOW, vcAdapterFixture } from "./external-adapter-fixture.ts";

interface TraceProjection {
	evidence_verdict: "PASS" | "FAIL" | "NOT_EXECUTED";
	evidence_reason_code: string;
	receipt_lifecycle: ExecutionLifecycle;
	receipt_valid: boolean;
	capability_admitted: boolean;
	request_receipt: "present" | "absent";
	assurance_profile: "cooperative" | "hook_enforced";
}

interface TraceCase {
	id: string;
	class: string;
	canonical: {
		receipt_lifecycle: ExecutionLifecycle;
		validator_verdict: "PASS" | "FAIL" | "NOT_EXECUTED";
		workflow_disposition: "ADVANCE" | "RETRY" | "BLOCK" | "TERMINATE" | "WAIT";
		outcome: "PASS" | "FAIL" | "BLOCKED" | "RETRY" | "NOT_EXECUTED";
		reason_code: string;
	};
	surectl: TraceProjection;
	python: { evidence_verdict: "PASS" | "FAIL" | "NOT_EXECUTED"; evidence_reason_code: string };
	pi: Omit<TraceProjection, "receipt_lifecycle" | "receipt_valid" | "capability_admitted">;
	relation: string;
}

const FIXTURE = JSON.parse(
	readFileSync(
		new URL("../../../sure/canonical/fixtures/external-adapter-differential-traces.v1.json", import.meta.url),
		"utf8",
	),
) as {
	schema: string;
	version: number;
	rules: Record<string, boolean>;
	cases: TraceCase[];
};

function register(fixture: ReturnType<typeof vcAdapterFixture>): ExecutorRegistry {
	const registry = new ExecutorRegistry();
	registry.registerExternal(fixture.port, fixture.binding);
	return registry;
}

function canonicalFromResult(result: Awaited<ReturnType<typeof dispatchExecutor>>, item: TraceCase): void {
	const lifecycle = result.receipt?.lifecycle ?? "NOT_STARTED";
	expect(lifecycle, item.id).toBe(item.canonical.receipt_lifecycle);
	expect(result.outcome, item.id).toMatchObject({
		validator_verdict: item.canonical.validator_verdict,
		workflow_disposition: item.canonical.workflow_disposition,
		outcome: item.canonical.outcome,
		reason_code: item.canonical.reason_code,
	});
}

describe("external adapter differential traces", () => {
	it("defines the five host-neutral admission and receipt cases", () => {
		expect(FIXTURE.schema).toBe("sure.execution.external_adapter_differential_traces.v1");
		expect(FIXTURE.version).toBe(1);
		expect(FIXTURE.rules.registration_is_required).toBe(true);
		expect(FIXTURE.rules.policy_drift_blocks_before_probe).toBe(true);
		expect(FIXTURE.rules.receipt_tamper_never_advances).toBe(true);
		expect(FIXTURE.rules.success_requires_independent_validation).toBe(true);
		expect(FIXTURE.cases).toHaveLength(5);
	});

	it("projects Core dispatch without allowing a receipt to advance workflow state", async () => {
		const cases = new Map(FIXTURE.cases.map((item) => [item.id, item]));

		const admittedDefaults = externalPort();
		const admitted = vcAdapterFixture({
			execute: async (request) => {
				const receipt = (await admittedDefaults.execute(request)) as ExecutionReceipt;
				return { ...receipt, lifecycle: "FAILED", exit_code: 7 };
			},
		});
		const admittedResult = await dispatchExecutor(register(admitted), admitted.request, { now: () => FIXTURE_NOW });
		const admittedCase = cases.get("admitted_executor_failure");
		if (!admittedCase) throw new Error("missing admitted trace");
		canonicalFromResult(admittedResult, admittedCase);
		expect(admittedResult.receipt_validation?.valid).toBe(true);
		expect(
			projectExecutionEvidence({
				lifecycle: admittedResult.receipt?.lifecycle,
				receipt_valid: admittedResult.receipt_validation?.valid === true,
				capability_admitted: admittedResult.capability.admitted,
				outcome_reason_code: admittedResult.outcome.reason_code,
			}),
		).toEqual({
			verdict: admittedCase.surectl.evidence_verdict,
			reason_code: admittedCase.surectl.evidence_reason_code,
		});

		const missingCase = cases.get("missing_registration");
		if (!missingCase) throw new Error("missing registration trace");
		const missing = vcAdapterFixture();
		const missingResult = await dispatchExecutor(new ExecutorRegistry(), missing.request, { now: () => FIXTURE_NOW });
		canonicalFromResult(missingResult, missingCase);
		expect(missingResult.receipt).toBeUndefined();
		expect(
			projectExecutionEvidence({
				lifecycle: missingResult.receipt?.lifecycle,
				receipt_valid: false,
				capability_admitted: missingResult.capability.admitted,
				outcome_reason_code: missingResult.outcome.reason_code,
			}),
		).toEqual({
			verdict: missingCase.surectl.evidence_verdict,
			reason_code: missingCase.surectl.evidence_reason_code,
		});

		const policyCase = cases.get("policy_drift");
		if (!policyCase) throw new Error("missing policy drift trace");
		const policy = vcAdapterFixture();
		const policyRequest = structuredClone(policy.request);
		policyRequest.runtime_requirements.vc_project = "unapproved-project";
		const policyResult = await dispatchExecutor(register(policy), policyRequest, { now: () => FIXTURE_NOW });
		canonicalFromResult(policyResult, policyCase);
		expect(policyResult.receipt).toBeUndefined();
		expect(policyResult.adapter_admission?.valid).toBe(false);
		expect(policyResult.outcome.reason_code).toBe("INVALID_CONTRACT");

		const tamperDefaults = externalPort();
		const tamper = vcAdapterFixture({
			execute: async (request) => {
				const receipt = (await tamperDefaults.execute(request)) as ExecutionReceipt;
				return { ...receipt, adapter_manifest_digest: FIXTURE_DIGEST_B };
			},
		});
		const tamperCase = cases.get("receipt_tamper");
		if (!tamperCase) throw new Error("missing receipt tamper trace");
		const tamperResult = await dispatchExecutor(register(tamper), tamper.request, { now: () => FIXTURE_NOW });
		canonicalFromResult(tamperResult, tamperCase);
		expect(tamperResult.receipt?.lifecycle).toBe("SUCCEEDED");
		expect(tamperResult.receipt_validation?.valid).toBe(false);
		expect(tamperResult.outcome.reason_code).toBe("INVALID_CONTRACT");
		expect(
			projectExecutionEvidence({
				lifecycle: tamperResult.receipt?.lifecycle,
				receipt_valid: false,
				capability_admitted: tamperResult.capability.admitted,
				outcome_reason_code: tamperResult.outcome.reason_code,
			}),
		).toEqual({ verdict: tamperCase.surectl.evidence_verdict, reason_code: tamperCase.surectl.evidence_reason_code });

		const success = vcAdapterFixture();
		const successCase = cases.get("success_waits_for_validation");
		if (!successCase) throw new Error("missing success trace");
		const successResult = await dispatchExecutor(register(success), success.request, { now: () => FIXTURE_NOW });
		canonicalFromResult(successResult, successCase);
		expect(successResult.outcome).toEqual(outcomeFromExecutionLifecycle("SUCCEEDED"));
		expect(successResult.receipt_validation?.valid).toBe(true);
		expect(
			projectExecutionEvidence({
				lifecycle: successResult.receipt?.lifecycle,
				receipt_valid: successResult.receipt_validation?.valid === true,
				capability_admitted: successResult.capability.admitted,
				outcome_reason_code: successResult.outcome.reason_code,
			}),
		).toEqual({
			verdict: successCase.surectl.evidence_verdict,
			reason_code: successCase.surectl.evidence_reason_code,
		});
	});
});
