// biome-ignore-all lint/suspicious/noTemplateCurlyInString: ${HOME} and ${REPO} are site policy tokens the loader expands, not JavaScript interpolation
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { resolveSitePolicy, validateSitePolicy } from "../../../../sure/site/loader.ts";

// node:os is the loader's only source of a home directory, and on a host with
// none Node throws from homedir() where Python raises RuntimeError from
// Path.home(). This is the seam that reproduces both.
const homeFailure = vi.hoisted(() => ({ active: false }));
vi.mock("node:os", async (importOriginal) => {
	const actual = await importOriginal<typeof import("node:os")>();
	return {
		...actual,
		homedir: () => {
			if (homeFailure.active) throw new Error("Could not determine home directory");
			return actual.homedir();
		},
	};
});

function withoutHome<T>(body: () => T): T {
	homeFailure.active = true;
	try {
		return body();
	} finally {
		homeFailure.active = false;
	}
}

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
		execution: { surfaces: ["local", "vc"], vc_project: "example-project" },
		...overrides,
	};
}

describe("validateSitePolicy execution.vc_default_partition", () => {
	it("returns the configured default partition", () => {
		const result = validateSitePolicy(
			policy({
				execution: {
					surfaces: ["vc"],
					vc_project: "example-project",
					vc_partitions: ["gpu-a"],
					vc_default_partition: "gpu-a",
				},
			}),
		);
		expect(result.execution.vc_default_partition).toBe("gpu-a");
	});

	it("rejects a default partition that is not an allowed partition", () => {
		expect(() =>
			validateSitePolicy(
				policy({
					execution: {
						surfaces: ["vc"],
						vc_project: "example-project",
						vc_partitions: ["gpu-a"],
						vc_default_partition: "gpu-b",
					},
				}),
			),
		).toThrow(/execution\.vc_default_partition/);
	});
});

