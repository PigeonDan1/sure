import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { validateJsonSchema } from "../src/contracts/schema.ts";
import {
	admitExternalAdapterManifest,
	createExternalAdapterManifest,
	type ExternalAdapterAdmissionContext,
	externalAdapterManifestDigest,
	validateExternalAdapterManifest,
} from "../src/execution/adapter-manifest.ts";

type FixtureCase = {
	id: string;
	delete?: string[];
	set?: Record<string, unknown>;
	schema_valid: boolean;
	expected_errors: string[];
};

type Fixture = {
	valid: Record<string, unknown>;
	admission_context: ExternalAdapterAdmissionContext;
	cases: FixtureCase[];
	admission_cases: FixtureCase[];
};

const fixturePath = fileURLToPath(
	new URL("../../../sure/canonical/fixtures/external-adapter-manifest.v1.json", import.meta.url),
);
const fixture = JSON.parse(readFileSync(fixturePath, "utf8")) as Fixture;
const schemaPath = fileURLToPath(
	new URL("../../../sure/core/contracts/execution_adapter_manifest.schema.json", import.meta.url),
);
const schema = JSON.parse(readFileSync(schemaPath, "utf8")) as Record<string, unknown>;

function clone<T>(value: T): T {
	return structuredClone(value);
}

function containerAt(
	root: Record<string, unknown>,
	path: string,
	create: boolean,
): { parent: Record<string, unknown>; key: string } {
	const parts = path.split(".");
	const key = parts.pop() as string;
	let parent = root;
	for (const part of parts) {
		const current = parent[part];
		if (typeof current !== "object" || current === null || Array.isArray(current)) {
			if (!create) return { parent, key: part };
			parent[part] = {};
		}
		parent = parent[part] as Record<string, unknown>;
	}
	return { parent, key };
}

function applyCase(root: Record<string, unknown>, currentCase: FixtureCase): void {
	for (const path of currentCase.delete ?? []) {
		const { parent, key } = containerAt(root, path, false);
		delete parent[key];
	}
	for (const [path, value] of Object.entries(currentCase.set ?? {})) {
		const { parent, key } = containerAt(root, path, true);
		parent[key] = value;
	}
}

describe("external adapter manifest contract", () => {
	it("accepts and self-binds the complete VC mock", () => {
		const result = validateExternalAdapterManifest(fixture.valid);
		expect(result).toMatchObject({ valid: true, errors: [], digest: fixture.valid.manifest_digest });
		expect(externalAdapterManifestDigest(fixture.valid as never)).toBe(fixture.valid.manifest_digest);
		expect(validateJsonSchema(schema, fixture.valid).ok).toBe(true);
	});

	it("normalizes an unsigned input without changing its contract meaning", () => {
		const unsigned = clone(fixture.valid);
		delete unsigned.manifest_digest;
		const created = createExternalAdapterManifest(unsigned as never);
		expect(created).toEqual(fixture.valid);
	});

	it("keeps non-VC external manifests free of queue-specific fields", () => {
		const remote = clone(fixture.valid);
		remote.surface = "remote";
		remote.authorization = { allowed_projects: ["example-project"] };
		delete (remote.runtime as Record<string, unknown>).container;
		remote.cancellation = {
			supported: false,
			strategy: "none",
			confirmation: "not_applicable",
			timeout_outcome: "BLOCKED",
		};
		delete remote.manifest_digest;
		const manifest = createExternalAdapterManifest(remote as never);
		expect(validateExternalAdapterManifest(manifest)).toMatchObject({ valid: true, errors: [] });
		expect(validateJsonSchema(schema, manifest).ok).toBe(true);
		const context = clone(fixture.admission_context);
		context.surface = "remote";
		context.policy_surfaces = ["remote"];
		context.policy_authorization = { allowed_projects: ["example-project"] };
		delete context.container_image_digest;
		context.project = undefined;
		context.partition = undefined;
		expect(admitExternalAdapterManifest(manifest, context)).toMatchObject({ valid: true, errors: [] });
	});

	it.each(fixture.cases)("rejects deterministic malformed case $id", (currentCase) => {
		const candidate = clone(fixture.valid);
		applyCase(candidate, currentCase);
		expect(validateJsonSchema(schema, candidate).ok).toBe(currentCase.schema_valid);
		const result = validateExternalAdapterManifest(candidate);
		expect(result.valid).toBe(false);
		expect(result.errors).toEqual(currentCase.expected_errors);
	});

	it("admits the complete mock only when the run context is bound", () => {
		const result = admitExternalAdapterManifest(fixture.valid, fixture.admission_context);
		expect(result).toMatchObject({ valid: true, errors: [], digest: fixture.valid.manifest_digest });
		expect(result.manifest).toEqual(fixture.valid);
	});

	it.each(fixture.admission_cases)("rejects deterministic admission case $id", (currentCase) => {
		const context = clone(fixture.admission_context) as unknown as Record<string, unknown>;
		applyCase(context, currentCase);
		const result = admitExternalAdapterManifest(fixture.valid, context as unknown as ExternalAdapterAdmissionContext);
		expect(result.valid).toBe(false);
		expect(result.errors).toEqual(currentCase.expected_errors);
	});
});
