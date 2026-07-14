#!/usr/bin/env node
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { basename, join, relative } from "node:path";

function parseArgs(argv) {
	const args = {
		query: "machine learning",
		target: 10,
		runId: undefined,
		runDir: undefined,
		skillName: "paper_collect",
	};
	for (let index = 0; index < argv.length; index += 1) {
		const arg = argv[index];
		const next = argv[index + 1];
		if (arg === "--query" && next) {
			args.query = next;
			index += 1;
		} else if (arg === "--target" && next) {
			args.target = Number.parseInt(next, 10);
			index += 1;
		} else if (arg === "--run-id" && next) {
			args.runId = next;
			index += 1;
		} else if (arg === "--run-dir" && next) {
			args.runDir = next;
			index += 1;
		} else if (arg === "--skill-name" && next) {
			args.skillName = next;
			index += 1;
		}
	}
	if (!args.runDir) {
		throw new Error("--run-dir is required");
	}
	if (!args.runId) {
		args.runId = basename(args.runDir);
	}
	if (!Number.isFinite(args.target) || args.target < 1) {
		args.target = 10;
	}
	return args;
}

function normalizeText(value) {
	return value
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, " ")
		.trim()
		.replace(/\s+/g, " ");
}

function makeId(prefix, value) {
	return `${prefix}_${createHash("sha1").update(value).digest("hex").slice(0, 12)}`;
}

function titleCase(value) {
	return value
		.split(" ")
		.filter(Boolean)
		.map((word) => `${word[0].toUpperCase()}${word.slice(1)}`)
		.join(" ");
}

function topicTokens(query) {
	const tokens = normalizeText(query)
		.split(" ")
		.filter((token) => token.length > 2 && !["and", "the", "for", "with", "after", "before", "target"].includes(token));
	return tokens.length > 0 ? tokens.slice(0, 6) : ["machine", "learning"];
}

function makeCandidate(query, index, source) {
	const tokens = topicTokens(query);
	const focus = tokens[index % tokens.length];
	const partner = tokens[(index + 1) % tokens.length] ?? "systems";
	const title = `${titleCase(focus)} ${titleCase(partner)} Methods for Research Automation ${index + 1}`;
	const dedupeKey = normalizeText(title);
	const id = makeId("paper", dedupeKey);
	const year = 2026 - (index % 6);
	return {
		id,
		title,
		authors: [`A. ${titleCase(focus)}`, `B. ${titleCase(partner)}`],
		year,
		venue: index % 2 === 0 ? "Sure Offline Proceedings" : "Synthetic Research Notes",
		abstract: `Offline synthetic candidate for ${query}. It represents a search result used to validate Sure paper collection plumbing.`,
		url: `https://example.org/papers/${id}`,
		doi: `10.0000/sure.${id.slice(-8)}`,
		arxiv_id: `2601.${String(index + 1).padStart(5, "0")}`,
		source,
		source_rank: index + 1,
		relevance_score: Number((1 - index * 0.01).toFixed(3)),
		download_status: "not_requested",
		pdf_path: null,
		collection_reason: `Matched offline query token "${focus}".`,
		dedupe_key: dedupeKey,
	};
}

function writeJson(path, value) {
	writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, "utf-8");
}

function writeJsonl(path, values) {
	writeFileSync(path, values.map((value) => JSON.stringify(value)).join("\n") + "\n", "utf-8");
}

function relativePath(fromDir, path) {
	return relative(fromDir, path).replaceAll("\\", "/");
}

const args = parseArgs(process.argv.slice(2));
const artifactsDir = join(args.runDir, "artifacts");
const metadataDir = join(artifactsDir, "metadata");
mkdirSync(metadataDir, { recursive: true });

const sources = ["offline_seed", "offline_semantic_scholar_mock", "offline_arxiv_mock"];
const rawCandidates = [];
for (let index = 0; index < args.target + 3; index += 1) {
	rawCandidates.push(makeCandidate(args.query, index, sources[index % sources.length]));
}
if (rawCandidates.length > 1) {
	rawCandidates.push({ ...rawCandidates[1], source: "offline_duplicate_probe", source_rank: rawCandidates.length + 1 });
}

