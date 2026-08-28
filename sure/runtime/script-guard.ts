/**
 * Skill scripts that a bash command actually runs.
 *
 * Two things the old per-skill `command.match(...)` could not do: it stopped at
 * the first name, so `python3 scripts/allowed.py && python3 scripts/other.py`
 * was judged entirely on the first one; and it could not tell running a script
 * from reading it, so `cat scripts/other.py` was blocked as an out-of-order
 * call.
 *
 * This raises the cost of running another unit's script. It cannot stop a
 * determined caller — cd into the directory, or build the path in a variable,
 * and no name matches.
 */
const READ_ONLY_HEAD = /^(?:cat|head|tail|less|more|grep|rg|sed|awk|wc|ls|stat|file|diff|md5sum|sha256sum|nl|od|xxd)$/;

export function invokedSkillScripts(command: string, prefix = "scripts"): string[] {
	const pattern = new RegExp(`${prefix}/([A-Za-z0-9_]+\\.py)\\b`, "g");
	const names = new Set<string>();
	for (const segment of command.split(/\|\||&&|[;&|\n]/)) {
		const head = segment.trim().split(/\s+/)[0] ?? "";
		if (READ_ONLY_HEAD.test(head)) {
			continue;
		}
		for (const match of segment.matchAll(pattern)) {
			names.add(`${prefix}/${match[1]}`);
		}
	}
	return [...names];
}
