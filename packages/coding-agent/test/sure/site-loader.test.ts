import { describe, expect, it } from "vitest";
import { validateSitePolicy } from "../../../../sure/site/loader.ts";

const ROOT = "/srv";

function policy(overrides: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		schema: "sure.site.policy.v1",
		site_id: "test-site",
		policy_version: 1,
		storage: {
			approved_models_roots: [`${ROOT}/models`],
			approved_results_roots: [`${ROOT}/results`],
			forbidden_output_roots: [ROOT],
			runtime_root: `${ROOT}/runtime`,
		},
		datasets: { allowed_source_roots: [`${ROOT}/datasets`] },
		execution: { surfaces: ["local", "vc"] },
		...overrides,
	};
}

describe("validateSitePolicy execution.vc_default_partition", () => {
	it("returns the configured default partition", () => {
		const result = validateSitePolicy(
			policy({ execution: { surfaces: ["vc"], vc_partitions: ["gpu-a"], vc_default_partition: "gpu-a" } }),
		);
		expect(result.execution.vc_default_partition).toBe("gpu-a");
	});

	it("rejects a default partition that is not an allowed partition", () => {
		expect(() =>
			validateSitePolicy(
				policy({ execution: { surfaces: ["vc"], vc_partitions: ["gpu-a"], vc_default_partition: "gpu-b" } }),
			),
		).toThrow(/execution\.vc_default_partition/);
	});
});

describe("validateSitePolicy absolute paths", () => {
	it("rejects a path that does not start with a slash", () => {
		expect(() =>
			validateSitePolicy(
				policy({
					storage: {
						approved_models_roots: ["C:/srv/models"],
						approved_results_roots: [`${ROOT}/results`],
						forbidden_output_roots: [ROOT],
						runtime_root: `${ROOT}/runtime`,
					},
				}),
			),
		).toThrow(/storage\.approved_models_roots\[0\]/);
	});
});

describe("validateSitePolicy network.container_registry", () => {
	it("returns the configured container registry", () => {
		const result = validateSitePolicy(policy({ network: { container_registry: "registry.example/hpc" } }));
		expect(result.network?.container_registry).toBe("registry.example/hpc");
	});

	it("rejects a non-string container registry", () => {
		expect(() => validateSitePolicy(policy({ network: { container_registry: 123 } }))).toThrow(
			/network\.container_registry/,
		);
	});
});
