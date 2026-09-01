#!/usr/bin/env node
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { parse } from "yaml";

function run(command, args, options = {}) {
	// npm resolves to npm.cmd on Windows, which spawnSync cannot execute
	// directly; without a shell it returns no stderr and every caller crashes.
	const shell = process.platform === "win32" && command === "npm";
	return spawnSync(command, args, { encoding: "utf8", shell, ...options });
}

const failures = [];

// The public export lists are closed: main_flow_agent is the single
// grandfathered export exclusion, and content exceptions are limited to the
// evaluation files whose technical identifiers must remain byte-for-byte stable.
const allowedExclusions = new Set([
	"private/site/**",
	"docs/internal/**",
	"config/site.bundled.yaml",
	"sure/skills/sure_eval/references/main_flow_agent/**",
	".gitlab-ci.yml",
]);
const allowedForbiddenContentExceptionPaths = new Set([
	"sure/skills/sure_eval/references/contracts/main_agent_worked_example.md",
	"sure/skills/sure_eval/scripts/dataset_alias.py",
	"sure/skills/sure_eval/scripts/resolve_eval_input.py",
	"sure/skills/sure_eval/scripts/sure_eval/agent/vc_submitter.py",
	"sure/skills/sure_eval/scripts/sure_eval/datasets/dataset_manager.py",
	"sure/skills/sure_eval/scripts/sure_eval/datasets/source_resolver.py",
	"sure/skills/sure_eval/scripts/test_dataset_alias.py",
	"sure/skills/sure_eval/scripts/test_model_registry.py",
	"sure/skills/sure_eval/scripts/test_report_provenance.py",
	"sure/skills/sure_eval/scripts/test_smoke_dataset_version_guard.py",
	"sure/skills/sure_eval/scripts/test_source_conversion.py",
	"sure/skills/sure_eval/scripts/test_source_naming_flow.py",
	"sure/skills/sure_eval/scripts/test_source_resolver.py",
	"sure/skills/sure_eval/scripts/test_vc_submit_readiness.py",
]);
const allowedForbiddenContentExceptionPairs = new Set([
	JSON.stringify(["9d55a85b9be66a014b0e18503055e5cb3257e4ffc8f1af82108a8eaf19d4a132", "sure/skills/sure_eval/references/contracts/main_agent_worked_example.md"]),
	JSON.stringify(["9d55a85b9be66a014b0e18503055e5cb3257e4ffc8f1af82108a8eaf19d4a132", "sure/skills/sure_eval/scripts/dataset_alias.py"]),
	JSON.stringify(["9d55a85b9be66a014b0e18503055e5cb3257e4ffc8f1af82108a8eaf19d4a132", "sure/skills/sure_eval/scripts/resolve_eval_input.py"]),
	JSON.stringify([
		"9d55a85b9be66a014b0e18503055e5cb3257e4ffc8f1af82108a8eaf19d4a132",
		"sure/skills/sure_eval/scripts/sure_eval/datasets/dataset_manager.py",
	]),
	JSON.stringify([
		"9d55a85b9be66a014b0e18503055e5cb3257e4ffc8f1af82108a8eaf19d4a132",
		"sure/skills/sure_eval/scripts/sure_eval/datasets/source_resolver.py",
	]),
	JSON.stringify(["9d55a85b9be66a014b0e18503055e5cb3257e4ffc8f1af82108a8eaf19d4a132", "sure/skills/sure_eval/scripts/test_dataset_alias.py"]),
	JSON.stringify(["9d55a85b9be66a014b0e18503055e5cb3257e4ffc8f1af82108a8eaf19d4a132", "sure/skills/sure_eval/scripts/test_model_registry.py"]),
	JSON.stringify(["9d55a85b9be66a014b0e18503055e5cb3257e4ffc8f1af82108a8eaf19d4a132", "sure/skills/sure_eval/scripts/test_report_provenance.py"]),
	JSON.stringify([
		"9d55a85b9be66a014b0e18503055e5cb3257e4ffc8f1af82108a8eaf19d4a132",
		"sure/skills/sure_eval/scripts/test_smoke_dataset_version_guard.py",
	]),
	JSON.stringify(["9d55a85b9be66a014b0e18503055e5cb3257e4ffc8f1af82108a8eaf19d4a132", "sure/skills/sure_eval/scripts/test_source_conversion.py"]),
	JSON.stringify(["9d55a85b9be66a014b0e18503055e5cb3257e4ffc8f1af82108a8eaf19d4a132", "sure/skills/sure_eval/scripts/test_source_naming_flow.py"]),
	JSON.stringify(["9d55a85b9be66a014b0e18503055e5cb3257e4ffc8f1af82108a8eaf19d4a132", "sure/skills/sure_eval/scripts/test_source_resolver.py"]),
	JSON.stringify(["9fd5dc77aafff3dbff36b4c77269a9aeb5a5dab7bfbd2056c287d28551c32e19", "sure/skills/sure_eval/scripts/test_model_registry.py"]),
	JSON.stringify(["14fca113a01f0b6f17c95360eec4f57463491c799de80c861e66260ea0f833b3", "sure/skills/sure_eval/scripts/sure_eval/agent/vc_submitter.py"]),
]);
if (existsSync("public-export.yaml")) {
	const exportText = readFileSync("public-export.yaml", "utf8");
	const exportConfiguration = parse(exportText);
	for (const entry of exportConfiguration?.exclude ?? []) {
		if (!allowedExclusions.has(entry)) {
			failures.push(`public-export.yaml exclude entry is not on the approved exception list: ${entry}`);
		}
	}
	const configuredPaths = exportConfiguration?.forbidden_content_exception_paths;
	if (!Array.isArray(configuredPaths) || configuredPaths.some((entry) => typeof entry !== "string")) {
		failures.push("public-export.yaml forbidden_content_exception_paths must be a string list");
	} else {
		const seen = new Set();
		for (const entry of configuredPaths) {
			if (seen.has(entry)) failures.push(`public-export.yaml contains duplicate content-exception path: ${entry}`);
			seen.add(entry);
			if (!allowedForbiddenContentExceptionPaths.has(entry)) {
				failures.push(`public-export.yaml content-exception path is not on the approved closed list: ${entry}`);
			}
		}
		for (const entry of allowedForbiddenContentExceptionPaths) {
			if (!seen.has(entry)) failures.push(`public-export.yaml is missing approved content-exception path: ${entry}`);
		}
	}
}

