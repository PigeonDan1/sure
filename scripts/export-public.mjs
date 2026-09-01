#!/usr/bin/env node
import { createHash } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, symlinkSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { parse } from "yaml";

function fail(message) {
	console.error(message);
	process.exit(1);
}

function git(args, encoding = "utf8") {
	const result = spawnSync("git", args, { encoding, maxBuffer: 8 * 1024 * 1024 });
	if (result.status !== 0) {
		const stderr = Buffer.isBuffer(result.stderr) ? result.stderr.toString("utf8") : result.stderr;
		fail(stderr?.trim() || `git ${args.join(" ")} failed`);
	}
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

function stringList(value, name, required = false) {
	if (value === undefined && !required) return [];
	if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
		fail(`${name} must be a string list`);
	}
	return value;
}

function configuration(content, name, overlay = false) {
	const value = parse(content.toString("utf8"));
	if (typeof value !== "object" || value === null || Array.isArray(value)) fail(`${name} must be a YAML object`);
	if (value.version !== 1) fail(`${name} version must be 1`);
	const allowed = new Set(["version", "forbidden_content", "forbidden_paths", ...(overlay ? [] : ["exclude"])]);
	for (const key of Object.keys(value)) {
		if (!allowed.has(key)) fail(`${name} contains unknown field ${key}`);
	}
	return {
		exclude: overlay ? [] : stringList(value.exclude, `${name} exclude`, true),
		forbiddenContent: stringList(value.forbidden_content, `${name} forbidden_content`, !overlay),
		forbiddenPaths: stringList(value.forbidden_paths, `${name} forbidden_paths`, !overlay),
	};
}

function option(name) {
	const index = process.argv.indexOf(name);
	return index >= 0 ? process.argv[index + 1] : undefined;
}

function inside(root, path) {
	const child = relative(root, path);
	return child === "" || (!child.startsWith("..") && !isAbsolute(child));
}

const outputArgument = option("--output");
if (!outputArgument) {
	fail(
		"usage: npm run public:export -- --output /absolute/empty/path [--private-attestation-output /absolute/file.json]",
	);
}
if (!isAbsolute(outputArgument)) fail("public export output must be an absolute path");
const outputRoot = resolve(outputArgument);
if (existsSync(outputRoot)) fail(`public export output already exists: ${outputRoot}`);

const attestationArgument = option("--private-attestation-output");
if (attestationArgument && !isAbsolute(attestationArgument)) fail("private attestation output must be an absolute path");
const attestationPath = attestationArgument ? resolve(attestationArgument) : undefined;
if (attestationPath && existsSync(attestationPath)) fail(`private attestation output already exists: ${attestationPath}`);
if (attestationPath && inside(outputRoot, attestationPath)) {
	fail("private attestation output must stay outside the public export");
}

const repositoryRoot = resolve(git(["rev-parse", "--show-toplevel"]).trim());
if (attestationPath && inside(repositoryRoot, attestationPath)) {
	fail("private attestation output must stay outside the source repository");
}
const dirty = git(["-C", repositoryRoot, "status", "--porcelain=v1", "--untracked-files=all"]).trim();
if (dirty) fail("public export requires a clean working tree, including untracked files");

const indexed = git(["-C", repositoryRoot, "ls-files", "--cached", "--stage", "-z"], "buffer")
	.toString("utf8")
	.split("\0")
	.filter(Boolean)
	.map((record) => {
		const match = record.match(/^(\d{6}) ([0-9a-f]{40,64}) (\d)\t([\s\S]+)$/);
		if (!match || match[3] !== "0") fail(`cannot parse a stage-zero Git index entry: ${record}`);
		return { mode: match[1], object: match[2], path: match[4] };
	})
	.sort((left, right) => left.path.localeCompare(right.path));
const indexedByPath = new Map(indexed.map((entry) => [entry.path, entry]));

function blob(entry) {
	return git(["-C", repositoryRoot, "cat-file", "blob", entry.object], "buffer");
}

const baseEntry = indexedByPath.get("public-export.yaml");
if (!baseEntry) fail("public-export.yaml must be tracked");
const base = configuration(blob(baseEntry), "public-export.yaml");
const overlayPath = "private/site/public-export.overlay.yaml";
const overlayEntry = indexedByPath.get(overlayPath);
const overlay = overlayEntry
	? configuration(blob(overlayEntry), overlayPath, true)
	: { exclude: [], forbiddenContent: [], forbiddenPaths: [] };
