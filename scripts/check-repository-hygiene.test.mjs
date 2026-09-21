import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

const hygiene = resolve(import.meta.dirname, "check-repository-hygiene.mjs");

// The check runs against a throwaway repository, so its ignore-case and
// tracked-path findings say nothing here; only the .gitmodules lines do.
function submoduleFailures(url) {
	const root = mkdtempSync(join(tmpdir(), "sure-hygiene-test-"));
	try {
		assert.equal(spawnSync("git", ["init", "--quiet"], { cwd: root }).status, 0);
		writeFileSync(join(root, ".gitmodules"), `[submodule "engine"]\n\tpath = engine\n\turl = ${url}\n`);
		const result = spawnSync(process.execPath, [hygiene], { cwd: root, encoding: "utf8" });
		return result.stderr.split(/\r?\n/).filter((line) => line.includes(".gitmodules:"));
	} finally {
		rmSync(root, { recursive: true, force: true });
	}
}

// A relative submodule URL resolves against the URL the parent was cloned
// from, so `git clone --recurse-submodules` of a fork looks for the submodule
// under the fork owner and fails. An absolute github.com URL is the fix, and
// this check is what used to forbid it.
test("accepts an absolute github.com submodule URL", () => {
	assert.deepEqual(submoduleFailures("https://github.com/PigeonDan1/sure-evaluation.git"), []);
});

// The rule exists to keep a non-public host out of the public tree.
test("rejects an absolute submodule URL on any other host", () => {
	const failures = submoduleFailures("https://git.example.invalid/internal/sure-evaluation.git");
	assert.equal(failures.length, 1, `expected one .gitmodules failure, got: ${failures.join(" | ")}`);
	assert.match(failures[0], /git\.example\.invalid/);
});

// The relative form must keep working: existing clones and the pinned gitlink
// depend on it resolving.
test("accepts a relative submodule URL", () => {
	assert.deepEqual(submoduleFailures("../sure-evaluation.git"), []);
});