const privateOverlayPath = "private/site/public-export.overlay.yaml";
if (existsSync(privateOverlayPath)) {
	const overlayConfiguration = parse(readFileSync(privateOverlayPath, "utf8"));
	const overlayPatterns = new Set(overlayConfiguration?.forbidden_content ?? []);
	const exceptions = overlayConfiguration?.forbidden_content_exceptions ?? [];
	if (!Array.isArray(exceptions)) {
		failures.push(`${privateOverlayPath} forbidden_content_exceptions must be a list`);
	} else {
		const pairs = new Set();
		for (const [index, exception] of exceptions.entries()) {
			if (typeof exception !== "object" || exception === null || Array.isArray(exception)) {
				failures.push(`${privateOverlayPath} forbidden_content_exceptions[${index}] must be an object`);
				continue;
			}
			if (typeof exception.pattern !== "string" || !overlayPatterns.has(exception.pattern)) {
				failures.push(`${privateOverlayPath} exception pattern must be declared by forbidden_content`);
			}
			if (!Array.isArray(exception.paths) || exception.paths.length === 0) {
				failures.push(`${privateOverlayPath} exception paths must be a non-empty list`);
				continue;
			}
			for (const path of exception.paths) {
				if (typeof path !== "string" || !allowedForbiddenContentExceptionPaths.has(path)) {
					failures.push(`${privateOverlayPath} exception path is not on the approved closed list: ${path}`);
					continue;
				}
				const patternSha256 = createHash("sha256").update(exception.pattern).digest("hex");
				const pair = JSON.stringify([patternSha256, path]);
				if (pairs.has(pair)) failures.push(`${privateOverlayPath} contains duplicate pattern/path exception: ${path}`);
				if (!allowedForbiddenContentExceptionPairs.has(pair)) {
					failures.push(`${privateOverlayPath} pattern/path exception is not on the approved closed list: ${path}`);
				}
				pairs.add(pair);
			}
		}
		for (const pair of allowedForbiddenContentExceptionPairs) {
			if (!pairs.has(pair)) failures.push(`${privateOverlayPath} is missing an approved pattern/path exception: ${pair}`);
		}
	}
}

