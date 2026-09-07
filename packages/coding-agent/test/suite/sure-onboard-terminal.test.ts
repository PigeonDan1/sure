import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createOperationExecutionEvidence } from "@earendil-works/sure-core";
import { describe, expect, it } from "vitest";
import { incompleteDeploymentError } from "../../../../sure/skills/sure_onboard/hooks/index.ts";
import { normalizeSureDisplayStatePatch } from "../../src/core/sure/state.ts";

describe("sure_onboard incomplete terminal contract", () => {
	const deployment = { status: "local_only", execution_policy: { container_only: false } };
	const packageGate = {
		status: "passed",
		readiness: { bundle_ready: false, registry_ready: false },
	};

	it("accepts blocked local-container evidence without claiming Eval readiness", () => {
		expect(incompleteDeploymentError(deployment, packageGate, "incomplete")).toBeUndefined();
	});

	it("rejects a blocked finish that claims registry readiness", () => {
		expect(
			incompleteDeploymentError(
				deployment,
				{ ...packageGate, readiness: { bundle_ready: false, registry_ready: true } },
				"incomplete",
			),
		).toContain("registry_ready=false");
	});
});

describe("sure_onboard non-success state patch", () => {
	it("marks the blocked deployment artifact with a status the state validator accepts", () => {
		const source = readFileSync(
			join(import.meta.dirname, "..", "..", "..", "..", "sure", "skills", "sure_onboard", "hooks", "index.ts"),
			"utf-8",
		);
		const artifactStatuses = [...source.matchAll(/type: "deployment_ready",[\s\S]{0,400}?status: "([a-z_]+)"/g)].map(
			(match) => match[1],
		);
		expect(artifactStatuses.length).toBeGreaterThan(0);
		for (const status of artifactStatuses) {
			const result = normalizeSureDisplayStatePatch({
				artifacts: [{ type: "deployment_ready", name: "marker", path: "artifacts/x.json", status }],
			});
			expect(result.ok, `artifact status "${status}" is dropped by the state validator`).toBe(true);
		}
	});

	it("preserves a Core-valid operation projection", () => {
		const evidence = createOperationExecutionEvidence({
			source: "pi_hook",
			operation_id: "sure.onboard.execute_env_compat",
			artifact_mode: "preexisting",
			verdict: "PASS",
			reason_code: "EXECUTION_SUCCEEDED",
			diagnostics: [],
			artifact_input_digest: `sha256:${"a".repeat(64)}`,
			artifact_output_digest: `sha256:${"a".repeat(64)}`,
		});
		const result = normalizeSureDisplayStatePatch({ last_execution: evidence });
		expect(result).toEqual({ ok: true, state: { last_execution: evidence } });
	});

	it("rejects a current PASS projection without output evidence", () => {
		const evidence = createOperationExecutionEvidence({
			source: "pi_hook",
			operation_id: "sure.onboard.execute_env_compat",
			artifact_mode: "preexisting",
			verdict: "PASS",
			reason_code: "EXECUTION_SUCCEEDED",
			diagnostics: [],
			artifact_input_digest: `sha256:${"a".repeat(64)}`,
		});
		const result = normalizeSureDisplayStatePatch({ last_execution: evidence });
		expect(result.ok).toBe(false);
		expect(result.message).toContain("current PASS operation evidence is missing artifact_output_digest");
	});
});