describe("validateSitePolicy execution.vc_project", () => {
	it("returns the configured project", () => {
		const result = validateSitePolicy(policy({ execution: { surfaces: ["vc"], vc_project: "example-project" } }));
		expect(result.execution.vc_project).toBe("example-project");
	});

	it("requires a project whenever VC is enabled", () => {
		expect(() => validateSitePolicy(policy({ execution: { surfaces: ["vc"] } }))).toThrow(/execution\.vc_project/);
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

describe("validateSitePolicy storage.approved_results_roots", () => {
	it("normalizes an omitted key to an empty list", () => {
		const result = validateSitePolicy(
			policy({
				storage: {
					approved_models_roots: [`${ROOT}/models`],
					forbidden_output_roots: [ROOT],
					runtime_root: `${ROOT}/runtime`,
				},
			}),
		);
		expect(result.storage.approved_results_roots).toEqual([]);
	});

	it("still rejects an explicit empty list", () => {
		expect(() =>
			validateSitePolicy(
				policy({
					storage: {
						approved_models_roots: [`${ROOT}/models`],
						approved_results_roots: [],
						forbidden_output_roots: [ROOT],
						runtime_root: `${ROOT}/runtime`,
					},
				}),
			),
		).toThrow(/storage\.approved_results_roots must be a non-empty list/);
	});
});

describe("validateSitePolicy absolute paths", () => {
	it("accepts a Windows drive-letter path", () => {
		const result = validateSitePolicy(
			policy({
				storage: {
					approved_models_roots: ["C:/Users/example/.sure/approved/models"],
					approved_results_roots: ["C:/Users/example/.sure/approved/results"],
					forbidden_output_roots: ["C:/Users/example/.sure/approved"],
					runtime_root: "C:\\Users\\example\\.sure\\runtime",
				},
			}),
		);
		expect(result.storage.approved_models_roots[0]).toBe("C:/Users/example/.sure/approved/models");
		expect(result.storage.runtime_root).toBe("C:\\Users\\example\\.sure\\runtime");
	});

	it("still accepts a POSIX path on every host", () => {
		const result = validateSitePolicy(policy());
		expect(result.storage.approved_models_roots[0]).toBe(`${ROOT}/models`);
	});

	it("rejects a path that is neither POSIX-rooted nor drive-rooted", () => {
		expect(() =>
			validateSitePolicy(
				policy({
					storage: {
						approved_models_roots: ["srv/models"],
						approved_results_roots: [`${ROOT}/results`],
						forbidden_output_roots: [ROOT],
						runtime_root: `${ROOT}/runtime`,
					},
				}),
			),
		).toThrow(/storage\.approved_models_roots\[0\]/);
	});

	it("rejects a drive letter with no separator", () => {
		expect(() =>
			validateSitePolicy(
				policy({
					storage: {
						approved_models_roots: ["C:models"],
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
		const result = validateSitePolicy(policy({ network: { container_registry: "registry.example/example-org" } }));
		expect(result.network?.container_registry).toBe("registry.example/example-org");
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

const TOKEN_FIXTURE = [
	"schema: sure.site.policy.v1",
	"site_id: token-fixture",
	"policy_version: 1",
	"storage:",
	'  approved_models_roots: ["${HOME}/.sure/approved/models"]',
	'  forbidden_output_roots: ["${HOME}/.sure/approved"]',
	'  runtime_root: "${HOME}/.sure/runtime"',
	"datasets:",
	"  allowed_source_roots:",
	'    smoke: "${REPO}/fixtures/tasks"',
	"execution:",
	"  surfaces: [local]",
	"",
].join("\n");

function expectedHome(): string {
	return homedir().replaceAll("\\", "/").replace(/\/+$/, "");
}

// Fixtures live in the system temporary directory, never inside the repository:
// scripts/check-site-boundary.mjs only runs its export probe on a clean tree.
function tokenRoot(name: string): string {
	const root = mkdtempSync(join(tmpdir(), `sure-site-${name}-`));
	mkdirSync(join(root, "config"), { recursive: true });
	return root;
}

describe("site policy token expansion", () => {
	it("expands ${HOME} and ${REPO} for an explicit SURE_SITE_POLICY path", () => {
		const root = tokenRoot("environment-source");
		const fixture = join(root, "config", "token.yaml");
		writeFileSync(fixture, TOKEN_FIXTURE, "utf-8");

		const resolved = resolveSitePolicy({ repositoryRoot: root, environment: { SURE_SITE_POLICY: fixture } });

		expect(resolved?.source).toBe("environment");
		expect(resolved?.policy.storage.approved_models_roots[0]).toBe(`${expectedHome()}/.sure/approved/models`);
		expect(resolved?.policy.storage.runtime_root).toBe(`${expectedHome()}/.sure/runtime`);
		expect(resolved?.policy.datasets.allowed_source_roots.smoke).toBe(`${root.replaceAll("\\", "/")}/fixtures/tasks`);
	});

	it("expands ${HOME} and ${REPO} for a local policy", () => {
		const root = tokenRoot("local-source");
		writeFileSync(join(root, "config", "site.local.yaml"), TOKEN_FIXTURE, "utf-8");

		const resolved = resolveSitePolicy({ repositoryRoot: root, environment: {} });

		expect(resolved?.source).toBe("local");
		expect(resolved?.policy.storage.forbidden_output_roots[0]).toBe(`${expectedHome()}/.sure/approved`);
	});

	it("hashes the expanded text, not the committed template", () => {
		const root = tokenRoot("digest");
		writeFileSync(join(root, "config", "site.local.yaml"), TOKEN_FIXTURE, "utf-8");
		const expanded = TOKEN_FIXTURE.replaceAll("${HOME}", expectedHome()).replaceAll(
			"${REPO}",
			root.replaceAll("\\", "/"),
		);

		const resolved = resolveSitePolicy({ repositoryRoot: root, environment: {} });

		expect(resolved?.sha256).toBe(createHash("sha256").update(Buffer.from(expanded, "utf8")).digest("hex"));
	});
});

const REPO_ROOT = resolve(__dirname, "../../../..");

function shipDefault(root: string): void {
	copyFileSync(join(REPO_ROOT, "config", "site.default.yaml"), join(root, "config", "site.default.yaml"));
}

describe("resolveSitePolicy candidate order", () => {
	it("selects the shipped default when nothing else is configured", () => {
		const root = tokenRoot("default-only");
		shipDefault(root);

		const resolved = resolveSitePolicy({ repositoryRoot: root, environment: {} });

		expect(resolved?.source).toBe("default");
		expect(resolved?.policy.site_id).toBe("local-default");
		expect(resolved?.policy.storage.approved_models_roots[0]).toBe(`${expectedHome()}/.sure/approved/models`);
		expect(resolved?.policy.storage.forbidden_output_roots[0]).toBe(`${expectedHome()}/.sure/approved`);
		expect(resolved?.policy.datasets.allowed_source_roots.smoke).toBe(`${root.replaceAll("\\", "/")}/fixtures/tasks`);
		expect(resolved?.policy.execution.local_runtimes).toEqual(["python", "container"]);
		expect(resolved?.policy.network).toBeUndefined();
	});

	it("lets a local policy outrank the shipped default", () => {
		const root = tokenRoot("local-beats-default");
		shipDefault(root);
		writeFileSync(join(root, "config", "site.local.yaml"), TOKEN_FIXTURE, "utf-8");

		expect(resolveSitePolicy({ repositoryRoot: root, environment: {} })?.source).toBe("local");
	});

	it("lets a bundled policy outrank a local one", () => {
		const root = tokenRoot("bundled-beats-local");
		shipDefault(root);
		writeFileSync(join(root, "config", "site.local.yaml"), TOKEN_FIXTURE, "utf-8");
		writeFileSync(join(root, "config", "site.bundled.yaml"), TOKEN_FIXTURE, "utf-8");

		expect(resolveSitePolicy({ repositoryRoot: root, environment: {} })?.source).toBe("bundled");
	});

	it("returns undefined when even the default file is absent", () => {
		const root = tokenRoot("nothing-at-all");

		expect(resolveSitePolicy({ repositoryRoot: root, environment: {} })).toBeUndefined();
	});
});

const PLAIN_FIXTURE = TOKEN_FIXTURE.replaceAll("${HOME}", "/srv").replaceAll("${REPO}", "/srv");

// Five modules load the policy at import scope, so a host without a home
// directory must get a policy or a readable error, never an uncaught throw.
describe("resolveSitePolicy without a usable home directory", () => {
	it("still loads a policy that has no ${HOME} token", () => {
		const root = tokenRoot("no-home-plain");
		writeFileSync(join(root, "config", "site.local.yaml"), PLAIN_FIXTURE, "utf-8");

		const resolved = withoutHome(() => resolveSitePolicy({ repositoryRoot: root, environment: {} }));

		expect(resolved?.source).toBe("local");
	});

	it("reports a policy error for a policy that needs ${HOME}", () => {
		const root = tokenRoot("no-home-token");
		const fixture = join(root, "config", "site.local.yaml");
		writeFileSync(fixture, TOKEN_FIXTURE, "utf-8");

		expect(() => withoutHome(() => resolveSitePolicy({ repositoryRoot: root, environment: {} }))).toThrow(
			`Cannot expand \${HOME} in local site policy ${fixture}: no home directory`,
		);
	});

	it("does not offer the shipped default", () => {
		const root = tokenRoot("no-home-default");
		shipDefault(root);

		expect(withoutHome(() => resolveSitePolicy({ repositoryRoot: root, environment: {} }))).toBeUndefined();
	});
});

describe("site policy encoding", () => {
	it("refuses a policy that is not valid UTF-8", () => {
		const root = tokenRoot("invalid-utf8");
		const fixture = join(root, "config", "site.local.yaml");
		const [head, tail] = PLAIN_FIXTURE.split("approved/models");
		writeFileSync(
			fixture,
			Buffer.concat([Buffer.from(head, "utf8"), Buffer.from([0xff, 0xfe]), Buffer.from(`approved/models${tail}`, "utf8")]),
		);

		expect(() => resolveSitePolicy({ repositoryRoot: root, environment: {} })).toThrow(
			`Cannot parse local site policy ${fixture}: `,
		);
	});

	it("still loads a policy that starts with a UTF-8 byte order mark", () => {
		const root = tokenRoot("byte-order-mark");
		const fixture = join(root, "config", "site.local.yaml");
		writeFileSync(fixture, Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from(TOKEN_FIXTURE, "utf8")]));
		const expanded = `﻿${TOKEN_FIXTURE.replaceAll("${HOME}", expectedHome()).replaceAll("${REPO}", root.replaceAll("\\", "/"))}`;

		const resolved = resolveSitePolicy({ repositoryRoot: root, environment: {} });

		expect(resolved?.sha256).toBe(createHash("sha256").update(Buffer.from(expanded, "utf8")).digest("hex"));
	});
});

describe("validateSitePolicy removed fields", () => {
	it("rejects network.internal_git_host", () => {
		expect(() => validateSitePolicy(policy({ network: { internal_git_host: "git.example" } }))).toThrow(
			/network has unknown field: internal_git_host/,
		);
	});

	it("rejects network.gateway_portal", () => {
		expect(() => validateSitePolicy(policy({ network: { gateway_portal: "https://portal.example" } }))).toThrow(
			/network has unknown field: gateway_portal/,
		);
	});

	it("rejects execution.vc_partition_priority", () => {
		expect(() =>
			validateSitePolicy(
				policy({
					execution: {
						surfaces: ["vc"],
						vc_project: "example-project",
						vc_partitions: ["gpu-a"],
						vc_partition_priority: { "gpu-a": 1 },
					},
				}),
			),
		).toThrow(/execution has unknown field: vc_partition_priority/);
	});
});
