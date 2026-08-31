export type ApproveMode = "audit" | "approve";

export interface Unit {
	id: string;
	label: string;
	produces: string;
	schemaRef: string;
	gateScript: string;
	gateScriptArgs?: string[];
	requiredFields: string[];
}

export const APPROVE_UNITS: Unit[] = [
	{
		id: "resolve_input",
		label: "Resolve approval input",
		produces: "approve_input_resolved.json",
		schemaRef: "approve_input_resolved.schema.json",
		gateScript: "resolve_approve_input.py",
		requiredFields: ["schema", "status", "mode", "source", "approval", "site_policy"],
	},
	{
		id: "classify_producer",
		label: "Classify producer contract",
		produces: "producer_contract_report.json",
		schemaRef: "producer_contract_report.schema.json",
		gateScript: "audit_bundle.py",
		gateScriptArgs: ["--kind", "producer"],
		requiredFields: ["schema", "status", "producer", "contract", "model_name", "runtime_kind", "findings"],
	},
	{
		id: "audit_integrity",
		label: "Audit bundle integrity",
		produces: "integrity_report.json",
		schemaRef: "integrity_report.schema.json",
		gateScript: "audit_bundle.py",
		gateScriptArgs: ["--kind", "integrity"],
		requiredFields: ["schema", "status", "source_digest", "checks", "findings"],
	},
	{
		id: "plan_repairs",
		label: "Plan bounded repairs",
		produces: "repair_plan.json",
		schemaRef: "repair_plan.schema.json",
		gateScript: "plan_repairs.py",
		requiredFields: ["schema", "status", "repair_mode", "safe_repairs", "rerun_required"],
	},
	{
		id: "apply_repairs",
		label: "Create isolated candidate",
		produces: "repair_report.json",
		schemaRef: "repair_report.schema.json",
		gateScript: "apply_safe_repairs.py",
		requiredFields: [
			"schema",
			"status",
			"candidate_dir",
			"source_digest_before",
			"source_digest_after",
			"repairs_applied",
		],
	},
	{
		id: "seal_candidate",
		label: "Seal candidate identity",
		produces: "approval_manifest.json",
		schemaRef: "approval_manifest.schema.json",
		gateScript: "build_approval_manifest.py",
		gateScriptArgs: ["--kind", "manifest"],
		requiredFields: ["schema", "status", "model_name", "candidate_dir", "candidate_digest", "files"],
	},
	{
		id: "verify_runtime",
		label: "Verify candidate runtime",
		produces: "runtime_verification.json",
		schemaRef: "runtime_verification.schema.json",
		gateScript: "verify_candidate_runtime.py",
		requiredFields: ["schema", "status", "runtime_kind", "binding", "smoke"],
	},
	{
		id: "prepare_review",
		label: "Prepare human review",
		produces: "review_packet.json",
		schemaRef: "review_packet.schema.json",
		gateScript: "build_approval_manifest.py",
		gateScriptArgs: ["--kind", "review"],
		requiredFields: [
			"schema",
			"status",
			"model_name",
			"candidate_dir",
			"candidate_digest",
			"approval_manifest_sha256",
			"site_policy_sha256",
			"findings",
			"packet_digest",
		],
	},
	{
		id: "verify_decision",
		label: "Verify human decision",
		produces: "approval_decision.json",
		schemaRef: "approval_decision.schema.json",
		gateScript: "verify_human_decision.py",
		requiredFields: [
			"schema",
			"status",
			"decision",
			"review_packet",
			"review_packet_digest",
			"candidate_digest",
			"actor",
		],
	},
	{
		id: "publish",
		label: "Publish approved bundle",
		produces: "publication_result.json",
		schemaRef: "publication_result.schema.json",
		gateScript: "publish_approved_bundle.py",
		requiredFields: ["schema", "status", "destination", "candidate_digest", "eval_visible"],
	},
	{
		id: "verify_publication",
		label: "Verify published bundle",
		produces: "approval_ready.json",
		schemaRef: "approval_ready.schema.json",
		gateScript: "verify_published_bundle.py",
		requiredFields: ["schema", "status", "destination", "candidate_digest", "eval_visible", "deployment_binding"],
	},
];

export const AUDIT_UNITS = APPROVE_UNITS.slice(0, 8);
export const DECISION_UNITS = APPROVE_UNITS.slice(8);

export function unitsForMode(mode: ApproveMode): Unit[] {
	return mode === "approve" ? DECISION_UNITS : AUDIT_UNITS;
}

export function findUnit(unitId: string): Unit | undefined {
	return APPROVE_UNITS.find((unit) => unit.id === unitId);
}

export function nextUnit(mode: ApproveMode, unitId: string): Unit | undefined {
	const units = unitsForMode(mode);
	const index = units.findIndex((unit) => unit.id === unitId);
	return index < 0 || index === units.length - 1 ? undefined : units[index + 1];
}
