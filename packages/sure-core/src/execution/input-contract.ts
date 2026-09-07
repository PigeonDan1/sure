import { posix as posixPath } from "node:path";
import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type { JsonValue } from "../contracts/types.ts";

export const EXECUTION_INPUT_LOCATOR_KINDS = ["run_artifact", "run_path", "resolved_input_field"] as const;
export type ExecutionInputLocatorKind = (typeof EXECUTION_INPUT_LOCATOR_KINDS)[number];

export const EXECUTION_INPUT_SELECTION_MODES = ["exactly_one"] as const;
export type ExecutionInputSelectionMode = (typeof EXECUTION_INPUT_SELECTION_MODES)[number];

export interface ExecutionInputSpec {
	input_id: string;
	locator_kind: ExecutionInputLocatorKind;
	path: string;
	required: boolean;
}

export interface ExecutionInputSelector {
	selector_id: string;
	/** Exact string comparisons against the resolved input context. */
	match: Readonly<Record<string, string>>;
	inputs: readonly ExecutionInputSpec[];
}

/**
 * Declarative input boundary for an execution operation.
 *
 * The contract intentionally does not resolve paths or inspect the filesystem.
 * It only describes which immutable context selects a branch and which inputs
 * that branch must bind before an executor may start.  Host adapters can use
 * the same contract without importing a skill or a Pi hook.
 */
export interface ExecutionInputContract {
	schema: "sure.execution_input_contract.v1";
	context_artifact: string;
	selection: ExecutionInputSelectionMode;
	selectors: readonly ExecutionInputSelector[];
}

export interface ExecutionInputContractValidation {
	valid: boolean;
	errors: readonly string[];
}

export class ExecutionInputContractError extends Error {
	readonly code: "INVALID_CONTRACT" | "NO_MATCH" | "AMBIGUOUS";

	constructor(code: ExecutionInputContractError["code"], message: string) {
		super(message);
		this.name = "ExecutionInputContractError";
		this.code = code;
	}
}

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const FIELD = /^[A-Za-z][A-Za-z0-9_.-]{0,127}$/;

