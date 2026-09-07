import { describe, expect, it } from "vitest";
import { getSureHookAliases, getSureHookVirtualModules } from "../../src/core/sure/module-loader.ts";

const EVALUATION_MODULE = "@earendil-works/sure-core/evaluation";
const CORE_MODULE = "@earendil-works/sure-core";

describe("SURE hook module loader", () => {
	it("injects the shared evaluation backend API into native and bundled hook hosts", () => {
		expect(getSureHookAliases()[CORE_MODULE]).toMatch(/sure-core\/(?:dist|src)\/index\.(?:js|ts)$/);
		expect(getSureHookAliases()[EVALUATION_MODULE]).toMatch(/sure-core\/(?:dist|src)\/evaluation\/index\.(?:js|ts)$/);

		const core = getSureHookVirtualModules()[CORE_MODULE] as Record<string, unknown>;
		const evaluation = getSureHookVirtualModules()[EVALUATION_MODULE] as Record<string, unknown>;
		expect(core.createOperationExecutionEvidence).toBeTypeOf("function");
		expect(evaluation.resolveSemanticBackendOperation).toBeTypeOf("function");
	});
});
