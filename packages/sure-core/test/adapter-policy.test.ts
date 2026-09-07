import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { JsonValue } from "../src/contracts/types.ts";
import type { ExecutionAdapterSurface } from "../src/execution/adapter.ts";
import { createPolicySnapshot, projectExternalAdapterPolicy } from "../src/index.ts";
import { type SitePolicy, snapshotResolvedSitePolicy, validateSitePolicy } from "../src/policy/site.ts";

interface FixtureCase {
	id: string;
	delete?: string[];
	set?: Record<string, unknown>;
	expected_errors: string[];
}

interface Fixture {
	site_policy: Record<string, unknown>;
	expected_vc_projection: Record<string, unknown>;
	unsupported_surfaces: Array<{ surface: ExecutionAdapterSurface; expected_errors: string[] }>;
	policy_cases: FixtureCase[];
}

const fixturePath = fileURLToPath(
	new URL("../../../sure/canonical/fixtures/external-adapter-policy.v1.json", import.meta.url),
);
const fixture = JSON.parse(readFileSync(fixturePath, "utf8")) as Fixture;

function snapshot(policyValue: Record<string, unknown>) {
	const policy = validateSitePolicy(policyValue);
	return snapshotResolvedSitePolicy(
		{
			policy,
			path: "/config/site.test.yaml",
			source: "bundled",
			sha256: "b".repeat(64),
		},
		{ resolvePath: (path) => path },
	);
}

function containerAt(root: Record<string, unknown>, path: string): { parent: Record<string, unknown>; key: string } {
	const parts = path.split(".");
	const key = parts.pop() as string;
	let parent = root;
	for (const part of parts) parent = parent[part] as Record<string, unknown>;
	return { parent, key };
}

function applyCase(policy: Record<string, unknown>, currentCase: FixtureCase): void {
	for (const path of currentCase.delete ?? []) {
		const { parent, key } = containerAt(policy, path);
		delete parent[key];
	}
	for (const [path, value] of Object.entries(currentCase.set ?? {})) {
		const { parent, key } = containerAt(policy, path);
		parent[key] = value;
	}
}

describe("external adapter site-policy projection", () => {
	it("derives one normalized VC authorization from a verified snapshot", () => {
		const policySnapshot = snapshot(structuredClone(fixture.site_policy));
		const result = projectExternalAdapterPolicy(policySnapshot, "vc");
		expect(result.valid).toBe(true);
		expect(result.projection).toMatchObject({
			...fixture.expected_vc_projection,
			policy_snapshot_digest: policySnapshot.snapshot_digest,
			allowed_output_roots: [
				{ path: "/tmp/sure-adapter/results", resolved_path: "/tmp/sure-adapter/results" },
				{ path: "/tmp/sure-adapter/runtime", resolved_path: "/tmp/sure-adapter/runtime" },
			],
			forbidden_output_roots: [{ path: "/reference", resolved_path: "/reference" }],
		});
		expect(result.projection?.site_policy).toEqual(validateSitePolicy(fixture.site_policy));
	});

	it.each(fixture.unsupported_surfaces)("fails closed for site-policy v1 surface $surface", (currentCase) => {
		const result = projectExternalAdapterPolicy(snapshot(structuredClone(fixture.site_policy)), currentCase.surface);
		expect(result).toEqual({ valid: false, errors: currentCase.expected_errors });
	});

	it.each(fixture.policy_cases)("rejects deterministic policy case $id", (currentCase) => {
		const policy = structuredClone(fixture.site_policy);
		applyCase(policy, currentCase);
		const result = projectExternalAdapterPolicy(snapshot(policy), "vc");
		expect(result).toEqual({ valid: false, errors: currentCase.expected_errors });
	});

	it("rejects a valid snapshot whose outer site identity disagrees with its embedded policy", () => {
		const policy = validateSitePolicy(fixture.site_policy);
		const mismatched = createPolicySnapshot({
			site_id: "other-site",
			policy_version: policy.policy_version,
			policy: policy as unknown as JsonValue,
			source: { kind: "test", raw_sha256: "b".repeat(64) },
			path_bindings: [],
		});
		expect(projectExternalAdapterPolicy(mismatched, "vc")).toMatchObject({
			valid: false,
			errors: ["adapter site policy site_id does not match policy snapshot"],
		});
	});

	it("recomputes snapshot digests before trusting embedded policy", () => {
		const policySnapshot = snapshot(structuredClone(fixture.site_policy));
		(policySnapshot.policy as unknown as SitePolicy).execution.vc_project = "forged-project";
		const result = projectExternalAdapterPolicy(policySnapshot, "vc");
		expect(result.valid).toBe(false);
		expect(result.errors.join(" ")).toContain("policy_digest does not match its canonical contents");
	});

	it("requires the snapshot to bind every site-policy output root", () => {
		const policy = validateSitePolicy(fixture.site_policy);
		const incomplete = createPolicySnapshot({
			site_id: policy.site_id,
			policy_version: policy.policy_version,
			policy: policy as unknown as JsonValue,
			source: { kind: "test", raw_sha256: "b".repeat(64) },
			path_bindings: [],
		});
		const result = projectExternalAdapterPolicy(incomplete, "vc");
		expect(result.valid).toBe(false);
		expect(result.errors).toContain(
			"adapter policy snapshot requires exactly one controlled_publication binding for /tmp/sure-adapter/results",
		);
		expect(result.errors).toContain(
			"adapter policy snapshot requires exactly one runtime_cache binding for /tmp/sure-adapter/runtime",
		);
		expect(result.errors).toContain(
			"adapter policy snapshot requires exactly one forbidden_output binding for /reference",
		);
	});
});
