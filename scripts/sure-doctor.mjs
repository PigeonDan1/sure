#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

const root = process.cwd();
const checks = [];

function record(level, name, detail) {
	checks.push({ level, name, detail });
}

function pass(name, detail) {
	record("pass", name, detail);
}

function warn(name, detail) {
	record("warn", name, detail);
}

function fail(name, detail) {
	record("fail", name, detail);
}

function readJson(path) {
	try {
		return JSON.parse(readFileSync(path, "utf8"));
	} catch {
		return undefined;
	}
}

function runGit(args, cwd = root) {
	try {
		return execFileSync("git", args, {
			cwd,
			encoding: "utf8",
			stdio: ["ignore", "pipe", "pipe"],
		}).trim();
	} catch {
		return undefined;
	}
}

function versionAtLeast(actual, expected) {
	const a = actual.split(".").map((part) => Number.parseInt(part, 10));
	const e = expected.split(".").map((part) => Number.parseInt(part, 10));
	for (let i = 0; i < Math.max(a.length, e.length); i += 1) {
		const av = a[i] ?? 0;
		const ev = e[i] ?? 0;
		if (av > ev) return true;
		if (av < ev) return false;
	}
	return true;
}

const pkgPath = join(root, "package.json");
const pkg = readJson(pkgPath);
if (!pkg || pkg.name !== "pi-monorepo") {
	fail("repository root", `run this from the SURE Harness repository root; current cwd is ${root}`);
} else {
	pass("repository root", root);
}

if (versionAtLeast(process.versions.node, "22.19.0")) {
	pass("node version", process.versions.node);
} else {
	fail("node version", `found ${process.versions.node}, need >=22.19.0`);
}

const requiredPaths = [
	"pi-test.sh",
	"node_modules/.bin/tsx",
	"packages/coding-agent/src/cli.ts",
	"packages/coding-agent/src/core/sure/module-loader.ts",
	"sure/skills/sure_feed/sure.skill.json",
	"sure/skills/sure_onboard/sure.skill.json",
	"sure/skills/sure_eval/sure.skill.json",
	"fixtures",
];

for (const relPath of requiredPaths) {
	const absPath = join(root, relPath);
	if (existsSync(absPath)) {
		pass(relPath, "found");
	} else {
		fail(relPath, "missing; run npm install --ignore-scripts and make sure the checkout is complete");
	}
}

try {
	const moduleLoaderPath = resolve(root, "packages/coding-agent/src/core/sure/module-loader.ts");
	const req = createRequire(moduleLoaderPath);
	for (const specifier of ["typebox", "typebox/compile", "typebox/value"]) {
		pass(`resolve ${specifier}`, req.resolve(specifier));
	}
} catch (error) {
	fail(
		"TypeBox resolution",
		`${error instanceof Error ? error.message : String(error)}; run npm install --ignore-scripts from the repository root`,
	);
}

const sparseCheckoutPath = join(root, ".git", "info", "sparse-checkout");
if (existsSync(sparseCheckoutPath)) {
	const sparseCheckout = readFileSync(sparseCheckoutPath, "utf8");
	for (const relPath of ["scripts", "fixtures", "packages/coding-agent/examples"]) {
		if (existsSync(join(root, relPath))) {
			pass(`sparse path ${relPath}`, "present");
		} else {
			warn(`sparse path ${relPath}`, `not present; add it with git sparse-checkout add ${relPath}`);
		}
	}
	if (!sparseCheckout.trim()) {
		warn("sparse checkout", "sparse-checkout file exists but is empty");
	}
}

// Mirrors the resolution order in packages/coding-agent/src/config.ts getAgentDir():
// PI_CODING_AGENT_DIR env override, else os.homedir()/.pi/agent.
const agentDir = process.env.PI_CODING_AGENT_DIR || join(homedir(), ".pi", "agent");
const authPath = join(agentDir, "auth.json");
const modelsPath = join(agentDir, "models.json");
if (existsSync(authPath)) {
	pass("Pi auth", `${authPath} exists`);
} else {
	warn("Pi auth", `missing ${authPath}; /sure_init can create or update provider auth`);
}
if (existsSync(modelsPath)) {
	pass("Pi models", `${modelsPath} exists`);
} else {
	warn("Pi models", `missing ${modelsPath}; create it when using an OpenAI-compatible API gateway`);
}