for (const document of [
	"README.md",
	"docs/site-configuration.md",
	"docs/evaluation_engine.md",
	"private/site/README.md",
	"private/site/docs/handbook.md",
	"private/site/docs/noninteractive_usage.md",
	"private/site/docs/company_model_onboarding.md",
	"private/site/docs/repository-governance.md",
]) {
	if (!existsSync(document)) continue;
	const text = readFileSync(document, "utf8");
	for (const match of text.matchAll(/\]\(([^)]+)\)/g)) {
		const target = match[1].split("#", 1)[0];
		if (!target || /^(?:https?:|mailto:)/.test(target)) continue;
		if (!existsSync(resolve(dirname(document), target))) failures.push(`${document}: broken local link ${match[1]}`);
	}
}

const ripgrep = run("rg", ["--version"]);
if (ripgrep.error || ripgrep.status !== 0) {
	failures.push("ripgrep (rg) is required for check:site-boundary; install ripgrep and retry");
} else {
	const publicImports = run("rg", [
	"-n",
	"private/site",
	"packages",
	"sure",
	"scripts",
	"--glob",
	"*.{ts,tsx,js,mjs,py}",
	"--glob",
	"!scripts/check-site-boundary.mjs",
	"--glob",
	"!scripts/check-repository-hygiene.mjs",
	"--glob",
	"!scripts/check-site-compatibility.mjs",
	"--glob",
	"!scripts/export-public.mjs",
	"--glob",
	"!scripts/export-public.test.mjs",
		]);
		if (publicImports.error) {
			failures.push(`ripgrep scan failed: ${publicImports.error.message}`);
		} else if (publicImports.status === 0) {
			failures.push(`public core references private/site:\n${publicImports.stdout.trim()}`);
		} else if (publicImports.status !== 1) {
			failures.push((publicImports.stderr ?? "").trim() || `ripgrep exited with status ${publicImports.status}`);
		}
	}

const typescript = run("node", [
	"--import",
	"tsx",
	"-e",
	'import("./sure/site/loader.ts").then(({resolveSitePolicy}) => console.log(JSON.stringify(resolveSitePolicy() ?? null)))',
]);
const python = run("python3", ["sure/site/loader.py"]);
if (typescript.status !== 0) failures.push(`TypeScript site loader failed: ${typescript.stderr.trim()}`);
let typescriptValue;
if (typescript.status === 0) {
	try {
		typescriptValue = JSON.parse(typescript.stdout);
	} catch (error) {
		failures.push(`cannot parse TypeScript loader JSON: ${error instanceof Error ? error.message : String(error)}`);
	}
}
if (typescriptValue !== null && typescriptValue !== undefined && python.status !== 0) {
	failures.push(`Python site loader failed: ${python.stderr.trim()}`);
}
if (typescriptValue === null && python.status === 0) {
	failures.push("Python site loader unexpectedly selected a policy");
}
if (typescriptValue !== null && typescriptValue !== undefined && python.status === 0) {
	try {
		const pythonValue = JSON.parse(python.stdout);
		for (const key of ["policy", "source", "sha256"]) {
			const normalize = (value) => {
				if (Array.isArray(value)) return value.map(normalize);
				if (typeof value !== "object" || value === null) return value;
				return Object.fromEntries(Object.keys(value).sort().map((name) => [name, normalize(value[name])]));
			};
			if (JSON.stringify(normalize(typescriptValue[key])) !== JSON.stringify(normalize(pythonValue[key]))) {
				failures.push(`TypeScript/Python site loader mismatch at ${key}`);
			}
		}
	} catch (error) {
		failures.push(`cannot compare site loader JSON: ${error instanceof Error ? error.message : String(error)}`);
	}
}

