#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
	existsSync,
	lstatSync,
	mkdirSync,
	readdirSync,
	readFileSync,
	readlinkSync,
	realpathSync,
	statSync,
	writeFileSync,
} from "node:fs";
import { dirname, extname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import {
	advance as advanceApprove,
	retryExhausted as approveRetryExhausted,
	bumpRetry as bumpRetryApprove,
	initialCheckpoint as initialApproveCheckpoint,
} from "../sure/skills/sure_approve/hooks/checkpoints.ts";
import { APPROVE_UNITS, unitsForMode } from "../sure/skills/sure_approve/hooks/state-machine.ts";
import {
	advance as advanceEval,
	bumpRetry as bumpRetryEval,
	type CheckpointData as EvalCheckpointData,
	retryExhausted as evalRetryExhausted,
} from "../sure/skills/sure_eval/hooks/checkpoints.ts";
import { MAIN_FLOW_UNITS as EVAL_UNITS } from "../sure/skills/sure_eval/hooks/state-machine.ts";
import {
	advance as advanceFeed,
	bumpRetry as bumpRetryFeed,
	type CheckpointData as FeedCheckpointData,
	retryExhausted as feedRetryExhausted,
} from "../sure/skills/sure_feed/hooks/checkpoints.ts";
import { MODEL_FEED_UNITS } from "../sure/skills/sure_feed/hooks/state-machine.ts";
import {
	advance as advanceInfer,
	bumpRetry as bumpRetryInfer,
	type CheckpointData as InferCheckpointData,
	retryExhausted as inferRetryExhausted,
} from "../sure/skills/sure_infer/hooks/checkpoints.ts";
import { MAIN_FLOW_UNITS as INFER_UNITS } from "../sure/skills/sure_infer/hooks/state-machine.ts";
import {
	advance as advanceOnboard,
	bumpRetry as bumpRetryOnboard,
	type CheckpointData as OnboardCheckpointData,
	retryExhausted as onboardRetryExhausted,
} from "../sure/skills/sure_onboard/hooks/checkpoints.ts";
import { MODEL_TOOL_UNITS as ONBOARD_UNITS } from "../sure/skills/sure_onboard/hooks/state-machine.ts";
import {
	advance as advanceTrans,
	bumpRetry as bumpRetryTrans,
	type CheckpointData as TransCheckpointData,
	retryExhausted as transRetryExhausted,
} from "../sure/skills/sure_trans/hooks/checkpoints.ts";
import { TRANS_UNITS } from "../sure/skills/sure_trans/hooks/state-machine.ts";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const fixtureRoot = join(repositoryRoot, "fixtures", "sure", "characterization");
const repositoryBaselinePath = join(fixtureRoot, "repository-baseline.v1.json");
const maxReferenceHashBytes = 2 * 1024 * 1024;
const digestA = "a".repeat(64);
const digestB = "b".repeat(64);

type JsonScalar = boolean | number | string | null;
type JsonValue = JsonScalar | JsonValue[] | { [key: string]: JsonValue };

interface UnitLike {
	id: string;
	label: string;
	kind?: string;
	produces: string;
	schemaRef?: string;
	requiredFields?: string[];
	allowedValues?: Record<string, unknown[]>;
	forbiddenFields?: string[];
	gateCheck?: (artifact: unknown) => unknown;
	gateScript?: string;
	gateScriptArgs?: string[] | ((context: never) => string[]);
	helperScripts?: string[];
	ownedScripts?: string[];
	gateInputs?: string[];
}

interface BaseCheckpointData {
	currentUnit: string;
	completedUnits: string[];
	retries: Record<string, number>;
	blocks?: number;
	failedArtifactDigests?: Record<string, string>;
}

interface CheckpointLike<Data extends BaseCheckpointData> {
	resumable: boolean;
	resume_hint: string;
	data: Data;
}

export interface ReferenceTarget {
	logicalPath: string;
	roles: string[];
	expect?: "directory" | "file" | "symlink";
}

export interface ReferenceCatalogOptions {
	referenceRoot: string;
	observedAt: string;
	targets?: ReferenceTarget[];
}

const defaultReferenceTargets: ReferenceTarget[] = [
	{
		logicalPath: "models/CohereLabs__cohere-transcribe-03-2026/model.spec.yaml",
		roles: ["sure-feed", "success", "model-input"],
		expect: "file",
	},
	{
		logicalPath: "models/CohereLabs__cohere-transcribe-03-2026/artifacts/artifact_manifest.json",
		roles: ["sure-onboard", "success", "artifact-contract"],
		expect: "file",
	},
	{
		logicalPath: "models/CohereLabs__cohere-transcribe-03-2026/artifacts/runtime_inventory.json",
		roles: ["sure-onboard", "success", "runtime-identity"],
		expect: "file",
	},
	{
		logicalPath: "models/CohereLabs__cohere-transcribe-03-2026/artifacts/deployment_ready.json",
		roles: ["sure-onboard", "success", "bundle-identity"],
		expect: "file",
	},
	{
		logicalPath: "models/CohereLabs__cohere-transcribe-03-2026/artifacts/verdict.json",
		roles: ["sure-onboard", "success", "terminal-verdict"],
		expect: "file",
	},
	{
		logicalPath: "models/FunAudioLLM__Fun-CosyVoice3-0.5B-2512/model.spec.yaml",
		roles: ["sure-trans", "success", "model-input"],
		expect: "file",
	},
	{
		logicalPath: "models/FunAudioLLM__Fun-CosyVoice3-0.5B-2512/artifacts/runtime_inventory.json",
		roles: ["sure-trans", "success", "runtime-identity"],
		expect: "file",
	},
	{
		logicalPath: "models/FunAudioLLM__Fun-CosyVoice3-0.5B-2512/artifacts/deployment_ready.json",
		roles: ["sure-trans", "success", "bundle-identity"],
		expect: "file",
	},
	{
		logicalPath:
			"models/FunAudioLLM__Fun-CosyVoice3-0.5B-2512/eval_runs/main_agent_FunAudioLLM__Fun-CosyVoice3-0.5B-2512_001/execution_surface.json",
		roles: ["sure-infer", "success", "execution-protocol"],
		expect: "file",
	},
	{
		logicalPath:
			"models/FunAudioLLM__Fun-CosyVoice3-0.5B-2512/eval_runs/main_agent_FunAudioLLM__Fun-CosyVoice3-0.5B-2512_001/prediction_generation_status.json",
		roles: ["sure-infer", "success", "execution-outcome"],
		expect: "file",
	},
	{
		logicalPath:
			"models/FunAudioLLM__Fun-CosyVoice3-0.5B-2512/eval_runs/main_agent_FunAudioLLM__Fun-CosyVoice3-0.5B-2512_001/protocol.yaml",
		roles: ["sure-infer", "sure-eval", "success", "frozen-protocol"],
		expect: "file",
	},
	{
		logicalPath:
			"models/FunAudioLLM__Fun-CosyVoice3-0.5B-2512/eval_runs/main_agent_FunAudioLLM__Fun-CosyVoice3-0.5B-2512_001/main_agent_run_report.json",
		roles: ["sure-infer", "success", "terminal-report"],
		expect: "file",
	},
	{
		logicalPath: "results/FunAudioLLM__Fun-CosyVoice3-0.5B-2512/standard_system/protocol.yaml",
		roles: ["sure-eval", "success", "frozen-protocol"],
		expect: "file",
	},
	{
		logicalPath: "results/FunAudioLLM__Fun-CosyVoice3-0.5B-2512/standard_system/report.jsonl",
		roles: ["sure-eval", "success", "formal-results"],
		expect: "file",
	},
	{
		logicalPath:
			"results/FunAudioLLM__SenseVoiceSmall.bad_protocol_20260805/main_agent_funaudiollm__SenseVoiceSmall_001/protocol.yaml",
		roles: ["sure-eval", "failure", "bad-protocol"],
		expect: "file",
	},
	{
		logicalPath:
			"results/FunAudioLLM__SenseVoiceSmall.bad_protocol_20260805/main_agent_funaudiollm__SenseVoiceSmall_001/report.jsonl",
		roles: ["sure-eval", "failure", "bad-protocol"],
		expect: "file",
	},
	{
		logicalPath:
			"results/zz-dup-nested-verified-identical-deleteme/FunAudioLLM__Fun-CosyVoice3-0.5B-2512/standard_system",
		roles: ["sure-eval", "failure", "nested-duplicate"],
		expect: "directory",
	},
	{
		logicalPath:
			"models/.sure-repair-rollback-20260831T004154/FunAudioLLM__SenseVoiceSmall/artifacts/migration_manifest.json",
		roles: ["sure-approve", "rollback", "provenance"],
		expect: "file",
	},
	{
		logicalPath:
			"models/.sure-repair-rollback-20260831T004154/FunAudioLLM__SenseVoiceSmall/artifacts/deployment_ready.json",
		roles: ["sure-approve", "rollback", "human-review-boundary"],
		expect: "file",
	},
	{
		logicalPath: "models/FireRedTeam__FireRedASR-LLM-L/.venv",
		roles: ["sure-onboard", "sure-trans", "external-runtime-symlink"],
		expect: "symlink",
	},
];

function pathForJson(path: string): string {
	return path.split(sep).join("/");
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function canonicalize(value: unknown): JsonValue {
	if (value === null || typeof value === "string" || typeof value === "boolean") return value;
	if (typeof value === "number") {
		if (!Number.isFinite(value)) throw new Error("Cannot canonicalize a non-finite number.");
		return value;
	}
	if (Array.isArray(value)) return value.map(canonicalize);
	if (isRecord(value)) {
		const result: { [key: string]: JsonValue } = {};
		for (const key of Object.keys(value).sort()) {
			if (value[key] !== undefined) result[key] = canonicalize(value[key]);
		}
		return result;
	}
	throw new Error(`Cannot canonicalize value of type ${typeof value}.`);
}

export function stableJson(value: unknown): string {
	return JSON.stringify(canonicalize(value));
}

function sha256Bytes(value: string | Buffer): string {
	return createHash("sha256").update(value).digest("hex");
}

export function semanticDigest(value: unknown): string {
	if (!isRecord(value)) return sha256Bytes(stableJson(value));
	const { semantic_digest: _ignored, ...payload } = value;
	return sha256Bytes(stableJson(payload));
}

function withSemanticDigest<T extends Record<string, unknown>>(value: T): T & { semantic_digest: string } {
	return { ...value, semantic_digest: semanticDigest(value) };
}

function normalizeUnit(unit: UnitLike): Record<string, unknown> {
	return {
		id: unit.id,
		label: unit.label,
		kind: unit.kind ?? "gate",
		produces: unit.produces,
		schema_ref: unit.schemaRef ?? null,
		required_fields: unit.requiredFields ?? [],
		allowed_values: unit.allowedValues ?? {},
		forbidden_fields: unit.forbiddenFields ?? [],
		gate_script: unit.gateScript ?? null,
		gate_script_args: typeof unit.gateScriptArgs === "function" ? "contextual-function" : (unit.gateScriptArgs ?? []),
		has_in_process_gate: unit.gateCheck !== undefined,
		helper_scripts: unit.helperScripts ?? [],
		owned_scripts: unit.ownedScripts ?? [],
		gate_inputs: unit.gateInputs ?? [],
	};
}

function transitionTrace<Unit extends UnitLike, Data extends BaseCheckpointData>(
	units: Unit[],
	initial: Data,
	advance: (unit: Unit, data: Data) => CheckpointLike<Data> | undefined,
): Record<string, unknown> {
	let data = initial;
	const transitions: Record<string, unknown>[] = [];
	for (const unit of units) {
		if (data.currentUnit !== unit.id) {
			throw new Error(`Trace drift: expected ${unit.id}, checkpoint is at ${data.currentUnit}.`);
		}
		const checkpoint = advance(unit, data);
		if (!checkpoint) throw new Error(`Trace unexpectedly stopped at ${unit.id}.`);
		transitions.push({
			unit: unit.id,
			from: data.currentUnit,
			to: checkpoint.data.currentUnit,
			completed_before: [...data.completedUnits],
			completed_after: [...checkpoint.data.completedUnits],
			resumable: checkpoint.resumable,
			resume_hint: checkpoint.resume_hint,
		});
		data = checkpoint.data;
	}

	const terminalUnit = units.at(-1);
	if (!terminalUnit) throw new Error("A SURE workflow cannot have zero units.");
	const terminalReentry = advance(terminalUnit, data);
	if (!terminalReentry) throw new Error(`Terminal re-entry unexpectedly stopped at ${terminalUnit.id}.`);
	return {
		transitions,
		terminal: {
			current_unit: data.currentUnit,
			completed_units: data.completedUnits,
			resumable: transitions.at(-1)?.resumable,
		},
		terminal_reentry: {
			completed_units: terminalReentry.data.completedUnits,
			double_counted: terminalReentry.data.completedUnits.length !== data.completedUnits.length,
			resumable: terminalReentry.resumable,
		},
	};
}

function retryProfile<Unit extends UnitLike, Data extends BaseCheckpointData>(
	unit: Unit,
	initial: Data,
	bumpRetry: (unit: Unit, data: Data, artifactDigest?: string) => CheckpointLike<Data>,
	retryExhausted: (unit: Unit, data: Data) => boolean,
	advance: (unit: Unit, data: Data) => CheckpointLike<Data> | undefined,
): Record<string, unknown> {
	const first = bumpRetry(unit, initial, digestA);
	const second = bumpRetry(unit, first.data, digestA);
	const third = bumpRetry(unit, second.data, digestB);
	const cleared = advance(unit, third.data);
	if (!cleared) throw new Error(`Retry characterization could not advance ${unit.id}.`);
	return {
		unit: unit.id,
		attempts: [first.data.retries[unit.id], second.data.retries[unit.id], third.data.retries[unit.id]],
		blocks: [first.data.blocks, second.data.blocks, third.data.blocks],
		failed_digest_after_same_bytes: second.data.failedArtifactDigests?.[unit.id],
		failed_digest_after_changed_bytes: third.data.failedArtifactDigests?.[unit.id],
		exhausted_after_attempt: [
			retryExhausted(unit, first.data),
			retryExhausted(unit, second.data),
			retryExhausted(unit, third.data),
		],
		advance_clears_current_retry: cleared.data.retries[unit.id] === undefined,
		advance_clears_current_digest: cleared.data.failedArtifactDigests?.[unit.id] === undefined,
		advance_preserves_blocks: cleared.data.blocks === third.data.blocks,
	};
}

function approveTrace(mode: "audit" | "approve"): Record<string, unknown> {
	let checkpoint = initialApproveCheckpoint(mode);
	const transitions: Record<string, unknown>[] = [];
	for (const unit of unitsForMode(mode)) {
		const before = checkpoint;
		checkpoint = advanceApprove(checkpoint);
		transitions.push({
			unit: unit.id,
			from: before.data.currentUnit,
			to: checkpoint.data.currentUnit,
			completed_before: before.data.completedUnits,
			completed_after: checkpoint.data.completedUnits,
			resumable: checkpoint.resumable,
			resume_hint: checkpoint.resume_hint,
		});
	}
	const terminalCount = checkpoint.data.completedUnits.length;
	checkpoint = advanceApprove(checkpoint);
	return {
		mode,
		transitions,
		terminal: {
			current_unit: checkpoint.data.currentUnit,
			completed_units: checkpoint.data.completedUnits,
			resumable: checkpoint.resumable,
			double_counted_on_reentry: checkpoint.data.completedUnits.length !== terminalCount,
		},
	};
}

function approveRetryProfile(): Record<string, unknown> {
	const initial = initialApproveCheckpoint("audit");
	const first = bumpRetryApprove(initial, digestA);
	const second = bumpRetryApprove(first, digestA);
	const third = bumpRetryApprove(second, digestB);
	const cleared = advanceApprove(third);
	return {
		unit: initial.data.currentUnit,
		attempts: [
			first.data.retries[initial.data.currentUnit],
			second.data.retries[initial.data.currentUnit],
			third.data.retries[initial.data.currentUnit],
		],
		blocks: [first.data.blocks, second.data.blocks, third.data.blocks],
		exhausted_after_attempt: [
			approveRetryExhausted(first),
			approveRetryExhausted(second),
			approveRetryExhausted(third),
		],
		advance_clears_current_retry: cleared.data.retries[initial.data.currentUnit] === undefined,
		advance_clears_current_digest: cleared.data.failedArtifactDigests[initial.data.currentUnit] === undefined,
		advance_preserves_blocks: cleared.data.blocks === third.data.blocks,
	};
}

function walkFiles(root: string): string[] {
	if (!existsSync(root)) return [];
	const files: string[] = [];
	const walk = (directory: string): void => {
		for (const entry of readdirSync(directory, { withFileTypes: true }).sort((a, b) =>
			a.name.localeCompare(b.name),
		)) {
			const path = join(directory, entry.name);
			const relativePath = pathForJson(relative(repositoryRoot, path));
			if (
				entry.name === "__pycache__" ||
				entry.name === ".runtime" ||
				entry.name.startsWith("tmp-") ||
				relativePath.startsWith("sure/external/")
			) {
				continue;
			}
			if (entry.isDirectory()) walk(path);
			else if (entry.isFile()) files.push(path);
		}
	};
	walk(root);
	return files;
}

function sourcePaths(): string[] {
	const paths = [
		...walkFiles(join(repositoryRoot, "sure", "skills")),
		...walkFiles(join(repositoryRoot, "sure", "runtime")),
		...walkFiles(join(repositoryRoot, "sure", "site")),
		...walkFiles(join(repositoryRoot, "packages", "coding-agent", "src", "core", "sure")),
		...walkFiles(join(repositoryRoot, "packages", "coding-agent", "test", "sure")),
		...walkFiles(join(repositoryRoot, "packages", "coding-agent", "test", "suite")).filter((path) =>
			/^sure-.*\.test\.ts$/.test(path.split(sep).at(-1) ?? ""),
		),
	].filter((path) => !path.endsWith(".pyc"));
	return [...new Set(paths)].sort((left, right) => left.localeCompare(right));
}

function fileInventory(paths: string[]): Record<string, unknown>[] {
	return paths.map((path) => {
		const stat = statSync(path);
		return {
			path: pathForJson(relative(repositoryRoot, path)),
			size_bytes: stat.size,
			sha256: sha256Bytes(readFileSync(path)),
		};
	});
}

function schemaProfiles(paths: string[]): Record<string, unknown>[] {
	return paths
		.filter((path) => path.includes(`${sep}schemas${sep}`) && path.endsWith(".json"))
		.map((path) => {
			const parsed: unknown = JSON.parse(readFileSync(path, "utf8"));
			const schema = isRecord(parsed) ? parsed : {};
			return {
				path: pathForJson(relative(repositoryRoot, path)),
				type: schema.type ?? null,
				required: Array.isArray(schema.required) ? schema.required : [],
				additional_properties: schema.additionalProperties ?? "unspecified",
				property_names: isRecord(schema.properties) ? Object.keys(schema.properties).sort() : [],
			};
		});
}

function evaluationSubmoduleIdentity(): Record<string, unknown> {
	const gitLink = readFileSync(join(repositoryRoot, ".gitmodules"), "utf8");
	const revision = spawnSync("git", ["rev-parse", "HEAD:sure/external/sure-evaluation"], {
		cwd: repositoryRoot,
		encoding: "utf8",
	});
	if (revision.status !== 0 || !/^[0-9a-f]{40}$/.test(revision.stdout.trim())) {
		throw new Error(revision.stderr.trim() || "Cannot resolve the pinned evaluation-engine gitlink.");
	}
	return {
		path: "sure/external/sure-evaluation",
		gitmodules_sha256: sha256Bytes(gitLink),
		gitlink_commit: revision.stdout.trim(),
	};
}

function evaluationIdentityVectors(): Record<string, unknown> {
	const common = {
		model_fingerprint: "1".repeat(64),
		protocol_id: "standard_system",
		prediction_sha256: "2".repeat(64),
		reference_sha256: "3".repeat(64),
		reference_samples: 3,
		metric: "wer",
		pipeline_id: "asr_wer_standard",
		nodes: [{ id: "normalize", implementation: "asr_simple_tn" }],
		engine_commit: "4".repeat(40),
		engine_tree_sha256: "5".repeat(64),
	};
	const identities = [
		{ ...common, dataset: "librispeech_clean__v1.0.1" },
		{ ...common, dataset: "aishell1__v1.0.2" },
	];
	const records = identities.map((identity) => {
		const recordId = sha256Bytes(stableJson(identity));
		return { identity, record_id: recordId, run_id: `sure_eval_${recordId.slice(0, 16)}` };
	});
	const recordIds = records.map((record) => record.record_id).sort();
	const batchDigest = sha256Bytes(stableJson({ record_ids: recordIds }));
	return {
		authority_source: "sure/skills/sure_infer/scripts/run_eval.py",
		canonicalization: "UTF-8 JSON, keys sorted, compact separators",
		records,
		batch: { record_ids: recordIds, batch_id: `sure_eval_${batchDigest.slice(0, 24)}` },
	};
}

function siblingSkillDependencies(): Record<string, unknown>[] {
	return [
		{
			consumer: "sure-eval",
			provider: "sure-infer",
			kind: "runtime-sibling-package-path",
			surfaces: [
				"scripts/run_eval.py",
				"scripts/resolve_prediction_source.py",
				"scripts/evaluation_runtime.py",
				"references/memory/ROUTING.md",
			],
			evidence: [
				"sure/skills/sure_eval/hooks/index.ts",
				"sure/skills/sure_eval/SKILL.md",
				"sure/skills/sure_eval/scripts/check_assessment.py",
				"sure/skills/sure_eval/scripts/check_run_report.py",
			],
		},
	];
}

function emptyData(firstUnit: string) {
	return {
		currentUnit: firstUnit,
		completedUnits: [],
		retries: {},
		failedArtifactDigests: {},
	};
}

export function buildRepositoryBaseline(): Record<string, unknown> {
	const sources = sourcePaths();
	const feedInitial: FeedCheckpointData = emptyData(MODEL_FEED_UNITS[0].id);
	const onboardInitial: OnboardCheckpointData = emptyData(ONBOARD_UNITS[0].id);
	const transInitial: TransCheckpointData = emptyData(TRANS_UNITS[0].id);
	const inferInitial: InferCheckpointData = emptyData(INFER_UNITS[0].id);
	const evalInitial: EvalCheckpointData = emptyData(EVAL_UNITS[0].id);
	return withSemanticDigest({
		schema: "sure.characterization.repository.v1",
		purpose: "Freeze pre-refactor semantics; this fixture is evidence, not a runtime authority.",
		workflow_units: {
			"sure-feed": MODEL_FEED_UNITS.map(normalizeUnit),
			"sure-onboard": ONBOARD_UNITS.map(normalizeUnit),
			"sure-trans": TRANS_UNITS.map(normalizeUnit),
			"sure-infer": INFER_UNITS.map(normalizeUnit),
			"sure-eval": EVAL_UNITS.map(normalizeUnit),
			"sure-approve": APPROVE_UNITS.map(normalizeUnit),
		},
		transition_traces: {
			"sure-feed": transitionTrace(MODEL_FEED_UNITS, feedInitial, advanceFeed),
			"sure-onboard": transitionTrace(ONBOARD_UNITS, onboardInitial, advanceOnboard),
			"sure-trans": transitionTrace(TRANS_UNITS, transInitial, advanceTrans),
			"sure-infer": transitionTrace(INFER_UNITS, inferInitial, advanceInfer),
			"sure-eval": transitionTrace(EVAL_UNITS, evalInitial, advanceEval),
			"sure-approve:audit": approveTrace("audit"),
			"sure-approve:approve": approveTrace("approve"),
		},
		retry_profiles: {
			"sure-feed": retryProfile(MODEL_FEED_UNITS[0], feedInitial, bumpRetryFeed, feedRetryExhausted, advanceFeed),
			"sure-onboard": retryProfile(
				ONBOARD_UNITS[0],
				onboardInitial,
				bumpRetryOnboard,
				onboardRetryExhausted,
				advanceOnboard,
			),
			"sure-trans": retryProfile(TRANS_UNITS[0], transInitial, bumpRetryTrans, transRetryExhausted, advanceTrans),
			"sure-infer": retryProfile(INFER_UNITS[0], inferInitial, bumpRetryInfer, inferRetryExhausted, advanceInfer),
			"sure-eval": retryProfile(EVAL_UNITS[0], evalInitial, bumpRetryEval, evalRetryExhausted, advanceEval),
			"sure-approve": approveRetryProfile(),
		},
		conditional_execution_profiles: {
			"sure-trans": {
				discriminator_unit: "load_trans_input",
				source_kinds: ["docker", "python"],
				package_profiles: ["docker-registry", "none"],
				shared_unit_order: TRANS_UNITS.map((unit) => unit.id),
				contract_schemas: [
					"source_image_result.schema.json",
					"adapter_image_result.schema.json",
					"docker_registry_result.schema.json",
				],
			},
		},
		orchestration_invariants: [
			{
				id: "unchanged-invalid-bytes-do-not-consume-retry",
				evidence: ["packages/coding-agent/test/suite/sure-feed.test.ts"],
			},
			{
				id: "missing-artifact-stays-without-retry",
				evidence: ["packages/coding-agent/test/suite/sure-trans-adapter-draft.test.ts"],
			},
			{
				id: "terminal-gate-runs-before-finish-and-does-not-double-count",
				evidence: [
					"packages/coding-agent/test/suite/sure-infer-state-machine.test.ts",
					"packages/coding-agent/test/suite/sure-trans-terminal.test.ts",
				],
			},
			{
				id: "debug-artifact-and-absolute-produces-paths-are-supported",
				evidence: ["packages/coding-agent/test/suite/sure-feed.test.ts"],
			},
			{
				id: "approve-audit-and-decision-are-distinct-flows",
				evidence: ["packages/coding-agent/test/suite/sure-approve-state-machine.test.ts"],
			},
			{
				id: "pi-lifecycle-enforces-pre-start-pre-finish-post-finish-and-on-error",
				evidence: ["packages/coding-agent/test/suite/sure-extension.test.ts"],
			},
		],
		legacy_contract: {
			format: "unversioned-pi-run-record-plus-schema-v1-result",
			files: [
				"legacy-run.v1.fixture.json",
				"legacy-state.v1.fixture.json",
				"legacy-events.v1.fixture.jsonl",
				"legacy-result.v1.fixture.json",
			],
			final_manifest_required_fields: [
				"schema_version",
				"run_id",
				"skill_name",
				"status",
				"created_at",
				"inputs",
				"outputs",
				"validation",
			],
			strict_additional_properties: false,
		},
		current_outcome_domains: {
			run_status: ["pending", "running", "success", "failed", "incomplete", "cancelled"],
			gate_result: ["pass", "block", "missing", "runner-failed"],
			capability_missing_is_first_class: false,
			note: "The target outcome algebra is intentionally not backported into this characterization PR.",
		},
		schema_profiles: schemaProfiles(sources),
		source_files: fileInventory(sources),
		evaluation_engine: evaluationSubmoduleIdentity(),
		evaluation_identity_vectors: evaluationIdentityVectors(),
		sibling_skill_dependencies: siblingSkillDependencies(),
	});
}

const legacyFixtures: Record<string, unknown> = {
	"legacy-run.v1.fixture.json": {
		runId: "20000101-000000-deadbeef",
		skillName: "sure_infer",
		command: "sure_infer",
		status: "running",
		cwd: "/workspace/project",
		packageDir: "/workspace/project/.pi/skills/sure_infer",
		runDir: "/workspace/project/.sure/runs/20000101-000000-deadbeef",
		args: "model=example dataset=fixture",
		startedAt: "2000-01-01T00:00:00.000Z",
		updatedAt: "2000-01-01T00:00:01.000Z",
	},
	"legacy-state.v1.fixture.json": {
		phase: { id: "execute_inference", label: "Execute inference", status: "running" },
		message: "Execute inference.",
		counters: { completed: 1, total: 4, blocks: 0 },
		checkpoint: {
			id: "main_flow",
			label: "SURE infer state machine",
			resumable: true,
			resume_hint: 'Resume at unit "execute_inference".',
			data: {
				currentUnit: "execute_inference",
				completedUnits: ["dataset_scope"],
				retries: {},
				failedArtifactDigests: {},
			},
		},
	},
	"legacy-result.v1.fixture.json": {
		schema: "sure.run_result.v1",
		command: "/sure_infer",
		args: "model=example dataset=fixture",
		run_id: "20000101-000000-deadbeef",
		run_dir: "/workspace/project/.sure/runs/20000101-000000-deadbeef",
		status: "success",
		started_at: "2000-01-01T00:00:00.000Z",
		updated_at: "2000-01-01T00:01:00.000Z",
		finished_at: "2000-01-01T00:01:00.000Z",
		products: ["artifacts/main_agent_run_report.json"],
	},
};

const legacyEvents = [
	{
		type: "created",
		timestamp: "2000-01-01T00:00:00.000Z",
		data: legacyFixtures["legacy-run.v1.fixture.json"],
	},
	{
		type: "state_patch",
		timestamp: "2000-01-01T00:00:01.000Z",
		data: { patch: legacyFixtures["legacy-state.v1.fixture.json"] },
	},
];

function serializedRepositoryArtifacts(): Map<string, string> {
	const artifacts = new Map<string, string>();
	artifacts.set(repositoryBaselinePath, `${JSON.stringify(buildRepositoryBaseline(), null, 2)}\n`);
	for (const [name, value] of Object.entries(legacyFixtures)) {
		artifacts.set(join(fixtureRoot, name), `${JSON.stringify(value, null, 2)}\n`);
	}
	artifacts.set(
		join(fixtureRoot, "legacy-events.v1.fixture.jsonl"),
		`${legacyEvents.map((event) => JSON.stringify(event)).join("\n")}\n`,
	);
	return artifacts;
}

export function writeRepositoryArtifacts(check: boolean): void {
	const mismatches: string[] = [];
	for (const [path, content] of serializedRepositoryArtifacts()) {
		if (check) {
			if (!existsSync(path) || readFileSync(path, "utf8") !== content) {
				mismatches.push(pathForJson(relative(repositoryRoot, path)));
			}
			continue;
		}
		mkdirSync(dirname(path), { recursive: true });
		writeFileSync(path, content, "utf8");
	}
	if (mismatches.length > 0) {
		throw new Error(
			`SURE characterization artifacts are stale: ${mismatches.join(", ")}. Run npm run sure:characterize.`,
		);
	}
}

function isPathInside(base: string, candidate: string): boolean {
	const rel = relative(base, candidate);
	return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
}

function nearestExistingAncestor(path: string): string {
	let candidate = path;
	while (!existsSync(candidate)) {
		const parent = dirname(candidate);
		if (parent === candidate) throw new Error(`No existing ancestor for ${path}.`);
		candidate = parent;
	}
	return candidate;
}

export function assertReferenceOutputOutside(referenceRoot: string, outputPath: string): void {
	const lexicalRoot = resolve(referenceRoot);
	const lexicalOutput = resolve(outputPath);
	if (isPathInside(lexicalRoot, lexicalOutput)) {
		throw new Error("Reference catalog output must not be written inside the production reference tree.");
	}
	const realRoot = realpathSync(lexicalRoot);
	const ancestor = nearestExistingAncestor(lexicalOutput);
	const realAncestor = realpathSync(ancestor);
	const projectedOutput = resolve(realAncestor, relative(ancestor, lexicalOutput));
	if (isPathInside(realRoot, projectedOutput)) {
		throw new Error("Reference catalog output resolves inside the production reference tree through a symlink.");
	}
}

function resolveReferencePath(referenceRoot: string, logicalPath: string): string {
	if (isAbsolute(logicalPath) || logicalPath.split(/[\\/]/).includes("..")) {
		throw new Error(`Reference target must be a relative logical path: ${logicalPath}`);
	}
	const root = realpathSync(referenceRoot);
	const candidate = resolve(root, logicalPath);
	if (!isPathInside(root, candidate)) throw new Error(`Reference target escapes the root: ${logicalPath}`);
	const parent = nearestExistingAncestor(dirname(candidate));
	const projected = resolve(realpathSync(parent), relative(parent, candidate));
	if (!isPathInside(root, projected)) throw new Error(`Reference target resolves outside the root: ${logicalPath}`);
	return candidate;
}

function selectedSignals(path: string, content: string): Record<string, unknown> {
	const extension = extname(path).toLowerCase();
	if (extension === ".json") {
		try {
			const parsed: unknown = JSON.parse(content);
			if (!isRecord(parsed)) return { format: "json", root_type: Array.isArray(parsed) ? "array" : typeof parsed };
			const safeScalars: Record<string, JsonScalar> = {};
			for (const key of ["schema", "status", "protocol_id", "package_profile", "runtime_kind", "decision"]) {
				const value = parsed[key];
				if (
					value === null ||
					typeof value === "string" ||
					typeof value === "number" ||
					typeof value === "boolean"
				) {
					safeScalars[key] = value;
				}
			}
			return { format: "json", top_level_keys: Object.keys(parsed).sort(), selected_scalars: safeScalars };
		} catch {
			return { format: "json", parse_status: "invalid" };
		}
	}
	if (extension === ".jsonl") {
		const firstLine = content.split("\n").find((line) => line.trim() !== "");
		if (!firstLine) return { format: "jsonl", first_record: "missing" };
		try {
			const parsed: unknown = JSON.parse(firstLine);
			return {
				format: "jsonl",
				first_record_keys: isRecord(parsed) ? Object.keys(parsed).sort() : [],
				first_record_schema: isRecord(parsed) && typeof parsed.schema === "string" ? parsed.schema : null,
			};
		} catch {
			return { format: "jsonl", first_record: "invalid" };
		}
	}
	if (extension === ".yaml" || extension === ".yml") {
		const keys = content
			.split("\n")
			.map((line) => line.match(/^([A-Za-z0-9_-]+):/)?.[1])
			.filter((key): key is string => key !== undefined);
		return { format: "yaml", top_level_keys: [...new Set(keys)].sort() };
	}
	return { format: extension.slice(1) || "unknown" };
}

function referenceEntry(referenceRoot: string, target: ReferenceTarget): Record<string, unknown> {
	const path = resolveReferencePath(referenceRoot, target.logicalPath);
	if (!existsSync(path) && !lstatExists(path)) {
		return {
			logical_path: target.logicalPath,
			roles: target.roles,
			status: "missing",
			expected_type: target.expect ?? null,
		};
	}
	const stat = lstatSync(path, { bigint: true });
	const actualType = stat.isSymbolicLink()
		? "symlink"
		: stat.isDirectory()
			? "directory"
			: stat.isFile()
				? "file"
				: "other";
	const entry: Record<string, unknown> = {
		logical_path: target.logicalPath,
		roles: target.roles,
		status: target.expect && target.expect !== actualType ? "type-mismatch" : "present",
		expected_type: target.expect ?? null,
		actual_type: actualType,
		mode: (Number(stat.mode) & 0o7777).toString(8).padStart(4, "0"),
		size_bytes: stat.size.toString(),
		mtime_ns: stat.mtimeNs.toString(),
	};
	if (actualType === "symlink") {
		entry.link_target = readlinkSync(path);
		return entry;
	}
	if (actualType === "directory") {
		entry.children = readdirSync(path).sort();
		return entry;
	}
	if (actualType === "file" && stat.size <= BigInt(maxReferenceHashBytes)) {
		const content = readFileSync(path);
		entry.sha256 = sha256Bytes(content);
		entry.signals = selectedSignals(path, content.toString("utf8"));
	} else if (actualType === "file") {
		entry.sha256 = null;
		entry.hash_status = "skipped-size-limit";
	}
	return entry;
}

function lstatExists(path: string): boolean {
	try {
		lstatSync(path);
		return true;
	} catch {
		return false;
	}
}

function topLevelDirectoryNames(referenceRoot: string, name: "models" | "results"): string[] {
	const path = resolveReferencePath(referenceRoot, name);
	return readdirSync(path, { withFileTypes: true })
		.filter((entry) => entry.isDirectory())
		.map((entry) => entry.name)
		.sort();
}

function directStandardSystemMirrors(referenceRoot: string, resultNames: string[]): string[] {
	return resultNames
		.map((name) => `results/${name}/standard_system`)
		.filter((logicalPath) => {
			const path = resolveReferencePath(referenceRoot, logicalPath);
			return existsSync(path) && statSync(path).isDirectory();
		});
}

function referenceMarker(models: string[], results: string[], entries: Record<string, unknown>[]): string {
	return semanticDigest({ models, results, entries });
}

export function buildReferenceCatalog(options: ReferenceCatalogOptions): Record<string, unknown> {
	const referenceRoot = realpathSync(options.referenceRoot);
	const models = topLevelDirectoryNames(referenceRoot, "models");
	const results = topLevelDirectoryNames(referenceRoot, "results");
	const targets = options.targets ?? defaultReferenceTargets;
	const entries = targets.map((target) => referenceEntry(referenceRoot, target));
	const markerBefore = referenceMarker(models, results, entries);
	const modelsAfter = topLevelDirectoryNames(referenceRoot, "models");
	const resultsAfter = topLevelDirectoryNames(referenceRoot, "results");
	const entriesAfter = targets.map((target) => referenceEntry(referenceRoot, target));
	const markerAfter = referenceMarker(modelsAfter, resultsAfter, entriesAfter);
	if (markerBefore !== markerAfter) {
		throw new Error("Production reference marker changed during the read-only catalog scan.");
	}
	return withSemanticDigest({
		schema: "sure.characterization.reference_catalog.v1",
		observed_at: options.observedAt,
		source_root: referenceRoot,
		access_mode: "read-only-observation",
		coverage: "bounded-metadata-selected-artifacts",
		hash_limit_bytes: maxReferenceHashBytes,
		top_level: {
			models: { count: models.length, names: models },
			results: { count: results.length, names: results },
			standard_system_mirrors: {
				count: directStandardSystemMirrors(referenceRoot, results).length,
				paths: directStandardSystemMirrors(referenceRoot, results),
			},
		},
		entries,
		read_only_marker: { before: markerBefore, after: markerAfter, unchanged: true },
		limitations: [
			"This is not a full-tree digest.",
			"Model weights and prediction payloads are not copied or hashed.",
			"Presence or a success-like scalar is evidence only and never a SURE PASS decision.",
		],
	});
}

export function writeReferenceCatalog(options: ReferenceCatalogOptions, outputPath: string): void {
	assertReferenceOutputOutside(options.referenceRoot, outputPath);
	const catalog = buildReferenceCatalog(options);
	mkdirSync(dirname(resolve(outputPath)), { recursive: true });
	writeFileSync(resolve(outputPath), `${JSON.stringify(catalog, null, 2)}\n`, "utf8");
}

function optionValue(args: string[], name: string): string | undefined {
	const index = args.indexOf(name);
	return index >= 0 ? args[index + 1] : undefined;
}

function runCli(args: string[]): void {
	const command = args[0] ?? "repository";
	if (command === "repository") {
		writeRepositoryArtifacts(args.includes("--check"));
		console.log(
			args.includes("--check") ? "ok   SURE characterization baseline" : "wrote SURE characterization baseline",
		);
		return;
	}
	if (command === "reference") {
		const referenceRoot = optionValue(args, "--root");
		const outputPath = optionValue(args, "--output");
		const observedAt = optionValue(args, "--observed-at");
		if (!referenceRoot || !outputPath || !observedAt) {
			throw new Error("reference requires --root, --output, and --observed-at.");
		}
		writeReferenceCatalog({ referenceRoot, observedAt }, outputPath);
		console.log(`wrote read-only SURE reference catalog to ${outputPath}`);
		return;
	}
	throw new Error(`Unknown command: ${command}`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
	try {
		runCli(process.argv.slice(2));
	} catch (error) {
		console.error(error instanceof Error ? error.message : String(error));
		process.exitCode = 1;
	}
}
