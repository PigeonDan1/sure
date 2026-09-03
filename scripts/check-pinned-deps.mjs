import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

const dependencySections = ["dependencies", "devDependencies", "optionalDependencies"];
const exactVersionPattern = /^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;
const ignoredDirectories = new Set([
	".git",
	".mypy_cache",
	".pytest_cache",
	".ruff_cache",
	".sure",
	".tox",
	".venv",
	"__pycache__",
	"dist",
	"node_modules",
	"venv",
]);
const ignoredPathPrefixes = [
	"data/datasets",
	"sure/handoffs",
	"sure/models",
	"sure/skills/sure_infer/results",
];
const packageJsonFiles = [];

function normalizedRelativePath(path) {
	return relative(".", path).replace(/\\/g, "/");
}

function isIgnoredPath(path) {
	const rel = normalizedRelativePath(path);
	return ignoredPathPrefixes.some((prefix) => rel === prefix || rel.startsWith(`${prefix}/`));
}

function collectPackageJsonFiles(directory) {
	for (const entry of readdirSync(directory, { withFileTypes: true })) {
		if (entry.isDirectory()) {
			const child = join(directory, entry.name);
			if (!ignoredDirectories.has(entry.name) && !isIgnoredPath(child)) {
				collectPackageJsonFiles(child);
			}
			continue;
		}

		if (entry.isFile() && entry.name === "package.json") {
			packageJsonFiles.push(join(directory, entry.name));
		}
	}
}

function isInternalWorkspaceDependency(name) {
	return name.startsWith("@earendil-works/pi-");
}

function isNonRegistrySpecifier(specifier) {
	return /^(?:workspace:|file:|link:|portal:|git\+|github:|git:|https?:|ssh:|git:\/\/)/.test(specifier);
}

function getVersionSpecifier(specifier) {
	if (!specifier.startsWith("npm:")) return specifier;
	const aliasTarget = specifier.slice("npm:".length);
	const versionSeparator = aliasTarget.lastIndexOf("@");
	if (versionSeparator <= 0) return specifier;
	return aliasTarget.slice(versionSeparator + 1);
}

const failures = [];

collectPackageJsonFiles(".");

for (const file of packageJsonFiles.sort()) {
	const packageJson = JSON.parse(readFileSync(file, "utf8"));

	for (const section of dependencySections) {
		const dependencies = packageJson[section];
		if (!dependencies) continue;

		for (const [name, specifier] of Object.entries(dependencies)) {
			if (isInternalWorkspaceDependency(name) || isNonRegistrySpecifier(specifier)) continue;
			if (exactVersionPattern.test(getVersionSpecifier(specifier))) continue;
			failures.push(`${file}: ${section}.${name} must be pinned, found ${specifier}`);
		}
	}
}

if (failures.length > 0) {
	console.error("Direct external dependencies must use exact versions:");
	for (const failure of failures) console.error(`  ${failure}`);
	process.exit(1);
}
