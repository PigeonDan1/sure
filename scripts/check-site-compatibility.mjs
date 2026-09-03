#!/usr/bin/env node
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { legacyValueDifferences, schemaCompatibilityDifferences } from "./site-compatibility-rules.mjs";

function run(command, args, options = {}) {
	return spawnSync(command, args, { encoding: "utf8", ...options });
}

function git(args) {
	const completed = run("git", args);
	if (completed.status !== 0) throw new Error(completed.stderr.trim() || `git ${args.join(" ")} failed`);
	return completed.stdout;
}

function normalized(value, dropDescriptions = false) {
	if (Array.isArray(value)) return value.map((item) => normalized(item, dropDescriptions));
	if (typeof value !== "object" || value === null) return value;
	return Object.fromEntries(
		Object.keys(value)
			.filter((name) => !dropDescriptions || name !== "description")
			.sort()
			.map((name) => [name, normalized(value[name], dropDescriptions)]),
	);
}

function digest(value) {
	return createHash("sha256").update(value).digest("hex");
}

function listedAt(commit, prefix) {
	return git(["ls-tree", "-r", "--name-only", commit, "--", prefix]).trim().split("\n").filter(Boolean).sort();
}

function listedNow(prefix) {
	return git(["ls-files", "--cached", "--others", "--exclude-standard", "--", prefix])
		.trim()
		.split("\n")
		.filter((path) => path && existsSync(path))
		.sort();
}

function baselineFile(commit, path) {
	const completed = spawnSync("git", ["show", `${commit}:${path}`]);
	if (completed.status !== 0) throw new Error(completed.stderr.toString().trim() || `cannot read ${commit}:${path}`);
	return completed.stdout;
}

const reportIndex = process.argv.indexOf("--report");
const reportPath = reportIndex >= 0 && process.argv[reportIndex + 1] ? resolve(process.argv[reportIndex + 1]) : undefined;
const baselinePath = resolve("private/site/tests/behavior-baseline.json");
const policySnapshotPath = resolve("private/site/tests/legacy-site-policy.json");

if (!existsSync(baselinePath) || !existsSync(policySnapshotPath)) {
	if (reportPath) {
		mkdirSync(dirname(reportPath), { recursive: true });
		writeFileSync(
			reportPath,
			`${JSON.stringify({ schema: "sure.site_compatibility_report.v1", distribution: "public", skipped: true }, null, 2)}\n`,
		);
	}
	console.log("ok   site compatibility: public distribution has no private legacy baseline");
	process.exit(0);
}

const baseline = JSON.parse(readFileSync(baselinePath, "utf8"));
const expectedPolicy = JSON.parse(readFileSync(policySnapshotPath, "utf8"));
const checks = [];
const failures = [];
let policySha256 = null;
let candidateEvaluationSubmoduleCommit = null;

function record(id, category, ok, detail, input = undefined) {
	checks.push({ id, category, status: ok ? "pass" : "fail", detail, ...(input === undefined ? {} : { input }) });
	if (!ok) failures.push(`${id}: ${detail}`);
}

try {
	git(["cat-file", "-e", `${baseline.baseline_commit}^{commit}`]);
	record("baseline-commit", "identity", true, baseline.baseline_commit);
} catch (error) {
	record("baseline-commit", "identity", false, error instanceof Error ? error.message : String(error));
}

