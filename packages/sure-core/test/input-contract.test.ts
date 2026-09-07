import { describe, expect, it } from "vitest";
import {
	type ExecutionInputContract,
	ExecutionInputContractError,
	executionInputContractDigest,
	selectExecutionInputSelector,
	validateExecutionInputContract,
} from "../src/execution/input-contract.ts";

const contract: ExecutionInputContract = {
	schema: "sure.execution_input_contract.v1",
	context_artifact: "trans_input_resolved.json",
	selection: "exactly_one",
	selectors: [
		{
			selector_id: "python-none",
			match: { source_kind: "python", package_profile: "none" },
			inputs: [
				{ input_id: "resolved", locator_kind: "run_artifact", path: "trans_input_resolved.json", required: true },
				{ input_id: "lock", locator_kind: "resolved_input_field", path: "lockfile", required: true },
			],
		},
		{
			selector_id: "python-registry",
			match: { source_kind: "python", package_profile: "docker-registry" },
			inputs: [
				{ input_id: "resolved", locator_kind: "run_artifact", path: "trans_input_resolved.json", required: true },
				{ input_id: "adapter", locator_kind: "run_artifact", path: "adapter_manifest.json", required: true },
			],
		},
	],
};

describe("execution input contracts", () => {
	it("validates and selects one exact runtime branch", () => {
		expect(validateExecutionInputContract(contract)).toEqual({ valid: true, errors: [] });
		expect(
			selectExecutionInputSelector(contract, { source_kind: "python", package_profile: "none" }).selector_id,
		).toBe("python-none");
		expect(
			selectExecutionInputSelector(contract, { source_kind: "python", package_profile: "docker-registry" }).inputs,
		).toHaveLength(2);
	});

	it("rejects an unsupported profile instead of falling back to another branch", () => {
		expect(() =>
			selectExecutionInputSelector(contract, { source_kind: "docker", package_profile: "docker-registry" }),
		).toThrow(/has no selector/);
		try {
			selectExecutionInputSelector(contract, { source_kind: "docker", package_profile: "docker-registry" });
		} catch (error) {
			expect(error).toBeInstanceOf(ExecutionInputContractError);
			expect((error as ExecutionInputContractError).code).toBe("NO_MATCH");
		}
	});

	it("rejects overlapping selectors and path escapes", () => {
		const overlapping = {
			...contract,
			selectors: [
				...contract.selectors,
				{
					selector_id: "broad",
					match: { source_kind: "python" },
					inputs: contract.selectors[0].inputs,
				},
			],
		};
		expect(validateExecutionInputContract(overlapping).errors.join(" ")).toMatch(/overlap/);
		expect(
			validateExecutionInputContract({
				...contract,
				selectors: [
					{
						...contract.selectors[0],
						inputs: [
							{
								...contract.selectors[0].inputs[0],
								path: "../outside.json",
							},
						],
					},
				],
			}).valid,
		).toBe(false);
	});

	it("has a stable canonical digest", () => {
		expect(executionInputContractDigest(contract)).toBe(executionInputContractDigest(structuredClone(contract)));
	});
});
