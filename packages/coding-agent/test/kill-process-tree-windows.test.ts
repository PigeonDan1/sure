import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { killProcessTree } from "../src/utils/shell.ts";

const taskkillPath = join(process.env.SystemRoot ?? "C:\\Windows", "System32", "taskkill.exe");

// A child that spawns a detached grandchild and exits immediately. Once the child is gone the
// grandchild is a live process whose parent pid is dead - the same state a descendant ends up in
// when it is spawned while taskkill /T is sweeping the tree.
const SPAWN_ORPHANED_GRANDCHILD = [
	"const { spawn } = require('child_process');",
	"const g = spawn(process.execPath, ['-e', 'setTimeout(() => {}, 30000)'], { detached: true, stdio: 'ignore' });",
	"g.unref();",
	"console.log(g.pid);",
].join("");

function isAlive(pid: number): boolean {
	try {
		process.kill(pid, 0);
		return true;
	} catch {
		return false;
	}
}

async function waitForExit(pid: number, timeoutMs: number): Promise<boolean> {
	const deadline = Date.now() + timeoutMs;
	while (isAlive(pid) && Date.now() < deadline) {
		await new Promise((resolve) => setTimeout(resolve, 50));
	}
	return !isAlive(pid);
}

// Windows only: the POSIX branch signals the whole process group in one syscall, so it has no
// userspace tree snapshot that a newly spawned descendant can slip past.
describe.skipIf(process.platform !== "win32")("killProcessTree on Windows", () => {
	let startedGrandchildPid = 0;

	afterEach(() => {
		// Only ever clean up the process this test started.
		if (startedGrandchildPid) {
			spawnSync(taskkillPath, ["/F", "/PID", String(startedGrandchildPid)], { stdio: "ignore" });
			startedGrandchildPid = 0;
		}
	});

	it("kills a descendant that outlived the process tree taskkill swept", async () => {
		const child = spawnSync(process.execPath, ["-e", SPAWN_ORPHANED_GRANDCHILD], {
			encoding: "utf-8",
			windowsHide: true,
		});
		startedGrandchildPid = Number(child.stdout.trim());
		expect(Number.isInteger(startedGrandchildPid)).toBe(true);
		expect(isAlive(startedGrandchildPid)).toBe(true);

		await killProcessTree(child.pid ?? 0);

		// Measured on this host: taskkill dispatch -> target gone takes 158 ms (median of 10, max
		// 173 ms), so a grandchild still running seconds later was never targeted at all.
		expect(await waitForExit(startedGrandchildPid, 5000)).toBe(true);
	});
});