const temporaryRoot = mkdtempSync(resolve(tmpdir(), "sure-public-export-"));
const exportRoot = resolve(temporaryRoot, "tree");
const repositoryStatus = run("git", ["status", "--porcelain=v1", "--untracked-files=all"]);
const repositoryDirty = repositoryStatus.status !== 0 || repositoryStatus.stdout.trim().length > 0;
try {
	const policyRoot = resolve(temporaryRoot, "policy-precedence");
	mkdirSync(resolve(policyRoot, "config"), { recursive: true });
	copyFileSync("config/site.example.yaml", resolve(policyRoot, "config/site.local.yaml"));
	copyFileSync("config/site.example.yaml", resolve(policyRoot, "config/site.bundled.yaml"));
	const precedence = run(
		"node",
		[
			"--import",
			"tsx",
			"-e",
			'import("./sure/site/loader.ts").then(({resolveSitePolicy}) => console.log(resolveSitePolicy({repositoryRoot: process.argv[1], environment: {}})?.source))',
			policyRoot,
		],
	);
	if (precedence.status !== 0 || precedence.stdout.trim() !== "bundled") {
		failures.push("bundled policy did not take precedence over local policy");
	}
	const invalidPolicy = resolve(temporaryRoot, "invalid-policy.yaml");
	writeFileSync(invalidPolicy, `${readFileSync("config/site.example.yaml", "utf8")}unknown_field: true\n`);
	const invalidTypescript = run("npm", ["run", "--silent", "sure:site-info"], {
		env: { ...process.env, SURE_SITE_POLICY: invalidPolicy },
	});
	const invalidPython = run("python3", ["sure/site/loader.py"], {
		env: { ...process.env, SURE_SITE_POLICY: invalidPolicy },
	});
	for (const [name, completed] of [
		["TypeScript", invalidTypescript],
		["Python", invalidPython],
	]) {
		if (completed.status === 0 || !completed.stderr.includes("unknown field")) {
			failures.push(`${name} loader did not reject an unknown explicit field`);
		}
	}

	const exportedScript = "scripts/export-public.mjs";
	if (existsSync(exportedScript)) {
		const exported = run("node", [exportedScript, "--output", exportRoot]);
		if (exported.error) {
			failures.push(`public export failed to start: ${exported.error.message}`);
		} else if (repositoryDirty) {
			if (exported.status === 0 || !exported.stderr.includes("requires a clean working tree")) {
				failures.push("public export did not fail closed for a dirty working tree");
			}
		} else {
			if (exported.status !== 0) failures.push((exported.stderr ?? "").trim() || (exported.stdout ?? "").trim());
			if (exported.status === 0) {
				const publicPolicy = run(
					"node",
					[
						"--import",
						"tsx",
						"-e",
						'import("./sure/site/loader.ts").then(({resolveSitePolicy}) => console.log(JSON.stringify(resolveSitePolicy({repositoryRoot: process.argv[1], environment: {}}) ?? null)))',
						exportRoot,
					],
				);
				if (publicPolicy.status !== 0 || publicPolicy.stdout.trim() !== "null") {
					failures.push("public export selected an implicit site policy");
				}
				const manifest = JSON.parse(readFileSync(resolve(exportRoot, "public-export-manifest.json"), "utf8"));
				if (manifest.schema !== "sure.public_export_manifest.v2") failures.push("public export manifest schema mismatch");
				if (manifest.projection_id !== `sha256:${manifest.tree_sha256}`) {
					failures.push("public export projection identity does not match its tree digest");
				}
				if ("source_commit" in manifest || "source_dirty" in manifest) {
					failures.push("public export manifest exposes private source state");
				}
				if (!manifest.files.every((entry) => typeof entry.mode === "string")) {
					failures.push("public export manifest omits Git modes");
				}
				if (existsSync(resolve(exportRoot, "private"))) failures.push("public export contains the private overlay");
				const resolver = "sure/skills/sure_eval/scripts/resolve_model_dir.py";
				const publicHelp = run("python3", [resolver, "--help"], { cwd: exportRoot });
				if (publicHelp.status !== 0) failures.push("public resource CLI help failed without a site policy");
				const publicResource = run("python3", [resolver, "--model", "missing-model"], { cwd: exportRoot });
				if (
					publicResource.status === 0 ||
					!publicResource.stderr.includes("README.md#publicself-hosted-site-policy") ||
					!publicResource.stderr.includes("docs/site-configuration.md")
				) {
					failures.push("public resource command did not fail with site-configuration guidance");
				}
			}
		}
	}
} finally {
	rmSync(temporaryRoot, { recursive: true, force: true });
}

const invalidExplicit = run("npm", ["run", "--silent", "sure:site-info"], {
	env: { ...process.env, SURE_SITE_POLICY: "relative.yaml" },
});
if (invalidExplicit.status === 0 || !invalidExplicit.stderr.includes("must be an absolute path")) {
	failures.push("invalid explicit site policy did not fail closed");
}

if (failures.length > 0) {
	console.error("Site boundary check failed:");
	for (const failure of failures) console.error(`  ${failure}`);
	process.exit(1);
}
console.log("ok   site boundary: private dependency, loader parity, fail-closed, and public export");
