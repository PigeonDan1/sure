#!/usr/bin/env node
import { createHash } from "node:crypto";
import { lstatSync, readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { spawnSync } from "node:child_process";

function fail(message) {
	console.error(`Fixture provenance check failed: ${message}`);
	process.exit(1);
}

const allowedLicenses = new Set(["Apache-2.0", "CC-BY-4.0", "CC0-1.0", "MIT"]);

function wavFormat(path, provenancePath) {
	const content = readFileSync(path);
	if (content.toString("ascii", 0, 4) !== "RIFF" || content.toString("ascii", 8, 12) !== "WAVE") {
		fail(`${provenancePath}: WAV header is invalid: ${path}`);
	}
	for (let offset = 12; offset + 8 <= content.length; ) {
		const name = content.toString("ascii", offset, offset + 4);
		const size = content.readUInt32LE(offset + 4);
		if (name === "fmt " && size >= 16 && offset + 8 + size <= content.length) {
			return {
				encoding: content.readUInt16LE(offset + 8),
				channels: content.readUInt16LE(offset + 10),
				sampleRate: content.readUInt32LE(offset + 12),
			};
		}
		offset += 8 + size + (size % 2);
	}
	fail(`${provenancePath}: WAV fmt chunk is missing: ${path}`);
}

const listed = spawnSync("git", ["ls-files", "--cached", "-z", "fixtures/**/provenance.json"], {
	encoding: "buffer",
});
if (listed.status !== 0) fail(listed.stderr?.toString("utf8").trim() || "cannot list provenance files");

const provenancePaths = listed.stdout.toString("utf8").split("\0").filter(Boolean);
for (const provenancePath of provenancePaths) {
	const root = dirname(provenancePath);
	let provenance;
	try {
		provenance = JSON.parse(readFileSync(provenancePath, "utf8"));
	} catch (error) {
		fail(`${provenancePath}: ${error instanceof Error ? error.message : String(error)}`);
	}
	if (provenance.schema !== "sure.fixture_provenance.v1") fail(`${provenancePath}: schema mismatch`);
	const dataset = provenance.dataset;
	for (const field of [
		"repository",
		"revision",
		"configuration",
		"split",
		"metadata_sha256",
		"license",
		"license_url",
		"source_url",
		"citation_url",
	]) {
		if (typeof dataset?.[field] !== "string" || dataset[field].length === 0) {
			fail(`${provenancePath}: dataset.${field} is required`);
		}
	}
	if (!/^[0-9a-f]{64}$/.test(dataset.metadata_sha256)) {
		fail(`${provenancePath}: dataset.metadata_sha256 is invalid`);
	}
	if (!allowedLicenses.has(dataset.license)) fail(`${provenancePath}: dataset.license is not approved`);
	for (const field of ["license_url", "source_url", "citation_url"]) {
		if (!dataset[field].startsWith("https://")) fail(`${provenancePath}: dataset.${field} must use HTTPS`);
	}
	if (!Array.isArray(provenance.files) || provenance.files.length === 0) {
		fail(`${provenancePath}: files must be non-empty`);
	}

	const declared = new Set();
	for (const entry of provenance.files) {
		if (typeof entry?.path !== "string" || entry.path.includes("/") || declared.has(entry.path)) {
			fail(`${provenancePath}: file paths must be unique basenames`);
		}
		if (!/^[0-9a-f]{64}$/.test(entry.sha256 ?? "")) {
			fail(`${provenancePath}: invalid SHA-256 for ${entry.path}`);
		}
		const path = resolve(root, entry.path);
		let stat;
		try {
			stat = lstatSync(path);
		} catch {
			fail(`${provenancePath}: declared file is missing: ${entry.path}`);
		}
		if (!stat.isFile() || stat.isSymbolicLink()) fail(`${provenancePath}: declared file must be regular: ${entry.path}`);
		const actual = createHash("sha256").update(readFileSync(path)).digest("hex");
		if (actual !== entry.sha256) fail(`${provenancePath}: SHA-256 mismatch for ${entry.path}`);
		if (entry.path.toLowerCase().endsWith(".wav")) {
			const format = wavFormat(path, provenancePath);
			if (![1, 3].includes(format.encoding) || format.channels !== 1 || format.sampleRate !== 16_000) {
				fail(`${provenancePath}: WAV must be mono 16 kHz PCM/float: ${entry.path}`);
			}
		}
		declared.add(entry.path);
	}

	const audioFiles = readdirSync(root).filter((name) => /\.(?:flac|mp3|ogg|wav)$/i.test(name));
	for (const name of audioFiles) {
		if (!declared.has(name)) fail(`${provenancePath}: audio file is not declared: ${name}`);
	}

	const groundTruthPath = resolve(root, "gt.jsonl");
	let groundTruth;
	try {
		groundTruth = readFileSync(groundTruthPath, "utf8");
	} catch {
		fail(`${provenancePath}: gt.jsonl is required`);
	}
	const referenced = new Set();
	for (const [index, line] of groundTruth.split("\n").filter(Boolean).entries()) {
		let row;
		try {
			row = JSON.parse(line);
		} catch (error) {
			fail(`${groundTruthPath}:${index + 1}: ${error instanceof Error ? error.message : String(error)}`);
		}
		if (!declared.has(row.audio)) fail(`${groundTruthPath}:${index + 1}: audio is not declared: ${row.audio}`);
		if (referenced.has(row.audio)) fail(`${groundTruthPath}:${index + 1}: duplicate audio reference: ${row.audio}`);
		referenced.add(row.audio);
	}
	for (const name of declared) {
		if (!referenced.has(name)) fail(`${groundTruthPath}: declared audio is not referenced: ${name}`);
	}
}

console.log(`ok   fixture provenance: ${provenancePaths.length} manifests`);
