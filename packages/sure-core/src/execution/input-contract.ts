import { posix as posixPath } from "node:path";
import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import {
	type ArtifactRef,
	EXECUTION_INPUT_LOCATOR_KINDS,
	type ExecutionInputBinding,
	type ExecutionInputBindingEntry,
	type ExecutionInputLocatorKind,
	type JsonValue,
} from "../contracts/types.ts";

export type { ExecutionInputLocatorKind } from "../contracts/types.ts";
export { EXECUTION_INPUT_LOCATOR_KINDS } from "../contracts/types.ts";

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
	readonly code: "INVALID_CONTRACT" | "NO_MATCH" | "AMBIGUOUS" | "INVALID_CONTEXT" | "MISSING_INPUT";

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

export interface ExecutionInputBindingResolver {
	/** Resolve a path relative to the run artifact root. */
	resolveRunArtifact(path: string): ArtifactRef | undefined;
	/** Resolve a path relative to the run/workspace root. */
	resolveRunPath(path: string): ArtifactRef | undefined;
	/** Resolve an absolute or site-policy-bound path carried by the context field. */
	resolveResolvedInputField(path: string, value: string): ArtifactRef | undefined;
}

export interface BindExecutionInputsOptions {
	contract: ExecutionInputContract;
	context: Readonly<Record<string, unknown>>;
	context_digest: string;
	resolver: ExecutionInputBindingResolver;
}

function fieldValue(context: Readonly<Record<string, unknown>>, path: string): unknown {
	let current: unknown = context;
	for (const part of path.split(".")) {
		if (!object(current)) return undefined;
		current = current[part];
	}
	return current;
}

function bindingDigest(value: Omit<ExecutionInputBinding, "binding_digest">): string {
	return canonicalJsonDigest(value as unknown as JsonValue);
}

/**
 * Resolve and bind every input selected by a contract.  This function is
 * deliberately filesystem-agnostic: the host supplies resolvers that enforce
 * its run/site boundary, while Core owns selection, required-input semantics,
 * ordering, and the bytes that are hashed into the binding.
 */
export function bindExecutionInputs(options: BindExecutionInputsOptions): ExecutionInputBinding {
	const validation = validateExecutionInputContract(options.contract);
	if (!validation.valid) {
		throw new ExecutionInputContractError(
			"INVALID_CONTRACT",
			`input contract is invalid: ${validation.errors.join("; ")}`,
		);
	}
	if (!object(options.context)) {
		throw new ExecutionInputContractError("INVALID_CONTEXT", "input context must be a JSON object");
	}
	if (typeof options.context_digest !== "string" || !/^(?:sha256:)?[0-9a-f]{64}$/i.test(options.context_digest)) {
		throw new ExecutionInputContractError("INVALID_CONTEXT", "input context digest must be a SHA-256 digest");
	}
	const selector = selectExecutionInputSelector(options.contract, options.context);
	const inputs: ExecutionInputBindingEntry[] = [];
	for (const spec of selector.inputs) {
		let artifact: ArtifactRef | undefined;
		if (spec.locator_kind === "run_artifact") artifact = options.resolver.resolveRunArtifact(spec.path);
		else if (spec.locator_kind === "run_path") artifact = options.resolver.resolveRunPath(spec.path);
		else {
			const value = fieldValue(options.context, spec.path);
			if (typeof value === "string" && value.trim() !== "") {
				artifact = options.resolver.resolveResolvedInputField(spec.path, value);
			}
		}
		if (artifact === undefined) {
			if (spec.required) {
				throw new ExecutionInputContractError(
					"MISSING_INPUT",
					`required execution input ${spec.input_id} could not be resolved (${spec.locator_kind}:${spec.path})`,
				);
			}
			continue;
		}
		// The contract's logical input id is the stable wire id.  Hosts may use
		// a different local artifact label, but must not let that label vary the
		// cross-host request shape.
		artifact = { ...artifact, artifact_id: spec.input_id };
		inputs.push({
			input_id: spec.input_id,
			locator_kind: spec.locator_kind,
			path: spec.path,
			artifact,
		});
	}
	const unsigned = {
		schema: "sure.execution_input_binding.v1" as const,
		contract_digest: executionInputContractDigest(options.contract),
		selector_id: selector.selector_id,
		context_artifact: options.contract.context_artifact,
		context_digest: options.context_digest,
		inputs,
	};
	return { ...unsigned, binding_digest: bindingDigest(unsigned) };
}

