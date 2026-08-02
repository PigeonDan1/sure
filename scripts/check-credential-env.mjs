#!/usr/bin/env node
// Guards the fix for FIX-112: test.sh and pi-test.ps1 --no-env used to keep
// their own hand-written lists of credential env vars to clear, and the
// lists drifted (pi-test.ps1 was missing 17 names, including
// DEEPSEEK_API_KEY, so "no keys" mode still made real billed requests).
//
// Both scripts now read scripts/credential-env.txt instead of inlining
// names. This check fails if either script goes back to hardcoding one of
// the listed names, or stops reading the shared file.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const listPath = join(repoRoot, "scripts/credential-env.txt");
const listText = readFileSync(listPath, "utf8");

const names = listText
	.split("\n")
	.map((line) => line.trim())
	.filter((line) => line.length > 0 && !line.startsWith("#"));

const failures = [];

if (names.length === 0) {
	failures.push(`${listPath} lists no variable names.`);
}

const namePattern = /^[A-Z][A-Z0-9_]*$/;
for (const name of names) {
	if (!namePattern.test(name)) {
		failures.push(`${listPath}: "${name}" is not a valid environment variable name.`);
	}
}

const sortedNames = [...names].sort();
for (let i = 0; i < names.length; i++) {
	if (names[i] !== sortedNames[i]) {
		failures.push(`${listPath} is not sorted alphabetically (starting at "${names[i]}").`);
		break;
	}
}

const seen = new Set();
for (const name of names) {
	if (seen.has(name)) {
		failures.push(`${listPath} lists "${name}" more than once.`);
	}
	seen.add(name);
}

// Neither script may hardcode a name from the shared list. If a name shows
// up as a literal token in the script source, someone re-inlined it instead
// of adding it to credential-env.txt.
const scripts = [
	{ label: "test.sh", path: join(repoRoot, "test.sh") },
	{ label: "pi-test.ps1", path: join(repoRoot, "pi-test.ps1") },
];

for (const { label, path } of scripts) {
	const source = readFileSync(path, "utf8");

	if (!source.includes("credential-env.txt")) {
		failures.push(`${label} does not read scripts/credential-env.txt.`);
	}

	for (const name of names) {
		const inlined = new RegExp(`\\b${name}\\b`);
		if (inlined.test(source)) {
			failures.push(`${label} hardcodes "${name}" -- add new credential env vars to scripts/credential-env.txt instead.`);
		}
	}
}

if (failures.length > 0) {
	console.error("Credential env list check failed:");
	for (const failure of failures) console.error(`  ${failure}`);
	process.exit(1);
}

console.log(`ok   scripts/credential-env.txt: ${names.length} names, test.sh and pi-test.ps1 both read from it`);
