#!/usr/bin/env node
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
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

const home = process.env.HOME;
if (home) {
	const authPath = join(home, ".pi", "agent", "auth.json");
	const modelsPath = join(home, ".pi", "agent", "models.json");
	if (existsSync(authPath)) {
		pass("Pi auth", `${authPath} exists`);
	} else {
		warn("Pi auth", "missing ~/.pi/agent/auth.json; /sure_init can create or update provider auth");
	}
	if (existsSync(modelsPath)) {
		pass("Pi models", `${modelsPath} exists`);
	} else {
		warn("Pi models", "missing ~/.pi/agent/models.json; create it when using an OpenAI-compatible API gateway");
	}
}

if (existsSync(join(root, "sure", "external", "sure-evaluation")) || process.env.SURE_EVALUATION_HOME) {
	pass("sure-evaluation", process.env.SURE_EVALUATION_HOME ?? "sure/external/sure-evaluation");
} else {
	warn("sure-evaluation", "not configured; needed before /sure_eval, not required for /sure_feed or /sure_onboard");
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
