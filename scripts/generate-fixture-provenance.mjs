#!/usr/bin/env node
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, join, relative, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const profiles = JSON.parse(readFileSync(join(root, "sure/runtime/evaluation/harness-task-profiles.json"), "utf8"));
const sourceRegistry = JSON.parse(readFileSync(join(root, "fixtures/source-registry.json"), "utf8"));
const checkOnly = process.argv.includes("--check");
let stale = false;

const taskSources = {
	asr: "librispeech",
	classification: "librispeech",
	gr: "librispeech",
	kws: "librispeech",
	s2tt: "covost2",
	sa_asr: "librispeech",
	sd: "librispeech",
	se: "librispeech",
	ser: "crema_d",
	slu: "fluent_speech_commands",
	sv: "librispeech",
	tse: "librispeech",
	tts: "librispeech",
	vad: "librispeech",
	vc: "librispeech",
};

const transforms = {
	sa_asr: {
		"librispeech_2spk_001.wav": {
			id: "legacy_concat_001",
			command: "ffmpeg concat 1089-134686-0001.wav + 0.4s silence + 1188-133604-0010.wav",
			source_sha256: ["6b0b24796a1017a65007d392b9f8d6e64e030cdafbf82d8ffa4235fe4b371443", "f0ba1d597a718a89e2906bd2173cf77d968efe5deee9248bd7cd71debafc3194"],
		},
		"librispeech_2spk_002.wav": {
			id: "legacy_concat_002",
			command: "ffmpeg concat 121-121726-0001.wav + 0.4s silence + 1221-135766-0002.wav",
			source_sha256: ["b1657235d4fbcfb2d8468b738e3c77b8bf171dc90ad6c1ca7cd7f57fb994059b", "65b222837919ccbb924a4e1077413ea7cc6af3e68b663b012a9539d5c05850f0"],
		},
		"librispeech_2spk_003.wav": {
			id: "legacy_concat_003",
			command: "ffmpeg concat 1284-1180-0001.wav + 0.4s silence + 1320-122612-0002.wav",
			source_sha256: ["7d6d027a1d7f75e8704ee7d3ffc9a7c18f37d4c26049f00794d042551c7fa2e0", "096812d54a8d3c4c5723a98bae12c783e087c98a407bc8094dcf2be1eb0cc3ad"],
		},
	},
	sd: "same_as_sa_asr",
	se: {
		"noisy_1580.wav": { id: "mix_noise_1580", command: "ffmpeg -i 1580-141083-0040.wav -i 1089-134691-0001.wav -filter_complex '[1:a]volume=0.12[n];[0:a][n]amix=inputs=2:duration=first' -ar 16000 -ac 1 -c:a pcm_s16le noisy_1580.wav", inputs: ["reference_1580.wav", "interference_1089.wav"] },
		"noisy_1089.wav": { id: "mix_noise_1089", command: "ffmpeg -i 1089-134691-0001.wav -i 1580-141083-0042.wav -filter_complex '[1:a]volume=0.12[n];[0:a][n]amix=inputs=2:duration=first' -ar 16000 -ac 1 -c:a pcm_s16le noisy_1089.wav", inputs: ["reference_1089.wav", "interference_1580.wav"] },
	},
	tse: {
		"mixture_1580_1089.wav": { id: "mix_tse_1580", command: "ffmpeg -i 1580-141083-0040.wav -i 1089-134691-0001.wav -filter_complex '[0:a]volume=0.5[a];[1:a]volume=0.5[b];[a][b]amix=inputs=2:duration=longest' -ar 16000 -ac 1 -c:a pcm_s16le mixture_1580_1089.wav", inputs: ["reference_1580.wav", "reference_1089.wav"] },
		"mixture_1089_1580.wav": { id: "mix_tse_1089", command: "ffmpeg -i 1089-134691-0001.wav -i 1580-141083-0040.wav -filter_complex '[0:a]volume=0.5[a];[1:a]volume=0.5[b];[a][b]amix=inputs=2:duration=longest' -ar 16000 -ac 1 -c:a pcm_s16le mixture_1089_1580.wav", inputs: ["reference_1089.wav", "reference_1580.wav"] },
	},
	vad: {
		"padded_1580.wav": { id: "pad_vad_1580", command: "ffmpeg -i 1580-141083-0040.wav -af 'adelay=500,apad=pad_dur=0.5' -ar 16000 -ac 1 -c:a pcm_s16le padded_1580.wav", inputs: ["source_1580.wav"] },
		"padded_1089.wav": { id: "pad_vad_1089", command: "ffmpeg -i 1089-134691-0001.wav -af 'adelay=500,apad=pad_dur=0.5' -ar 16000 -ac 1 -c:a pcm_s16le padded_1089.wav", inputs: ["source_1089.wav"] },
	},
};
transforms.sd = transforms.sa_asr;