function object(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validRelativePath(value: unknown): value is string {
	if (typeof value !== "string" || value.length === 0 || value.startsWith("/") || value.includes("\\")) return false;
	const normalized = posixPath.normalize(value);
	return normalized === value && normalized !== "." && normalized !== ".." && !normalized.startsWith("../");
}

function selectorsOverlap(left: ExecutionInputSelector, right: ExecutionInputSelector): boolean {
	const fields = new Set([...Object.keys(left.match), ...Object.keys(right.match)]);
	for (const field of fields) {
		const leftValue = left.match[field];
		const rightValue = right.match[field];
		if (leftValue !== undefined && rightValue !== undefined && leftValue !== rightValue) return false;
	}
	return true;
}

/** Validate the declarative contract without resolving or touching any path. */
export function validateExecutionInputContract(value: unknown): ExecutionInputContractValidation {
	const errors: string[] = [];
	if (!object(value)) return { valid: false, errors: ["execution input contract must be an object"] };
	if (value.schema !== "sure.execution_input_contract.v1") errors.push("input contract schema is unsupported");
	if (!validRelativePath(value.context_artifact)) {
		errors.push("input contract context_artifact must be a normalized relative POSIX path");
	}
	if (value.selection !== "exactly_one") errors.push("input contract selection must be exactly_one");
	if (!Array.isArray(value.selectors) || value.selectors.length === 0) {
		errors.push("input contract must declare at least one selector");
		return { valid: false, errors };
	}
	const selectorIds = new Set<string>();
	const selectors: ExecutionInputSelector[] = [];
	for (const [index, rawSelector] of value.selectors.entries()) {
		const prefix = `input_contract.selectors[${index}]`;
		if (!object(rawSelector)) {
			errors.push(`${prefix} must be an object`);
			continue;
		}
		if (typeof rawSelector.selector_id !== "string" || !ID.test(rawSelector.selector_id)) {
			errors.push(`${prefix}.selector_id is invalid`);
		} else if (selectorIds.has(rawSelector.selector_id)) {
			errors.push(`${prefix}.selector_id is duplicated`);
		} else {
			selectorIds.add(rawSelector.selector_id);
		}
		if (!object(rawSelector.match) || Object.keys(rawSelector.match).length === 0) {
			errors.push(`${prefix}.match must be a non-empty object`);
		} else {
			for (const [field, expected] of Object.entries(rawSelector.match)) {
				if (!FIELD.test(field) || typeof expected !== "string" || expected.length === 0) {
					errors.push(`${prefix}.match.${field} must be a non-empty string field/value pair`);
				}
			}
		}
		if (!Array.isArray(rawSelector.inputs) || rawSelector.inputs.length === 0) {
			errors.push(`${prefix}.inputs must declare at least one input`);
			continue;
		}
		const inputIds = new Set<string>();
		const inputs: ExecutionInputSpec[] = [];
		for (const [inputIndex, rawInput] of rawSelector.inputs.entries()) {
			const inputPrefix = `${prefix}.inputs[${inputIndex}]`;
			if (!object(rawInput)) {
				errors.push(`${inputPrefix} must be an object`);
				continue;
			}
			if (typeof rawInput.input_id !== "string" || !ID.test(rawInput.input_id)) {
				errors.push(`${inputPrefix}.input_id is invalid`);
			} else if (inputIds.has(rawInput.input_id)) {
				errors.push(`${inputPrefix}.input_id is duplicated`);
			} else {
				inputIds.add(rawInput.input_id);
			}
			if (!EXECUTION_INPUT_LOCATOR_KINDS.includes(rawInput.locator_kind as ExecutionInputLocatorKind)) {
				errors.push(`${inputPrefix}.locator_kind is invalid`);
			}
			const pathValid =
				rawInput.locator_kind === "resolved_input_field"
					? typeof rawInput.path === "string" && FIELD.test(rawInput.path)
					: validRelativePath(rawInput.path);
			if (!pathValid) errors.push(`${inputPrefix}.path is invalid for its locator_kind`);
			if (typeof rawInput.required !== "boolean") errors.push(`${inputPrefix}.required must be boolean`);
			if (
				typeof rawInput.input_id === "string" &&
				ID.test(rawInput.input_id) &&
				EXECUTION_INPUT_LOCATOR_KINDS.includes(rawInput.locator_kind as ExecutionInputLocatorKind) &&
				pathValid &&
				typeof rawInput.required === "boolean"
			) {
				inputs.push({
					input_id: rawInput.input_id,
					locator_kind: rawInput.locator_kind as ExecutionInputLocatorKind,
					path: rawInput.path as string,
					required: rawInput.required,
				});
			}
		}
		if (
			typeof rawSelector.selector_id === "string" &&
			ID.test(rawSelector.selector_id) &&
			object(rawSelector.match) &&
			Object.keys(rawSelector.match).length > 0 &&
			Array.isArray(rawSelector.inputs) &&
			inputs.length === rawSelector.inputs.length
		) {
			selectors.push({
				selector_id: rawSelector.selector_id,
				match: Object.fromEntries(Object.entries(rawSelector.match).map(([key, entry]) => [key, String(entry)])),
				inputs,
			});
		}
	}
	for (let left = 0; left < selectors.length; left += 1) {
		for (let right = left + 1; right < selectors.length; right += 1) {
			if (selectorsOverlap(selectors[left], selectors[right])) {
				errors.push(
					`input_contract selectors ${selectors[left].selector_id} and ${selectors[right].selector_id} overlap`,
				);
			}
		}
	}
	return { valid: errors.length === 0, errors };
}

export function executionInputContractDigest(contract: ExecutionInputContract): string {
	return canonicalJsonDigest(contract as unknown as JsonValue);
}

/** Select exactly one input branch using the resolved, immutable context. */
export function selectExecutionInputSelector(
	contract: ExecutionInputContract,
	context: Readonly<Record<string, unknown>>,
): ExecutionInputSelector {
	const validation = validateExecutionInputContract(contract);
	if (!validation.valid) {
		throw new ExecutionInputContractError(
			"INVALID_CONTRACT",
			`input contract is invalid: ${validation.errors.join("; ")}`,
		);
	}
	const matches = contract.selectors.filter((selector) =>
		Object.entries(selector.match).every(([field, expected]) => context[field] === expected),
	);
	if (matches.length === 0) {
		throw new ExecutionInputContractError(
			"NO_MATCH",
			`input contract has no selector for context ${JSON.stringify(context)}`,
		);
	}
	if (matches.length > 1) {
		throw new ExecutionInputContractError(
			"AMBIGUOUS",
			`input contract has multiple selectors for context ${JSON.stringify(context)}: ${matches
				.map((selector) => selector.selector_id)
				.join(", ")}`,
		);
	}
	return matches[0];
}
