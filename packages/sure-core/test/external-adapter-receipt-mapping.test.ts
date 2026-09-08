import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

interface MappingFixture {
	schema: string;
	version: number;
	request_profile: {
		execution_surface: string;
		executor_kind: string;
		capability_ids: string[];
	};
	cases: Array<{
		id: string;
		result: { exit_code: number | null; timed_out: boolean };
		expected: { lifecycle: string; contract_valid: boolean; diagnostic_codes: string[] };
	}>;
}

const fixture = JSON.parse(
	readFileSync(
		new URL("../../../sure/canonical/fixtures/external-adapter-receipt-mapping.v1.json", import.meta.url),
		"utf8",
	),
) as MappingFixture;

describe("external adapter receipt mapping fixture", () => {
	it("keeps the normalized VC outcomes host-neutral", () => {
		expect(fixture.schema).toBe("sure.external_adapter.receipt_mapping.v1");
		expect(fixture.version).toBe(1);
		expect(fixture.request_profile.execution_surface).toBe("vc");
		expect(["remote", "trusted"]).toContain(fixture.request_profile.executor_kind);
		expect(fixture.request_profile.capability_ids).toEqual(expect.arrayContaining(["sure.execution.vc"]));
	});

	it("never classifies timeout, capability absence, or boundary escape as success", () => {
		for (const current of fixture.cases) {
			if (
				current.result.timed_out ||
				current.id === "capability-missing" ||
				current.id === "output-boundary-escape"
			) {
				expect(current.expected.contract_valid && current.expected.lifecycle === "SUCCEEDED").toBe(false);
			}
		}
		expect(fixture.cases.map((current) => current.id)).toEqual([
			"success",
			"executor-failure",
			"timeout-cancel-unconfirmed",
			"capability-missing",
			"capability-missing-before-submit",
			"output-boundary-escape",
		]);
	});
});
