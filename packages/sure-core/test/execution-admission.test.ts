import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { validateJsonSchema } from "../src/contracts/schema.ts";
import {
	createExecutionAdmissionTrace,
	validateExecutionAdmissionBinding,
	validateExecutionAdmissionTrace,
} from "../src/execution/admission.ts";
import { createOutcome } from "../src/workflow/outcome.ts";
import { FIXTURE_NOW, vcAdapterFixture } from "./external-adapter-fixture.ts";

const SCHEMA = JSON.parse(
	readFileSync(new URL("../../../sure/core/contracts/execution_admission.schema.json", import.meta.url), "utf8"),
) as Record<string, unknown>;

describe("execution admission trace", () => {
	it("records preflight provenance without creating an executor receipt", () => {
		const fixture = vcAdapterFixture();
		const trace = createExecutionAdmissionTrace(
			fixture.request,
			FIXTURE_NOW,
			createOutcome({
				validatorVerdict: "NOT_EXECUTED",
				workflowDisposition: "WAIT",
				reasonCode: "VALIDATION_PENDING",
			}),
			{
				probe_invoked: true,
				execute_invoked: true,
				receipt: undefined,
				receipt_valid: false,
			},
		);

		expect(trace).toMatchObject({
			schema: "sure.execution_admission.v1",
			status: "ADMITTED",
			reason_code: "VALIDATION_PENDING",
			request_id: fixture.request.request_id,
			execution_surface: "vc",
			probe_invoked: true,
			execute_invoked: true,
			receipt_present: false,
			receipt_valid: false,
		});
		expect(validateExecutionAdmissionTrace(trace)).toEqual([]);
		expect(validateExecutionAdmissionBinding(fixture.request, trace)).toEqual([]);
		expect(validateJsonSchema(SCHEMA, trace).ok).toBe(true);

		const direct = createExecutionAdmissionTrace(
			fixture.request,
			FIXTURE_NOW,
			createOutcome({
				validatorVerdict: "NOT_EXECUTED",
				workflowDisposition: "WAIT",
				reasonCode: "VALIDATION_PENDING",
			}),
			{ probe_invoked: false, execute_invoked: true, receipt_valid: false },
		);
		expect(direct.status).toBe("ADMITTED");
	});

	it("distinguishes missing capability from a rejected contract", () => {
		const fixture = vcAdapterFixture();
		const missing = createExecutionAdmissionTrace(
			fixture.request,
			FIXTURE_NOW,
			createOutcome({
				validatorVerdict: "NOT_EXECUTED",
				workflowDisposition: "BLOCK",
				reasonCode: "CAPABILITY_MISSING",
			}),
			{ probe_invoked: false, execute_invoked: false },
		);
		const rejected = createExecutionAdmissionTrace(
			fixture.request,
			FIXTURE_NOW,
			createOutcome({
				validatorVerdict: "NOT_EXECUTED",
				workflowDisposition: "BLOCK",
				reasonCode: "INVALID_CONTRACT",
			}),
			{ probe_invoked: false, execute_invoked: false },
		);

		expect(missing.status).toBe("CAPABILITY_MISSING");
		expect(rejected.status).toBe("REJECTED");
		expect(validateExecutionAdmissionTrace(missing)).toEqual([]);
		expect(validateExecutionAdmissionTrace(rejected)).toEqual([]);
	});

	it("rejects a forged receipt-valid flag and a rebinding digest", () => {
		const fixture = vcAdapterFixture();
		const trace = createExecutionAdmissionTrace(
			fixture.request,
			FIXTURE_NOW,
			createOutcome({
				validatorVerdict: "NOT_EXECUTED",
				workflowDisposition: "WAIT",
				reasonCode: "VALIDATION_PENDING",
			}),
			{ probe_invoked: true, execute_invoked: true, receipt_valid: true },
		);
		const forged = { ...trace, receipt_present: false };
		expect(validateExecutionAdmissionTrace(forged)).toContain("admission.receipt_valid requires receipt_present");
		expect(validateJsonSchema(SCHEMA, forged).ok).toBe(false);
		expect(
			validateExecutionAdmissionTrace({
				...trace,
				status: "ADMITTED",
				probe_invoked: false,
				execute_invoked: false,
			}),
		).toContain("ADMITTED admission requires probe_invoked or execute_invoked");
		expect(
			validateJsonSchema(SCHEMA, { ...trace, status: "ADMITTED", probe_invoked: false, execute_invoked: false }).ok,
		).toBe(false);
		expect(validateExecutionAdmissionTrace({ ...trace, status: "REJECTED", execute_invoked: true })).toContain(
			"REJECTED admission cannot invoke execute",
		);
		expect(validateJsonSchema(SCHEMA, { ...trace, status: "REJECTED", execute_invoked: true }).ok).toBe(false);
		expect(
			validateExecutionAdmissionBinding(fixture.request, { ...trace, request_digest: `sha256:${"f".repeat(64)}` }),
		).toContain("admission.request_digest does not match request");
		expect(validateExecutionAdmissionBinding(fixture.request, { ...trace, request_id: undefined })).toContain(
			"admission.request_id is missing from request binding",
		);
		expect(
			validateExecutionAdmissionBinding(fixture.request, { ...trace, requested_executor_kind: undefined }),
		).toContain("admission.requested_executor_kind is missing from request binding");
		expect(validateExecutionAdmissionBinding(fixture.request, { ...trace, execution_surface: undefined })).toContain(
			"admission.execution_surface is missing from request binding",
		);
		expect(
			validateExecutionAdmissionBinding(fixture.request, { ...trace, adapter_manifest_digest: undefined }),
		).toContain("admission.adapter_manifest_digest is missing from request binding");
	});
});
