import type { TSchema } from "typebox";
import { Check, Errors } from "typebox/value";

export type JsonSchema = Record<string, unknown>;

export interface SchemaValidationIssue {
	keyword: string;
	schema_path: string;
	instance_path: string;
	message: string;
}

export type SchemaValidationResult<T> =
	| { ok: true; value: T; issues: [] }
	| { ok: false; issues: SchemaValidationIssue[] };

function issueFromError(error: {
	keyword: string;
	schemaPath: string;
	instancePath: string;
	message: string;
}): SchemaValidationIssue {
	return {
		keyword: error.keyword,
		schema_path: error.schemaPath,
		instance_path: error.instancePath,
		message: error.message,
	};
}

export function validateJsonSchema<T>(schema: JsonSchema, value: unknown): SchemaValidationResult<T> {
	try {
		const typedSchema = schema as TSchema;
		if (Check(typedSchema, value)) return { ok: true, value: value as T, issues: [] };
		return { ok: false, issues: [...Errors(typedSchema, value)].map(issueFromError) };
	} catch (error) {
		return {
			ok: false,
			issues: [
				{
					keyword: "schema",
					schema_path: "#",
					instance_path: "",
					message: error instanceof Error ? error.message : String(error),
				},
			],
		};
	}
}

export function parseAndValidateJson<T>(schema: JsonSchema, source: string): SchemaValidationResult<T> {
	try {
		return validateJsonSchema<T>(schema, JSON.parse(source));
	} catch (error) {
		return {
			ok: false,
			issues: [
				{
					keyword: "parse",
					schema_path: "#",
					instance_path: "",
					message: error instanceof Error ? error.message : String(error),
				},
			],
		};
	}
}
