import type { JsonValue } from "../contracts/types.ts";
import type { WorkflowUnit } from "./types.ts";

export interface StructuralSchema {
	type?: string | readonly string[];
	required?: readonly string[];
	properties?: Readonly<Record<string, unknown>>;
	additionalProperties?: boolean;
}

export interface StructuralValidationResult {
	ok: boolean;
	missing?: boolean;
	repair?: string;
	reason?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function typeOf(value: unknown): string {
	if (value === null) return "null";
	if (Array.isArray(value)) return "array";
	return typeof value;
}

function schemaTypes(spec: Record<string, unknown>): string[] {
	if (typeof spec.type === "string") return [spec.type];
	if (Array.isArray(spec.type)) return spec.type.filter((entry): entry is string => typeof entry === "string");
	return [];
}

function allowedRuntimeTypes(declared: string): string[] {
	// Keep the legacy validator's integer behaviour: JSON numbers are represented
	// by one JS `number` type, so a schema integer accepts a number here.
	return declared === "number" ? ["number", "integer"] : declared === "integer" ? ["number"] : [declared];
}

function jsonEqual(left: unknown, right: JsonValue): boolean {
	if (left === right) return true;
	if (Array.isArray(left) && Array.isArray(right)) {
		return left.length === right.length && left.every((entry, index) => jsonEqual(entry, right[index]));
	}
	if (isRecord(left) && isRecord(right)) {
		const leftKeys = Object.keys(left);
		const rightKeys = Object.keys(right);
		return (
			leftKeys.length === rightKeys.length &&
			leftKeys.every((key) => Object.hasOwn(right, key) && jsonEqual(left[key], right[key]))
		);
	}
	return false;
}

function describeExpected(spec: unknown): string {
	if (!isRecord(spec)) return "(see schema)";
	const parts: string[] = [];
	const types = schemaTypes(spec);
	if (types.length > 0) parts.push(`type: ${types.length === 1 ? types[0] : JSON.stringify(types)}`);
	if (Array.isArray(spec.enum)) parts.push(`enum: ${JSON.stringify(spec.enum)}`);
	return parts.join("; ") || "(see schema)";
}

function describeShape(unit: WorkflowUnit, schema: StructuralSchema | undefined): string {
	const required = [...(unit.required_fields ?? []), ...(schema?.required ?? [])];
	return required.length > 0 ? `{ ${[...new Set(required)].join(", ")} }` : "(see SKILL.md)";
}

function fail(repair: string, reason: string, missing = false): StructuralValidationResult {
	return { ok: false, repair, reason, ...(missing ? { missing: true } : {}) };
}

/**
 * Validate only the deterministic, structural part of a unit contract. This
 * function has no filesystem, process, or host dependencies; semantic checks
 * remain explicit validator/executor inputs owned by the caller.
 */
export function validateStructuralArtifact(
	unit: WorkflowUnit,
	artifact: unknown,
	schema?: StructuralSchema,
): StructuralValidationResult {
	if (artifact === undefined) {
		return fail(
			`Produce ${unit.produces} under the run artifacts directory before advancing from unit "${unit.id}". Expected shape: ${describeShape(unit, schema)}.`,
			"artifact missing",
			true,
		);
	}
	if (!isRecord(artifact)) {
		return fail(
			`${unit.produces} must be a JSON object. Expected shape: ${describeShape(unit, schema)}.`,
			"artifact is not an object",
		);
	}

	const schemaRequired = schema?.required ?? [];
	const requiredFields = [...new Set([...(unit.required_fields ?? []), ...schemaRequired])];
	for (const field of requiredFields) {
		if (!(field in artifact)) {
			return fail(
				`${unit.produces} is missing required field "${field}" (unit "${unit.id}"). Expected ${field}: ${describeExpected(schema?.properties?.[field])}. Full expected shape: ${describeShape(unit, schema)}.`,
				`missing field ${field}`,
			);
		}
	}

	const typeViolations: string[] = [];
	for (const [field, typeSpec] of Object.entries(schema?.properties ?? {})) {
		if (!(field in artifact) || !isRecord(typeSpec)) continue;
		const declared = schemaTypes(typeSpec);
		const allowed = declared.flatMap(allowedRuntimeTypes);
		if (allowed.length > 0 && !allowed.includes(typeOf(artifact[field]))) {
			typeViolations.push(
				`Field "${field}" in ${unit.produces} must be ${declared.length === 1 ? declared[0] : JSON.stringify(declared)} (got ${typeOf(artifact[field])}). Expected ${field}: ${describeExpected(typeSpec)}.`,
			);
		}
	}
	if (typeViolations.length > 0) {
		return fail(`${typeViolations.join(" ")} Full expected shape: ${describeShape(unit, schema)}`, "type mismatch");
	}

	const enumViolations: string[] = [];
	for (const [field, allowed] of Object.entries(unit.allowed_values ?? {})) {
		if (field in artifact && !allowed.some((candidate) => jsonEqual(artifact[field], candidate))) {
			enumViolations.push(
				`Field "${field}" in ${unit.produces} must be one of ${JSON.stringify(allowed)} (got ${JSON.stringify(artifact[field])}).`,
			);
		}
	}
	for (const [field, typeSpec] of Object.entries(schema?.properties ?? {})) {
		if (!(field in artifact) || Object.hasOwn(unit.allowed_values ?? {}, field)) continue;
		if (!isRecord(typeSpec) || !Array.isArray(typeSpec.enum)) continue;
		const enumValues = typeSpec.enum.filter((value): value is JsonValue => isJsonValue(value));
		if (!enumValues.some((candidate) => jsonEqual(artifact[field], candidate))) {
			enumViolations.push(
				`Field "${field}" in ${unit.produces} must be one of ${JSON.stringify(typeSpec.enum)} (got ${JSON.stringify(artifact[field])}).`,
			);
		}
	}
	if (enumViolations.length > 0) return fail(enumViolations.join(" "), "value out of domain");

	for (const field of unit.forbidden_fields ?? []) {
		if (field in artifact) {
			return fail(
				`${unit.produces} must not contain field "${field}" - it belongs to a later unit. Do not merge units; produce only unit "${unit.id}"'s output.`,
				`forbidden field ${field} (step merge)`,
			);
		}
	}

	if (schema?.additionalProperties === false && schema.properties) {
		const declared = new Set(Object.keys(schema.properties));
		const extra = Object.keys(artifact).filter((key) => !declared.has(key) && key !== "$schema");
		if (extra.length > 0) {
			return fail(
				`${unit.produces} contains undeclared field(s) [${extra.join(", ")}]. The schema for unit "${unit.id}" sets additionalProperties:false.`,
				`additional properties [${extra.join(", ")}]`,
			);
		}
	}

	return { ok: true };
}

function isJsonValue(value: unknown): value is JsonValue {
	if (value === null || typeof value === "string" || typeof value === "boolean") return true;
	if (typeof value === "number") return Number.isFinite(value);
	if (Array.isArray(value)) return value.every(isJsonValue);
	if (isRecord(value)) return Object.values(value).every(isJsonValue);
	return false;
}

export { isJsonValue };
