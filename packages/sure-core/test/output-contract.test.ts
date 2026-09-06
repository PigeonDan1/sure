import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
	type ArtifactRef,
	canonicalJsonDigest,
	type ExecutionOutputContract,
	type ExecutionReceipt,
	type ExecutionRequest,
	executionOutputContractDigest,
	executionOutputSetDigest,
	inspectExecutionArtifact,
	validateExecutionOutputBinding,
	validateExecutionOutputContract,
} from "../src/index.ts";

const A = `sha256:${"a".repeat(64)}`;
const B = `sha256:${"b".repeat(64)}`;
const NOW = "2026-09-07T00:00:00.000Z";
const roots: string[] = [];

function freshRoot(): string {
	const root = mkdtempSync(join("/tmp", "sure-output-contract-"));
	roots.push(root);
	return root;
}

function contract(mode: ExecutionOutputContract["mode"] = "producing"): ExecutionOutputContract {
	return {
		schema: "sure.execution_output_contract.v1",
		mode,
		outputs: [
			{ artifact_id: "manifest", path: "manifest.json", kind: "file", required: true },
			{ artifact_id: "bundle", path: "bundle", kind: "directory", required: false },
		],
		temporary_paths: [".staging"],
		allow_missing_on_failure: true,
		retain_failed_outputs: true,
	};
}

function request(root: string, outputContract?: ExecutionOutputContract): ExecutionRequest {
	return {
		schema: "sure.execution_request.v1",
		request_id: "request-output-contract",
		semantic_request_digest: A,
		run_id: "run-output-contract",
		unit_id: "produce",
		attempt: 1,
		operation: "package",
		subject: {
			bundle_manifest_path: join(root, "subject.json"),
			bundle_digest: A,
			runtime_identity_digest: B,
		},
		inputs: [],
		entrypoint: { executable: "python3", argv: ["-c", "pass"] },
		runtime_requirements: {},
		capability_requirements: [],
		reference_snapshot_digest: B,
		output_root: {
			path: root,
			resolved_path: root,
			scope_id: "run-output-contract",
			policy_digest: A,
			writable: true,
		},
		policy_digest: A,
		created_at: NOW,
		...(outputContract === undefined ? {} : { output_contract: outputContract }),
	};
}

function receipt(input: ExecutionRequest, overrides: Partial<ExecutionReceipt> = {}): ExecutionReceipt {
	const base: ExecutionReceipt = {
		schema: "sure.execution_receipt.v1",
		receipt_id: "receipt-output-contract",
		request_id: input.request_id,
		request_digest: canonicalJsonDigest(input as never),
		semantic_request_digest: input.semantic_request_digest,
		run_id: input.run_id,
		unit_id: input.unit_id,
		attempt: input.attempt,
		executor: {
			executor_id: "surectl.python",
			kind: "python",
			version: "test",
			digest: A,
			trust_level: "cooperative",
		},
		lifecycle: "FAILED",
		capability_evidence: [],
		outputs: [],
		reference_snapshot_digest: input.reference_snapshot_digest,
		output_root: input.output_root,
		policy_digest: input.policy_digest,
		started_at: NOW,
		finished_at: NOW,
		...overrides,
	};
	if (input.output_contract !== undefined) {
		base.output_contract_digest = executionOutputContractDigest(input.output_contract);
		base.output_set_digest = executionOutputSetDigest(base.outputs, base.residuals ?? []);
		base.residuals ??= [];
		base.output_set_digest = executionOutputSetDigest(base.outputs, base.residuals);
	}
	return base;
}

function fileArtifact(root: string, artifactId: string, relativePath: string): ArtifactRef {
	const path = join(root, relativePath);
	return {
		artifact_id: artifactId,
		path,
		resolved_path: path,
		sha256: A,
		size: 1,
		media_type: "application/octet-stream",
		origin: "generated",
		source_root: root,
		kind: "file",
		digest_kind: "file_sha256",
	};
}

afterEach(() => {
	for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe("execution output contract", () => {
	it("validates a producing contract and keeps its digest stable", () => {
		const value = contract();
		expect(validateExecutionOutputContract(value)).toEqual({ valid: true, errors: [] });
		expect(executionOutputContractDigest(value)).toMatch(/^sha256:[0-9a-f]{64}$/);
		expect(executionOutputSetDigest([], [])).toBe(executionOutputSetDigest([], []));
	});

	it("uses the same UTF-16 output ordering as the Python bridge", () => {
		const outputs: ArtifactRef[] = [
			{
				artifact_id: "z",
				path: "/tmp/根/z",
				resolved_path: "/tmp/根/z",
				sha256: A,
				size: 1,
				media_type: "application/octet-stream",
				origin: "generated",
				source_root: "/tmp/根",
			},
			{
				artifact_id: "a",
				path: "/tmp/根/😀",
				resolved_path: "/tmp/根/😀",
				sha256: B,
				size: 2,
				media_type: "application/octet-stream",
				origin: "generated",
				source_root: "/tmp/根",
			},
		];
		expect(executionOutputSetDigest(outputs, [])).toBe(
			"sha256:a67a440e2802ca59ec5977ed45820d99c5d69ef303fffc49f4f8378f4551056d",
		);
	});

	it("allows missing outputs and retained temporary residue only on failure", () => {
		const root = freshRoot();
		const input = request(root, contract());
		const staging = join(root, ".staging");
		mkdirSync(staging);
		writeFileSync(join(staging, "partial.bin"), "x");
		const residual = {
			path: join(root, ".staging"),
			resolved_path: join(root, ".staging"),
			kind: "directory" as const,
			status: "present" as const,
			sha256: inspectExecutionArtifact(staging).sha256,
			digest_kind: "tree_sha256" as const,
			size: 1,
		};
		const current = receipt(input, { residuals: [residual] });
		current.output_set_digest = executionOutputSetDigest(current.outputs, current.residuals);
		expect(validateExecutionOutputBinding(input, current)).toEqual([]);
	});

	it("rejects a successful receipt that omits a required producer output", () => {
		const root = freshRoot();
		const input = request(root, contract());
		const current = receipt(input, { lifecycle: "SUCCEEDED", exit_code: 0 });
		current.output_set_digest = executionOutputSetDigest([], []);
		const errors = validateExecutionOutputBinding(input, current);
		expect(errors).toContain("required output manifest is missing from receipt");
	});

	it("rejects output-set digest tampering and unlisted output paths", () => {
		const root = freshRoot();
		const input = request(root, contract("mutating"));
		const current = receipt(input, { outputs: [fileArtifact(root, "unexpected", "unexpected.json")] });
		current.output_set_digest = B;
		const errors = validateExecutionOutputBinding(input, current);
		expect(errors).toEqual(
			expect.arrayContaining([
				"receipt.outputs[0].artifact_id is not declared by output_contract",
				"receipt.output_set_digest does not match observed outputs and residuals",
			]),
		);
	});

	it("uses a deterministic tree digest and rejects symlinked directory entries", () => {
		const root = freshRoot();
		const bundle = join(root, "bundle");
		mkdirSync(bundle);
		writeFileSync(join(bundle, "a.txt"), "a");
		const first = inspectExecutionArtifact(bundle);
		writeFileSync(join(bundle, "b.txt"), "b");
		const second = inspectExecutionArtifact(bundle);
		expect(first.digest_kind).toBe("tree_sha256");
		expect(first.sha256).not.toBe(second.sha256);
		symlinkSync(join(root, "outside"), join(bundle, "escape"));
		expect(() => inspectExecutionArtifact(bundle)).toThrow(/symlink/);
	});
});
