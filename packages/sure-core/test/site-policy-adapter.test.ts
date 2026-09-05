import { describe, expect, it } from "vitest";
import { validateSitePolicy } from "../../../sure/site/loader.ts";
import { snapshotResolvedSitePolicy } from "../../../sure/site/snapshot.ts";

describe("site policy snapshot adapter", () => {
	it("projects current site policy roots without importing site policy into Core", () => {
		const policy = validateSitePolicy({
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
		});
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
});
