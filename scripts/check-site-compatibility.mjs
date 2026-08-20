#!/usr/bin/env node
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";

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
const baselinePath = resolve("private/aispeech/tests/behavior-baseline.json");
const policySnapshotPath = resolve("private/aispeech/tests/legacy-site-policy.json");

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

function record(id, category, ok, detail, input = undefined) {
	checks.push({ id, category, status: ok ? "pass" : "fail", detail, ...(input === undefined ? {} : { input }) });
	if (!ok) failures.push(`${id}: ${detail}`);
}

function compareExactTree(id, prefix) {
	const before = listedAt(baseline.baseline_commit, prefix);
	const after = listedNow(prefix);
	if (JSON.stringify(before) !== JSON.stringify(after)) {
		record(id, "runtime", false, `${prefix} file set changed`);
		return;
	}
	const changed = before.filter((path) => !readFileSync(path).equals(baselineFile(baseline.baseline_commit, path)));
	record(id, "runtime", changed.length === 0, changed.length === 0 ? `${before.length} files are byte-identical` : `changed: ${changed.join(", ")}`);
}

function compareExactFiles(id, paths, category) {
	const changed = paths.filter(
		(path) => !existsSync(path) || !readFileSync(path).equals(baselineFile(baseline.baseline_commit, path)),
	);
	record(id, category, changed.length === 0, changed.length === 0 ? `${paths.length} files are byte-identical` : `changed: ${changed.join(", ")}`);
}

try {
	git(["cat-file", "-e", `${baseline.baseline_commit}^{commit}`]);
	record("baseline-commit", "identity", true, baseline.baseline_commit);
} catch (error) {
	record("baseline-commit", "identity", false, error instanceof Error ? error.message : String(error));
}

if (failures.length === 0) {
	compareExactFiles(
		"command-state-manifests",
		["sure/skills/sure_feed/sure.skill.json", "sure/skills/sure_onboard/sure.skill.json", "sure/skills/sure_eval/sure.skill.json", "sure/skills/sure_reval/sure.skill.json"],
		"state",
	);
	compareExactTree("runtime-boundary", "sure/runtime");
	compareExactFiles("dependency-locks", ["package-lock.json", "packages/coding-agent/npm-shrinkwrap.json"], "runtime");

	const beforeSchemas = listedAt(baseline.baseline_commit, "sure/skills").filter((path) => path.includes("/schemas/") && path.endsWith(".json"));
	const afterSchemas = listedNow("sure/skills").filter((path) => path.includes("/schemas/") && path.endsWith(".json"));
	let schemaDetail = `${beforeSchemas.length} artifact schemas are structurally identical`;
	let schemasMatch = JSON.stringify(beforeSchemas) === JSON.stringify(afterSchemas);
	if (schemasMatch) {
		for (const path of beforeSchemas) {
			const before = normalized(JSON.parse(baselineFile(baseline.baseline_commit, path).toString("utf8")), true);
			const after = normalized(JSON.parse(readFileSync(path, "utf8")), true);
			if (JSON.stringify(before) !== JSON.stringify(after)) {
				schemasMatch = false;
				schemaDetail = `${path} changed beyond description text`;
				break;
			}
		}
	} else {
		schemaDetail = "artifact schema file set changed";
	}
	record("artifact-schemas", "artifact", schemasMatch, schemaDetail);

	const baselineGitlink = git(["rev-parse", `${baseline.baseline_commit}:sure/external/sure-evaluation`]).trim();
	const candidateGitlink = git(["rev-parse", "HEAD:sure/external/sure-evaluation"]).trim();
	const checkoutGitlink = git(["-C", "sure/external/sure-evaluation", "rev-parse", "HEAD"]).trim();
	const gitlinkMatches = [baselineGitlink, candidateGitlink, checkoutGitlink].every(
		(commit) => commit === baseline.evaluation_submodule_commit,
	);
	record(
		"evaluation-submodule",
		"runtime",
		gitlinkMatches,
		gitlinkMatches ? baseline.evaluation_submodule_commit : `baseline=${baselineGitlink} candidate=${candidateGitlink} checkout=${checkoutGitlink}`,
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
		const matches = actual?.source === "bundled" && JSON.stringify(normalized(actual.policy)) === JSON.stringify(normalized(expectedPolicy));
		record("bundled-policy", "policy", matches, matches ? `exact legacy values, sha256 ${policySha256}` : "bundled policy differs from the legacy snapshot");
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
	const expectedDatasetRoot = expectedPolicy.datasets.allowed_source_roots[0];
	const datasetMatches = datasetProbe.status === 0 && datasetProbe.stdout.trim() === expectedDatasetRoot;
	record("dataset-root", "path", datasetMatches, datasetMatches ? expectedDatasetRoot : datasetProbe.stderr.trim() || datasetProbe.stdout.trim());

	const executionProbe = run("python3", [
		"-c",
		"import json, sys; sys.path.insert(0, 'sure/skills/sure_eval/scripts'); import resolve_eval_input as r; out=[]; cases=[('auto-no-vc',None,None,False),('auto-vc',None,None,True),('explicit-local','local',None,True),('explicit-vc','vc',None,False),('legacy-local',None,'local_bash',False)];\nfor name,execution,path,available in cases:\n r._vc_available=lambda value=available:value; value=r._normalize_execution(execution,path,['local','vc']); out.append({'case':name,'planned':value['planned'],'path':value['path_planned'],'available':value['vc_available_at_resolve'],'fallback':value['fallback_allowed'],'reason':value['reason']})\nprint(json.dumps(out,sort_keys=True))",
	]);
	const expectedExecution = [
		{ case: "auto-no-vc", planned: "local", path: "local_docker", available: false, fallback: true, reason: "auto_selected_local_vc_unavailable" },
		{ case: "auto-vc", planned: "vc", path: "vc_submit", available: true, fallback: true, reason: "auto_selected_vc_available" },
		{ case: "explicit-local", planned: "local", path: "local_docker", available: true, fallback: false, reason: "user_requested_local" },
		{ case: "explicit-vc", planned: "vc", path: "vc_submit", available: false, fallback: false, reason: "user_requested_vc" },
		{ case: "legacy-local", planned: "local", path: "local_docker", available: false, fallback: false, reason: "user_requested_local" },
	];
	let actualExecution;
	try {
		actualExecution = JSON.parse(executionProbe.stdout);
	} catch {
		actualExecution = null;
	}
	const executionMatches =
		executionProbe.status === 0 && JSON.stringify(normalized(actualExecution)) === JSON.stringify(normalized(expectedExecution));
	record("execution-selection", "command", executionMatches, executionMatches ? "five legacy execution decisions match" : executionProbe.stderr.trim() || executionProbe.stdout.trim(), expectedExecution.map((item) => item.case));

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
	evaluation_submodule_commit: baseline.evaluation_submodule_commit,
	aispeech_policy_sha256: policySha256,
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
