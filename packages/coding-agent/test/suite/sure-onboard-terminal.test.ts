import { readFileSync } from "node:fs";
import { join } from "node:path";
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
});
