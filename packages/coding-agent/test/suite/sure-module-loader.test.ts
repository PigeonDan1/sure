import { describe, expect, it } from "vitest";
import { getSureHookAliases, getSureHookVirtualModules } from "../../src/core/sure/module-loader.ts";

const EVALUATION_MODULE = "@earendil-works/sure-core/evaluation";

describe("SURE hook module loader", () => {
	it("injects the shared evaluation backend API into native and bundled hook hosts", () => {
		expect(getSureHookAliases()[EVALUATION_MODULE]).toMatch(/sure-core\/(?:dist|src)\/evaluation\/index\.(?:js|ts)$/);

		const evaluation = getSureHookVirtualModules()[EVALUATION_MODULE] as Record<string, unknown>;
		expect(evaluation.resolveSemanticBackendOperation).toBeTypeOf("function");
	});
});