const defaultEngineRelPath = "sure/external/sure-evaluation";
const defaultEnginePath = join(root, "sure", "external", "sure-evaluation");
const engineOverride = process.env.SURE_EVALUATION_HOME;
if (engineOverride) {
	if (existsSync(engineOverride)) {
		pass("sure-evaluation override", engineOverride);
	} else {
		warn("sure-evaluation override", `${engineOverride} does not exist; unset SURE_EVALUATION_HOME or fix the path`);
	}
} else {
	const gitmodulesPath = join(root, ".gitmodules");
	const gitlink = runGit(["ls-files", "--stage", "--", defaultEngineRelPath]);
	const indexedCommit = gitlink?.startsWith("160000 ") ? gitlink.split(/\s+/)[1] : undefined;
	const moduleUrl = existsSync(gitmodulesPath)
		? runGit(["config", "--file", ".gitmodules", "--get", `submodule.${defaultEngineRelPath}.url`])
		: undefined;
	const moduleBranch = existsSync(gitmodulesPath)
		? runGit(["config", "--file", ".gitmodules", "--get", `submodule.${defaultEngineRelPath}.branch`])
		: undefined;

	if (!existsSync(gitmodulesPath) || !indexedCommit) {
		warn(
			"sure-evaluation submodule",
			"not registered as a git submodule; expected sure/external/sure-evaluation for /sure_eval and /sure_reval",
		);
	} else if (!existsSync(defaultEnginePath) || !existsSync(join(defaultEnginePath, "pyproject.toml"))) {
		warn(
			"sure-evaluation submodule",
			"not initialized; run git submodule update --init --recursive",
		);
	} else {
		const headCommit = runGit(["rev-parse", "HEAD"], defaultEnginePath);
		const commitDetail = headCommit
			? `${headCommit.slice(0, 12)}${headCommit !== indexedCommit ? `; indexed ${indexedCommit.slice(0, 12)}` : ""}`
			: `indexed ${indexedCommit.slice(0, 12)}`;
		const locationDetail = `${moduleUrl ?? defaultEngineRelPath}${moduleBranch ? ` (${moduleBranch})` : ""}`;
		if (headCommit && headCommit !== indexedCommit) {
			warn(
				"sure-evaluation submodule",
				`${locationDetail} at ${commitDetail}; commit the gitlink after verification or reset the submodule`,
			);
		} else {
			pass("sure-evaluation submodule", `${locationDetail} @ ${commitDetail}`);
		}
	}
}

const datasetRoot = process.env.SURE_EVAL_DATASETS_ROOT
	? join(process.env.SURE_EVAL_DATASETS_ROOT, "sure_benchmark", "jsonl")
	: join(root, "data", "datasets", "sure_benchmark", "jsonl");
if (existsSync(datasetRoot)) {
	const jsonlCount = readdirSync(datasetRoot).filter((name) => name.endsWith(".jsonl")).length;
	if (jsonlCount > 0) {
		pass("sure-eval datasets", `${datasetRoot} (${jsonlCount} jsonl files)`);
	} else {
		warn("sure-eval datasets", `${datasetRoot} exists but contains no .jsonl files`);
	}
} else {
	warn(
		"sure-eval datasets",
		`missing ${datasetRoot}; /sure_eval needs sure_benchmark/jsonl. Link it or set SURE_EVAL_DATASETS_ROOT`,
	);
}

const marks = {
	pass: "PASS",
	warn: "WARN",
	fail: "FAIL",
};

for (const check of checks) {
	console.log(`${marks[check.level]} ${check.name}: ${check.detail}`);
}

const failed = checks.filter((check) => check.level === "fail");
const warned = checks.filter((check) => check.level === "warn");
console.log("");
console.log(`SURE doctor summary: ${failed.length} failed, ${warned.length} warning(s), ${checks.length} total.`);

if (failed.length > 0) {
	console.log("");
	console.log("Recommended first fix:");
	console.log("  npm install --ignore-scripts");
	console.log("  npm run sure:doctor");
	process.exit(1);
}
