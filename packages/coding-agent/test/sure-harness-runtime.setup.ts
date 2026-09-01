import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

// vitest globalSetup: materialize the Harness Runtime once, before any worker starts.
//
// sure_onboard's preStart resolves the runtime through sure/runtime/harness/bootstrap.py. On a
// fresh checkout, a fresh CI runner, or after the dependency lock changes, that bootstrap runs
// `uv sync` for half a minute to two minutes while holding the runtime lock. Left to the first
// test that needs the runtime, the wait lands inside a 30s test budget and every other worker's
// first harness test blocks on the same lock, so unrelated files time out together.
export default function setup(): void {
	const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));
	const python = process.env.SURE_HARNESS_BOOTSTRAP_PYTHON?.trim() || "python3";
	const started = Date.now();
	const result = spawnSync(python, ["sure/runtime/harness/bootstrap.py", "--json"], {
		cwd: repoRoot,
		encoding: "utf-8",
		timeout: 900_000,
	});
	if (result.status !== 0) {
		// Not fatal: the tests that need the runtime report this failure themselves.
		const detail = (result.stderr || result.stdout || result.error?.message || "").trim().split("\n").pop();
		console.warn(`[sure] harness runtime bootstrap skipped: ${detail || `exit ${result.status}`}`);
		return;
	}
	const seconds = (Date.now() - started) / 1000;
	if (seconds > 5) {
		console.log(`[sure] harness runtime materialized in ${seconds.toFixed(1)}s before the tests`);
	}
}
