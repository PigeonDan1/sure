import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Unit } from "./state-machine.ts";

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

const SCHEMAS: Record<string, string> = {
	resolve_input: "sure.approve.input_resolved.v1",
	classify_producer: "sure.approve.producer_contract_report.v1",
	audit_integrity: "sure.approve.integrity_report.v1",
	plan_repairs: "sure.approve.repair_plan.v1",
	apply_repairs: "sure.approve.repair_report.v1",
	seal_candidate: "sure.approve.approval_manifest.v1",
	verify_runtime: "sure.approve.runtime_verification.v1",
	prepare_review: "sure.approve.review_packet.v1",
	verify_decision: "sure.approve.approval_decision.v1",
	publish: "sure.approve.publication_result.v1",
	verify_publication: "sure.approve.approval_ready.v1",
};

export function readArtifact(runDir: string, name: string): unknown | undefined {
	for (const path of [join(runDir, "artifacts", "debug", name), join(runDir, "artifacts", name)]) {
		try {
			return JSON.parse(readFileSync(path, "utf-8"));
		} catch {
			// Continue to the normal artifact path or report the artifact as absent.
		}
	}
	return undefined;
}

export function validateProduces(unit: Unit, value: unknown): string | undefined {
	if (!isRecord(value)) {
		return `${unit.produces} must be a JSON object.`;
	}
	if (value.schema !== SCHEMAS[unit.id]) {
		return `${unit.produces} has unsupported schema ${JSON.stringify(value.schema)}.`;
	}
	for (const field of unit.requiredFields) {
		if (!(field in value)) {
			return `${unit.produces} is missing required field "${field}".`;
		}
	}
	if (
		value.status !== "passed" &&
		!(unit.id === "prepare_review" && value.status === "awaiting_approval") &&
		!(unit.id === "verify_decision" && (value.status === "approved" || value.status === "rejected")) &&
		!(unit.id === "verify_publication" && value.status === "ready")
	) {
		return `${unit.produces} has non-passing status ${JSON.stringify(value.status)}.`;
	}
	return undefined;
}
