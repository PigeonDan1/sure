import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import type { GateResult, Unit } from "./checkpoints.ts";

// Product (produces) validation: three tiers, mirroring the SURE-EVAL unit
// contract (Required Output fields + Allowed value domain + Must Not Do).
//
// The Unit declares required fields, allowed enums per field, and forbidden
// fields (the last prevents step-merging: a unit's produces must not carry
// fields that belong to a later unit). When the unit declares a schemaRef, the
// schema is loaded and becomes the source of truth for: required fields,
// property types, schema-level `enum`s, and `additionalProperties: false`.
//
// Repair messages are schema-aware: when a field is wrong, the repair quotes
// the expected type / enum / shape inline so the agent can self-repair without
// re-reading SKILL.md. This is the quality bar — repair text must contain the
// correct schema.

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readJson(path: string): unknown {
	return JSON.parse(readFileSync(path, "utf-8"));
}

function schemaPath(ctx: SureHookContext, schemaRef: string): string {
	return join(ctx.packageDir, "schemas", schemaRef);
}

interface LoadedSchema {
	required?: string[];
	properties?: Record<string, unknown>;
	additionalProperties?: boolean;
	$schema?: string;
	$id?: string;
	title?: string;
	description?: string;
	type?: string;
}

function loadSchema(ctx: SureHookContext, schemaRef?: string): LoadedSchema | undefined {
	if (!schemaRef) {
		return undefined;
	}
	const path = schemaPath(ctx, schemaRef);
	if (!existsSync(path)) {
		return undefined;
	}
	try {
		const raw = readJson(path);
		return isRecord(raw) ? (raw as unknown as LoadedSchema) : undefined;
	} catch {
		return undefined;
	}
}

function typeOf(value: unknown): string {
	if (Array.isArray(value)) {
		return "array";
	}
	return typeof value;
}

const SCHEMA_TYPE_MAP: Record<string, string[]> = {
	string: ["string"],
	number: ["number", "integer"],
	integer: ["number"],
	boolean: ["boolean"],
	object: ["object"],
	array: ["array"],
};

// Render the expected shape of a property for the repair message. Pulls the
// type, enum, required sub-fields, and item shape from the schema spec so the
// agent gets an inline, copy-pastable contract fragment.
function describeExpected(spec: unknown): string {
	const s = isRecord(spec) ? spec : undefined;
	if (!s) {
		return "(see schema)";
	}
	const parts: string[] = [];
	if (typeof s.type === "string") {
		parts.push(`type: ${s.type}`);
	}
	if (Array.isArray(s.enum)) {
		parts.push(`enum: ${JSON.stringify(s.enum)}`);
	}
	if (s.type === "object" && isRecord(s.properties)) {
		const req = Array.isArray(s.required) ? (s.required as string[]) : [];
		const fields = Object.keys(s.properties);
		if (fields.length > 0) {
			const marker = (f: string) => (req.includes(f) ? `${f}*` : f);
			parts.push(`object fields: [${fields.map(marker).join(", ")}]`);
		}
	}
	if (s.type === "array" && isRecord(s.items)) {
		parts.push(`items: {${describeExpected(s.items)}}`);
	}
	return parts.join("; ") || "(see schema)";
}

// One-line summary of the expected schema: the top-level fields with their
// types/enums, so a repair message carries the contract inline.
function describeSchemaSummary(ctx: SureHookContext, unit: Unit): string {
	const schema = loadSchema(ctx, unit.schemaRef);
	if (!schema || !schema.properties) {
		const req = unit.requiredFields ?? schema?.required ?? [];
		return req.length ? `{ ${req.join(", ")} }` : "(see SKILL.md)";
	}
	const required = Array.isArray(schema.required) ? schema.required : [];
	const entries = Object.entries(schema.properties).map(([field, spec]) => {
		const s = isRecord(spec) ? spec : undefined;
		let desc = field;
		if (s && typeof s.type === "string") {
			desc += `(${s.type}`;
			if (Array.isArray(s.enum)) {
				desc += `,enum:${JSON.stringify(s.enum)}`;
			}
			desc += ")";
		}
		return required.includes(field) ? `${desc}*` : desc;
	});
	return `{ ${entries.join(", ")} }`;
}

