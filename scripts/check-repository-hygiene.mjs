#!/usr/bin/env node
import { lstatSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";

const maxBlobBytes = 5 * 1024 * 1024;
const ignoreCases = [
	["data/example.jsonl", true],
	["packages/ai/test/data/example.png", false],
	["results_asr/run.json", true],
	["results_custom/run.json", true],
	["events.jsonl", true],
	[".sure/runs/state.json", true],
	["auth.json", true],
	["models.json", true],
	["packages/demo/auth.json", true],
	["packages/demo/models.json", true],
	["packages/demo/.env", true],
	["packages/demo/.env.local", true],
	["packages/demo/.env.example", false],
	["config/site.local.yaml", true],
	["config/site.example.yaml", false],
	["sure/models/model.safetensors", true],
	["fixtures/audio/sample.wav", false],
	["config/site.bundled.yaml", false],
	["private/aispeech/README.md", false],
	["sure/runtime/evaluation/runtime.json", false],
	[".public-export/public-export-manifest.json", true],
];
const forbiddenTrackedPath = /^(?:\.sure|data|results(?:_[^/]+)?|sure\/results)(?:\/|$)|(?:^|\/)(?:auth|models)\.json$|(?:^|\/)events\.jsonl$|(?:^|\/)\.env(?:\.(?!example$)[^/]+)?$|\.(?:pt|pth|ckpt|safetensors|onnx|gguf|nemo|bin|h5|npz|p12|pfx|pem|key)$/i;
const secretPatterns = [
	/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/,
	/\bAKIA[0-9A-Z]{16}\b/,
	/\bgh[pousr]_[A-Za-z0-9_]{30,}\b/,
	/\bglpat-[A-Za-z0-9_-]{20,}\b/,
];

function git(args, encoding = "utf8") {
	const result = spawnSync("git", args, { encoding });
	if (result.status !== 0) {
		throw new Error(result.stderr?.trim() || `git ${args.join(" ")} failed`);
	}
	return result.stdout;
}

const failures = [];

for (const [path, expectedIgnored] of ignoreCases) {
	const result = spawnSync("git", ["check-ignore", "--quiet", "--no-index", "--", path]);
	const ignored = result.status === 0;
	if (ignored !== expectedIgnored) {
		failures.push(`${path}: expected ${expectedIgnored ? "ignored" : "tracked/visible"}`);
	}
}

const trackedIgnored = git(["ls-files", "-ci", "--exclude-standard", "-z"], "buffer")
	.toString("utf8")
	.split("\0")
	.filter(Boolean);
for (const path of trackedIgnored) failures.push(`${path}: tracked file is also ignored`);

const tracked = git(["ls-files", "--cached", "--others", "--exclude-standard", "-z"], "buffer")
	.toString("utf8")
	.split("\0")
	.filter(Boolean);
for (const path of tracked) {
	if (forbiddenTrackedPath.test(path)) {
		failures.push(`${path}: generated, credential, or large-model path must not be tracked`);
		continue;
	}
	let stat;
	try {
		stat = lstatSync(path);
	} catch {
		continue;
	}
	if (!stat.isFile()) continue;
	if (stat.size > maxBlobBytes) {
		failures.push(`${path}: ${stat.size} bytes exceeds the ${maxBlobBytes}-byte Git blob limit`);
		continue;
	}
	if (stat.size === 0) continue;
	const content = readFileSync(path);
	if (content.includes(0)) continue;
	const text = content.toString("utf8");
	for (const pattern of secretPatterns) {
		if (pattern.test(text)) {
			failures.push(`${path}: matches a high-confidence secret pattern`);
			break;
		}
	}
}

const gitmodules = readFileSync(".gitmodules", "utf8");
for (const line of gitmodules.split("\n")) {
	const match = line.match(/^\s*url\s*=\s*(.+)\s*$/);
	if (match && !match[1].startsWith("../")) {
		failures.push(`.gitmodules: submodule URL must stay relative, found ${match[1]}`);
	}
}

if (failures.length > 0) {
	console.error("Repository hygiene check failed:");
	for (const failure of failures) console.error(`  ${failure}`);
	process.exit(1);
}

console.log(`ok   repository hygiene: ${ignoreCases.length} ignore cases, ${tracked.length} repository paths`);
