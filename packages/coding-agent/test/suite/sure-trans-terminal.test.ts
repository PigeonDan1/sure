import { describe, expect, it } from "vitest";
import {
	blockedDeploymentError,
	digestOf,
	gateAlreadyRejected,
	gateRewriteNotice,
} from "../../../../sure/skills/sure_trans/hooks/index.ts";
import { TRANS_UNITS } from "../../../../sure/skills/sure_trans/hooks/state-machine.ts";

describe("sure_trans blocked terminal contract", () => {
	const marker = {
		status: "blocked",
		blocked_reason: "source image push was rejected",
		execution_policy: { container_only: false },
	};

	it("accepts a blocked marker that records why the run stopped", () => {
		expect(blockedDeploymentError(marker, "failed")).toBeUndefined();
	});

	it("rejects a non-success finish that still claims the bundle is ready", () => {
		expect(blockedDeploymentError({ ...marker, status: "ready" }, "failed")).toContain("status=blocked");
	});

	it("rejects a blocked marker that claims container-only readiness", () => {
		expect(blockedDeploymentError({ ...marker, execution_policy: { container_only: true } }, "failed")).toContain(
			"container_only",
		);
	});

	it("rejects a blocked marker with no reason", () => {
		expect(blockedDeploymentError({ ...marker, blocked_reason: "  " }, "incomplete")).toContain("blocked_reason");
	});

	it("rejects a non-success finish that does not declare failed or incomplete", () => {
		expect(blockedDeploymentError(marker, "cancelled")).toContain("incomplete");
	});
});

describe("sure_trans unit script ownership", () => {
	it("lets every unit that submits a vc job run the vc CLI SKILL.md points at", () => {
		const vcUnits = ["validate_env_compat", "package_container"];
		for (const id of vcUnits) {
			const unit = TRANS_UNITS.find((candidate) => candidate.id === id);
			expect(unit?.ownedScripts ?? []).toContain("vc_exec.py");
		}
	});
});

describe("gateRewriteNotice", () => {
	const unit = { id: "validate_env_compat", produces: "execution_compat.json" };

	it("says nothing when the gate left the artifact alone", () => {
		expect(gateRewriteNotice(unit, { status: "ready" }, { status: "ready" })).toBeUndefined();
	});

	it("names the artifact and both statuses when the gate replaced it", () => {
		const notice = gateRewriteNotice(unit, { status: "blocked" }, { status: "ready" });
		expect(notice).toContain("execution_compat.json");
		expect(notice).toContain("blocked");
		expect(notice).toContain("ready");
	});
});

describe("gateAlreadyRejected", () => {
	const artifact = { status: "failed", vc_job_id: "job-1" };
	const rejected = { failedArtifactDigests: { validate_contract: digestOf(artifact) } };

	it("holds the gate back when the artifact is what it already rejected", () => {
		expect(gateAlreadyRejected(rejected, "validate_contract", artifact)).toBe(true);
	});

	it("lets the gate run once the artifact changes", () => {
		expect(gateAlreadyRejected(rejected, "validate_contract", { ...artifact, vc_job_id: "job-2" })).toBe(false);
	});

	it("lets the gate run when the unit has never been rejected", () => {
		expect(gateAlreadyRejected({ failedArtifactDigests: {} }, "validate_contract", artifact)).toBe(false);
	});
});
