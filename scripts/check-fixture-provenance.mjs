#!/usr/bin/env node
import { createHash } from "node:crypto";
import { lstatSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";

function fail(message) {
	console.error(`Fixture provenance check failed: ${message}`);
	process.exit(1);
}

const root = resolve(import.meta.dirname, "..");
const allowedLicenses = new Set([
	"Apache-2.0",
	"CC-BY-4.0",
	"CC0-1.0",
	"Fluent-Speech-Commands-Public-License",
	"MIT",
	"ODbL-1.0/DbCL-1.0",
]);
const hashPattern = /^[0-9a-f]{64}$/;
const audioPattern = /\.(?:flac|m4a|mp3|ogg|wav)$/i;
const audioFields = new Set([
	"audio",
	"wav",
	"audio_path",
	"source_audio",
	"reference_audio",
	"noisy_audio",
	"mixed_audio",
	"enrollment_audio",
	"prompt_audio",
]);
const profiles = JSON.parse(
	readFileSync(join(root, "sure/runtime/evaluation/harness-task-profiles.json"), "utf8"),
);

function sha256(path) {
	return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function walk(path, predicate) {
	const matches = [];
	for (const entry of readdirSync(path, { withFileTypes: true })) {
		const child = join(path, entry.name);
		if (entry.isDirectory()) matches.push(...walk(child, predicate));
		else if (entry.isFile() && predicate(child)) matches.push(child);
	}
	return matches;
}

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

function validateDataset(dataset, provenancePath) {
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
	if (!hashPattern.test(dataset.metadata_sha256)) fail(`${provenancePath}: dataset.metadata_sha256 is invalid`);
	if (!allowedLicenses.has(dataset.license)) fail(`${provenancePath}: unapproved dataset license ${dataset.license}`);
	for (const field of ["license_url", "source_url", "citation_url"]) {
		if (!dataset[field].startsWith("https://")) fail(`${provenancePath}: dataset.${field} must use HTTPS`);
	}
}

function validateV1(provenance, provenancePath) {
	const fixtureRoot = dirname(provenancePath);
	validateDataset(provenance.dataset, provenancePath);
	if (!Array.isArray(provenance.selection?.sentence_ids) || provenance.selection.sentence_ids.length === 0) {
		fail(`${provenancePath}: selection.sentence_ids must be non-empty`);
	}
	if (!Array.isArray(provenance.files) || provenance.files.length === 0) {
		fail(`${provenancePath}: files must be non-empty`);
	}
	const declared = new Set();
	for (const item of provenance.files) {
		if (
			typeof item.path !== "string" ||
			item.path.startsWith("/") ||
			item.path.includes("..") ||
			declared.has(item.path)
		) {
			fail(`${provenancePath}: file paths must be unique and fixture-relative`);
		}
		if (typeof item.source_path !== "string" || item.source_path.length === 0) {
			fail(`${provenancePath}: source_path is required for ${item.path}`);
		}
		if (!hashPattern.test(item.sha256 ?? "")) fail(`${provenancePath}: invalid SHA-256 for ${item.path}`);
		const path = resolve(fixtureRoot, item.path);
		const stat = lstatSync(path);
		if (!stat.isFile() || stat.isSymbolicLink()) fail(`${provenancePath}: declared file must be regular: ${item.path}`);
		if (sha256(path) !== item.sha256) fail(`${provenancePath}: SHA-256 mismatch for ${item.path}`);
		if (item.path.toLowerCase().endsWith(".wav")) {
			const format = wavFormat(path, provenancePath);
			if (![1, 3].includes(format.encoding) || format.channels !== 1 || format.sampleRate !== 16_000) {
				fail(`${provenancePath}: WAV must be mono 16 kHz PCM/float: ${item.path}`);
			}
		}
		declared.add(item.path);
	}
	for (const audioPath of walk(fixtureRoot, (path) => audioPattern.test(path))) {
		const name = relative(fixtureRoot, audioPath);
		if (!declared.has(name)) fail(`${provenancePath}: audio file is not declared: ${name}`);
	}
}

function validateV2(provenance, provenancePath) {
	const fixtureRoot = dirname(provenancePath);
	validateDataset(provenance.dataset, provenancePath);
	if (!Array.isArray(provenance.selection?.sample_ids) || provenance.selection.sample_ids.length === 0) {
		fail(`${provenancePath}: selection.sample_ids must be non-empty`);
	}
	const gtPath = join(fixtureRoot, String(provenance.annotations?.ground_truth_path || ""));
	if (!lstatSync(gtPath).isFile()) fail(`${provenancePath}: ground truth is missing`);
	if (sha256(gtPath) !== provenance.annotations?.ground_truth_sha256) {
		fail(`${provenancePath}: ground-truth SHA-256 mismatch`);
	}
	const transforms = new Set((provenance.transforms ?? []).map((item) => item?.id));
	if (transforms.has(undefined) || transforms.size === 0) fail(`${provenancePath}: transforms must declare ids`);
	for (const item of provenance.transforms) {
		if (typeof item.command !== "string" || item.command.length === 0) {
			fail(`${provenancePath}: transform ${item.id} must record a command or import procedure`);
		}
	}
	if (!Array.isArray(provenance.files) || provenance.files.length === 0) {
		fail(`${provenancePath}: files must be non-empty`);
	}
	const declared = new Set();
	for (const item of provenance.files) {
		if (typeof item.path !== "string" || item.path.startsWith("/") || item.path.includes("..") || declared.has(item.path)) {
			fail(`${provenancePath}: file paths must be unique and fixture-relative`);
		}
		if (!hashPattern.test(item.sha256 ?? "")) fail(`${provenancePath}: invalid SHA-256 for ${item.path}`);
		const sourceHashes = Array.isArray(item.source_sha256) ? item.source_sha256 : [item.source_sha256];
		if (sourceHashes.length === 0 || sourceHashes.some((value) => !hashPattern.test(value ?? ""))) {
			fail(`${provenancePath}: invalid source SHA-256 for ${item.path}`);
		}
		if (!transforms.has(item.transform_id)) fail(`${provenancePath}: unknown transform ${item.transform_id}`);
		const path = resolve(fixtureRoot, item.path);
		const stat = lstatSync(path);
		if (!stat.isFile() || stat.isSymbolicLink()) fail(`${provenancePath}: declared file must be regular: ${item.path}`);
		if (sha256(path) !== item.sha256) fail(`${provenancePath}: SHA-256 mismatch for ${item.path}`);
		if (item.path.toLowerCase().endsWith(".wav")) {
			const format = wavFormat(path, provenancePath);
			if (![1, 3].includes(format.encoding) || format.channels !== 1 || format.sampleRate !== 16_000) {
				fail(`${provenancePath}: WAV must be mono 16 kHz PCM/float: ${item.path}`);
			}
		}
		declared.add(item.path);
	}
	for (const audioPath of walk(fixtureRoot, (path) => audioPattern.test(path))) {
		const name = relative(fixtureRoot, audioPath);
		if (!declared.has(name)) fail(`${provenancePath}: audio file is not declared: ${name}`);
	}
	const rows = readFileSync(gtPath, "utf8")
		.split("\n")
		.filter(Boolean)
		.map((line) => JSON.parse(line));
	const keys = rows.map((row) => String(row.key ?? row.id));
	if (JSON.stringify(keys) !== JSON.stringify(provenance.selection.sample_ids)) {
		fail(`${provenancePath}: selection.sample_ids does not match gt.jsonl order`);
	}
	for (const row of rows) {
		for (const [field, value] of Object.entries(row)) {
			if (audioFields.has(field) && typeof value === "string" && !declared.has(value)) {
				fail(`${provenancePath}: gt.jsonl references undeclared ${field}: ${value}`);
			}
		}
	}
}

const selected = new Set(Object.values(profiles.tasks).map((profile) => join(root, profile.fixture_root, "provenance.json")));
for (const path of selected) {
	try {
		if (!lstatSync(path).isFile()) fail(`selected fixture provenance is not a file: ${relative(root, path)}`);
	} catch {
		fail(`selected fixture is missing provenance: ${relative(root, path)}`);
	}
}

const provenancePaths = walk(join(root, "fixtures"), (path) => path.endsWith("provenance.json"));
for (const provenancePath of provenancePaths) {
	let provenance;
	try {
		provenance = JSON.parse(readFileSync(provenancePath, "utf8"));
	} catch (error) {
		fail(`${provenancePath}: ${error instanceof Error ? error.message : String(error)}`);
	}
	if (provenance.schema === "sure.fixture_provenance.v1") validateV1(provenance, provenancePath);
	else if (provenance.schema === "sure.fixture_provenance.v2") validateV2(provenance, provenancePath);
	else fail(`${provenancePath}: schema mismatch`);
}

console.log(`ok   fixture provenance: ${selected.size} selected, ${provenancePaths.length} manifests`);
