import { type CoreOutcome, createOutcome, type ReasonCode } from "../workflow/outcome.ts";
import {
	CAPABILITY_CLASSES,
	CAPABILITY_EVIDENCE_SOURCES,
	CAPABILITY_STATUSES,
	type CapabilityEvidence,
	type CapabilityRequirement,
} from "./types.ts";

const CAPABILITY_ID = /^sure\.[a-z0-9][a-z0-9.-]*$/;
const DIGEST = /^(?:sha256:)?[0-9a-f]{64}$/i;

function object(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Validate the untrusted wire shape of one capability evidence record.
 *
 * TypeScript callers normally construct `CapabilityEvidence` values, but
 * receipts and external executor probes arrive as JSON.  Keeping this check
 * next to capability admission prevents an arbitrary `source` string from
 * being treated as authoritative merely because it is not `agent`.
 */
export function validateCapabilityEvidence(value: unknown, field = "capability_evidence"): string[] {
	const errors: string[] = [];
	if (!object(value)) return [`${field} must be an object`];
	if (typeof value.capability_id !== "string" || !CAPABILITY_ID.test(value.capability_id)) {
		errors.push(`${field}.capability_id is invalid`);
	}
	if (!CAPABILITY_CLASSES.includes(value.capability_class as (typeof CAPABILITY_CLASSES)[number])) {
		errors.push(`${field}.capability_class is invalid`);
	}
	if (!CAPABILITY_STATUSES.includes(value.status as (typeof CAPABILITY_STATUSES)[number])) {
		errors.push(`${field}.status is invalid`);
	}
	if (!CAPABILITY_EVIDENCE_SOURCES.includes(value.source as (typeof CAPABILITY_EVIDENCE_SOURCES)[number])) {
		errors.push(`${field}.source is invalid`);
	}
	if (
		typeof value.capability_class === "string" &&
		value.capability_class === "execution_capability" &&
		value.source === "agent"
	) {
		errors.push(`${field}.source agent cannot satisfy an execution capability`);
	}
	if (typeof value.observed_at !== "string" || value.observed_at.trim() === "") {
		errors.push(`${field}.observed_at must be a non-empty string`);
	}
	if (
		value.evidence_digest !== undefined &&
		(typeof value.evidence_digest !== "string" || !DIGEST.test(value.evidence_digest))
	) {
		errors.push(`${field}.evidence_digest must be a SHA-256 digest when present`);
	}
	if (
		value.status === "AVAILABLE" &&
		(typeof value.evidence_digest !== "string" || !DIGEST.test(value.evidence_digest))
	) {
		errors.push(`${field}.evidence_digest is required for AVAILABLE evidence`);
	}
	if (value.details !== undefined && !object(value.details))
		errors.push(`${field}.details must be an object when present`);
	return errors;
}

/** Validate a capability evidence array received from a host or receipt. */
export function validateCapabilityEvidenceList(value: unknown, field = "capability_evidence"): string[] {
	if (!Array.isArray(value)) return [`${field} must be an array`];
	return value.flatMap((entry, index) => validateCapabilityEvidence(entry, `${field}[${index}]`));
}

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
				continue;
			}
			if (validateCapabilityEvidence(candidate).length > 0) {
				invalidEvidence.push(`${candidate.capability_id}:malformed`);
			}
		}
		const authoritative = matchingClass.filter(
			(entry) =>
				validateCapabilityEvidence(entry).length === 0 &&
				(requirement.capability_class !== "execution_capability" || entry.source !== "agent"),
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