const excluded = [...base.exclude, ...overlay.exclude].map(globExpression);
const forbiddenPaths = [...base.forbiddenPaths, ...overlay.forbiddenPaths].map(globExpression);
const forbiddenContent = [...base.forbiddenContent, ...overlay.forbiddenContent].map((pattern) => [
	pattern,
	new RegExp(pattern, "i"),
]);

const manifestFiles = [];
const exportEntries = [];
const failures = [];
for (const entry of indexed) {
	if (entry.path === "public-export-manifest.json") continue;
	if (excluded.some((expression) => expression.test(entry.path))) continue;
	if (forbiddenPaths.some((expression) => expression.test(entry.path))) {
		failures.push(`${entry.path}: forbidden public path`);
		continue;
	}
	if (entry.mode === "160000") {
		manifestFiles.push({ path: entry.path, type: "gitlink", mode: entry.mode, commit: entry.object });
		exportEntries.push(entry);
		continue;
	}
	if (!["100644", "100755", "120000"].includes(entry.mode)) {
		failures.push(`${entry.path}: unsupported Git mode ${entry.mode}`);
		continue;
	}
	const content = blob(entry);
	if (entry.mode === "120000") {
		const link = content.toString("utf8");
		const resolvedLink = resolve(repositoryRoot, dirname(entry.path), link);
		if (isAbsolute(link) || !inside(repositoryRoot, resolvedLink)) {
			failures.push(`${entry.path}: public symlink must stay inside the repository`);
			continue;
		}
		for (const [pattern, expression] of forbiddenContent) {
			if (expression.test(link)) failures.push(`${entry.path}: symlink contains forbidden public content ${pattern}`);
		}
		manifestFiles.push({
			path: entry.path,
			type: "symlink",
			mode: entry.mode,
			sha256: createHash("sha256").update(content).digest("hex"),
		});
		exportEntries.push({ ...entry, content, link });
		continue;
	}
	if (!content.includes(0)) {
		const text = content.toString("utf8");
		for (const [pattern, expression] of forbiddenContent) {
			if (expression.test(text)) failures.push(`${entry.path}: contains forbidden public content ${pattern}`);
		}
	}
	manifestFiles.push({
		path: entry.path,
		type: "file",
		mode: entry.mode,
		sha256: createHash("sha256").update(content).digest("hex"),
	});
	exportEntries.push({ ...entry, content });
}

if (failures.length > 0) {
	console.error("Public export failed:");
	for (const failure of failures) console.error(`  ${failure}`);
	process.exit(1);
}

mkdirSync(outputRoot, { recursive: false });
for (const entry of exportEntries) {
	const target = resolve(outputRoot, entry.path);
	if (entry.mode === "160000") {
		mkdirSync(target, { recursive: true });
		continue;
	}
	mkdirSync(dirname(target), { recursive: true });
	if (entry.mode === "120000") {
		symlinkSync(entry.link, target);
		continue;
	}
	writeFileSync(target, entry.content, { mode: entry.mode === "100755" ? 0o755 : 0o644 });
	chmodSync(target, entry.mode === "100755" ? 0o755 : 0o644);
}

const treeSha256 = createHash("sha256").update(JSON.stringify(manifestFiles)).digest("hex");
const manifest = {
	schema: "sure.public_export_manifest.v2",
	projection_id: `sha256:${treeSha256}`,
	tree_sha256: treeSha256,
	files: manifestFiles,
};
writeFileSync(resolve(outputRoot, "public-export-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);

if (attestationPath) {
	const attestation = {
		schema: "sure.private_public_export_attestation.v1",
		mappings: [
			{
				source_commit: git(["-C", repositoryRoot, "rev-parse", "HEAD"]).trim(),
				projection_id: manifest.projection_id,
			},
		],
	};
	mkdirSync(dirname(attestationPath), { recursive: true });
	writeFileSync(attestationPath, `${JSON.stringify(attestation, null, 2)}\n`, { mode: 0o600 });
	chmodSync(attestationPath, 0o600);
}

console.log(`ok   public export: ${manifestFiles.length} entries, projection ${manifest.projection_id}`);
