import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { canonicalJsonDigest } from "../../sure-core/src/index.ts";

const repositoryRoot = fileURLToPath(new URL("../../..", import.meta.url));
const source = join(repositoryRoot, "packages/sure-cli/src/surectl.ts");
const definition = join(repositoryRoot, "sure/dist/agent-skills/sure-feed/canonical-definition.json");
const registryPath = join(repositoryRoot, "sure/dist/agent-skills/sure-feed/validator-registry.json");
const evalDefinition = join(repositoryRoot, "sure/dist/agent-skills/sure-eval/canonical-definition.json");
const DIGEST_A = "a".repeat(64);
const DIGEST_B = "b".repeat(64);

interface CommandResult {
	status: number | null;
	stdout: string;
	stderr: string;
	value?: Record<string, unknown>;
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

describe("surectl cooperative control plane", () => {
	let root: string;

	beforeEach(() => {
		root = mkdtempSync(join(tmpdir(), "surectl-test-"));
	});

	afterEach(() => {
		rmSync(root, { recursive: true, force: true });
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
		expect(missing.status).toBe(0);
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
		expect(noEvidence.status).toBe(0);
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
		expect(
			((validated.value?.transition as Record<string, unknown>).checkpoint as { data: { currentUnit: string } }).data
				.currentUnit,
		).toBe("collect_metadata");
	});

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
		expect(admitted.status).toBe(0);
		expect((admitted.value?.outcome as Record<string, unknown>).outcome).toBe("NOT_EXECUTED");
		expect((admitted.value?.checkpoint as { data: { currentUnit: string } }).data.currentUnit).toBe(
			"scan_modelscope",
		);

		const receipt = {
			schema: "sure.execution_receipt.v1",
			receipt_id: "receipt-execution",
			request_id: request.request_id,
			request_digest: canonicalJsonDigest(request),
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
		expect(receiptResult.status).toBe(0);
		expect((receiptResult.value?.outcome as Record<string, unknown>).reason_code).toBe("VALIDATION_PENDING");
	});

	it("reports capability absence as a non-passing status", () => {
		const result = command(root, "capabilities", ["--skill", "sure_eval", "--definition", evalDefinition]);
		expect(result.status).toBe(0);
		expect((result.value?.admission as Record<string, unknown>).admitted).toBe(false);
		const report = result.value?.report;
		expect(report).toBeDefined();
	});
});