if (failures.length === 0) {
	const manifestPaths = [
		"sure/skills/sure_feed/sure.skill.json",
		"sure/skills/sure_onboard/sure.skill.json",
		"sure/skills/sure_eval/sure.skill.json",
		"sure/skills/sure_reval/sure.skill.json",
	];
	const manifestDifferences = manifestPaths.flatMap((path) => {
		if (!existsSync(path)) return [`${path} is missing`];
		const before = normalized(JSON.parse(baselineFile(baseline.baseline_commit, path).toString("utf8")), true);
		const after = normalized(JSON.parse(readFileSync(path, "utf8")), true);
		return legacyValueDifferences(before, after, "$", {
			orderedArray: (valuePath) => valuePath.startsWith("$.hooks.") || valuePath.startsWith("$.ui."),
		}).map((difference) => `${path}: ${difference}`);
	});
	record(
		"command-state-manifests",
		"state",
		manifestDifferences.length === 0,
		manifestDifferences.length === 0 ? `${manifestPaths.length} legacy manifests remain compatible` : manifestDifferences.join("; "),
	);

	const beforeSchemas = listedAt(baseline.baseline_commit, "sure/skills").filter((path) => path.includes("/schemas/") && path.endsWith(".json"));
	const afterSchemas = listedNow("sure/skills").filter((path) => path.includes("/schemas/") && path.endsWith(".json"));
	const schemaDifferences = [];
	for (const path of beforeSchemas) {
		if (!afterSchemas.includes(path)) {
			schemaDifferences.push(`${path} is missing`);
			continue;
		}
		const before = JSON.parse(baselineFile(baseline.baseline_commit, path).toString("utf8"));
		const after = JSON.parse(readFileSync(path, "utf8"));
		for (const difference of schemaCompatibilityDifferences(before, after)) {
			schemaDifferences.push(`${path}: ${difference}`);
		}
	}
	record(
		"artifact-schemas",
		"artifact",
		schemaDifferences.length === 0,
		schemaDifferences.length === 0
			? `${beforeSchemas.length} legacy schemas remain compatible; ${afterSchemas.length - beforeSchemas.length} additions allowed`
			: schemaDifferences.join("; "),
	);

	const candidateGitlink = git(["rev-parse", "HEAD:sure/external/sure-evaluation"]).trim();
	candidateEvaluationSubmoduleCommit = candidateGitlink;
	const checkoutGitlink = git(["-C", "sure/external/sure-evaluation", "rev-parse", "HEAD"]).trim();
	const gitlinkMatches = candidateGitlink === checkoutGitlink;
	record(
		"evaluation-submodule",
		"runtime",
		gitlinkMatches,
		gitlinkMatches ? `checkout matches candidate gitlink ${candidateGitlink}` : `candidate=${candidateGitlink} checkout=${checkoutGitlink}`,
	);
}

const siteInfo = run("node", [
	"--import",
	"tsx",
	"-e",
	'import("./sure/site/loader.ts").then(({resolveSitePolicy}) => console.log(JSON.stringify(resolveSitePolicy() ?? null)))',
]);
if (siteInfo.status !== 0) {
	record("bundled-policy", "policy", false, siteInfo.stderr.trim() || "site policy lookup failed");
} else {
	try {
		const actual = JSON.parse(siteInfo.stdout);
		policySha256 = actual?.sha256 ?? null;
		const policyDifferences = actual?.source === "bundled" ? legacyValueDifferences(expectedPolicy, actual.policy) : ["bundled policy is not selected"];
		record(
			"bundled-policy",
			"policy",
			policyDifferences.length === 0,
			policyDifferences.length === 0 ? `legacy values retained, sha256 ${policySha256}` : policyDifferences.join("; "),
		);
	} catch (error) {
		record("bundled-policy", "policy", false, error instanceof Error ? error.message : String(error));
	}
}

