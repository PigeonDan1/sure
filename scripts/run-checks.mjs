#!/usr/bin/env node
// Runs every repository check. Two properties matter here:
//   1. No check mutates the working tree. `biome check` runs without --write,
//      so `npm run check` is safe on a dirty tree and in CI. Use `npm run
//      format` when you want Biome to rewrite files.
//   2. No check short-circuits the rest. Every check runs even after an
//      earlier one fails, so one Biome warning no longer hides a type error.
import { spawnSync } from "node:child_process";

const checks = [
	["biome", ["check", "--error-on-warnings", "."]],
	["npm", ["run", "check:pinned-deps"]],
	["npm", ["run", "check:ts-imports"]],
	["npm", ["run", "check:sure-hooks"]],
	["npm", ["run", "check:shrinkwrap"]],
	["npm", ["run", "check:install-lock:coding-agent"]],
	["tsgo", ["--noEmit"]],
	["npm", ["run", "check:browser-smoke"]],
];

const failed = [];
for (const [command, args] of checks) {
	const label = [command, ...args].join(" ");
	const result = spawnSync(command, args, { stdio: "inherit", shell: process.platform === "win32" });
	if (result.status === 0) {
		console.log(`ok   ${label}`);
	} else {
		failed.push(label);
		console.error(`FAIL ${label}`);
	}
}

console.log(`\n${checks.length - failed.length}/${checks.length} checks passed.`);
for (const label of failed) {
	console.error(`  failed: ${label}`);
}
process.exit(failed.length > 0 ? 1 : 0);
