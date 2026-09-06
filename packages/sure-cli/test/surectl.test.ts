import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
	applyValidation,
	canonicalJsonDigest,
	createPolicySnapshot,
	type ExecutionRequest,
	initialCheckpoint,
	type JsonValue,
	type PolicySnapshot,
	type TransitionResult,
	type WorkflowDefinition,
} from "../../sure-core/src/index.ts";

const repositoryRoot = fileURLToPath(new URL("../../..", import.meta.url));
const source = join(repositoryRoot, "packages/sure-cli/src/surectl.ts");
const definition = join(repositoryRoot, "sure/dist/agent-skills/sure-feed/canonical-definition.json");
const registryPath = join(repositoryRoot, "sure/dist/agent-skills/sure-feed/validator-registry.json");
const evalDefinition = join(repositoryRoot, "sure/dist/agent-skills/sure-eval/canonical-definition.json");
const evalRegistryPath = join(repositoryRoot, "sure/dist/agent-skills/sure-eval/validator-registry.json");
const inferDefinition = join(repositoryRoot, "sure/dist/agent-skills/sure-infer/canonical-definition.json");
const inferRegistryPath = join(repositoryRoot, "sure/dist/agent-skills/sure-infer/validator-registry.json");
const portableRuntime = join(repositoryRoot, "sure/dist/portable-runtime");
const portableMemoryContract = join(repositoryRoot, "sure/dist/agent-skills/sure-feed/memory-contract.json");
const hostParityFixturePath = join(repositoryRoot, "sure/canonical/fixtures/host-parity-traces.json");
const DIGEST_A = "a".repeat(64);
const DIGEST_B = "b".repeat(64);
const DIGEST_C = "c".repeat(64);

function policySnapshot(root: string): PolicySnapshot {
	return createPolicySnapshot({
		site_id: "cli-test",
		policy_version: 1,
		policy: { schema: "sure.site.policy.v1", execution: { surfaces: ["local"] } },
		source: { kind: "test", path: "/tmp/site-policy.yaml", raw_sha256: DIGEST_A },
		path_bindings: [
			{
				root_id: "reference",
				role: "read_only_reference",
				path: join(root, "policy-reference"),
				resolved_path: join(root, "policy-reference"),
			},
			{
				root_id: "publication",
				role: "controlled_publication",
				path: join(root, "policy-publication"),
				resolved_path: join(root, "policy-publication"),
			},
		],
	});
}

interface CommandResult {
	status: number | null;
	stdout: string;
	stderr: string;
	value?: Record<string, unknown>;
}

interface HostParityFixture {
	schema: string;
	recipe: string[];
	portable: {
		skill_id: string;
		branch_id: string;
		invalid_artifacts: Record<string, JsonValue>;
		valid_artifacts: Record<string, JsonValue>;
	};
}

function command(root: string, name: string, args: string[]): CommandResult {
	const result = spawnSync(process.execPath, ["--import", "tsx", source, name, "--root", root, ...args], {
		cwd: repositoryRoot,
		encoding: "utf8",
	});
	let value: Record<string, unknown> | undefined;
	if (result.stdout.trim()) value = JSON.parse(result.stdout) as Record<string, unknown>;
	return { status: result.status, stdout: result.stdout, stderr: result.stderr, value };
}

function digest(path: string): string {
	return `sha256:${createHash("sha256").update(readFileSync(path)).digest("hex")}`;
}

function executionRequest(runId: string, artifacts: string, overrides: Record<string, unknown> = {}): ExecutionRequest {
	return {
		schema: "sure.execution_request.v1",
		request_id: `${runId}-request`,
		semantic_request_digest: `sha256:${DIGEST_A}`,
		run_id: runId,
		unit_id: "scan_modelscope",
		attempt: 1,
		operation: "validation",
		subject: {
			bundle_manifest_path: join(artifacts, "bundle.json"),
			bundle_digest: `sha256:${DIGEST_A}`,
			runtime_identity_digest: `sha256:${DIGEST_B}`,
		},
		inputs: [],
		entrypoint: { executable: process.execPath, argv: ["-e", ""] },
		runtime_requirements: {},
		capability_requirements: [],
		reference_snapshot_digest: `sha256:${DIGEST_B}`,
		output_root: {
			path: artifacts,
			resolved_path: artifacts,
			scope_id: runId,
			policy_digest: `sha256:${DIGEST_A}`,
			writable: true,
		},
		policy_digest: `sha256:${DIGEST_A}`,
		created_at: "2026-09-06T00:00:00.000Z",
		...overrides,
	} as unknown as ExecutionRequest;
}

