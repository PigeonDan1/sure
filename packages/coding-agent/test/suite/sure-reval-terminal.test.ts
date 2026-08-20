import { describe, expect, it } from "vitest";
import { incompleteReportError } from "../../../../sure/skills/sure_reval/hooks/index.ts";

describe("sure_reval incomplete terminal contract", () => {
	const report = {
		schema: "sure.reval.run_report.v1",
		status: "incomplete",
		error_code: "INPUT_EVIDENCE_MISSING",
		evaluation_only: true,
		inference_executed: false,
		old_evaluation_reused: false,
		append_attempted: false,
		source_identity: { protocol_id: "standard_system" },
	};

	it("accepts an explicit incomplete report that proves no side effects", () => {
		expect(incompleteReportError(report, "incomplete")).toBeUndefined();
	});

	it("rejects an incomplete report that claims an append", () => {
		expect(incompleteReportError({ ...report, append_attempted: true }, "incomplete")).toContain(
			"append_attempted=false",
		);
	});
});