const audioPattern = /\.(?:flac|mp3|ogg|wav)$/i;
const hash = (path) => createHash("sha256").update(readFileSync(path)).digest("hex");
const canonicalHash = (value) => createHash("sha256").update(JSON.stringify(value)).digest("hex");

function readJsonl(path) {
	return readFileSync(path, "utf8")
		.split("\n")
		.filter(Boolean)
		.map((line) => JSON.parse(line));
}

function rolesByFile(rows) {
	const result = new Map();
	for (const row of rows) {
		for (const [field, value] of Object.entries(row)) {
			if (typeof value !== "string" || !audioPattern.test(value)) continue;
			const name = basename(value);
			const roles = result.get(name) ?? new Set();
			roles.add(field);
			result.set(name, roles);
		}
	}
	return result;
}

for (const [task, profile] of Object.entries(profiles.tasks)) {
	const fixtureRoot = join(root, profile.fixture_root);
	const groundTruthPath = join(fixtureRoot, "gt.jsonl");
	const rows = readJsonl(groundTruthPath);
	const fileRoles = rolesByFile(rows);
	const taskTransforms = transforms[task] ?? {};
	const files = readdirSync(fixtureRoot)
		.filter((name) => audioPattern.test(name))
		.sort()
		.map((name) => {
			const path = join(fixtureRoot, name);
			const transform = taskTransforms[name];
			const entry = {
				path: name,
				roles: [...(fileRoles.get(name) ?? ["transform_source"])].sort(),
				sha256: hash(path),
				transform_id: transform?.id ?? "source_media_copy",
			};
			if (transform?.inputs) entry.source_sha256 = transform.inputs.map((input) => hash(join(fixtureRoot, input)));
			else if (transform?.source_sha256) entry.source_sha256 = transform.source_sha256;
			else entry.source_sha256 = entry.sha256;
			return entry;
		});
	const transformList = [
		{ id: "source_media_copy", command: "copy source media bytes without modification; verify SHA-256" },
		...Object.values(taskTransforms),
	]
		.filter((item, index, all) => all.findIndex((candidate) => candidate.id === item.id) === index)
		.map(({ id, command }) => ({ id, command }));
	const metadata = rows.map((row) => ({
		key: row.key ?? row.id,
		task: row.task ?? task,
		label: row.label ?? row.ground_truth ?? row.reference_text ?? row.text ?? row.segments ?? row.speech_segments,
	}));
	const sourceId = taskSources[task];
	const payload = {
		schema: "sure.fixture_provenance.v2",
		task,
		dataset: {
			...sourceRegistry.sources[sourceId],
			source_id: sourceId,
			metadata_sha256: canonicalHash(metadata),
			metadata_hash_basis: "canonical selected key/task/annotation projection",
		},
		selection: {
			rule: "deterministic smoke subset with task-native labels and at most five inference samples",
			sample_ids: rows.map((row) => String(row.key ?? row.id)),
		},
		annotations: {
			source: task === "ser" || task === "slu" ? "dataset-published labels" : "dataset metadata or deterministic fixture transform",
			ground_truth_path: "gt.jsonl",
			ground_truth_sha256: hash(groundTruthPath),
		},
		transforms: transformList,
		files,
	};
	for (const extra of ["trial_manifest.json", "trials.tsv"]) {
		const path = join(fixtureRoot, extra);
		try {
			payload.files.push({ path: extra, roles: [extra === "trials.tsv" ? "trials" : "trial_manifest"], sha256: hash(path), source_sha256: hash(path), transform_id: "source_media_copy" });
		} catch (error) {
			if (!(error instanceof Error) || !error.message.includes("ENOENT")) throw error;
		}
	}
	const outputPath = join(fixtureRoot, "provenance.json");
	const output = `${JSON.stringify(payload, null, 2)}\n`;
	if (checkOnly) {
		let actual = "";
		try {
			actual = readFileSync(outputPath, "utf8");
		} catch (error) {
			if (!(error instanceof Error) || !error.message.includes("ENOENT")) throw error;
		}
		if (actual !== output) {
			console.error(`stale fixture provenance: ${relative(root, outputPath)}`);
			stale = true;
		}
	} else {
		writeFileSync(outputPath, output);
		console.log(`updated ${relative(root, outputPath)}`);
	}
}

if (checkOnly) {
	if (stale) process.exit(1);
	console.log(`ok   fixture provenance generation: ${Object.keys(profiles.tasks).length} selected fixtures`);
}