const testRoot = mkdtempSync(resolve(tmpdir(), "sure-compatibility-"));
try {
	const forbiddenRoot = expectedPolicy.storage.forbidden_output_roots[0];
	const outside = join(testRoot, "jobs", "job-1234");
	const forbiddenOutput = join(forbiddenRoot, "results", "job");
	const outputProbe = run("node", [
		"--import",
		"tsx",
		"-e",
		'import("./packages/coding-agent/src/core/sure/output-dir.ts").then(({resolveOutputDir}) => console.log(JSON.stringify([resolveOutputDir(`output_dir=${process.argv[1]}`), resolveOutputDir(`output_dir=${process.argv[2]}`)])))',
		forbiddenOutput,
		outside,
	]);
	if (outputProbe.status !== 0) {
		record("output-path-decisions", "failure", false, outputProbe.stderr.trim());
	} else {
		const [forbidden, allowed] = JSON.parse(outputProbe.stdout);
		const matches = forbidden.ok === false && String(forbidden.error).includes(forbiddenRoot) && allowed.ok === true && allowed.dir === outside;
		record("output-path-decisions", "failure", matches, matches ? "forbidden and allowed decisions match" : "output decision differs", { forbiddenOutput, outside });
	}

	const datasetProbe = run("python3", [
		"-c",
		"import sys; sys.path.insert(0, 'sure/skills/sure_eval/scripts'); from sure_eval.datasets.source_resolver import accepted_source_root; print(accepted_source_root())",
	]);
	// Get the single configured root from the key-value map
	const sourceRoots = expectedPolicy.datasets.allowed_source_roots;
	const expectedDatasetRoot = Array.isArray(sourceRoots) ? sourceRoots[0] : Object.values(sourceRoots)[0];
	const datasetMatches = datasetProbe.status === 0 && datasetProbe.stdout.trim() === expectedDatasetRoot;
	record("dataset-root", "path", datasetMatches, datasetMatches ? expectedDatasetRoot : datasetProbe.stderr.trim() || datasetProbe.stdout.trim());

	const explicitConfig = join(testRoot, "explicit.yaml");
	const environmentConfig = join(testRoot, "environment.yaml");
	const datasetsRoot = join(testRoot, "datasets");
	mkdirSync(join(datasetsRoot, "sure_benchmark", "jsonl"), { recursive: true });
	writeFileSync(explicitConfig, "data: {}\n");
	writeFileSync(environmentConfig, "data: {}\n");
	const configProbe = run(
		"python3",
		[
			"-c",
			"import json, os, sys; from pathlib import Path; sys.path.insert(0, 'sure/skills/sure_eval/scripts'); import resolve_eval_input as r; explicit=Path(sys.argv[1]); environment=Path(sys.argv[2]); run_dir=Path(sys.argv[3]); os.environ['SURE_EVAL_CONFIG']=str(environment); first=r._write_harness_config(run_dir=run_dir,config_path=str(explicit)); second=r._write_harness_config(run_dir=run_dir,config_path=None); os.environ.pop('SURE_EVAL_CONFIG'); third=r._write_harness_config(run_dir=run_dir,config_path=None); print(json.dumps([first.name,second.name,third.name]))",
			explicitConfig,
			environmentConfig,
			join(testRoot, "config-run"),
		],
		{ env: { ...process.env, SURE_EVAL_DATASETS_ROOT: datasetsRoot } },
	);
	let actualPrecedence;
	try {
		actualPrecedence = JSON.parse(configProbe.stdout);
	} catch {
		actualPrecedence = null;
	}
	const expectedPrecedence = ["explicit.yaml", "environment.yaml", "_harness_config.yaml"];
	const precedenceMatches = configProbe.status === 0 && JSON.stringify(actualPrecedence) === JSON.stringify(expectedPrecedence);
	record("evaluation-config-precedence", "command", precedenceMatches, precedenceMatches ? "config argument > SURE_EVAL_CONFIG > submodule default" : configProbe.stderr.trim() || configProbe.stdout.trim(), expectedPrecedence);
} finally {
	rmSync(testRoot, { recursive: true, force: true });
}

const categoryFailures = (category) => checks.filter((check) => check.category === category && check.status === "fail").length;
const report = {
	schema: "sure.site_compatibility_report.v1",
	baseline_commit: baseline.baseline_commit,
	candidate_commit: git(["rev-parse", "HEAD"]).trim(),
	candidate_dirty: git(["status", "--porcelain", "--untracked-files=all"]).trim().length > 0,
	evaluation_submodule_commit: candidateEvaluationSubmoduleCommit,
	baseline_evaluation_submodule_commit: baseline.evaluation_submodule_commit,
	site_policy_sha256: policySha256,
	normalization_rules: baseline.normalization_rules,
	checks,
	summary: {
		semantic_differences: failures.length,
		command_trace_differences: categoryFailures("command"),
		state_transition_differences: categoryFailures("state"),
		artifact_contract_differences: categoryFailures("artifact"),
		failure_semantic_differences: categoryFailures("failure"),
		allowed_nondeterministic_differences: [],
	},
};

if (reportPath) {
	mkdirSync(dirname(reportPath), { recursive: true });
	writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
}

if (failures.length > 0) {
	console.error("Site compatibility check failed:");
	for (const failure of failures) console.error(`  ${failure}`);
	process.exit(1);
}
console.log(`ok   site compatibility: ${checks.length} baseline/candidate checks, semantic differences 0`);
if (reportPath) console.log(`ok   site compatibility report: ${reportPath} (${digest(JSON.stringify(report))})`);
