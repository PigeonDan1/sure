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
		datasets: { allowed_source_roots: { default: `${ROOT}/datasets` } },
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

describe("validateSitePolicy execution.local_runtimes", () => {
	it("keeps omitted policies container-only", () => {
		const result = validateSitePolicy(policy({ execution: { surfaces: ["local"] } }));
		expect(result.execution.local_runtimes).toEqual(["container"]);
	});

	it("returns explicitly permitted Python and container runtimes", () => {
		const result = validateSitePolicy(
			policy({ execution: { surfaces: ["local"], local_runtimes: ["python", "container"] } }),
		);
		expect(result.execution.local_runtimes).toEqual(["python", "container"]);
	});

	it("rejects an unsupported local runtime", () => {
		expect(() =>
			validateSitePolicy(policy({ execution: { surfaces: ["local"], local_runtimes: ["virtualenv"] } })),
		).toThrow(/execution\.local_runtimes/);
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

	it("returns an absolute dataset projection root", () => {
		const result = validateSitePolicy(
			policy({
				datasets: {
					allowed_source_roots: [`${ROOT}/datasets`],
					projection_root: "/var/lib/sure/dataset-projections",
				},
			}),
		);
		expect(result.datasets.projection_root).toBe("/var/lib/sure/dataset-projections");
	});

	it("rejects a relative dataset projection root", () => {
		expect(() =>
			validateSitePolicy(
				policy({
					datasets: {
						allowed_source_roots: [`${ROOT}/datasets`],
						projection_root: "data/projections",
					},
				}),
			),
		).toThrow(/datasets\.projection_root/);
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

describe("validateSitePolicy container_delivery", () => {
	it("returns an explicit repository template", () => {
		const result = validateSitePolicy(
			policy({
				network: { container_registry: "registry.example" },
				container_delivery: { repository_template: "{registry}/my-org/sure-{task}-{model_name}" },
			}),
		);
		expect(result.container_delivery?.repository_template).toBe("{registry}/my-org/sure-{task}-{model_name}");
	});

	it("requires a configured registry", () => {
		expect(() =>
			validateSitePolicy(
				policy({ container_delivery: { repository_template: "{registry}/my-org/sure-{model_name}" } }),
			),
		).toThrow(/network\.container_registry/);
	});

	it("rejects unsupported template fields", () => {
		expect(() =>
			validateSitePolicy(
				policy({
					network: { container_registry: "registry.example" },
					container_delivery: { repository_template: "{registry}/{owner}/{model_name}" },
				}),
			),
		).toThrow(/unsupported field: owner/);
	});
});
