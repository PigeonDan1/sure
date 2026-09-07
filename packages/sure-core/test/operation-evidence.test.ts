import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
	admitOperationExecutionEvidence,
	createOperationExecutionEvidence,
	decodeOperationExecutionEvidence,
	type ExecutionArtifactMode,
	operationExecutionSemanticProjection,
} from "../src/index.ts";

interface ParityCase {
	id: string;
	operation_id: string;
	artifact_mode: ExecutionArtifactMode;
	artifact_input_digest: string;
	artifact_output_digest: string;
}

interface ParityFixture {
	schema: string;
	cases: ParityCase[];
}

const FIXTURE = JSON.parse(
	readFileSync(new URL("../../../sure/canonical/fixtures/operation-evidence-parity.json", import.meta.url), "utf8"),
) as ParityFixture;

function admit(value: unknown, item: ParityCase) {
	const decoded = decodeOperationExecutionEvidence(value);
	if (!decoded.ok) throw new Error(decoded.errors.join("; "));
	const admitted = admitOperationExecutionEvidence(decoded, {
		expected_operation_id: item.operation_id,
		expected_artifact_mode: item.artifact_mode,
		receipt_output_digest: item.artifact_output_digest,
	});
	if (!admitted.ok) throw new Error(admitted.errors.join("; "));
	return admitted.evidence;
}

describe("registered operation evidence", () => {
	it("projects Pi and surectl evidence identically for every artifact mode", () => {
		expect(FIXTURE.schema).toBe("sure.operation_evidence.parity.v1");
		for (const item of FIXTURE.cases) {
			const common = {
				operation_id: item.operation_id,
				artifact_mode: item.artifact_mode,
				verdict: "PASS" as const,
				reason_code: "EXECUTION_SUCCEEDED",
				diagnostics: [],
				artifact_input_digest: item.artifact_input_digest,
				artifact_output_digest: item.artifact_output_digest,
			};
			const portable = admit(createOperationExecutionEvidence({ source: "surectl", ...common }), item);
			const pi = admit(createOperationExecutionEvidence({ source: "pi_hook", ...common }), item);
			expect(operationExecutionSemanticProjection(pi), item.id).toEqual(
				operationExecutionSemanticProjection(portable),
			);
		}
	});

	it("derives a missing legacy output digest from the receipt without mutating legacy state", () => {
		const item = FIXTURE.cases[1];
		if (!item) throw new Error("mutating fixture is missing");
		const legacy = {
			schema: "sure.operation.execution.v1",
			source: "registered_operation",
			operation_id: item.operation_id,
			verdict: "PASS",
			reason_code: "EXECUTION_SUCCEEDED",
			diagnostics: [],
			artifact_input_digest: item.artifact_input_digest,
		};
		const admitted = admit(legacy, item);
		expect(admitted.compatibility).toBe("legacy-v1");
		expect(admitted.artifact_output_digest).toBe(item.artifact_output_digest);
		expect(admitted.artifact_output_digest_source).toBe("receipt");
		expect(legacy).not.toHaveProperty("artifact_output_digest");
	});

	it("fails closed when current PASS evidence loses its mode or output digest", () => {
		const item = FIXTURE.cases[2];
		if (!item) throw new Error("producing fixture is missing");
		const current = createOperationExecutionEvidence({
			source: "surectl",
			operation_id: item.operation_id,
			artifact_mode: item.artifact_mode,
			verdict: "PASS",
			reason_code: "EXECUTION_SUCCEEDED",
			diagnostics: [],
			artifact_input_digest: item.artifact_input_digest,
			artifact_output_digest: item.artifact_output_digest,
		});
		const missingMode = { ...current, artifact_mode: undefined };
		const missingOutput = { ...current, artifact_output_digest: undefined };
		expect(decodeOperationExecutionEvidence(missingMode)).toMatchObject({ ok: false });
		expect(decodeOperationExecutionEvidence(missingOutput)).toMatchObject({ ok: false });
	});

	it("rejects mode and receipt digest mismatches", () => {
		const item = FIXTURE.cases[0];
		if (!item) throw new Error("preexisting fixture is missing");
		const decoded = decodeOperationExecutionEvidence(
			createOperationExecutionEvidence({
				source: "surectl",
				operation_id: item.operation_id,
				artifact_mode: item.artifact_mode,
				verdict: "PASS",
				reason_code: "EXECUTION_SUCCEEDED",
				diagnostics: [],
				artifact_input_digest: item.artifact_input_digest,
				artifact_output_digest: item.artifact_output_digest,
			}),
		);
		if (!decoded.ok) throw new Error(decoded.errors.join("; "));
		expect(
			admitOperationExecutionEvidence(decoded, {
				expected_operation_id: item.operation_id,
				expected_artifact_mode: "mutating",
				receipt_output_digest: `sha256:${"f".repeat(64)}`,
			}),
		).toMatchObject({ ok: false });
	});
});
