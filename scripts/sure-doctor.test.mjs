import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

const doctor = resolve(import.meta.dirname, "sure-doctor.mjs");

// The agent resolves rg and fd with getToolPath() in
// packages/coding-agent/src/utils/tools-manager.ts, which reads its own bin
// directory before PATH, so a tool installed only there must not be reported as
// missing. The doctor runs outside the repository root on purpose: this covers
// the tool lookup, not the checkout checks.
test("finds rg and fd in the agent bin directory when they are not on PATH", () => {
	const home = mkdtempSync(join(tmpdir(), "sure-doctor-test-"));
	try {
		const binDir = join(home, ".pi", "agent", "bin");
		mkdirSync(binDir, { recursive: true });
		const suffix = process.platform === "win32" ? ".exe" : "";
		for (const name of ["rg", "fd"]) writeFileSync(join(binDir, `${name}${suffix}`), "");

		const env = { ...process.env, HOME: home, USERPROFILE: home };
		for (const key of Object.keys(env)) {
			if (/^(path|pi_coding_agent_dir)$/i.test(key)) delete env[key];
		}
		// Nothing executable lives in this directory, so PATH cannot satisfy the checks.
		env.PATH = home;

		const result = spawnSync(process.execPath, [doctor], { cwd: home, encoding: "utf8", env });
		const lines = result.stdout.split(/\r?\n/);
		for (const label of ["rg", "fd"]) {
			const line = lines.find((candidate) => candidate.includes(` ${label}: `));
			assert.ok(line?.startsWith("PASS "), `expected a PASS line for ${label}, got: ${line}`);
			assert.ok(line.includes(binDir), `expected ${label} to be reported from ${binDir}, got: ${line}`);
		}
	} finally {
		rmSync(home, { recursive: true, force: true });
	}
});
