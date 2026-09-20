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

// No `which` / `where`: asking the binary for its own version is the one probe that
// behaves the same on Linux, macOS and Windows. Same shape as commandExists() in
// packages/coding-agent/src/utils/tools-manager.ts, so no shell either; a Windows
// tool installed only as a .cmd shim reads as missing, which is a warning here.
function commandVersion(command) {
	try {
		return execFileSync(command, ["--version"], {
			encoding: "utf8",
			stdio: ["ignore", "pipe", "pipe"],
			timeout: 15_000,
		})
			.split(/\r?\n/)[0]
			.trim();
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
	"sure/skills/sure_approve/sure.skill.json",
	"sure/skills/sure_onboard/sure.skill.json",
	"sure/skills/sure_trans/sure.skill.json",
	"sure/skills/sure_infer/sure.skill.json",
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
	for (const relPath of ["scripts", "fixtures"]) {
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

const uvVersion = commandVersion("uv");
if (uvVersion) {
	pass("uv", uvVersion);
} else {
	warn(
		"uv",
		'not found on PATH; the harness, evaluation and model runtimes are materialized with uv. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh (Linux/macOS) or powershell -c "irm https://astral.sh/uv/install.ps1 | iex" (Windows)',
	);
}

const gitVersion = commandVersion("git");
if (gitVersion) {
	pass("git", gitVersion);
} else {
	warn("git", "not found on PATH; cloning, submodules and the public export all need it");
}

// Command names and fallback wording follow TOOLS in
// packages/coding-agent/src/utils/tools-manager.ts, including Debian's fdfind.
for (const [label, commands, fallbackNote] of [
	["rg", ["rg"], "the grep tool falls back to a slower built-in search"],
	["fd", ["fd", "fdfind"], "file autocomplete and the find tool fall back to a slower built-in scan"],
]) {
	let found;
	for (const command of commands) {
		found = commandVersion(command);
		if (found) break;
	}
	if (found) {
		pass(label, found);
	} else {
		warn(label, `not found on PATH; ${fallbackNote}. Install it for faster searches`);
	}
}

if (process.platform === "win32") {
	// Same search order as getShellConfig() in packages/coding-agent/src/utils/shell.ts.
	const bashCandidates = [];
	if (process.env.ProgramFiles) bashCandidates.push(join(process.env.ProgramFiles, "Git", "bin", "bash.exe"));
	if (process.env["ProgramFiles(x86)"])
		bashCandidates.push(join(process.env["ProgramFiles(x86)"], "Git", "bin", "bash.exe"));
	const gitBash =
		bashCandidates.find((candidate) => existsSync(candidate)) ?? (commandVersion("bash") ? "bash" : undefined);
	if (gitBash) {
		pass("bash for the agent", gitBash);
	} else {
		warn(
			"bash for the agent",
			"no bash found; pi's bash tool and the skills' .sh templates need Git for Windows (https://git-scm.com/download/win) or a shellPath in settings.json",
		);
	}
}

try {
	// Same entry as npm run sure:site-info, so the doctor reports whatever the loader
	// picked rather than second-guessing the search order.
	const siteInfo = JSON.parse(
		execFileSync(process.execPath, ["--import", "tsx", join(root, "scripts", "sure-site-info.ts"), "--json"], {
			cwd: root,
			encoding: "utf8",
			stdio: ["ignore", "pipe", "pipe"],
			timeout: 60_000,
		}),
	);
	if (siteInfo.configured) {
		pass("site policy", `source: ${siteInfo.source}; path: ${siteInfo.path}`);
	} else {
		warn("site policy", "not configured; commands that need site storage will refuse until one is in place");
	}
} catch (error) {
	// sure-site-info.ts prints the loader's own message on stderr; error.message only
	// carries the command line.
	const stderr = typeof error?.stderr === "string" ? error.stderr.trim() : "";
	const detail = stderr || (error instanceof Error ? error.message : String(error));
	warn("site policy", `could not be read: ${detail.split(/\r?\n/)[0]}`);
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
	const engineFilesReady =
		existsSync(defaultEnginePath) && existsSync(join(defaultEnginePath, "pyproject.toml"));
	const engineHead = engineFilesReady
		? runGit(["-c", `safe.directory=${defaultEnginePath}`, "rev-parse", "HEAD"], defaultEnginePath)
		: undefined;

	if (!existsSync(gitmodulesPath) || !indexedCommit) {
		const runtimeSpec = readJson(join(root, "sure", "runtime", "evaluation", "runtime.json"));
		const expectedCommit =
			typeof runtimeSpec?.engine_commit === "string" ? runtimeSpec.engine_commit : undefined;
		if (!engineFilesReady || !engineHead) {
			warn(
				"sure-evaluation checkout",
				"missing or not a readable Git checkout at sure/external/sure-evaluation",
			);
		} else if (expectedCommit && engineHead !== expectedCommit) {
			warn(
				"sure-evaluation checkout",
				`standalone checkout ${engineHead.slice(0, 12)} differs from runtime lock ${expectedCommit.slice(0, 12)}`,
			);
		} else {
			pass(
				"sure-evaluation checkout",
				`standalone Git checkout @ ${engineHead.slice(0, 12)}; runtime lock matches`,
			);
		}
	} else if (!engineFilesReady) {
		warn(
			"sure-evaluation submodule",
			"not initialized; run git submodule update --init --recursive",
		);
	} else {
		const headCommit = engineHead;
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
		`missing ${datasetRoot}; /sure_infer needs sure_benchmark/jsonl. Link it or set SURE_EVAL_DATASETS_ROOT`,
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