// Validate a single produces artifact against the unit contract.
//   - tier 1 (location): caller guarantees path; here we require parseable JSON.
//   - tier 2 (format): required fields present + typed per schema.
//   - tier 3 (value domain): allowed enums (unit.allowedValues + schema enum)
//     + forbidden fields (anti step-merge) + additionalProperties:false.
export function validateProduces(ctx: SureHookContext, unit: Unit, artifact: unknown): GateResult {
	if (artifact === undefined) {
		// Artifact not yet produced — stay on this unit (do not advance).
		return {
			ok: false,
			repair: `Produce ${unit.produces} under the run artifacts directory before advancing from unit "${unit.id}". Expected shape: ${describeSchemaSummary(ctx, unit)}.`,
			reason: "artifact missing",
			missing: true,
		};
	}
	const record = isRecord(artifact) ? artifact : undefined;
	if (!record) {
		return {
			ok: false,
			repair: `${unit.produces} must be a JSON object. Expected shape: ${describeSchemaSummary(ctx, unit)}.`,
			reason: "artifact is not an object",
		};
	}

	const schema = loadSchema(ctx, unit.schemaRef);

	// Tier 2: format — required fields. UNION of the unit's requiredFields and
	// the schema's `required` (both must be present). The schema is the declared
	// contract; the unit may add fields, but must never drop a schema-required
	// field. (Previously this was `unit.requiredFields ?? schema?.required`, which
	// let a unit with fewer fields silently override the schema — e.g. an
	// artifact_manifest missing its `artifacts` field passed save_artifacts.)
	const requiredFields = Array.from(new Set([...(unit.requiredFields ?? []), ...(schema?.required ?? [])]));
	for (const field of requiredFields) {
		if (!(field in record)) {
			const spec = schema?.properties?.[field];
			return {
				ok: false,
				repair: `${unit.produces} is missing required field "${field}" (unit "${unit.id}"). Expected ${field}: ${describeExpected(spec)}. Full expected shape: ${describeSchemaSummary(ctx, unit)}.`,
				reason: `missing field ${field}`,
			};
		}
	}

	// Tier 2: format — property types per schema.
	const typeViolations: string[] = [];
	if (schema?.properties) {
		for (const [field, typeSpec] of Object.entries(schema.properties)) {
			if (!(field in record)) {
				continue;
			}
			const spec = isRecord(typeSpec) ? typeSpec : undefined;
			const declaredType = spec && typeof spec.type === "string" ? spec.type : undefined;
			if (declaredType) {
				const allowed = SCHEMA_TYPE_MAP[declaredType] ?? [declaredType];
				if (!allowed.includes(typeOf(record[field]))) {
					typeViolations.push(
						`Field "${field}" in ${unit.produces} must be ${declaredType} (got ${typeOf(record[field])}). Expected ${field}: ${describeExpected(spec)}.`,
					);
				}
			}
		}
	}
	if (typeViolations.length > 0) {
		return {
			ok: false,
			repair: `${typeViolations.join(" ")} Full expected shape: ${describeSchemaSummary(ctx, unit)}`,
			reason: `type mismatch ${typeViolations.length} field(s)`,
		};
	}

	// Tier 3: value domain — allowed enums. Merge unit.allowedValues (the unit
	// contract) with schema-level `enum`s (the JSON Schema). When both constrain
	// the same field, the unit contract wins; otherwise the schema enum is the
	// floor. This enforces enums SKILL.md declares (source, deployment_type, …).
	const enumViolations: string[] = [];
	if (unit.allowedValues) {
		for (const [field, allowed] of Object.entries(unit.allowedValues)) {
			if (!(field in record)) {
				continue;
			}
			const value = record[field];
			if (!allowed.includes(value as never)) {
				enumViolations.push(
					`Field "${field}" in ${unit.produces} must be one of ${JSON.stringify(allowed)} (got ${JSON.stringify(value)}).`,
				);
			}
		}
	}
	if (schema?.properties) {
		for (const [field, typeSpec] of Object.entries(schema.properties)) {
			// Skip fields the unit contract already checked, and absent fields.
			if (!(field in record) || (unit.allowedValues && field in unit.allowedValues)) {
				continue;
			}
			const spec = isRecord(typeSpec) ? typeSpec : undefined;
			const enumValues = spec && Array.isArray(spec.enum) ? (spec.enum as unknown[]) : undefined;
			if (enumValues && !enumValues.includes(record[field] as never)) {
				enumViolations.push(
					`Field "${field}" in ${unit.produces} must be one of ${JSON.stringify(enumValues)} (got ${JSON.stringify(record[field])}).`,
				);
			}
		}
	}
	if (enumViolations.length > 0) {
		return {
			ok: false,
			repair: enumViolations.join(" "),
			reason: "value out of domain",
		};
	}

	// Tier 3: forbidden fields — anti step-merge. A unit's produces must not
	// carry fields that belong to a later unit (e.g. execution_surface.json
	// must not contain report_persisted, which is the run_report unit's field).
	if (unit.forbiddenFields) {
		for (const field of unit.forbiddenFields) {
			if (field in record) {
				return {
					ok: false,
					repair: `${unit.produces} must not contain field "${field}" — it belongs to a later unit. Do not merge units; produce only unit "${unit.id}"'s output. Allowed top-level fields: ${describeSchemaSummary(ctx, unit)}.`,
					reason: `forbidden field ${field} (step merge)`,
				};
			}
		}
	}

	// Tier 3: additionalProperties:false — the schema forbids undeclared keys.
	// Enforce it so an artifact cannot smuggle in a later unit's field under a
	// name the forbidden list does not anticipate.
	if (schema?.additionalProperties === false && schema.properties) {
		const declared = new Set(Object.keys(schema.properties));
		const extra = Object.keys(record).filter((k) => !declared.has(k) && k !== "$schema");
		if (extra.length > 0) {
			return {
				ok: false,
				repair: `${unit.produces} contains undeclared field(s) [${extra.join(", ")}]. The schema for unit "${unit.id}" sets additionalProperties:false — emit only the declared fields: [${[...declared].join(", ")}].`,
				reason: `additional properties [${extra.join(", ")}]`,
			};
		}
	}

	return { ok: true };
}