describe("surectl cooperative control plane", () => {
	let root: string;

	beforeEach(() => {
		root = mkdtempSync(join(tmpdir(), "surectl-test-"));
	});

	afterEach(() => {
		rmSync(root, { recursive: true, force: true });
	});

	it("validates a portable memory contract and logical URI without Pi modules", () => {
		const checked = command(root, "memory", [
			"--contract",
			portableMemoryContract,
			"--skill",
			"sure_feed",
			"--uri",
			"memory://sure_feed/bad_case/missing-runtime",
		]);
		expect(checked.status).toBe(0);
		expect(checked.value?.ok).toBe(true);
		expect(checked.value?.command).toBe("memory");
		expect(checked.value?.uri).toEqual({
			skill: "sure_feed",
			kind: "bad_case",
			slug: "missing-runtime",
		});
	});

	it("runs feed gates through the shared portable semantic backends", () => {
		const runId = "run-automatic-feed-validators";
		const base = ["--skill", "sure_feed", "--definition", definition, "--validator-registry", registryPath];
		const started = command(root, "start", [
			...base,
			"--run-id",
			runId,
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(started.status).toBe(0);
		const runDir = String((started.value?.run as Record<string, unknown>).runDir);
		const artifacts = join(runDir, "artifacts");

		writeFileSync(join(artifacts, "scan_result.json"), JSON.stringify({ candidates: [{ model_id: "owner/model" }] }));
		expect(command(root, "validate", [...base, "--run-id", runId]).status).toBe(0);

		writeFileSync(
			join(artifacts, "match_task_result.json"),
			JSON.stringify({
				candidates: [
					{ model_id: "owner/model", match: { matched: true, match_source: "tasks", task_type: "asr" } },
				],
			}),
		);
		const missing = command(root, "validate", [...base, "--run-id", runId]);
		expect(missing.status).toBe(5);
		expect((missing.value?.outcome as Record<string, unknown>).reason_code).toBe("CAPABILITY_MISSING");
		const matched = command(root, "validate", [...base, "--run-id", runId, "--semantic-runtime", portableRuntime]);
		expect(matched.status).toBe(0);
		expect((matched.value?.outcome as Record<string, unknown>).outcome).toBe("PASS");
		let state = JSON.parse(readFileSync(join(runDir, "state.json"), "utf8")) as Record<string, unknown>;
		let validation = state.last_validation as Record<string, unknown>;
		let evidence = validation.evidence as { validators: Array<Record<string, unknown>> };
		expect(evidence.validators[0]?.backend_operation_id).toBe("sure.feed.validate_match_task");

		writeFileSync(join(artifacts, "metadata_result.json"), JSON.stringify({ models: [] }));
		expect(command(root, "validate", [...base, "--run-id", runId]).status).toBe(0);
		writeFileSync(join(artifacts, "oref_result.json"), JSON.stringify({ converted: [] }));
		expect(command(root, "validate", [...base, "--run-id", runId]).status).toBe(0);

		const modelInput = {
			model_id: "owner/model",
			model_name: "model",
			task_type: "asr",
			deployment_type: "local",
			repo: { url: "https://huggingface.co/owner/model", commit: null },
			weights: {
				source: "huggingface",
				required: true,
				local_path: null,
				cache_policy: "download",
				local_dir_name: "owner__model",
			},
			environment_hint: {
				preferred_backend: "python",
				python_version: "3.12",
				requires_gpu: false,
				system_packages: [],
			},
			phase1_runtime_target: "python",
			entrypoints: {
				import_test: "python -c pass",
				load_test: "python -c pass",
				infer_test: "python -c pass",
			},
			fixture: {
				task_specific: true,
				fallback_allowed: false,
				fixture_source: "task_registry",
				fixture_id: "asr-basic",
				fixture_index: "fixtures/tasks/asr/index.json",
				fixture_root: "fixtures/tasks/asr",
				audio: "fixtures/tasks/asr/sample.wav",
			},
			io_contract: {
				input_type: "audio",
				output_type: "json",
				primary_field: "text",
				required_fields: ["text"],
				nonempty_fields: ["text"],
				json_serializable: true,
			},
		};
		const evidenceFields: Array<[string, unknown]> = [
			["repo.url", modelInput.repo.url],
			["weights.source", modelInput.weights.source],
			["environment_hint.preferred_backend", modelInput.environment_hint.preferred_backend],
			["environment_hint.python_version", modelInput.environment_hint.python_version],
			["environment_hint.requires_gpu", modelInput.environment_hint.requires_gpu],
			["phase1_runtime_target", modelInput.phase1_runtime_target],
			["entrypoints.import_test", modelInput.entrypoints.import_test],
			["entrypoints.load_test", modelInput.entrypoints.load_test],
			["entrypoints.infer_test", modelInput.entrypoints.infer_test],
			["fixture", modelInput.fixture],
			["io_contract", modelInput.io_contract],
		];
		writeFileSync(
			join(artifacts, "model_input_result.json"),
			JSON.stringify({
				model_inputs: [
					{
						model_id: "owner/model",
						model_input: modelInput,
						evidence: evidenceFields.map(([field, value]) => ({
							source: field === "fixture" ? "local" : "huggingface",
							field: field === "fixture" ? "fixture_registry.index" : field,
							model_input_field: field,
							value,
						})),
					},
				],
			}),
		);
		const modelInputValidation = command(root, "validate", [
			...base,
			"--run-id",
			runId,
			"--semantic-runtime",
			portableRuntime,
		]);
		expect(modelInputValidation.status).toBe(0);
		state = JSON.parse(readFileSync(join(runDir, "state.json"), "utf8")) as Record<string, unknown>;
		validation = state.last_validation as Record<string, unknown>;
		evidence = validation.evidence as { validators: Array<Record<string, unknown>> };
		expect(evidence.validators[0]?.backend_operation_id).toBe("sure.feed.validate_model_input");

		writeFileSync(
			join(artifacts, "rank_select_result.json"),
			JSON.stringify({ selected: [{ model_id: "owner/model", repo: "owner/model", score: 1 }] }),
		);
		const ranked = command(root, "validate", [...base, "--run-id", runId, "--semantic-runtime", portableRuntime]);
		expect(ranked.status).toBe(0);
		state = JSON.parse(readFileSync(join(runDir, "state.json"), "utf8")) as Record<string, unknown>;
		validation = state.last_validation as Record<string, unknown>;
		evidence = validation.evidence as { validators: Array<Record<string, unknown>> };
		expect(evidence.validators[0]?.backend_operation_id).toBe("sure.feed.validate_rank_select");

		writeFileSync(
			join(artifacts, "extraction_declaration.json"),
			JSON.stringify({
				schema: "sure.memory.extraction.v2",
				no_new_lessons: true,
				no_lessons_reason: "No reusable lesson in this fixture.",
				covered_by: [],
				candidates: [],
				infra_noise: false,
				infra_evidence: [],
			}),
		);
		const extraction = command(root, "validate", [...base, "--run-id", runId, "--semantic-runtime", portableRuntime]);
		expect(extraction.status).toBe(0);
		state = JSON.parse(readFileSync(join(runDir, "state.json"), "utf8")) as Record<string, unknown>;
		validation = state.last_validation as Record<string, unknown>;
		evidence = validation.evidence as { validators: Array<Record<string, unknown>> };
		expect(evidence.validators[0]?.backend_operation_id).toBe("sure.memory.validate_extraction");
	}, 30_000);

	it("reports a malformed memory contract as not executed instead of an advisory success", () => {
		const malformed = join(root, "memory-contract.json");
		writeFileSync(malformed, JSON.stringify({ schema: "sure.memory.contract.v0" }));
		const checked = command(root, "memory", ["--contract", malformed]);
		expect(checked.status).toBe(5);
		expect(checked.value?.ok).toBe(false);
		expect((checked.value?.outcome as Record<string, unknown>).outcome).toBe("NOT_EXECUTED");
		expect((checked.value?.outcome as Record<string, unknown>).reason_code).toBe("INVALID_CONTRACT");
	});

	it("advances only from Core validation and registered gate evidence", () => {
		const base = ["--skill", "sure_feed", "--definition", definition, "--validator-registry", registryPath];
		const started = command(root, "start", [
			...base,
			"--run-id",
			"run-one",
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(started.status).toBe(0);
		const run = started.value?.run as Record<string, unknown>;
		expect(run.status).toBe("running");
		const runDir = String(run.runDir);

		const missing = command(root, "validate", [...base, "--run-id", "run-one"]);
		expect(missing.status).toBe(5);
		expect((missing.value?.outcome as Record<string, unknown>).outcome).toBe("NOT_EXECUTED");
		expect(
			((missing.value?.transition as Record<string, unknown>).checkpoint as { data: { currentUnit: string } }).data
				.currentUnit,
		).toBe("scan_modelscope");

		const scan = join(runDir, "artifacts", "scan_result.json");
		writeFileSync(scan, '{"candidates":[]}\n');
		const outsideSchema = join(root, "outside-schema.json");
		writeFileSync(outsideSchema, '{"type":"object"}\n');
		const rejectedSchema = command(root, "validate", [...base, "--run-id", "run-one", "--schema", outsideSchema]);
		expect(rejectedSchema.status).toBe(1);
		expect(rejectedSchema.stderr).toMatch(/outside every allowed root|Path .* outside/);
		const structural = command(root, "validate", [...base, "--run-id", "run-one"]);
		expect(structural.status).toBe(0);
		expect(
			((structural.value?.transition as Record<string, unknown>).checkpoint as { data: { currentUnit: string } })
				.data.currentUnit,
		).toBe("match_task");

		const match = join(runDir, "artifacts", "match_task_result.json");
		writeFileSync(match, '{"candidates":[]}\n');
		const outsideEvidence = join(root, "outside-evidence.json");
		writeFileSync(outsideEvidence, '{"verdict":"PASS"}\n');
		const rejectedEvidence = command(root, "validate", [
			...base,
			"--run-id",
			"run-one",
			"--evidence",
			outsideEvidence,
		]);
		expect(rejectedEvidence.status).toBe(1);
		expect(rejectedEvidence.stderr).toMatch(/outside every allowed root|Path .* outside/);
		const noEvidence = command(root, "validate", [...base, "--run-id", "run-one"]);
		expect(noEvidence.status).toBe(5);
		expect((noEvidence.value?.outcome as Record<string, unknown>).outcome).toBe("NOT_EXECUTED");

		const registry = JSON.parse(readFileSync(registryPath, "utf8")) as {
			digest: string;
			validators: Array<{ id: string; unit_id?: string }>;
		};
		const validator = registry.validators.find((entry) => entry.unit_id === "match_task");
		expect(validator).toBeDefined();
		const evidencePath = join(runDir, "artifacts", "validator-evidence.json");
		writeFileSync(
			evidencePath,
			JSON.stringify({
				schema: "sure.validator.evidence.v1",
				registry_digest: registry.digest,
				validators: [{ validator_id: validator?.id, verdict: "PASS", artifact_digest: digest(match) }],
			}),
		);
		const validated = command(root, "validate", [...base, "--run-id", "run-one", "--evidence", evidencePath]);
		expect(validated.status).toBe(0);
		expect((validated.value?.outcome as Record<string, unknown>).outcome).toBe("PASS");
		const persistedState = JSON.parse(readFileSync(join(runDir, "state.json"), "utf8")) as Record<string, unknown>;
		expect((persistedState.last_validation as Record<string, unknown>).evidence_source).toBe("external");
		expect(
			((validated.value?.transition as Record<string, unknown>).checkpoint as { data: { currentUnit: string } }).data
				.currentUnit,
		).toBe("collect_metadata");
	});

	it("runs a registered gate validator through the locked portable runtime", () => {
		const runId = "run-automatic-eval-validator";
		const base = ["--skill", "sure_eval", "--definition", evalDefinition, "--validator-registry", evalRegistryPath];
		const started = command(root, "start", [
			...base,
			"--run-id",
			runId,
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(started.status).toBe(0);
		const runDir = String((started.value?.run as Record<string, unknown>).runDir);
		const artifacts = join(runDir, "artifacts");
		writeFileSync(
			join(artifacts, "dataset_decision.json"),
			JSON.stringify({ selected_datasets: [], skipped_datasets: [], selection_basis: [] }),
		);
		expect(command(root, "validate", [...base, "--run-id", runId]).status).toBe(0);

		const evalReport = join(artifacts, "eval_run_report.json");
		writeFileSync(
			evalReport,
			JSON.stringify({
				schema: "sure.eval.run_report.v1",
				run_id: runId,
				run_dir: runDir,
				evaluation_only: true,
				old_evaluation_reused: false,
				artifacts: {},
				summary: {},
				source_identity: {
					model_fingerprint: DIGEST_A,
					protocol_id: "standard_system",
					dataset_set_digest: DIGEST_B,
					source_report_sha256: DIGEST_C,
				},
				staging_append: {
					staging_result_dir: "staging",
					staging_report: "report.jsonl",
					staging_snapshot: "snapshot.md",
					approved_base_result_dir: "approved",
					batch_id: `sure_eval_${"a".repeat(24)}`,
					batch_dir: "batch",
					artifact_manifest: "manifest.json",
					artifact_manifest_sha256: DIGEST_A,
					persisted_artifacts: { report: "report.jsonl" },
					persisted_artifact_count: 1,
					base_materialized: true,
					batch_materialized: true,
					appended_record_ids: ["record-1"],
					requested_record_ids: ["record-1"],
					idempotent: false,
					staging_report_sha256: DIGEST_B,
					staging_snapshot_sha256: DIGEST_C,
				},
			}),
		);
		const registry = JSON.parse(readFileSync(evalRegistryPath, "utf8")) as {
			digest: string;
			validators: Array<{ id: string; unit_id?: string }>;
		};
		const reportValidator = registry.validators.find((entry) => entry.unit_id === "execute_evaluation");
		const compatibilityEvidence = join(artifacts, "eval-report-compatibility-evidence.json");
		writeFileSync(
			compatibilityEvidence,
			JSON.stringify({
				schema: "sure.validator.evidence.v1",
				registry_digest: registry.digest,
				validators: [
					{
						validator_id: reportValidator?.id,
						verdict: "PASS",
						artifact_digest: digest(evalReport),
					},
				],
			}),
		);
		expect(command(root, "validate", [...base, "--run-id", runId, "--evidence", compatibilityEvidence]).status).toBe(
			0,
		);

		const assessment = join(artifacts, "assessment_report.json");
		writeFileSync(assessment, JSON.stringify({ anomaly_detected: false, user_confirmed: false, status: "ok" }));
		const unavailable = command(root, "validate", [...base, "--run-id", runId]);
		expect(unavailable.status).toBe(5);
		expect((unavailable.value?.outcome as Record<string, unknown>).reason_code).toBe("CAPABILITY_MISSING");
		expect(
			((unavailable.value?.transition as Record<string, unknown>).checkpoint as { data: { currentUnit: string } })
				.data.currentUnit,
		).toBe("assessment");

		writeFileSync(assessment, JSON.stringify({ anomaly_detected: true, user_confirmed: false, status: "ok" }));
		const rejected = command(root, "validate", [...base, "--run-id", runId, "--semantic-runtime", portableRuntime]);
		expect(rejected.status).toBe(4);
		expect((rejected.value?.outcome as Record<string, unknown>).outcome).toBe("RETRY");
		const rejectedState = JSON.parse(readFileSync(join(runDir, "state.json"), "utf8")) as Record<string, unknown>;
		const rejectedValidation = rejectedState.last_validation as Record<string, unknown>;
		const rejectedEvidence = JSON.parse(readFileSync(String(rejectedValidation.evidence_path), "utf8")) as {
			validators: Array<Record<string, unknown>>;
		};
		expect(rejectedEvidence.validators[0]?.verdict).toBe("FAIL");
		const rejectedReceipt = JSON.parse(
			readFileSync(String(rejectedEvidence.validators[0]?.receipt_path), "utf8"),
		) as Record<string, unknown>;
		expect(rejectedReceipt.lifecycle).toBe("FAILED");

		writeFileSync(assessment, JSON.stringify({ anomaly_detected: false, user_confirmed: false, status: "ok" }));
		const validated = command(root, "validate", [...base, "--run-id", runId, "--semantic-runtime", portableRuntime]);
		expect(validated.status).toBe(0);
		expect((validated.value?.outcome as Record<string, unknown>).outcome).toBe("PASS");
		expect(
			((validated.value?.transition as Record<string, unknown>).checkpoint as { data: { currentUnit: string } }).data
				.currentUnit,
		).toBe("extract_lessons");
		const state = JSON.parse(readFileSync(join(runDir, "state.json"), "utf8")) as Record<string, unknown>;
		const lastValidation = state.last_validation as Record<string, unknown>;
		expect(lastValidation.evidence_source).toBe("surectl_executor");
		const evidencePath = String(lastValidation.evidence_path);
		expect(digest(evidencePath)).toBe(lastValidation.evidence_digest);
		const evidence = JSON.parse(readFileSync(evidencePath, "utf8")) as {
			source: string;
			validators: Array<Record<string, unknown>>;
		};
		expect(evidence.source).toBe("surectl");
		expect(evidence.validators).toHaveLength(1);
		expect(evidence.validators[0]?.verdict).toBe("PASS");
		const requestPath = String(evidence.validators[0]?.request_path);
		const receiptPath = String(evidence.validators[0]?.receipt_path);
		const request = JSON.parse(readFileSync(requestPath, "utf8")) as Record<string, unknown>;
		const receipt = JSON.parse(readFileSync(receiptPath, "utf8")) as Record<string, unknown>;
		const runtimeLock = JSON.parse(
			readFileSync(join(portableRuntime, "runtime-support.lock.json"), "utf8"),
		) as Record<string, unknown>;
		expect((request.subject as Record<string, unknown>).runtime_identity_digest).toBe(runtimeLock.runtime_digest);
		expect(receipt.lifecycle).toBe("SUCCEEDED");
		expect(receipt.request_digest).toBe(canonicalJsonDigest(request as unknown as JsonValue));
	});

	it("runs registered inference result and report validators through the same runtime", () => {
		const runId = "run-automatic-infer-validators";
		const base = [
			"--skill",
			"sure_infer",
			"--definition",
			inferDefinition,
			"--validator-registry",
			inferRegistryPath,
		];
		const started = command(root, "start", [
			...base,
			"--run-id",
			runId,
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(started.status).toBe(0);
		const runDir = String((started.value?.run as Record<string, unknown>).runDir);
		const artifacts = join(runDir, "artifacts");
		writeFileSync(
			join(artifacts, "dataset_decision.json"),
			JSON.stringify({ selected_datasets: ["demo__v1"], skipped_datasets: [], selection_basis: ["test"] }),
		);
		expect(command(root, "validate", [...base, "--run-id", runId]).status).toBe(0);

		const approved = {
			schema: "sure.eval.deployment_binding.v2",
			runtime_kind: "container",
			target_image_ref: `registry.example/sure/demo@sha256:${DIGEST_A}`,
			policy: {
				execution_mode: "container_only",
				model_integrity: "image_digest",
				host_python_fallback: false,
			},
			evidence: { bundle_identity_sha256: DIGEST_B },
		};
		writeFileSync(
			join(artifacts, "eval_input_resolved.json"),
			JSON.stringify({ model: { deployment_binding: approved } }),
		);
		writeFileSync(
			join(artifacts, "execution_surface.json"),
			JSON.stringify({
				execution: { requested: "local", path_planned: "local_docker" },
				deployment_binding: {
					schema: approved.schema,
					runtime_kind: approved.runtime_kind,
					bundle_identity_sha256: DIGEST_B,
					execution_mode: "container_only",
					model_mount_read_only: true,
					model_integrity: "image_digest",
					result_mount_writable: true,
					target_image_ref: approved.target_image_ref,
				},
			}),
		);
		writeFileSync(
			join(artifacts, "execution_result.json"),
			JSON.stringify({
				job_status: "failed",
				exit_code: 3,
				execution_path: "local_docker",
				runtime_kind: "container",
				product_dir: "",
				failed_stage: "generate",
				input_digest: DIGEST_C,
				datasets: [],
			}),
		);
		const unavailable = command(root, "validate", [...base, "--run-id", runId]);
		expect(unavailable.status).toBe(5);
		expect((unavailable.value?.outcome as Record<string, unknown>).reason_code).toBe("CAPABILITY_MISSING");
		expect(
			((unavailable.value?.transition as Record<string, unknown>).checkpoint as { data: { currentUnit: string } })
				.data.currentUnit,
		).toBe("execute_inference");
		const execution = command(root, "validate", [...base, "--run-id", runId, "--semantic-runtime", portableRuntime]);
		expect(execution.status).toBe(0);
		expect((execution.value?.outcome as Record<string, unknown>).outcome).toBe("PASS");
		expect(
			((execution.value?.transition as Record<string, unknown>).checkpoint as { data: { currentUnit: string } }).data
				.currentUnit,
		).toBe("extract_lessons");
		let state = JSON.parse(readFileSync(join(runDir, "state.json"), "utf8")) as Record<string, unknown>;
		let validation = state.last_validation as Record<string, unknown>;
		let evidence = validation.evidence as { validators: Array<Record<string, unknown>> };
		expect(evidence.validators[0]?.backend_operation_id).toBe("sure.infer.validate_execution_result");
		expect(
			(JSON.parse(readFileSync(String(evidence.validators[0]?.receipt_path), "utf8")) as Record<string, unknown>)
				.lifecycle,
		).toBe("SUCCEEDED");

		const extraction = join(artifacts, "extraction_declaration.json");
		writeFileSync(
			extraction,
			JSON.stringify({
				schema: "sure.memory.extraction.v2",
				no_new_lessons: true,
				no_lessons_reason: "No reusable lesson in this fixture.",
				covered_by: [],
				candidates: [],
				infra_noise: false,
				infra_evidence: [],
			}),
		);
		const unavailableMemory = command(root, "validate", [...base, "--run-id", runId]);
		expect(unavailableMemory.status).toBe(5);
		expect((unavailableMemory.value?.outcome as Record<string, unknown>).reason_code).toBe("CAPABILITY_MISSING");
		expect(
			(
				(unavailableMemory.value?.transition as Record<string, unknown>).checkpoint as {
					data: { currentUnit: string };
				}
			).data.currentUnit,
		).toBe("extract_lessons");
		const extractionResult = command(root, "validate", [
			...base,
			"--run-id",
			runId,
			"--semantic-runtime",
			portableRuntime,
		]);
		expect(extractionResult.status).toBe(0);
		expect((extractionResult.value?.outcome as Record<string, unknown>).outcome).toBe("PASS");
		state = JSON.parse(readFileSync(join(runDir, "state.json"), "utf8")) as Record<string, unknown>;
		validation = state.last_validation as Record<string, unknown>;
		evidence = validation.evidence as { validators: Array<Record<string, unknown>> };
		expect(evidence.validators[0]?.backend_operation_id).toBe("sure.memory.validate_extraction");
		expect(
			(JSON.parse(readFileSync(String(evidence.validators[0]?.receipt_path), "utf8")) as Record<string, unknown>)
				.lifecycle,
		).toBe("SUCCEEDED");
		expect(
			(
				(extractionResult.value?.transition as Record<string, unknown>).checkpoint as {
					data: { currentUnit: string };
				}
			).data.currentUnit,
		).toBe("run_report");

		writeFileSync(
			join(artifacts, "main_agent_run_report.json"),
			JSON.stringify({
				run_id: runId,
				timestamp: "2026-09-06T00:00:00.000Z",
				task_type: "evaluate_existing_model",
				goal: "record the bounded terminal inference failure",
				selected_datasets: ["demo__v1"],
				executed_steps: ["dataset_scope", "execute_inference"],
				status: "failed",
				report_persisted: true,
				execution_path_actual: "local_docker",
				next_action: "Repair the failed generation stage before retrying inference.",
			}),
		);
		const report = command(root, "validate", [...base, "--run-id", runId, "--semantic-runtime", portableRuntime]);
		expect(report.status).toBe(0);
		expect((report.value?.outcome as Record<string, unknown>).outcome).toBe("PASS");
		state = JSON.parse(readFileSync(join(runDir, "state.json"), "utf8")) as Record<string, unknown>;
		validation = state.last_validation as Record<string, unknown>;
		evidence = validation.evidence as { validators: Array<Record<string, unknown>> };
		expect(evidence.validators[0]?.backend_operation_id).toBe("sure.eval.validate_run_report");
		const request = JSON.parse(readFileSync(String(evidence.validators[0]?.request_path), "utf8")) as {
			entrypoint: { argv: string[] };
		};
		expect(request.entrypoint.argv).toEqual(expect.arrayContaining(["--profile", "infer"]));
	}, 15_000);

	it("matches the canonical host-parity trace through the portable control plane", () => {
		const fixture = JSON.parse(readFileSync(hostParityFixturePath, "utf8")) as HostParityFixture;
		expect(fixture.schema).toBe("sure.host_parity.traces.v1");
		expect(fixture.recipe).toEqual([
			"missing",
			"fail_new",
			"fail_unchanged",
			"fail_until_exhausted",
			"pass_current",
			"pass_remaining",
		]);
		const canonical = JSON.parse(readFileSync(definition, "utf8")) as { workflow: WorkflowDefinition };
		const workflow = canonical.workflow;
		const branch = workflow.branches.find((candidate) => candidate.id === fixture.portable.branch_id);
		if (!branch) throw new Error("Host-parity fixture branch is missing.");
		const registry = JSON.parse(readFileSync(registryPath, "utf8")) as {
			digest: string;
			validators: Array<{ id: string; branch_id?: string; unit_id?: string }>;
		};
		const base = [
			"--skill",
			fixture.portable.skill_id,
			"--definition",
			definition,
			"--validator-registry",
			registryPath,
		];
		const started = command(root, "start", [
			...base,
			"--run-id",
			"host-parity",
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(started.status).toBe(0);
		const run = started.value?.run as Record<string, unknown>;
		expect(run.workflowDigest).toBe(canonicalJsonDigest(workflow as unknown as JsonValue));
		expect(run.validatorDigest).toBe(registry.digest);
		const runDir = String(run.runDir);
		let core = initialCheckpoint(workflow, fixture.portable.branch_id);

		const compare = (
			actual: CommandResult,
			expected: TransitionResult,
			outcome: "PASS" | "BLOCKED" | "RETRY" | "NOT_EXECUTED",
			disposition: "ADVANCE" | "RETRY" | "BLOCK" | "TERMINATE" | "WAIT",
			status: number,
		): void => {
			expect(actual.status).toBe(status);
			const transition = actual.value?.transition as unknown as TransitionResult;
			expect(transition.action).toBe(expected.action);
			expect(transition.accepted).toBe(expected.accepted);
			expect(transition.retry_consumed).toBe(expected.retry_consumed);
			expect(transition.exhausted).toBe(expected.exhausted);
			expect(transition.checkpoint).toEqual(expected.checkpoint);
			const actualOutcome = actual.value?.outcome as Record<string, unknown>;
			expect(actualOutcome.outcome).toBe(outcome);
			expect(actualOutcome.workflow_disposition).toBe(disposition);
		};

		let expected = applyValidation(workflow, core, { kind: "missing", reason: "artifact missing" });
		compare(command(root, "validate", [...base, "--run-id", "host-parity"]), expected, "NOT_EXECUTED", "WAIT", 5);
		core = expected.checkpoint;

		const initialUnit = branch.units[0];
		if (!initialUnit) throw new Error("Host-parity fixture workflow is empty.");
		const initialArtifact = join(runDir, "artifacts", initialUnit.produces);
		writeFileSync(initialArtifact, `${JSON.stringify(fixture.portable.invalid_artifacts["failure-1"])}\n`);
		expected = applyValidation(workflow, core, {
			kind: "fail",
			reason: "fixture failure",
			artifact_digest: digest(initialArtifact),
		});
		compare(command(root, "validate", [...base, "--run-id", "host-parity"]), expected, "RETRY", "RETRY", 4);
		core = expected.checkpoint;

		expected = applyValidation(workflow, core, {
			kind: "fail",
			reason: "fixture bytes unchanged",
			artifact_digest: digest(initialArtifact),
		});
		compare(command(root, "validate", [...base, "--run-id", "host-parity"]), expected, "RETRY", "RETRY", 4);
		core = expected.checkpoint;

		for (let attempt = 2; attempt <= workflow.retry_policy.default_max_retries; attempt += 1) {
			const invalid = fixture.portable.invalid_artifacts[`failure-${attempt}`];
			if (invalid === undefined) throw new Error(`Missing invalid artifact fixture for attempt ${attempt}.`);
			writeFileSync(initialArtifact, `${JSON.stringify(invalid)}\n`);
			expected = applyValidation(workflow, core, {
				kind: "fail",
				reason: `fixture failure ${attempt}`,
				artifact_digest: digest(initialArtifact),
			});
			const exhausted = attempt === workflow.retry_policy.default_max_retries;
			compare(
				command(root, "validate", [...base, "--run-id", "host-parity"]),
				expected,
				exhausted ? "BLOCKED" : "RETRY",
				exhausted ? "BLOCK" : "RETRY",
				exhausted ? 3 : 4,
			);
			core = expected.checkpoint;
		}

		for (const unit of branch.units) {
			const artifact = fixture.portable.valid_artifacts[unit.id];
			if (artifact === undefined) throw new Error(`Missing valid artifact fixture for ${unit.id}.`);
			const artifactPath = join(runDir, "artifacts", unit.produces);
			writeFileSync(artifactPath, `${JSON.stringify(artifact)}\n`);
			const validateArgs = [...base, "--run-id", "host-parity"];
			if (unit.kind === "gate") {
				const validators = registry.validators.filter(
					(entry) => entry.branch_id === branch.id && entry.unit_id === unit.id,
				);
				expect(validators.length, `registered validators for ${unit.id}`).toBeGreaterThan(0);
				const evidencePath = join(runDir, "artifacts", `host-parity-${unit.id}-evidence.json`);
				writeFileSync(
					evidencePath,
					`${JSON.stringify({
						schema: "sure.validator.evidence.v1",
						registry_digest: registry.digest,
						validators: validators.map((validator) => ({
							validator_id: validator.id,
							verdict: "PASS",
							artifact_digest: digest(artifactPath),
						})),
					})}\n`,
				);
				validateArgs.push("--evidence", evidencePath);
			}
			expected = applyValidation(workflow, core, {
				kind: "pass",
				artifact_digest: digest(artifactPath),
			});
			compare(
				command(root, "validate", validateArgs),
				expected,
				"PASS",
				expected.action === "terminal" ? "TERMINATE" : "ADVANCE",
				0,
			);
			core = expected.checkpoint;
		}

		const finalStatus = command(root, "status", [...base, "--run-id", "host-parity"]);
		expect(finalStatus.status).toBe(0);
		expect(finalStatus.value?.checkpoint).toEqual(core);
		expect(core.resumable).toBe(false);
		expect(core.data.completedUnits).toEqual(branch.units.map((unit) => unit.id));
		expect(core.data.blocks).toBe(workflow.retry_policy.default_max_retries);
	}, 30_000);

	it("rejects output beneath an explicit read-only reference root", () => {
		const reference = join(root, "reference");
		mkdirSync(reference);
		const rejected = command(root, "start", [
			"--skill",
			"sure_feed",
			"--definition",
			definition,
			"--validator-registry",
			registryPath,
			"--run-id",
			"run-reference",
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
			"--reference-root",
			reference,
			"--output-dir",
			join(reference, "result"),
		]);
		expect(rejected.status).toBe(1);
		expect(rejected.stderr).toContain("read-only reference root");
	});

	it("does not validate an artifact outside the run scope", () => {
		const base = ["--skill", "sure_feed", "--definition", definition, "--validator-registry", registryPath];
		const started = command(root, "start", [
			...base,
			"--run-id",
			"run-artifact-scope",
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(started.status).toBe(0);
		const outside = join(root, "outside-artifact.json");
		writeFileSync(outside, '{"candidates":[]}', "utf8");
		const rejected = command(root, "validate", [...base, "--run-id", "run-artifact-scope", "--artifact", outside]);
		expect(rejected.status).toBe(1);
		expect(rejected.stderr).toMatch(/outside every allowed root|Path .* outside/);
	});

	it("does not read success evidence outside the run scope", () => {
		const base = ["--skill", "sure_feed", "--definition", definition, "--validator-registry", registryPath];
		const started = command(root, "start", [
			...base,
			"--run-id",
			"run-finalize-scope",
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(started.status).toBe(0);
		const outsideRequest = join(root, "outside-request.json");
		const outsideReceipt = join(root, "outside-receipt.json");
		writeFileSync(outsideRequest, "not a request", "utf8");
		writeFileSync(outsideReceipt, "not a receipt", "utf8");
		const rejected = command(root, "finalize", [
			"--run-id",
			"run-finalize-scope",
			"--status",
			"success",
			"--execution-request",
			outsideRequest,
			"--execution-receipt",
			outsideReceipt,
		]);
		expect(rejected.status).toBe(1);
		expect(rejected.stderr).toMatch(/outside every allowed root|Path .* outside/);
	});

	it("records execution evidence without advancing the workflow", () => {
		const base = ["--skill", "sure_feed", "--definition", definition, "--validator-registry", registryPath];
		const started = command(root, "start", [
			...base,
			"--run-id",
			"run-execution",
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(started.status).toBe(0);
		const run = started.value?.run as Record<string, unknown>;
		const runDir = String(run.runDir);
		const artifacts = join(runDir, "artifacts");
		const request = {
			schema: "sure.execution_request.v1",
			request_id: "request-execution",
			semantic_request_digest: `sha256:${DIGEST_A}`,
			run_id: "run-execution",
			unit_id: "scan_modelscope",
			attempt: 1,
			operation: "validation",
			subject: {
				bundle_manifest_path: join(artifacts, "bundle.json"),
				bundle_digest: `sha256:${DIGEST_A}`,
				runtime_identity_digest: `sha256:${DIGEST_B}`,
			},
			inputs: [],
			entrypoint: { executable: "python3", argv: ["check.py"] },
			runtime_requirements: {},
			capability_requirements: [],
			reference_snapshot_digest: `sha256:${DIGEST_B}`,
			output_root: {
				path: artifacts,
				resolved_path: artifacts,
				scope_id: "run-execution",
				policy_digest: `sha256:${DIGEST_A}`,
				writable: true,
			},
			policy_digest: `sha256:${DIGEST_A}`,
			created_at: "2026-09-06T00:00:00.000Z",
		};
		const requestPath = join(artifacts, "execution_request.json");
		writeFileSync(requestPath, JSON.stringify(request));
		const admitted = command(root, "validate", [
			...base,
			"--run-id",
			"run-execution",
			"--execution-request",
			requestPath,
		]);
		expect(admitted.status).toBe(5);
		expect((admitted.value?.outcome as Record<string, unknown>).outcome).toBe("NOT_EXECUTED");
		expect((admitted.value?.checkpoint as { data: { currentUnit: string } }).data.currentUnit).toBe(
			"scan_modelscope",
		);

		const receipt = {
			schema: "sure.execution_receipt.v1",
			receipt_id: "receipt-execution",
			request_id: request.request_id,
			request_digest: canonicalJsonDigest(request as unknown as JsonValue),
			semantic_request_digest: request.semantic_request_digest,
			run_id: request.run_id,
			unit_id: request.unit_id,
			attempt: request.attempt,
			executor: {
				executor_id: "executor",
				kind: "python",
				version: "1",
				digest: `sha256:${DIGEST_B}`,
				trust_level: "host_enforced",
			},
			lifecycle: "SUCCEEDED",
			capability_evidence: [],
			outputs: [],
			reference_snapshot_digest: request.reference_snapshot_digest,
			output_root: request.output_root,
			policy_digest: request.policy_digest,
			started_at: request.created_at,
			finished_at: request.created_at,
			exit_code: 0,
		};
		const receiptPath = join(artifacts, "execution_receipt.json");
		writeFileSync(receiptPath, JSON.stringify(receipt));
		const receiptResult = command(root, "validate", [
			...base,
			"--run-id",
			"run-execution",
			"--execution-request",
			requestPath,
			"--execution-receipt",
			receiptPath,
		]);
		expect(receiptResult.status).toBe(5);
		expect((receiptResult.value?.outcome as Record<string, unknown>).reason_code).toBe("VALIDATION_PENDING");
	});

	it("executes a local request into a receipt without advancing the checkpoint", () => {
		const base = ["--skill", "sure_feed", "--definition", definition, "--validator-registry", registryPath];
		const started = command(root, "start", [
			...base,
			"--run-id",
			"run-local-executor",
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(started.status).toBe(0);
		const run = started.value?.run as Record<string, unknown>;
		const runDir = String(run.runDir);
		const artifacts = join(runDir, "artifacts");
		const outputFile = join(artifacts, "generated.txt");
		const request = executionRequest("run-local-executor", artifacts, {
			entrypoint: {
				executable: process.execPath,
				argv: ["-e", `require('node:fs').writeFileSync(${JSON.stringify(outputFile)}, 'ok')`],
			},
		});
		const requestPath = join(artifacts, "local-request.json");
		writeFileSync(requestPath, JSON.stringify(request));
		const executed = command(root, "execute", [
			...base,
			"--run-id",
			"run-local-executor",
			"--execution-request",
			requestPath,
			"--kind",
			"local",
			"--output",
			outputFile,
		]);
		expect(executed.status).toBe(5);
		expect((executed.value?.outcome as Record<string, unknown>).outcome).toBe("NOT_EXECUTED");
		const receiptPath = join(artifacts, "execution_receipt.json");
		const receipt = JSON.parse(readFileSync(receiptPath, "utf8")) as Record<string, unknown>;
		expect(receipt.lifecycle).toBe("SUCCEEDED");
		expect((receipt.executor as Record<string, unknown>).trust_level).toBe("cooperative");
		expect(existsSync(outputFile)).toBe(true);
		const status = command(root, "status", [...base, "--run-id", "run-local-executor"]);
		expect((status.value?.checkpoint as { data: { currentUnit: string } }).data.currentUnit).toBe("scan_modelscope");
	});

	it("turns missing executor capability into a non-executed receipt", () => {
		const base = ["--skill", "sure_feed", "--definition", definition, "--validator-registry", registryPath];
		const started = command(root, "start", [
			...base,
			"--run-id",
			"run-missing-capability",
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(started.status).toBe(0);
		const runDir = String((started.value?.run as Record<string, unknown>).runDir);
		const artifacts = join(runDir, "artifacts");
		const requestPath = join(artifacts, "missing-request.json");
		writeFileSync(
			requestPath,
			JSON.stringify(
				executionRequest("run-missing-capability", artifacts, {
					capability_requirements: [
						{
							capability_id: "sure.execution.trusted",
							capability_class: "execution_capability",
							required: true,
						},
					],
				}),
			),
		);
		const executed = command(root, "execute", [
			...base,
			"--run-id",
			"run-missing-capability",
			"--execution-request",
			requestPath,
			"--kind",
			"local",
		]);
		expect(executed.status).toBe(5);
		expect((executed.value?.outcome as Record<string, unknown>).reason_code).toBe("CAPABILITY_MISSING");
		const receipt = JSON.parse(readFileSync(join(artifacts, "execution_receipt.json"), "utf8")) as Record<
			string,
			unknown
		>;
		expect(receipt.lifecycle).toBe("NOT_STARTED");
	});

	it("records missing external adapters for registered remote and trusted executors", () => {
		for (const kind of ["remote", "trusted"] as const) {
			const runId = `run-${kind}-executor`;
			const base = ["--skill", "sure_feed", "--definition", definition, "--validator-registry", registryPath];
			const started = command(root, "start", [
				...base,
				"--run-id",
				runId,
				"--policy-digest",
				DIGEST_A,
				"--executor-digest",
				DIGEST_B,
			]);
			expect(started.status).toBe(0);
			const runDir = String((started.value?.run as Record<string, unknown>).runDir);
			const artifacts = join(runDir, "artifacts");
			const requestPath = join(artifacts, `${kind}-request.json`);
			writeFileSync(requestPath, JSON.stringify(executionRequest(runId, artifacts)));
			const executed = command(root, "execute", [
				...base,
				"--run-id",
				runId,
				"--execution-request",
				requestPath,
				"--kind",
				kind,
			]);
			expect(executed.status).toBe(5);
			expect((executed.value?.outcome as Record<string, unknown>).reason_code).toBe("CAPABILITY_MISSING");
			const receipt = JSON.parse(readFileSync(join(artifacts, "execution_receipt.json"), "utf8")) as Record<
				string,
				unknown
			>;
			expect(receipt.lifecycle).toBe("NOT_STARTED");
			expect((receipt.executor as Record<string, unknown>).kind).toBe(kind);
		}
	});

	it("supports the Python adapter and fails closed when Docker is unavailable", () => {
		const base = ["--skill", "sure_feed", "--definition", definition, "--validator-registry", registryPath];
		const pythonStarted = command(root, "start", [
			...base,
			"--run-id",
			"run-python-executor",
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(pythonStarted.status).toBe(0);
		const pythonRunDir = String((pythonStarted.value?.run as Record<string, unknown>).runDir);
		const pythonArtifacts = join(pythonRunDir, "artifacts");
		const pythonRequestPath = join(pythonArtifacts, "python-request.json");
		writeFileSync(
			pythonRequestPath,
			JSON.stringify(
				executionRequest("run-python-executor", pythonArtifacts, {
					capability_requirements: [
						{
							capability_id: "sure.execution.local-python",
							capability_class: "execution_capability",
							required: true,
						},
					],
				}),
			),
		);
		const pythonResult = command(root, "execute", [
			...base,
			"--run-id",
			"run-python-executor",
			"--execution-request",
			pythonRequestPath,
			"--kind",
			"python",
		]);
		expect(pythonResult.status).toBe(5);
		const pythonReceipt = JSON.parse(readFileSync(join(pythonArtifacts, "execution_receipt.json"), "utf8")) as Record<
			string,
			unknown
		>;
		expect(pythonReceipt.lifecycle).toBe("SUCCEEDED");
		expect((pythonReceipt.capability_evidence as Array<Record<string, unknown>>)[0]?.status).toBe("AVAILABLE");

		const dockerStarted = command(root, "start", [
			...base,
			"--run-id",
			"run-docker-executor",
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(dockerStarted.status).toBe(0);
		const dockerRunDir = String((dockerStarted.value?.run as Record<string, unknown>).runDir);
		const dockerArtifacts = join(dockerRunDir, "artifacts");
		const dockerRequestPath = join(dockerArtifacts, "docker-request.json");
		writeFileSync(
			dockerRequestPath,
			JSON.stringify(
				executionRequest("run-docker-executor", dockerArtifacts, {
					entrypoint: { executable: "/surectl/missing-docker", argv: ["version"] },
					runtime_requirements: { docker_executable: "/surectl/missing-docker" },
					capability_requirements: [
						{
							capability_id: "sure.execution.docker",
							capability_class: "execution_capability",
							required: true,
						},
					],
				}),
			),
		);
		const dockerResult = command(root, "execute", [
			...base,
			"--run-id",
			"run-docker-executor",
			"--execution-request",
			dockerRequestPath,
			"--kind",
			"docker",
		]);
		expect(dockerResult.status).toBe(5);
		const dockerReceipt = JSON.parse(readFileSync(join(dockerArtifacts, "execution_receipt.json"), "utf8")) as Record<
			string,
			unknown
		>;
		expect(dockerReceipt.lifecycle).toBe("NOT_STARTED");
	});

	it("resumes only a failed run with the current binding", () => {
		const base = ["--skill", "sure_feed", "--definition", definition, "--validator-registry", registryPath];
		const started = command(root, "start", [
			...base,
			"--run-id",
			"run-resume",
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(started.status).toBe(0);
		const finalized = command(root, "finalize", ["--run-id", "run-resume", "--status", "failed"]);
		expect(finalized.status).toBe(0);
		const resumed = command(root, "resume", [...base, "--run-id", "run-resume"]);
		expect(resumed.status).toBe(0);
		expect((resumed.value?.run as Record<string, unknown>).status).toBe("running");
	});

	it("blocks a direct checkpoint edit before validation can advance", () => {
		const base = ["--skill", "sure_feed", "--definition", definition, "--validator-registry", registryPath];
		const started = command(root, "start", [
			...base,
			"--run-id",
			"run-forged-checkpoint",
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(started.status).toBe(0);
		const runDir = String((started.value?.run as Record<string, unknown>).runDir);
		const statePath = join(runDir, "state.json");
		const state = JSON.parse(readFileSync(statePath, "utf8")) as Record<string, unknown>;
		const checkpoint = state.checkpoint as Record<string, unknown>;
		const data = checkpoint.data as Record<string, unknown>;
		writeFileSync(
			statePath,
			JSON.stringify({
				...state,
				checkpoint: { ...checkpoint, data: { ...data, currentUnit: "collect_metadata", completedUnits: [] } },
			}),
		);
		const result = command(root, "validate", [...base, "--run-id", "run-forged-checkpoint"]);
		expect(result.status).toBe(5);
		expect((result.value?.outcome as Record<string, unknown>).reason_code).toBe("INVALID_CONTRACT");
	});

	it("keeps cooperative conformance explicitly non-formal", () => {
		const base = ["--skill", "sure_feed", "--definition", definition, "--validator-registry", registryPath];
		const started = command(root, "start", [
			...base,
			"--run-id",
			"run-conformance",
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(started.status).toBe(0);
		const runDir = String((started.value?.run as Record<string, unknown>).runDir);
		const artifacts = join(runDir, "artifacts");
		const requestPath = join(artifacts, "conformance-request.json");
		const receiptPath = join(artifacts, "conformance-receipt.json");
		const request = executionRequest("run-conformance", artifacts);
		writeFileSync(requestPath, JSON.stringify(request));
		const outputPath = join(artifacts, "conformance-output.txt");
		writeFileSync(outputPath, "stable\n");
		const receipt = {
			schema: "sure.execution_receipt.v1",
			receipt_id: "conformance-receipt",
			request_id: request.request_id,
			request_digest: canonicalJsonDigest(request as unknown as JsonValue),
			semantic_request_digest: request.semantic_request_digest,
			run_id: request.run_id,
			unit_id: request.unit_id,
			attempt: request.attempt,
			executor: {
				executor_id: "surectl.local",
				kind: "local",
				version: "0.80.3",
				digest: `sha256:${DIGEST_B}`,
				trust_level: "cooperative",
			},
			lifecycle: "SUCCEEDED",
			capability_evidence: [],
			outputs: [
				{
					artifact_id: "conformance-output",
					path: outputPath,
					resolved_path: outputPath,
					sha256: digest(outputPath),
					size: readFileSync(outputPath).byteLength,
					media_type: "text/plain",
					origin: "generated",
					source_root: artifacts,
				},
			],
			reference_snapshot_digest: request.reference_snapshot_digest,
			output_root: request.output_root,
			policy_digest: request.policy_digest,
			started_at: request.created_at,
			finished_at: request.created_at,
			exit_code: 0,
		};
		writeFileSync(receiptPath, JSON.stringify(receipt));
		const result = command(root, "conformance", [
			"--run-id",
			"run-conformance",
			"--definition",
			definition,
			"--validator-registry",
			registryPath,
			"--execution-request",
			requestPath,
			"--execution-receipt",
			receiptPath,
			"--dataset-digest",
			DIGEST_A,
			"--scoring-digest",
			DIGEST_B,
			"--inference-protocol-digest",
			DIGEST_A,
			"--validator-verdict",
			"PASS",
			"--workflow-disposition",
			"TERMINATE",
		]);
		expect(result.status).toBe(5);
		expect((result.value?.eligibility as Record<string, unknown>).eligible).toBe(false);
		expect((result.value?.conformance as Record<string, unknown>).reason_code).toBe("UPGRADE_REQUIRED");
		writeFileSync(outputPath, "tampered\n");
		const tamperedReceipt = command(root, "conformance", [
			"--run-id",
			"run-conformance",
			"--definition",
			definition,
			"--validator-registry",
			registryPath,
			"--execution-request",
			requestPath,
			"--execution-receipt",
			receiptPath,
			"--dataset-digest",
			DIGEST_A,
			"--scoring-digest",
			DIGEST_B,
			"--inference-protocol-digest",
			DIGEST_A,
			"--validator-verdict",
			"PASS",
			"--workflow-disposition",
			"TERMINATE",
		]);
		expect(tamperedReceipt.status).toBe(5);
		expect((tamperedReceipt.value?.conformance as Record<string, unknown>).reason_code).toBe("DIGEST_MISMATCH");
	});

	it("does not promote caller-supplied gate evidence to formal conformance", () => {
		const runId = "run-external-validator-evidence";
		const base = ["--skill", "sure_feed", "--definition", definition, "--validator-registry", registryPath];
		const started = command(root, "start", [
			...base,
			"--run-id",
			runId,
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		const runDir = String((started.value?.run as Record<string, unknown>).runDir);
		const artifacts = join(runDir, "artifacts");
		writeFileSync(join(artifacts, "scan_result.json"), '{"candidates":[]}\n');
		expect(command(root, "validate", [...base, "--run-id", runId]).status).toBe(0);

		const request = executionRequest(runId, artifacts, {
			unit_id: "match_task",
			operation: "formal_evaluation",
			subject: {
				bundle_manifest_path: join(artifacts, "bundle.json"),
				bundle_digest: `sha256:${DIGEST_A}`,
				runtime_identity_digest: `sha256:${DIGEST_B}`,
				inference_protocol_digest: `sha256:${DIGEST_C}`,
				dataset_identity_digest: `sha256:${DIGEST_A}`,
				scoring_protocol_digest: `sha256:${DIGEST_B}`,
			},
		});
		const requestPath = join(artifacts, "external-evidence-request.json");
		writeFileSync(requestPath, JSON.stringify(request));
		const receipt = {
			schema: "sure.execution_receipt.v1",
			receipt_id: "external-evidence-receipt",
			request_id: request.request_id,
			request_digest: canonicalJsonDigest(request as unknown as JsonValue),
			semantic_request_digest: request.semantic_request_digest,
			run_id: request.run_id,
			unit_id: request.unit_id,
			attempt: request.attempt,
			executor: {
				executor_id: "pi-python",
				kind: "python",
				version: "1",
				digest: `sha256:${DIGEST_B}`,
				trust_level: "host_enforced",
			},
			lifecycle: "SUCCEEDED",
			capability_evidence: [],
			outputs: [],
			reference_snapshot_digest: request.reference_snapshot_digest,
			output_root: request.output_root,
			policy_digest: request.policy_digest,
			started_at: request.created_at,
			finished_at: request.created_at,
			exit_code: 0,
		};
		const receiptPath = join(artifacts, "external-evidence-receipt.json");
		writeFileSync(receiptPath, JSON.stringify(receipt));
		expect(
			command(root, "validate", [
				...base,
				"--run-id",
				runId,
				"--execution-request",
				requestPath,
				"--execution-receipt",
				receiptPath,
			]).status,
		).toBe(5);

		const matchArtifact = join(artifacts, "match_task_result.json");
		writeFileSync(matchArtifact, '{"candidates":[]}\n');
		const registry = JSON.parse(readFileSync(registryPath, "utf8")) as {
			digest: string;
			validators: Array<{ id: string; unit_id?: string }>;
		};
		const validator = registry.validators.find((entry) => entry.unit_id === "match_task");
		const evidencePath = join(artifacts, "external-formal-evidence.json");
		writeFileSync(
			evidencePath,
			JSON.stringify({
				registry_digest: registry.digest,
				validators: [{ validator_id: validator?.id, verdict: "PASS", artifact_digest: digest(matchArtifact) }],
			}),
		);
		expect(command(root, "validate", [...base, "--run-id", runId, "--evidence", evidencePath]).status).toBe(0);
		const conformance = command(root, "conformance", [
			...base,
			"--run-id",
			runId,
			"--execution-request",
			requestPath,
			"--execution-receipt",
			receiptPath,
			"--assurance-profile",
			"pi_enforced",
		]);
		expect(conformance.status).toBe(5);
		expect((conformance.value?.eligibility as { eligible: boolean }).eligible).toBe(false);
		expect((conformance.value?.eligibility as { diagnostics: string[] }).diagnostics).toContain(
			"formal conformance does not accept caller-supplied validator verdicts",
		);
	});

	it("freezes a fully bound evaluation subject and refuses tampered subjects", () => {
		const base = ["--skill", "sure_feed", "--definition", definition, "--validator-registry", registryPath];
		const started = command(root, "start", [
			...base,
			"--run-id",
			"run-freeze",
			"--policy-digest",
			DIGEST_A,
			"--executor-digest",
			DIGEST_B,
		]);
		expect(started.status).toBe(0);
		const runDir = String((started.value?.run as Record<string, unknown>).runDir);
		const artifacts = join(runDir, "artifacts");
		const request = executionRequest("run-freeze", artifacts, {
			operation: "formal_evaluation",
			subject: {
				bundle_manifest_path: join(artifacts, "bundle.json"),
				bundle_digest: `sha256:${DIGEST_A}`,
				runtime_identity_digest: `sha256:${DIGEST_B}`,
				inference_protocol_digest: `sha256:${DIGEST_C}`,
				dataset_identity_digest: `sha256:${DIGEST_A}`,
				scoring_protocol_digest: `sha256:${DIGEST_B}`,
			},
		});
		const requestPath = join(artifacts, "freeze-request.json");
		writeFileSync(requestPath, JSON.stringify(request));
		const receipt = {
			schema: "sure.execution_receipt.v1",
			receipt_id: "freeze-receipt",
			request_id: request.request_id,
			request_digest: canonicalJsonDigest(request as unknown as JsonValue),
			semantic_request_digest: request.semantic_request_digest,
			run_id: request.run_id,
			unit_id: request.unit_id,
			attempt: request.attempt,
			executor: {
				executor_id: "pi-python",
				kind: "python",
				version: "1",
				digest: `sha256:${DIGEST_B}`,
				trust_level: "host_enforced",
			},
			lifecycle: "SUCCEEDED",
			capability_evidence: [],
			outputs: [],
			reference_snapshot_digest: request.reference_snapshot_digest,
			output_root: request.output_root,
			policy_digest: request.policy_digest,
			started_at: request.created_at,
			finished_at: request.created_at,
			exit_code: 0,
		};
		const receiptPath = join(artifacts, "freeze-receipt.json");
		writeFileSync(receiptPath, JSON.stringify(receipt));
		const prematureConformance = command(root, "conformance", [
			"--run-id",
			"run-freeze",
			"--definition",
			definition,
			"--validator-registry",
			registryPath,
			"--execution-request",
			requestPath,
			"--execution-receipt",
			receiptPath,
			"--assurance-profile",
			"pi_enforced",
			"--validator-verdict",
			"PASS",
			"--workflow-disposition",
			"TERMINATE",
		]);
		expect(prematureConformance.status).toBe(5);
		expect((prematureConformance.value?.conformance as Record<string, unknown>).reason_code).toBe(
			"VALIDATION_PENDING",
		);
		const executionRecorded = command(root, "validate", [
			...base,
			"--run-id",
			"run-freeze",
			"--execution-request",
			requestPath,
			"--execution-receipt",
			receiptPath,
		]);
		expect(executionRecorded.status).toBe(5);
		const validatedArtifact = join(artifacts, "scan_result.json");
		writeFileSync(validatedArtifact, '{"candidates":[]}\n');
		const validated = command(root, "validate", [...base, "--run-id", "run-freeze"]);
		expect(validated.status).toBe(0);
		const predictionPath = join(artifacts, "predictions.txt");
		writeFileSync(predictionPath, "sample\tanswer\n");
		const frozen = command(root, "freeze", [
			"--run-id",
			"run-freeze",
			"--definition",
			definition,
			"--validator-registry",
			registryPath,
			"--execution-request",
			requestPath,
			"--execution-receipt",
			receiptPath,
			"--prediction",
			predictionPath,
			"--engine-digest",
			DIGEST_A,
			"--route",
			"asr.zh.cer.v1",
			"--approval-digest",
			DIGEST_C,
			"--assurance-profile",
			"pi_enforced",
		]);
		expect(frozen.status).toBe(0);
		const subjectPath = join(artifacts, "evaluation_subject.json");
		const subject = JSON.parse(readFileSync(subjectPath, "utf8")) as Record<string, unknown>;
		expect(subject.legacy_unverified).toBe(false);
		expect(typeof subject.subject_digest).toBe("string");

		const conformance = command(root, "conformance", [
			"--run-id",
			"run-freeze",
			"--definition",
			definition,
			"--validator-registry",
			registryPath,
			"--execution-request",
			requestPath,
			"--execution-receipt",
			receiptPath,
			"--subject",
			subjectPath,
			"--assurance-profile",
			"pi_enforced",
			"--validator-verdict",
			"PASS",
			"--workflow-disposition",
			"TERMINATE",
		]);
		expect(conformance.status).toBe(0);
		expect((conformance.value?.eligibility as Record<string, unknown>).eligible).toBe(true);

		subject.prediction_digest = `sha256:${DIGEST_C}`;
		writeFileSync(subjectPath, JSON.stringify(subject));
		const tampered = command(root, "conformance", [
			"--run-id",
			"run-freeze",
			"--definition",
			definition,
			"--validator-registry",
			registryPath,
			"--execution-request",
			requestPath,
			"--execution-receipt",
			receiptPath,
			"--subject",
			subjectPath,
			"--assurance-profile",
			"pi_enforced",
			"--validator-verdict",
			"PASS",
			"--workflow-disposition",
			"TERMINATE",
		]);
		expect(tampered.status).toBe(5);
		expect((tampered.value?.eligibility as Record<string, unknown>).eligible).toBe(false);
		expect((tampered.value?.conformance as Record<string, unknown>).reason_code).toBe("DIGEST_MISMATCH");
	});

	it("reports capability absence as a non-passing status", () => {
		const result = command(root, "capabilities", ["--skill", "sure_eval", "--definition", evalDefinition]);
		expect(result.status).toBe(5);
		expect((result.value?.admission as Record<string, unknown>).admitted).toBe(false);
		const report = result.value?.report;
		expect(report).toBeDefined();
		const executorRegistry = result.value?.executor_registry as Record<string, unknown>;
		expect(executorRegistry.schema).toBe("sure.executor.registry.v1");
		expect(executorRegistry.registry_digest).toMatch(/^sha256:[0-9a-f]{64}$/);
		expect(executorRegistry.executors).toEqual(
			expect.arrayContaining([
				expect.objectContaining({ kind: "remote", implementation: "external_registration_required" }),
				expect.objectContaining({ kind: "trusted", implementation: "external_registration_required" }),
			]),
		);
	});

	it("binds start/resume to an immutable site-policy snapshot", () => {
		const snapshotPath = join(root, "site-policy.snapshot.json");
		const snapshot = policySnapshot(root);
		mkdirSync(join(root, "policy-reference"));
		mkdirSync(join(root, "policy-publication"));
		writeFileSync(snapshotPath, JSON.stringify(snapshot));
		const base = [
			"--skill",
			"sure_feed",
			"--definition",
			definition,
			"--validator-registry",
			registryPath,
			"--policy-snapshot",
			snapshotPath,
		];
		const started = command(root, "start", [
			...base,
			"--run-id",
			"run-policy-snapshot",
			"--executor-digest",
			DIGEST_B,
		]);
		expect(started.status).toBe(0);
		const run = started.value?.run as Record<string, unknown>;
		expect(run.policyDigest).toBe(snapshot.policy_digest);
		expect(run.policySnapshotDigest).toBe(snapshot.snapshot_digest);
		const persistedPath = String(run.policySnapshotPath);
		expect(JSON.parse(readFileSync(persistedPath, "utf8"))).toMatchObject({
			snapshot_digest: snapshot.snapshot_digest,
		});

		const failed = command(root, "finalize", ["--run-id", "run-policy-snapshot", "--status", "failed"]);
		expect(failed.status).toBe(0);
		const resumed = command(root, "resume", [...base, "--run-id", "run-policy-snapshot"]);
		expect(resumed.status).toBe(0);
		const failedAgain = command(root, "finalize", ["--run-id", "run-policy-snapshot", "--status", "failed"]);
		expect(failedAgain.status).toBe(0);

		const changed = { ...snapshot, policy: { changed: true } };
		const changedPath = join(root, "changed-policy.snapshot.json");
		writeFileSync(
			changedPath,
			JSON.stringify(
				createPolicySnapshot({
					site_id: changed.site_id,
					policy_version: changed.policy_version,
					policy: changed.policy,
					source: changed.source,
					path_bindings: changed.path_bindings,
				}),
			),
		);
		const rejected = command(root, "resume", [...base.slice(0, -1), changedPath, "--run-id", "run-policy-snapshot"]);
		expect(rejected.status).toBe(1);
		expect(rejected.stderr).toMatch(/does not match the run binding|policy_digest/);

		const rejectedOutput = command(root, "start", [
			...base,
			"--run-id",
			"run-policy-reference-output",
			"--executor-digest",
			DIGEST_B,
			"--output-dir",
			join(root, "policy-reference", "result"),
		]);
		expect(rejectedOutput.status).toBe(1);
		expect(rejectedOutput.stderr).toMatch(/read-only reference root/);

		const persisted = JSON.parse(readFileSync(persistedPath, "utf8")) as Record<string, unknown>;
		persisted.policy = { tampered: true };
		writeFileSync(persistedPath, JSON.stringify(persisted));
		const rejectedTamper = command(root, "status", ["--run-id", "run-policy-snapshot"]);
		expect(rejectedTamper.status).toBe(1);
		expect(rejectedTamper.stderr).toMatch(/policy_digest|canonical contents/);
	});
});
