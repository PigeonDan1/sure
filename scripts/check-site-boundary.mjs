#!/usr/bin/env node
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { parse } from "yaml";

function run(command, args, options = {}) {
	// npm resolves to npm.cmd on Windows, which spawnSync cannot execute
	// directly; without a shell it returns no stderr and every caller crashes.
	const shell = process.platform === "win32" && command === "npm";
	const completed = spawnSync(command, args, { encoding: "utf8", shell, ...options });
	// A program that never started (python3 is absent wherever Python is only
	// `python`) leaves status, stdout and stderr null, and every caller reads
	// .stderr or .stdout: name the missing program as an ordinary non-zero
	// result instead of dying with a TypeError. error stays set so the callers
	// that already branch on it keep their own message.
	if (completed.error) {
		return {
			...completed,
			status: 127,
			stdout: "",
			stderr: `${command} is required for check:site-boundary but could not be started (${completed.error.message}); install it and retry`,
		};
	}
	return completed;
}

const failures = [];

// The public export exception list is closed (see public-export.yaml): every
// private asset must live under private/ and new exclusions must be rejected.
const allowedExclusions = new Set([
	"private/site/**",
	"docs/internal/**",
	"config/site.bundled.yaml",
	".gitlab-ci.yml",
]);
if (existsSync("public-export.yaml")) {
	const exportText = readFileSync("public-export.yaml", "utf8");
	const exportConfiguration = parse(exportText);
	for (const entry of exportConfiguration?.exclude ?? []) {
		if (!allowedExclusions.has(entry)) {
			failures.push(`public-export.yaml exclude entry is not on the approved exception list: ${entry}`);
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

	// A fresh machine has none of the default roots yet: sure:site-check must
	// say so and still exit 0, or the zero-config default is unusable.
	const missingRootHome = resolve(temporaryRoot, "empty-home");
	const missingRootCheck = run("node", ["--import", "tsx", "scripts/sure-site-check.ts"], {
		env: {
			...process.env,
			SURE_SITE_POLICY: resolve("config/site.default.yaml"),
			HOME: missingRootHome,
			USERPROFILE: missingRootHome,
		},
	});
	if (missingRootCheck.status !== 0) {
		failures.push(`sure:site-check failed on a policy whose roots do not exist yet: ${missingRootCheck.stderr.trim()}`);
	} else if (!missingRootCheck.stdout.includes("does not exist yet")) {
		failures.push("sure:site-check did not warn about a site root that does not exist yet");
	} else if (!missingRootCheck.stdout.includes("ok   site policy:")) {
		failures.push("sure:site-check dropped its ok line while warning about missing roots");
	}

	// The other half of that warning: a root that exists but cannot be
	// written. sure-site-check.ts probes it by creating a directory rather
	// than by asking accessSync, so a regular file is a root that no
	// platform lets it write into, and the warning must still exit 0.
	const unwritableHome = resolve(temporaryRoot, "unwritable-home");
	const unwritableRoot = `${unwritableHome.replaceAll("\\", "/")}/.sure/runtime`;
	mkdirSync(dirname(unwritableRoot), { recursive: true });
	writeFileSync(unwritableRoot, "");
	const unwritableCheck = run("node", ["--import", "tsx", "scripts/sure-site-check.ts"], {
		env: {
			...process.env,
			SURE_SITE_POLICY: resolve("config/site.default.yaml"),
			HOME: unwritableHome,
			USERPROFILE: unwritableHome,
		},
	});
	if (unwritableCheck.status !== 0) {
		failures.push(`sure:site-check failed on a root that exists but is not writable: ${unwritableCheck.stderr.trim()}`);
	} else if (!unwritableCheck.stdout.includes(`warn runtime root is not writable: ${unwritableRoot}`)) {
		failures.push("sure:site-check did not warn that an existing site root is not writable");
	}

	// The parity run above diffs whatever policy this checkout resolves, and a
	// developer's config/site.local.yaml shadows the shipped default, the only
	// policy that carries ${HOME} and ${REPO}. Diff the twins on it directly,
	// from a home directory named like a replacement pattern: $& and $$ are
	// literal text to str.replace in loader.py and must stay literal in
	// loader.ts, where String.replaceAll would otherwise expand them.
	const tokenHome = resolve(temporaryRoot, "home-$&-$$-tokens");
	const tokenEnvironment = {
		...process.env,
		SURE_SITE_POLICY: resolve("config/site.default.yaml"),
		HOME: tokenHome,
		USERPROFILE: tokenHome,
	};
	const tokenTypescript = run(
		"node",
		[
			"--import",
			"tsx",
			"-e",
			'import("./sure/site/loader.ts").then(({resolveSitePolicy}) => console.log(JSON.stringify(resolveSitePolicy())))',
		],
		{ env: tokenEnvironment },
	);
	const tokenPython = run("python3", ["sure/site/loader.py"], { env: tokenEnvironment });
	if (tokenTypescript.status !== 0 || tokenPython.status !== 0) {
		failures.push(`site loaders failed on the shipped default policy: ${tokenTypescript.stderr.trim()}${tokenPython.stderr.trim()}`);
	} else {
		try {
			if (JSON.parse(tokenTypescript.stdout).sha256 !== JSON.parse(tokenPython.stdout).sha256) {
				failures.push("TypeScript/Python site loader mismatch at ${HOME}/${REPO} expansion");
			}
		} catch (error) {
			failures.push(`cannot compare site loader JSON: ${error instanceof Error ? error.message : String(error)}`);
		}
	}
	const exportedScript = "scripts/export-public.mjs";
	if (existsSync(exportedScript)) {
		const exported = run("node", [exportedScript, "--output", exportRoot]);
		if (exported.error) {
			failures.push(`public export failed to start: ${exported.error.message}`);
		} else if (repositoryDirty) {
			console.log("warn public export probes skipped: working tree is dirty");
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
				let publicResolved = null;
				if (publicPolicy.status === 0) {
					try {
						publicResolved = JSON.parse(publicPolicy.stdout);
					} catch {
						publicResolved = null;
					}
				}
				if (publicResolved?.source !== "default") {
					failures.push("public export did not select the repository default site policy");
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
				const resolver = "sure/skills/sure_infer/scripts/resolve_model_dir.py";
				const publicHelp = run("python3", [resolver, "--help"], { cwd: exportRoot });
				if (publicHelp.status !== 0) failures.push("public resource CLI help failed under the default site policy");
				// The gate that matters on a personal machine: a policy resolves,
				// but nothing has been approved yet, so the command still refuses.
				// resolve_model_dir.py prints the root through Path.resolve(), so the
				// policy root needs the same canonicalisation before the two can be
				// compared: a symlinked home, or a Windows home's on-disk casing,
				// differs from os.homedir(). Only the home prefix may be realpath'd --
				// the approved root is the one path this probe knows is absent. The
				// replacement is a function: $& in a home would be a replace pattern.
				const policyHome = homedir().replaceAll("\\", "/");
				const realHome = realpathSync.native(homedir()).replaceAll("\\", "/");
				const approvedRoot = String(publicResolved?.policy?.storage?.approved_models_roots?.[0] ?? "").replace(
					policyHome,
					() => realHome,
				);
				const publicResource = run("python3", [resolver, "--model", "missing-model"], { cwd: exportRoot });
				const resourceStderr = (publicResource.stderr ?? "").replaceAll("\\", "/");
				if (
					publicResource.status !== 1 ||
					!resourceStderr.includes("approved model is not ready under") ||
					approvedRoot === "" ||
					!resourceStderr.includes(approvedRoot)
				) {
					failures.push("public resource command did not fail closed on an empty approved model root");
				}
				// Deleting the shipped default must still point the user at the docs.
				const defaultPolicyPath = resolve(exportRoot, "config/site.default.yaml");
				const parkedPolicyPath = `${defaultPolicyPath}.parked`;
				if (existsSync(defaultPolicyPath)) {
					renameSync(defaultPolicyPath, parkedPolicyPath);
					try {
						const unconfigured = run("python3", [resolver, "--model", "missing-model"], { cwd: exportRoot });
						if (
							unconfigured.status === 0 ||
							!unconfigured.stderr.includes("README.md#publicself-hosted-site-policy") ||
							!unconfigured.stderr.includes("docs/site-configuration.md")
						) {
							failures.push("removing the default site policy did not restore the site-configuration guidance");
						}
					} finally {
						try {
							renameSync(parkedPolicyPath, defaultPolicyPath);
						} catch {
							// Deliberately ignored: the whole export tree is removed next,
							// and an aborted restore would take the failures above with it.
						}
					}
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
console.log(
	repositoryDirty
		? "ok   site boundary: private dependency, loader parity, and fail-closed"
		: "ok   site boundary: private dependency, loader parity, fail-closed, and public export",
);