/** Recompute the digest of a previously serialized binding without resolving paths. */
export function executionInputBindingDigest(binding: ExecutionInputBinding): string {
	const { binding_digest: _ignored, ...unsigned } = binding;
	return bindingDigest(unsigned);
}

export interface ExecutionInputBindingValidation {
	valid: boolean;
	errors: readonly string[];
}

function validDigest(value: unknown): value is string {
	return typeof value === "string" && /^(?:sha256:)?[0-9a-f]{64}$/i.test(value);
}

function validId(value: unknown): value is string {
	return typeof value === "string" && ID.test(value);
}

/** Validate a serialized binding and, when supplied, its request input list. */
export function validateExecutionInputBinding(
	value: unknown,
	requestInputs?: readonly ArtifactRef[],
): ExecutionInputBindingValidation {
	const errors: string[] = [];
	if (!object(value)) return { valid: false, errors: ["execution input binding must be an object"] };
	if (value.schema !== "sure.execution_input_binding.v1") errors.push("input binding schema is unsupported");
	if (!validDigest(value.contract_digest)) errors.push("input binding contract_digest must be a SHA-256 digest");
	if (!validId(value.selector_id)) errors.push("input binding selector_id is invalid");
	if (!validRelativePath(value.context_artifact)) errors.push("input binding context_artifact is invalid");
	if (!validDigest(value.context_digest)) errors.push("input binding context_digest must be a SHA-256 digest");
	if (!Array.isArray(value.inputs)) errors.push("input binding inputs must be an array");
	else {
		if (value.inputs.length === 0) errors.push("input binding inputs must not be empty");
		const ids = new Set<string>();
		const artifacts: ArtifactRef[] = [];
		for (const [index, raw] of value.inputs.entries()) {
			const prefix = `input_binding.inputs[${index}]`;
			if (!object(raw)) {
				errors.push(`${prefix} must be an object`);
				continue;
			}
			if (!validId(raw.input_id)) errors.push(`${prefix}.input_id is invalid`);
			else if (ids.has(raw.input_id)) errors.push(`${prefix}.input_id is duplicated`);
			else ids.add(raw.input_id);
			if (!EXECUTION_INPUT_LOCATOR_KINDS.includes(raw.locator_kind as ExecutionInputLocatorKind)) {
				errors.push(`${prefix}.locator_kind is invalid`);
			} else if (
				raw.locator_kind === "resolved_input_field"
					? typeof raw.path !== "string" || !FIELD.test(raw.path)
					: !validRelativePath(raw.path)
			) {
				errors.push(`${prefix}.path is invalid for its locator_kind`);
			}
			const artifact = raw.artifact;
			if (!object(artifact)) errors.push(`${prefix}.artifact must be an object`);
			else {
				if (!validId(artifact.artifact_id)) errors.push(`${prefix}.artifact.artifact_id is invalid`);
				if (!validDigest(artifact.sha256)) errors.push(`${prefix}.artifact.sha256 is invalid`);
				if (typeof artifact.path !== "string" || typeof artifact.resolved_path !== "string")
					errors.push(`${prefix}.artifact paths are required`);
				else if (!artifact.path.startsWith("/") || !artifact.resolved_path.startsWith("/"))
					errors.push(`${prefix}.artifact paths must be absolute`);
				artifacts.push(artifact as unknown as ArtifactRef);
			}
		}
		if (requestInputs !== undefined) {
			if (artifacts.length !== requestInputs.length) {
				errors.push("input binding inputs must correspond one-to-one with request.inputs");
			} else {
				for (let index = 0; index < artifacts.length; index += 1) {
					if (
						canonicalJsonDigest(artifacts[index] as unknown as JsonValue) !==
						canonicalJsonDigest(requestInputs[index] as unknown as JsonValue)
					) {
						errors.push(`input binding artifact ${index} does not match request.inputs[${index}]`);
					}
				}
			}
		}
	}
	if (!validDigest(value.binding_digest)) errors.push("input binding binding_digest must be a SHA-256 digest");
	if (
		errors.length === 0 &&
		executionInputBindingDigest(value as unknown as ExecutionInputBinding) !== value.binding_digest
	) {
		errors.push("input binding binding_digest does not match its content");
	}
	return { valid: errors.length === 0, errors };
}
