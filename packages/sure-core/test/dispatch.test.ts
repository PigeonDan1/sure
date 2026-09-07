import { describe, expect, it } from "vitest";
import { type ExecutionDispatchCase, ExecutionDispatchError, selectExecutionDispatch } from "../src/index.ts";

const contract = (match: Record<string, string>) => ({
	schema: "sure.execution_input_contract.v1" as const,
	context_artifact: "context.json",
	selection: "exactly_one" as const,
	selectors: [
		{
			selector_id: Object.values(match).join("-") || "case",
			match,
			inputs: [{ input_id: "context", locator_kind: "run_artifact" as const, path: "context.json", required: true }],
		},
	],
});

const cases: readonly ExecutionDispatchCase[] = [
	{
		case_id: "python",
		match: { source_kind: "python" },
		operation_id: "sure.test.python",
		input_contract: contract({ source_kind: "python" }),
	},
	{
		case_id: "docker",
		match: { source_kind: "docker" },
		operation_id: "sure.test.docker",
		input_contract: contract({ source_kind: "docker" }),
	},
];

describe("execution dispatch", () => {
	it("selects the operation from immutable context", () => {
		expect(selectExecutionDispatch(cases, { source_kind: "python" }).operation_id).toBe("sure.test.python");
	});

	it("does not fall back when the context has no case", () => {
		expect(() => selectExecutionDispatch(cases, { source_kind: "cuda" })).toThrow(ExecutionDispatchError);
		try {
			selectExecutionDispatch(cases, { source_kind: "cuda" });
		} catch (error) {
			expect((error as ExecutionDispatchError).code).toBe("NO_MATCH");
		}
	});

	it("rejects a case whose selector identity was tampered", () => {
		const tampered = [{ ...cases[0], input_contract: contract({ source_kind: "docker" }) }];
		expect(() => selectExecutionDispatch(tampered, { source_kind: "python" })).toThrow(/no selector|does not match/);
	});

	it("rejects dispatch cases that do not share one context artifact", () => {
		const tampered = [
			cases[0],
			{
				...cases[1],
				input_contract: { ...cases[1].input_contract, context_artifact: "other-context.json" },
			},
		];
		try {
			selectExecutionDispatch(tampered, { source_kind: "python" });
			throw new Error("expected dispatch validation to fail");
		} catch (error) {
			expect((error as ExecutionDispatchError).code).toBe("INVALID_DISPATCH");
			expect(String(error)).toMatch(/share one context artifact/);
		}
	});
});