const byDedupeKey = new Map();
const dedupeRecords = [];
for (const candidate of rawCandidates) {
	const existing = byDedupeKey.get(candidate.dedupe_key);
	if (existing) {
		dedupeRecords.push({
			action: "duplicate",
			kept_id: existing.id,
			dropped_id: candidate.id,
			dedupe_key: candidate.dedupe_key,
			source: candidate.source,
		});
		continue;
	}
	byDedupeKey.set(candidate.dedupe_key, candidate);
	dedupeRecords.push({
		action: "keep",
		kept_id: candidate.id,
		dedupe_key: candidate.dedupe_key,
		source: candidate.source,
	});
}

const papers = Array.from(byDedupeKey.values()).slice(0, args.target);
for (const paper of papers) {
	const metadataPath = join(metadataDir, `${paper.id}.json`);
	writeJson(metadataPath, {
		...paper,
		metadata_schema: "sure.paper_metadata.v1",
		generated_at: new Date().toISOString(),
	});
	paper.metadata_path = relativePath(args.runDir, metadataPath);
}

const rawCandidatesPath = join(artifactsDir, "raw_candidates.jsonl");
const searchLogPath = join(artifactsDir, "search_log.jsonl");
const dedupeIndexPath = join(artifactsDir, "dedupe_index.json");
const failuresPath = join(artifactsDir, "failures.jsonl");
const paperCollectionPath = join(artifactsDir, "papers.manifest.json");
const finalManifestPath = join(args.runDir, "manifest.json");

writeJsonl(rawCandidatesPath, rawCandidates);
writeJsonl(
	searchLogPath,
	sources.map((source, index) => ({
		source,
		query: args.query,
		returned: rawCandidates.filter((candidate) => candidate.source === source).length,
		rank_offset: index * 10,
		timestamp: new Date().toISOString(),
	})),
);
writeJson(dedupeIndexPath, {
	schema_version: "sure.paper_dedupe.v1",
	raw_count: rawCandidates.length,
	unique_count: byDedupeKey.size,
	records: dedupeRecords,
});
writeJsonl(failuresPath, []);

const paperCollection = {
	schema_version: "sure.paper_collection.v1",
	query: args.query,
	target_count: args.target,
	collected_count: papers.length,
	generated_at: new Date().toISOString(),
	sources: sources.map((source) => ({ name: source, mode: "offline_mock" })),
	deduplication: {
		key: "normalized_title",
		raw_count: rawCandidates.length,
		unique_count: byDedupeKey.size,
		duplicate_count: rawCandidates.length - byDedupeKey.size,
		index_path: relativePath(args.runDir, dedupeIndexPath),
	},
	files: {
		raw_candidates: relativePath(args.runDir, rawCandidatesPath),
		search_log: relativePath(args.runDir, searchLogPath),
		failures: relativePath(args.runDir, failuresPath),
		metadata_dir: relativePath(args.runDir, metadataDir),
	},
	papers,
};
writeJson(paperCollectionPath, paperCollection);

const projectManifestPath = `.sure/runs/${args.runId}/artifacts/papers.manifest.json`;
const finalManifest = {
	schema_version: "1",
	run_id: args.runId,
	skill_name: args.skillName,
	status: papers.length >= args.target ? "success" : "incomplete",
	created_at: new Date().toISOString(),
	inputs: {
		query: args.query,
		target_count: args.target,
		mode: "offline_mock",
	},
	outputs: {
		paper_collection: projectManifestPath,
		collected_count: papers.length,
		target_count: args.target,
		raw_count: rawCandidates.length,
		unique_count: byDedupeKey.size,
	},
	validation: {
		passed: papers.length >= args.target,
		checks: {
			target_count_met: papers.length >= args.target,
			no_duplicate_dedupe_keys: new Set(papers.map((paper) => paper.dedupe_key)).size === papers.length,
			required_fields_present: papers.every(
				(paper) => paper.id && paper.title && paper.year && paper.authors && paper.source && paper.dedupe_key,
			),
		},
	},
	artifacts: [
		{
			type: "paper_collection",
			path: projectManifestPath,
		},
		{
			type: "search_log",
			path: `.sure/runs/${args.runId}/artifacts/search_log.jsonl`,
		},
		{
			type: "dedupe_index",
			path: `.sure/runs/${args.runId}/artifacts/dedupe_index.json`,
		},
	],
};
writeJson(finalManifestPath, finalManifest);

console.log(
	JSON.stringify({
		status: finalManifest.status,
		run_id: args.runId,
		query: args.query,
		target_count: args.target,
		collected_count: papers.length,
		manifest_path: finalManifestPath,
		paper_collection_path: paperCollectionPath,
	}),
);
