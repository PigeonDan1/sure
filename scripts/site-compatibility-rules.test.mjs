import assert from "node:assert/strict";
import test from "node:test";
import { legacyValueDifferences, schemaCompatibilityDifferences } from "./site-compatibility-rules.mjs";

test("legacy policy allows additive configuration", () => {
	const expected = {
		datasets: { allowed_source_roots: ["/datasets"] },
		execution: { surfaces: ["local", "vc"] },
	};
	const actual = {
		datasets: { allowed_source_roots: ["/datasets", "/datasets-secondary"] },
		execution: { surfaces: ["local", "vc", "native"], default_surface: "native" },
	};
	assert.deepEqual(legacyValueDifferences(expected, actual), []);
});

test("legacy policy rejects changed values", () => {
	const differences = legacyValueDifferences(
		{ datasets: { allowed_source_roots: ["/datasets"] } },
		{ datasets: { allowed_source_roots: ["/other"] } },
	);
	assert.deepEqual(differences, ['$.datasets.allowed_source_roots no longer contains "/datasets"']);
});

test("legacy manifests preserve ordered hook and UI entries", () => {
	const options = { orderedArray: (path) => path.startsWith("$.hooks.") || path.startsWith("$.ui.") };
	const expected = {
		hooks: { pre_start: [{ handler: "first" }, { handler: "second" }] },
		ui: { primaryCounters: ["completed", "total"] },
	};
	const actual = {
		hooks: { pre_start: [{ handler: "second" }, { handler: "first" }] },
		ui: { primaryCounters: ["total", "completed"] },
	};
	assert.deepEqual(legacyValueDifferences(expected, actual, "$", options), [
		'$.hooks.pre_start[0].handler changed from "first" to "second"',
		'$.hooks.pre_start[1].handler changed from "second" to "first"',
		'$.ui.primaryCounters[0] changed from "completed" to "total"',
		'$.ui.primaryCounters[1] changed from "total" to "completed"',
	]);
});

test("legacy manifests allow appended ordered entries and unordered artifacts", () => {
	const options = { orderedArray: (path) => path.startsWith("$.hooks.") || path.startsWith("$.ui.") };
	const expected = {
		hooks: { pre_start: [{ handler: "legacy" }] },
		artifacts: [{ type: "report" }, { type: "manifest" }],
	};
	const actual = {
		hooks: { pre_start: [{ handler: "legacy" }, { handler: "new" }] },
		artifacts: [{ type: "new" }, { type: "manifest" }, { type: "report" }],
	};
	assert.deepEqual(legacyValueDifferences(expected, actual, "$", options), []);
});

test("schema compatibility allows optional properties and enum extensions", () => {
	const expected = {
		type: "object",
		required: ["status"],
		properties: {
			status: { type: "string", enum: ["ready", "blocked"] },
		},
		additionalProperties: false,
	};
	const actual = {
		type: "object",
		required: ["status"],
		properties: {
			status: { type: "string", enum: ["ready", "blocked", "pending"] },
			reason: { type: "string" },
		},
		additionalProperties: false,
	};
	assert.deepEqual(schemaCompatibilityDifferences(expected, actual), []);
});

test("schema compatibility rejects property removal", () => {
	const expected = { type: "object", properties: { status: { type: "string" } } };
	const actual = { type: "object", properties: {} };
	assert.deepEqual(schemaCompatibilityDifferences(expected, actual), ["$.properties.status is missing"]);
});

test("schema compatibility rejects changed types and required fields", () => {
	const expected = {
		type: "object",
		required: ["status"],
		properties: { status: { type: "string" } },
	};
	const actual = {
		type: "object",
		required: ["status", "reason"],
		properties: { status: { type: "number" }, reason: { type: "string" } },
	};
	assert.deepEqual(schemaCompatibilityDifferences(expected, actual), [
		"$.required changed",
		'$.properties.status.type changed from "string" to "number"',
	]);
});
