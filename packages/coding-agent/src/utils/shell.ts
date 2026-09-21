import { existsSync } from "node:fs";
import { delimiter, join } from "node:path";
import { spawn, spawnSync } from "child_process";
import { getBinDir } from "../config.ts";
import { sleep } from "./sleep.ts";

export interface ShellConfig {
	shell: string;
	args: string[];
	commandTransport?: "argv" | "stdin";
}

/**
 * Find bash executable on PATH (cross-platform)
 */
function isLegacyWslBashPath(path: string): boolean {
	const normalized = path.replace(/\//g, "\\").toLowerCase();
	return /^[a-z]:\\windows\\(?:system32|sysnative)\\bash\.exe$/.test(normalized);
}

function getBashShellConfig(shell: string): ShellConfig {
	return isLegacyWslBashPath(shell) ? { shell, args: ["-s"], commandTransport: "stdin" } : { shell, args: ["-c"] };
}

function findExecutableOnPath(executable: string): string | null {
	if (process.platform === "win32") {
		// Windows: Use 'where' and verify file exists (where can return non-existent paths)
		try {
			const result = spawnSync("where", [executable], {
				encoding: "utf-8",
				timeout: 5000,
				windowsHide: true,
			});
			if (result.status === 0 && result.stdout) {
				const firstMatch = result.stdout.trim().split(/\r?\n/)[0];
				if (firstMatch && existsSync(firstMatch)) {
					return firstMatch;
				}
			}
		} catch {
			// Ignore errors
		}
		return null;
	}

	// Unix: Use 'which' and trust its output (handles Termux and special filesystems)
	try {
		const result = spawnSync("which", [executable], { encoding: "utf-8", timeout: 5000 });
		if (result.status === 0 && result.stdout) {
			const firstMatch = result.stdout.trim().split(/\r?\n/)[0];
			if (firstMatch) {
				return firstMatch;
			}
		}
	} catch {
		// Ignore errors
	}
	return null;
}

/**
 * Resolve shell configuration based on platform and an optional explicit shell path.
 * Resolution order:
 * 1. User-specified shellPath
 * 2. On Windows: Git Bash in known locations, then bash on PATH
 * 3. On Unix: /bin/bash, then bash on PATH, then fallback to sh
 */
export function getShellConfig(customShellPath?: string): ShellConfig {
	// 1. Check user-specified shell path
	if (customShellPath) {
		if (existsSync(customShellPath)) {
			return getBashShellConfig(customShellPath);
		}
		throw new Error(`Custom shell path not found: ${customShellPath}`);
	}

	if (process.platform === "win32") {
		// 2. Try Git Bash in known locations
		const paths: string[] = [];
		const programFiles = process.env.ProgramFiles;
		if (programFiles) {
			paths.push(`${programFiles}\\Git\\bin\\bash.exe`);
		}
		const programFilesX86 = process.env["ProgramFiles(x86)"];
		if (programFilesX86) {
			paths.push(`${programFilesX86}\\Git\\bin\\bash.exe`);
		}

		for (const path of paths) {
			if (existsSync(path)) {
				return getBashShellConfig(path);
			}
		}

		// 3. Fallback: search bash.exe on PATH (Cygwin, MSYS2, WSL, etc.)
		const bashOnPath = findExecutableOnPath("bash.exe");
		if (bashOnPath) {
			return getBashShellConfig(bashOnPath);
		}

		throw new Error(
			`No bash shell found. Options:\n` +
				`  1. Install Git for Windows: https://git-scm.com/download/win\n` +
				`  2. Add your bash to PATH (Cygwin, MSYS2, etc.)\n` +
				"  3. Set shellPath in settings.json\n\n" +
				`Searched Git Bash in:\n${paths.map((p) => `  ${p}`).join("\n")}`,
		);
	}

	// Unix: try /bin/bash, then bash on PATH, then fallback to sh
	if (existsSync("/bin/bash")) {
		return getBashShellConfig("/bin/bash");
	}

	const bashOnPath = findExecutableOnPath("bash");
	if (bashOnPath) {
		return getBashShellConfig(bashOnPath);
	}

	return { shell: "sh", args: ["-c"] };
}

export const POWERSHELL_ARGS = ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"] as const;

/** Resolve PowerShell on Windows, preferring PowerShell 7 when available. */
export function getPowerShellConfig(): ShellConfig {
	if (process.platform !== "win32") {
		throw new Error("The powershell tool is only available on Windows.");
	}

	const shell = findExecutableOnPath("pwsh.exe") ?? findExecutableOnPath("powershell.exe");
	if (!shell) {
		throw new Error("No PowerShell executable found. Install PowerShell or add powershell.exe/pwsh.exe to PATH.");
	}

	return { shell, args: [...POWERSHELL_ARGS] };
}

export function getShellEnv(): NodeJS.ProcessEnv {
	const binDir = getBinDir();
	const pathKey = Object.keys(process.env).find((key) => key.toLowerCase() === "path") ?? "PATH";
	const currentPath = process.env[pathKey] ?? "";
	const pathEntries = currentPath.split(delimiter).filter(Boolean);
	const hasBinDir = pathEntries.includes(binDir);
	const updatedPath = hasBinDir ? currentPath : [binDir, currentPath].filter(Boolean).join(delimiter);

	return {
		...process.env,
		[pathKey]: updatedPath,
	};
}

/**
 * Sanitize binary output for display/storage.
 * Removes characters that crash string-width or cause display issues:
 * - Control characters (except tab, newline, carriage return)
 * - Lone surrogates
 * - Unicode Format characters (crash string-width due to a bug)
 * - Characters with undefined code points
 */
export function sanitizeBinaryOutput(str: string): string {
	// Use Array.from to properly iterate over code points (not code units)
	// This handles surrogate pairs correctly and catches edge cases where
	// codePointAt() might return undefined
	return Array.from(str)
		.filter((char) => {
			// Filter out characters that cause string-width to crash
			// This includes:
			// - Unicode format characters
			// - Lone surrogates (already filtered by Array.from)
			// - Control chars except \t \n \r
			// - Characters with undefined code points

			const code = char.codePointAt(0);

			// Skip if code point is undefined (edge case with invalid strings)
			if (code === undefined) return false;

			// Allow tab, newline, carriage return
			if (code === 0x09 || code === 0x0a || code === 0x0d) return true;

			// Filter out control characters (0x00-0x1F, except 0x09, 0x0a, 0x0x0d)
			if (code <= 0x1f) return false;

			// Filter out Unicode format characters
			if (code >= 0xfff9 && code <= 0xfffb) return false;

			return true;
		})
		.join("");
}

/**
 * Detached child processes must be tracked so they can be killed on parent
 * shutdown signals (SIGHUP/SIGTERM).
 */
const trackedDetachedChildPids = new Set<number>();

export function trackDetachedChildPid(pid: number): void {
	trackedDetachedChildPids.add(pid);
}

export function untrackDetachedChildPid(pid: number): void {
	trackedDetachedChildPids.delete(pid);
}

export function killTrackedDetachedChildren(): void {
	for (const pid of trackedDetachedChildPids) {
		killProcessTree(pid);
	}
	trackedDetachedChildPids.clear();
}

/**
 * `taskkill /T` kills the tree as it existed when it took its snapshot, so a descendant spawned
 * while the sweep is running - or one already orphaned by a parent that exited first - outlives it.
 *
 * Budget measured on a Windows 11 host: a `taskkill`ed process is gone 158 ms after dispatch
 * (median of 10 samples, max 173 ms), and reading the process table costs 1.6-1.9 s for ~355
 * processes. Three rounds - one to find survivors, one to re-kill them and confirm, one spare -
 * bound the check at roughly 6 s of background work per killed tree.
 *
 * Why it runs in the background rather than being awaited, measured rather than assumed: ~1.05 s of
 * that read is PowerShell's own start-up (`-Command exit` alone costs that), so narrowing the query
 * to the pids already recorded saves only ~280 ms and no query shape makes confirming cheap enough
 * to put in front of a user-visible timeout. `tasklist` is six times faster but reports neither
 * parent pid nor creation time, and `wmic` is gone from current Windows. Nothing here is unref'd -
 * a plain Node exit waits for the confirmation - but a host that exits at once still loses it.
 */
const WINDOWS_KILL_ATTEMPTS = 3;
const WINDOWS_KILL_SETTLE_MS = 250;
const WINDOWS_PROCESS_TABLE_TIMEOUT_MS = 10000; // ~6x the measured read
const WINDOWS_PROCESS_TABLE_QUERY =
	'Get-CimInstance -Query "SELECT ProcessId,ParentProcessId,CreationDate FROM Win32_Process" | ForEach-Object { "$($_.ProcessId) $($_.ParentProcessId) $([long]($_.CreationDate.ToUniversalTime() - [datetime]\'1970-01-01\').TotalMilliseconds)" }';

interface ProcessTableEntry {
	parentPid: number;
	/** Creation time in epoch milliseconds; with the pid, this identifies the process. */
	createdAt: number;
}

function windowsSystem32(...segments: string[]): string {
	// Use the trusted System32 executables so cleanup does not depend on PATH.
	return join(process.env.SystemRoot ?? "C:\\Windows", "System32", ...segments);
}

function spawnTaskkill(pids: number[]): void {
	try {
		const child = spawn(
			windowsSystem32("taskkill.exe"),
			["/F", "/T", ...pids.flatMap((pid) => ["/PID", String(pid)])],
			{
				stdio: "ignore",
				detached: true,
				windowsHide: true,
			},
		);
		// A failed spawn emits "error" asynchronously; consume it to avoid crashing Node.
		child.once("error", () => {});
	} catch {
		// Ignore errors if taskkill fails.
	}
}

/** Every live process by pid, or null when the table cannot be read. */
function readWindowsProcessTable(): Promise<Map<number, ProcessTableEntry> | null> {
	return new Promise((resolve) => {
		let settled = false;
		let timer: NodeJS.Timeout | undefined;
		const finish = (table: Map<number, ProcessTableEntry> | null) => {
			if (settled) return;
			settled = true;
			if (timer) clearTimeout(timer);
			resolve(table);
		};

		try {
			const child = spawn(
				windowsSystem32("WindowsPowerShell", "v1.0", "powershell.exe"),
				[...POWERSHELL_ARGS, WINDOWS_PROCESS_TABLE_QUERY],
				{ stdio: ["ignore", "pipe", "ignore"], windowsHide: true },
			);
			child.once("error", () => finish(null));
			const stdout = child.stdout;
			if (!stdout) {
				finish(null);
				return;
			}
			let output = "";
			stdout.setEncoding("utf-8");
			stdout.on("data", (chunk: string) => {
				output += chunk;
			});
			timer = setTimeout(() => {
				child.kill();
				finish(null);
			}, WINDOWS_PROCESS_TABLE_TIMEOUT_MS);
			child.once("close", () => {
				const table = new Map<number, ProcessTableEntry>();
				for (const line of output.split("\n")) {
					const [pid, parentPid, createdAt] = line.trim().split(" ").map(Number);
					if (Number.isInteger(pid) && Number.isInteger(parentPid) && Number.isInteger(createdAt)) {
						table.set(pid, { parentPid, createdAt });
					}
				}
				finish(table.size > 0 ? table : null);
			});
		} catch {
			finish(null);
		}
	});
}

/**
 * Drop every pid whose identity no longer matches, so a later round can never fire at a process
 * that merely inherited the number.
 *
 * A pid is not an identity on Windows: it is reissued once its owner exits, so a pid remembered
 * from the first sweep can name an unrelated process seconds later. The pair that does identify a
 * process is (ProcessId, CreationDate), both of which the table read already carries. Windows only
 * reissues a pid after its owner has exited, so a replacement is always created strictly later and
 * a millisecond of resolution separates the two.
 *
 * The pid we were asked to kill is the one we never got to timestamp - the caller hands us a bare
 * number and the kill goes out before any table can be read - so its rule is weaker but still
 * sufficient: whatever we were asked to kill existed before we were asked, so a process sitting at
 * that pid that was created after the kill was dispatched cannot be it. Dropping it also drops
 * everything hanging off it, because a process only ever joins the set through a parent that is
 * already in it.
 */
function forgetReusedPids(
	tracked: Map<number, number | undefined>,
	table: Map<number, ProcessTableEntry>,
	killedAt: number,
): void {
	for (const [pid, createdAt] of tracked) {
		const entry = table.get(pid);
		// A pid that is not running cannot be confused with anything. Keep it, so a descendant
		// orphaned by its death stays attributable to this tree.
		if (!entry) continue;
		const isSameProcess = createdAt === undefined ? entry.createdAt <= killedAt : entry.createdAt === createdAt;
		if (isSameProcess) tracked.set(pid, entry.createdAt);
		else tracked.delete(pid);
	}
}

/**
 * Live processes whose ancestry reaches a pid we already track. `tracked` grows as we go - keyed by
 * pid, valued by the creation time we attributed it at - so a descendant is still recognised once
 * the parent that links it to the tree has itself been killed, and is still identifiable next round.
 */
function collectLiveDescendants(
	tracked: Map<number, number | undefined>,
	table: Map<number, ProcessTableEntry>,
): number[] {
	let grew = true;
	while (grew) {
		grew = false;
		for (const [pid, entry] of table) {
			if (!tracked.has(pid) && tracked.has(entry.parentPid)) {
				tracked.set(pid, entry.createdAt);
				grew = true;
			}
		}
	}
	return [...tracked.keys()].filter((pid) => table.has(pid));
}

async function confirmWindowsTreeGone(pid: number, killedAt: number): Promise<void> {
	const tracked = new Map<number, number | undefined>([[pid, undefined]]);
	let survivors: number[] = [];
	for (let attempt = 1; attempt <= WINDOWS_KILL_ATTEMPTS; attempt++) {
		await sleep(WINDOWS_KILL_SETTLE_MS);
		const table = await readWindowsProcessTable();
		// Without a process table there is no evidence that anything survived, so say no more than
		// the taskkill spawn itself does when it fails.
		if (!table) return;
		forgetReusedPids(tracked, table, killedAt);
		survivors = collectLiveDescendants(tracked, table);
		if (survivors.length === 0) return;
		if (attempt < WINDOWS_KILL_ATTEMPTS) spawnTaskkill(survivors);
	}
	console.warn(
		`Warning: could not kill the process tree of ${pid}. These processes were left running and have to be ended manually: ${survivors.join(", ")}.`,
	);
}

/**
 * Kill a process and all its children (cross-platform). The kill is dispatched synchronously; the
 * returned promise settles once the tree has been confirmed gone and never rejects, so callers that
 * only want the tree dead can ignore it.
 */
export function killProcessTree(pid: number): Promise<void> {
	if (process.platform === "win32") {
		// Read before the kill goes out: whatever we were asked to kill already existed by now.
		const killedAt = Date.now();
		spawnTaskkill([pid]);
		return confirmWindowsTreeGone(pid, killedAt);
	}
	// Use SIGKILL on Unix/Linux/Mac. Signalling the process group is a single kernel operation
	// covering every current member, so it has no snapshot for a new descendant to slip past.
	try {
		process.kill(-pid, "SIGKILL");
	} catch {
		// Fallback to killing just the child if process group kill fails
		try {
			process.kill(pid, "SIGKILL");
		} catch {
			// Process already dead
		}
	}
	return Promise.resolve();
}
