import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

const doctor = resolve(import.meta.dirname, "sure-doctor.mjs");
const suffix = process.platform === "win32" ? ".exe" : "";

// The agent resolves rg and fd with getToolPath() in
// packages/coding-agent/src/utils/tools-manager.ts, which reads its own bin
// directory before PATH. The doctor runs outside the repository root on
// purpose: these cover the tool lookup, not the checkout checks.
function runDoctor(fillManagedBinary) {
	const home = mkdtempSync(join(tmpdir(), "sure-doctor-test-"));
	const binDir = join(home, ".pi", "agent", "bin");
	try {
		mkdirSync(binDir, { recursive: true });
		for (const name of ["rg", "fd"]) fillManagedBinary(join(binDir, `${name}${suffix}`));

		const env = { ...process.env, HOME: home, USERPROFILE: home };
		for (const key of Object.keys(env)) {
			if (/^(path|pi_coding_agent_dir)$/i.test(key)) delete env[key];
		}
		// Nothing executable lives in this directory, so PATH cannot satisfy the checks.
		env.PATH = home;

		const result = spawnSync(process.execPath, [doctor], { cwd: home, encoding: "utf8", env });
		return { binDir, lines: result.stdout.split(/\r?\n/) };
	} finally {
		rmSync(home, { recursive: true, force: true });
	}
}

// A tool installed only in the agent bin directory must not be reported as
// missing. node is the one executable every host running this test has.
test("finds rg and fd in the agent bin directory when they are not on PATH", () => {
	const { binDir, lines } = runDoctor((path) => copyFileSync(process.execPath, path));
	for (const label of ["rg", "fd"]) {
		const line = lines.find((candidate) => candidate.includes(` ${label}: `));
		assert.ok(line?.startsWith("PASS "), `expected a PASS line for ${label}, got: ${line}`);
		assert.ok(line.includes(binDir), `expected ${label} to be reported from ${binDir}, got: ${line}`);
	}
});

// A truncated or non-executable download. getToolPath() hands the agent this
// path on existsSync alone, so PATH is no fallback for it and the doctor must
// not report the host as healthy.
test("warns when the managed rg and fd exist but cannot run", () => {
	const { binDir, lines } = runDoctor((path) => writeFileSync(path, ""));
	for (const label of ["rg", "fd"]) {
		const line = lines.find((candidate) => candidate.includes(` ${label}: `));
		assert.ok(line?.startsWith("WARN "), `expected a WARN line for ${label}, got: ${line}`);
		assert.ok(line.includes(binDir), `expected ${label} to name the managed path, got: ${line}`);
	}
});
