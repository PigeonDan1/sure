import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type { JsonValue } from "../contracts/types.ts";

export const MEMORY_CONTRACT_SCHEMA = "sure.memory.contract.v1" as const;
export const MEMORY_ENTRY_KINDS = ["bad_case", "fact"] as const;
export type MemoryEntryKind = (typeof MEMORY_ENTRY_KINDS)[number];

export interface ParsedMemoryUri {
	skill: string;
	kind: MemoryEntryKind;
	slug: string;
}

export interface MemoryContractValidation {
	valid: boolean;
	errors: string[];
	digest?: string;
	contract?: Record<string, unknown>;
}

const SAFE_SEGMENT = /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/;
const URI_PREFIX = "memory://";
const OPERATIONS = new Set([
	"read_index",
	"append_usage",
	"write_run_digest",
	"check_extraction",
	"publish_reference",
	"promote_reference",
	"resolve_reference",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeSegment(value: string, label: string): string {
	if (!SAFE_SEGMENT.test(value) || value === "." || value === "..") {
		throw new Error(`${label} must be a single safe path segment: ${value}`);
	}
	return value;
}

function validateKind(skill: string, kind: MemoryEntryKind): void {
	safeSegment(skill, "memory skill");
	if (kind === "fact" && skill !== "_shared") throw new Error("memory facts must use the _shared skill");
	if (kind === "bad_case" && skill === "_shared")
		throw new Error("memory bad cases must use a skill-specific namespace");
}

function requiredString(record: Record<string, unknown>, key: string, errors: string[]): string | undefined {
	if (typeof record[key] !== "string" || record[key].trim() === "") {
		errors.push(`${key} must be a non-empty string`);
		return undefined;
	}
	return record[key] as string;
}

/**
 * Validate the data-only memory contract shared by Pi and portable projections.
 * This intentionally does not inspect a repository or execute a memory backend.
 */
export function validateMemoryContract(value: unknown): MemoryContractValidation {
	const errors: string[] = [];
	if (!isRecord(value)) return { valid: false, errors: ["memory contract must be an object"] };
	if (value.schema !== MEMORY_CONTRACT_SCHEMA) errors.push(`schema must be ${MEMORY_CONTRACT_SCHEMA}`);
	if (value.contract_version !== 1) errors.push("contract_version must be 1");
	if (value.advisory !== true) errors.push("advisory must be true");

	const identity = value.identity;
	if (!isRecord(identity)) {
		errors.push("identity must be an object");
	} else {
		if (identity.uri_prefix !== URI_PREFIX) errors.push("identity.uri_prefix must be memory://");
		if (identity.entry_id_pattern !== "<skill>/<slug>") errors.push("identity.entry_id_pattern is invalid");
		if (
			!Array.isArray(identity.kinds) ||
			identity.kinds.length !== MEMORY_ENTRY_KINDS.length ||
			identity.kinds.some((kind, index) => kind !== MEMORY_ENTRY_KINDS[index])
		)
			errors.push("identity.kinds must be [bad_case, fact]");
		if (identity.fact_namespace !== "_shared") errors.push("identity.fact_namespace must be _shared");
		if (identity.bad_case_namespace !== "skill_specific")
			errors.push("identity.bad_case_namespace must be skill_specific");
	}

	const roots = value.roots;
	if (!Array.isArray(roots) || roots.length !== 3) {
		errors.push("roots must contain exactly memory_root, canonical_root, and legacy_skills_root");
	} else {
		const expected = new Map([
			["memory_root", ["state", "sure/memory", true]],
			["canonical_root", ["canonical_references", "sure/canonical", false]],
			["legacy_skills_root", ["compatibility_reference_alias", "sure/skills", true]],
		]);
		for (const root of roots) {
			if (!isRecord(root)) {
				errors.push("each memory root must be an object");
				continue;
			}
			const id = requiredString(root, "id", errors);
			if (!id) continue;
			const rule = expected.get(id);
			if (!rule) {
				errors.push(`unknown memory root ${id}`);
				continue;
			}
			if (root.role !== rule[0]) errors.push(`${id}.role is invalid`);
			if (root.default_relative !== rule[1]) errors.push(`${id}.default_relative is invalid`);
			if (root.mutable !== rule[2]) errors.push(`${id}.mutable is invalid`);
			if (root.binding !== "explicit_or_default") errors.push(`${id}.binding is invalid`);
		}
		if (new Set(roots.filter(isRecord).map((root) => root.id)).size !== 3)
			errors.push("memory root ids must be unique");
	}

	if (
		!Array.isArray(value.operations) ||
		value.operations.length !== OPERATIONS.size ||
		value.operations.some((operation) => typeof operation !== "string" || !OPERATIONS.has(operation))
	)
		errors.push("operations must contain the registered memory operations exactly once");
	else if (new Set(value.operations).size !== OPERATIONS.size) errors.push("memory operations must be unique");

	const lifecycle = value.lifecycle;
	if (!isRecord(lifecycle)) {
		errors.push("lifecycle must be an object");
	} else {
		if (lifecycle.checkpoint_field !== "memory") errors.push("lifecycle.checkpoint_field must be memory");
		if (lifecycle.failure_effect !== "diagnostic_only")
			errors.push("lifecycle.failure_effect must be diagnostic_only");
		if (lifecycle.business_verdict_authority !== "workflow_and_validators")
			errors.push("lifecycle.business_verdict_authority is invalid");
		if (lifecycle.approve_consumes_memory !== false) errors.push("lifecycle.approve_consumes_memory must be false");
		if (lifecycle.confirmed_knowledge_must_be_preserved !== true)
			errors.push("lifecycle.confirmed_knowledge_must_be_preserved must be true");
	}

	const profiles = value.host_profiles;
	if (!isRecord(profiles)) {
		errors.push("host_profiles must be an object");
	} else {
		const portable = profiles.portable;
		const pi = profiles.pi;
		if (!isRecord(portable)) errors.push("host_profiles.portable must be an object");
		else {
			if (portable.enforcement !== "cooperative") errors.push("portable memory enforcement must be cooperative");
			if (portable.control_plane !== "surectl_or_host_adapter")
				errors.push("portable memory control plane is invalid");
			if (portable.trusted_execution !== false) errors.push("portable memory cannot claim trusted execution");
		}
		if (!isRecord(pi)) errors.push("host_profiles.pi must be an object");
		else {
			if (pi.enforcement !== "mandatory_lifecycle_hooks") errors.push("Pi memory enforcement is invalid");
			if (pi.control_plane !== "pi_hook_adapter") errors.push("Pi memory control plane is invalid");
		}
	}

	const projection = value.skill;
	if (projection !== undefined) {
		if (!isRecord(projection)) errors.push("skill projection must be an object");
		else {
			const skillId = requiredString(projection, "skill_id", errors);
			if (skillId && !SAFE_SEGMENT.test(skillId)) errors.push("skill.skill_id is not a safe identifier");
			if (typeof projection.enabled !== "boolean") errors.push("skill.enabled must be boolean");
			if (typeof projection.participates_in_checkpoint !== "boolean")
				errors.push("skill.participates_in_checkpoint must be boolean");
			if (
				skillId === "sure_approve" &&
				(projection.enabled !== false || projection.participates_in_checkpoint !== false)
			)
				errors.push("sure_approve memory must be disabled");
			if (
				skillId !== undefined &&
				skillId !== "sure_approve" &&
				(projection.enabled !== true || projection.participates_in_checkpoint !== true)
			)
				errors.push("enabled memory skills must participate in the checkpoint");
		}
	}

	let digest: string | undefined;
	try {
		digest = canonicalJsonDigest(value as JsonValue);
	} catch (error) {
		errors.push(`memory contract is not canonical JSON: ${error instanceof Error ? error.message : String(error)}`);
	}
	return { valid: errors.length === 0, errors, ...(digest === undefined ? {} : { digest }), contract: value };
}

export function memoryUri(skill: string, kind: MemoryEntryKind, slug: string): string {
	validateKind(skill, kind);
	safeSegment(slug, "memory slug");
	return `${URI_PREFIX}${skill}/${kind}/${slug}`;
}

export function parseMemoryUri(uri: string): ParsedMemoryUri {
	if (!uri.startsWith(URI_PREFIX)) throw new Error(`memory URI must start with ${URI_PREFIX}`);
	const parts = uri.slice(URI_PREFIX.length).split("/");
	if (parts.length !== 3) throw new Error(`memory URI must have skill/kind/slug: ${uri}`);
	const skill = safeSegment(parts[0] ?? "", "memory skill");
	const kind = parts[1] as MemoryEntryKind;
	if (!MEMORY_ENTRY_KINDS.includes(kind)) throw new Error(`unknown memory URI kind: ${parts[1]}`);
	validateKind(skill, kind);
	const slug = safeSegment(parts[2] ?? "", "memory slug");
	return { skill, kind, slug };
}
