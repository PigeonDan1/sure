#!/usr/bin/env node
import { createHash } from "node:crypto";
import {
	copyFileSync,
	existsSync,
	lstatSync,
	mkdirSync,
	readFileSync,
	readlinkSync,
	symlinkSync,
	writeFileSync,
} from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { parse } from "yaml";

function fail(message) {
	console.error(message);
	process.exit(1);
}

function git(args, encoding = "utf8") {
	const result = spawnSync("git", args, { encoding });
	if (result.status !== 0) fail(result.stderr?.trim() || `git ${args.join(" ")} failed`);
	return result.stdout;
}

function globExpression(pattern) {
	let expression = "";
	for (let index = 0; index < pattern.length; index++) {
		const character = pattern[index];
		if (character === "*" && pattern[index + 1] === "*") {
			if (pattern[index + 2] === "/") {
				expression += "(?:.*/)?";
				index += 2;
			} else {
				expression += ".*";
				index++;
			}
		} else if (character === "*") {
			expression += "[^/]*";
		} else if (character === "?") {
			expression += "[^/]";
		} else {
			expression += character.replace(/[|\\{}()[\]^$+?.]/g, "\\$&");
		}
	}
	return new RegExp(`^${expression}$`);
}

function patterns(values, name) {
	if (!Array.isArray(values) || values.some((value) => typeof value !== "string")) {
		fail(`public-export.yaml ${name} must be a string list`);
	}
	return values;
}

const outputIndex = process.argv.indexOf("--output");
const outputArgument = outputIndex >= 0 ? process.argv[outputIndex + 1] : undefined;
if (!outputArgument) fail("usage: npm run public:export -- --output /absolute/empty/path");
const outputRoot = resolve(outputArgument);
if (!isAbsolute(outputArgument)) fail("public export output must be an absolute path");
if (existsSync(outputRoot)) fail(`public export output already exists: ${outputRoot}`);

const repositoryRoot = resolve(git(["rev-parse", "--show-toplevel"]).trim());
const sourceCommit = git(["rev-parse", "HEAD"]).trim();
const sourceDirty = git(["status", "--porcelain", "--untracked-files=all"]).trim().length > 0;
const configuration = parse(readFileSync(resolve(repositoryRoot, "public-export.yaml"), "utf8"));
if (configuration?.version !== 1) fail("public-export.yaml version must be 1");
const excluded = patterns(configuration.exclude, "exclude").map(globExpression);
const forbiddenPaths = patterns(configuration.forbidden_paths, "forbidden_paths").map(globExpression);
const forbiddenContent = patterns(configuration.forbidden_content, "forbidden_content").map(
	(pattern) => [pattern, new RegExp(pattern, "i")],
);
const listed = git(["ls-files", "--cached", "--others", "--exclude-standard", "-z"], "buffer")
	.toString("utf8")
	.split("\0")
	.filter(Boolean)
	.sort();

mkdirSync(outputRoot, { recursive: false });
const manifestFiles = [];
const failures = [];
for (const path of listed) {
	if (excluded.some((expression) => expression.test(path))) continue;
	if (forbiddenPaths.some((expression) => expression.test(path))) {
		failures.push(`${path}: forbidden public path`);
		continue;
	}
	const source = resolve(repositoryRoot, path);
	if (!existsSync(source)) continue;
	const stat = lstatSync(source);
	if (stat.isDirectory()) {
		const commit = spawnSync("git", ["-C", source, "rev-parse", "HEAD"], { encoding: "utf8" });
		if (commit.status === 0) {
			mkdirSync(resolve(outputRoot, path), { recursive: true });
			manifestFiles.push({ path, type: "gitlink", sha256: commit.stdout.trim() });
		}
		continue;
	}
	const target = resolve(outputRoot, path);
	mkdirSync(dirname(target), { recursive: true });
	if (stat.isSymbolicLink()) {
		const link = readlinkSync(source);
		if (isAbsolute(link) || !resolve(dirname(source), link).startsWith(`${repositoryRoot}/`)) {
			failures.push(`${path}: public symlink must stay inside the repository`);
			continue;
		}
		for (const [pattern, expression] of forbiddenContent) {
			if (expression.test(link)) failures.push(`${path}: symlink contains forbidden public content ${pattern}`);
		}
		symlinkSync(link, target);
		manifestFiles.push({ path, type: "symlink", sha256: createHash("sha256").update(link).digest("hex") });
		continue;
	}
	const content = readFileSync(source);
	if (path !== "public-export.yaml" && !content.includes(0)) {
		const text = content.toString("utf8");
		for (const [pattern, expression] of forbiddenContent) {
			if (expression.test(text)) failures.push(`${path}: contains forbidden public content ${pattern}`);
		}
	}
	copyFileSync(source, target);
	manifestFiles.push({ path, type: "file", sha256: createHash("sha256").update(content).digest("hex") });
}

if (failures.length > 0) {
	console.error("Public export failed:");
	for (const failure of failures) console.error(`  ${failure}`);
	process.exit(1);
}

const manifest = {
	schema: "sure.public_export_manifest.v1",
	source_commit: sourceCommit,
	source_dirty: sourceDirty,
	tree_sha256: createHash("sha256").update(JSON.stringify(manifestFiles)).digest("hex"),
	files: manifestFiles,
};
writeFileSync(resolve(outputRoot, "public-export-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
console.log(`ok   public export: ${manifestFiles.length} entries from ${sourceCommit}`);
