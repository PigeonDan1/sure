import { describe, expect, it } from "vitest";
import { incompleteDeploymentError } from "../../../../sure/skills/sure_onboard/hooks/index.ts";

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
