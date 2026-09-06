import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { resolveSitePolicy, snapshotResolvedSitePolicy, validateSitePolicy } from "../src/policy/site.ts";

const roots: string[] = [];

afterEach(() => {
	for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

function policyDocument(): Record<string, unknown> {
	return {
		schema: "sure.site.policy.v1",
		site_id: "test-site",
		policy_version: 1,
		storage: {
			approved_models_roots: ["/reference/models"],
			approved_results_roots: ["/reference/results"],
			forbidden_output_roots: ["/reference"],
			runtime_root: "/runtime",
		},
		datasets: { allowed_source_roots: { speech: "/datasets/speech" }, projection_root: "/projection" },
		execution: { surfaces: ["local"], local_runtimes: ["python"] },
	};
}

describe("site policy snapshot adapter", () => {
	it("projects validated site-policy roots into host-neutral snapshot evidence", () => {
		const policy = validateSitePolicy(policyDocument());
		const snapshot = snapshotResolvedSitePolicy(
			{
				policy,
				path: "/config/site.yaml",
				source: "environment",
				sha256: "b".repeat(64),
			},
			{ resolvePath: (path) => path },
		);
		expect(snapshot.site_id).toBe("test-site");
		expect(snapshot.path_bindings.map((binding) => [binding.root_id, binding.role])).toEqual([
			["approved-models.0", "read_only_reference"],
			["approved-results.0", "controlled_publication"],
			["dataset-projection", "controlled_publication"],
			["dataset.speech", "dataset_source"],
			["forbidden-output.0", "forbidden_output"],
			["runtime", "runtime_cache"],
		]);
		expect(snapshot.source.raw_sha256).toBe(`sha256:${"b".repeat(64)}`);
	});

	it("loads and digests a repository-local policy without a repository facade", () => {
		const root = mkdtempSync(join(tmpdir(), "sure-site-policy-"));
		roots.push(root);
		const config = join(root, "config");
		mkdirSync(config);
		const path = join(config, "site.local.yaml");
		const content = `${JSON.stringify(policyDocument(), null, 2)}\n`;
		writeFileSync(path, content);

		const resolved = resolveSitePolicy({ repositoryRoot: root, environment: {} });

		expect(resolved?.path).toBe(path);
		expect(resolved?.source).toBe("local");
		expect(resolved?.policy).toEqual(validateSitePolicy(policyDocument()));
		expect(resolved?.sha256).toMatch(/^[0-9a-f]{64}$/);
	});
});
