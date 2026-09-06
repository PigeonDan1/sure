import { type CoreOutcome, createOutcome, type ReasonCode } from "../workflow/outcome.ts";
import type { CapabilityEvidence, CapabilityRequirement } from "./types.ts";

export const KNOWN_CAPABILITY_IDS = [
	"sure.core",
	"sure.execution.docker",
	"sure.execution.docker-optional",
	"sure.execution.evaluation-runtime",
	"sure.execution.harness-python",
	"sure.execution.human-approval",
	"sure.execution.local-python",
	"sure.execution.model-runtime",
	"sure.execution.remote",
	"sure.execution.site-publication",
	"sure.execution.source-runtime",
	"sure.execution.trusted",
	"sure.execution.uv",
	"sure.execution.vc",
	"sure.execution.gpu",
	"sure.engine.evaluation",
	"sure.network.egress",
	"sure.resource.gpu",
	"sure.resource.queue",
] as const;

export interface CapabilityEvaluation {
	admitted: boolean;
	unknown: string[];
	missing: string[];
	denied: string[];
	invalid_evidence: string[];
	blocking_outcome?: CoreOutcome;
}

function uniqueSorted(values: string[]): string[] {
	return [...new Set(values)].sort();
}

function blockingOutcome(reasonCode: ReasonCode, details: string[]): CoreOutcome {
	return createOutcome({
		validatorVerdict: "NOT_EXECUTED",
		workflowDisposition: "BLOCK",
		reasonCode,
		diagnostics: details.map((message) => ({ code: reasonCode, message })),
	});
}

export function evaluateCapabilityRequirements(
	requirements: readonly CapabilityRequirement[],
	evidence: readonly CapabilityEvidence[],
	knownCapabilityIds: ReadonlySet<string> = new Set(KNOWN_CAPABILITY_IDS),
): CapabilityEvaluation {
	const unknown: string[] = [];
	const missing: string[] = [];
	const denied: string[] = [];
	const invalidEvidence: string[] = [];

	for (const requirement of requirements) {
		if (!requirement.required) continue;
		if (!knownCapabilityIds.has(requirement.capability_id)) {
			unknown.push(requirement.capability_id);
			continue;
		}

		const candidates = evidence.filter((entry) => entry.capability_id === requirement.capability_id);
		const matchingClass = candidates.filter((entry) => entry.capability_class === requirement.capability_class);
		for (const candidate of candidates) {
			if (candidate.capability_class !== requirement.capability_class) {
				invalidEvidence.push(`${candidate.capability_id}:${candidate.capability_class}`);
			}
		}
		const authoritative = matchingClass.filter(
			(entry) => requirement.capability_class !== "execution_capability" || entry.source !== "agent",
		);
		if (matchingClass.length > authoritative.length) {
			invalidEvidence.push(`${requirement.capability_id}:agent-source-for-execution`);
		}
		if (authoritative.some((entry) => entry.status === "DENIED")) {
			denied.push(requirement.capability_id);
			continue;
		}
		if (!authoritative.some((entry) => entry.status === "AVAILABLE")) {
			missing.push(requirement.capability_id);
		}
	}

	const normalized = {
		unknown: uniqueSorted(unknown),
		missing: uniqueSorted(missing),
		denied: uniqueSorted(denied),
		invalid_evidence: uniqueSorted(invalidEvidence),
	};
	if (normalized.unknown.length > 0) {
		return {
			admitted: false,
			...normalized,
			blocking_outcome: blockingOutcome(
				"UNKNOWN_CAPABILITY",
				normalized.unknown.map((id) => `Capability ${id} is not registered`),
			),
		};
	}
	if (normalized.denied.length > 0) {
		return {
			admitted: false,
			...normalized,
			blocking_outcome: blockingOutcome(
				"POLICY_DENIED",
				normalized.denied.map((id) => `Capability ${id} is denied by authoritative evidence`),
			),
		};
	}
	if (normalized.missing.length > 0 || normalized.invalid_evidence.length > 0) {
		return {
			admitted: false,
			...normalized,
			blocking_outcome: blockingOutcome("CAPABILITY_MISSING", [
				...normalized.missing.map((id) => `Required capability ${id} is unavailable`),
				...normalized.invalid_evidence.map((id) => `Capability evidence ${id} cannot satisfy the requirement`),
			]),
		};
	}
	return { admitted: true, ...normalized };
}
