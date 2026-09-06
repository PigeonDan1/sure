import { describe, expect, it } from "vitest";
import { type ValidatorDescriptor, ValidatorRegistry } from "../src/index.ts";

const structural: ValidatorDescriptor = {
	id: "sure.structural.artifact",
	version: "1.0.0",
	authority: "structural",
	operation: "validate",
	deterministic: true,
	input_contract: "sure.artifact.v1",
	output_contract: "sure.validation.signal.v1",
};

describe("host-neutral validator registry", () => {
	it("sorts descriptors and produces a stable snapshot digest", () => {
		const first = new ValidatorRegistry([
			structural,
			{
				id: "sure.semantic.eval",
				version: "2.1.0",
				authority: "semantic",
				operation: "validate",
				deterministic: true,
				backend_operation_id: "sure.eval.validate_report",
				resource_path: "backends/eval/check.py",
			},
		]);
		const second = new ValidatorRegistry([
			{
				id: "sure.semantic.eval",
				version: "2.1.0",
				authority: "semantic",
				operation: "validate",
				deterministic: true,
				backend_operation_id: "sure.eval.validate_report",
				resource_path: "backends/eval/check.py",
			},
			structural,
		]);
		expect(first.list().map((entry) => entry.id)).toEqual(["sure.semantic.eval", "sure.structural.artifact"]);
		expect(first.snapshot()).toEqual(second.snapshot());
		expect(first.snapshot().digest).toMatch(/^sha256:[0-9a-f]{64}$/);
	});

	it("returns defensive descriptor copies", () => {
		const registry = new ValidatorRegistry([structural]);
		const copy = registry.require(structural.id);
		copy.version = "tampered";
		expect(registry.require(structural.id).version).toBe(structural.version);
	});

	it("rejects duplicate IDs, malformed IDs, and escaping resources", () => {
		const registry = new ValidatorRegistry([structural]);
		expect(() => registry.register(structural)).toThrow(/already registered/);
		expect(() => new ValidatorRegistry([{ ...structural, id: "Sure/unsafe" }])).toThrow(/Invalid validator id/);
		expect(() => new ValidatorRegistry([{ ...structural, id: "sure.unsafe", resource_path: "../check.py" }])).toThrow(
			/resource path/,
		);
		expect(
			() => new ValidatorRegistry([{ ...structural, id: "sure.absolute", resource_path: "/tmp/check.py" }]),
		).toThrow(/resource path/);
		expect(
			() => new ValidatorRegistry([{ ...structural, id: "sure.operation", backend_operation_id: "unsafe/op" }]),
		).toThrow(/backend operation id/);
	});

	it("distinguishes an unregistered validator from a registered one", () => {
		const registry = new ValidatorRegistry([structural]);
		expect(registry.get("sure.missing")).toBeUndefined();
		expect(() => registry.require("sure.missing")).toThrow(/not registered/);
	});
});
