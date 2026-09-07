import {
	type ExecutionInputContractError,
	selectExecutionInputSelector,
	validateExecutionInputContract,
} from "../execution/input-contract.ts";
import type { ExecutionDispatchCase } from "./types.ts";

export class ExecutionDispatchError extends Error {
	readonly code: "INVALID_DISPATCH" | "NO_MATCH" | "AMBIGUOUS";

	constructor(code: ExecutionDispatchError["code"], message: string) {
		super(message);
		this.name = "ExecutionDispatchError";
		this.code = code;
	}
}

function matchCase(entry: ExecutionDispatchCase, context: Readonly<Record<string, unknown>>): boolean {
	return Object.entries(entry.match).every(([field, expected]) => context[field] === expected);
}

function sameMatch(left: Readonly<Record<string, string>>, right: Readonly<Record<string, string>>): boolean {
	const leftKeys = Object.keys(left);
	const rightKeys = Object.keys(right);
	return leftKeys.length === rightKeys.length && leftKeys.every((key) => left[key] === right[key]);
}

function matchesOverlap(left: Readonly<Record<string, string>>, right: Readonly<Record<string, string>>): boolean {
	const fields = new Set([...Object.keys(left), ...Object.keys(right)]);
	for (const field of fields) {
		if (left[field] !== undefined && right[field] !== undefined && left[field] !== right[field]) return false;
	}
	return true;
}

function validateDispatchCases(cases: readonly ExecutionDispatchCase[]): void {
	if (cases.length === 0) throw new ExecutionDispatchError("INVALID_DISPATCH", "execution dispatch has no cases");
	const caseIds = new Set<string>();
	const contextArtifact = cases[0]?.input_contract.context_artifact;
	for (const [index, entry] of cases.entries()) {
		if (caseIds.has(entry.case_id)) {
			throw new ExecutionDispatchError(
				"INVALID_DISPATCH",
				`execution dispatch case is duplicated: ${entry.case_id}`,
			);
		}
		caseIds.add(entry.case_id);
		if (entry.input_contract.context_artifact !== contextArtifact) {
			throw new ExecutionDispatchError(
				"INVALID_DISPATCH",
				`execution dispatch cases must share one context artifact (case ${entry.case_id})`,
			);
		}
		const validation = validateExecutionInputContract(entry.input_contract);
		if (!validation.valid) {
			throw new ExecutionDispatchError(
				"INVALID_DISPATCH",
				`execution dispatch case ${entry.case_id} has an invalid input contract: ${validation.errors.join("; ")}`,
			);
		}
		const selector = entry.input_contract.selectors[0];
		if (
			entry.input_contract.selectors.length !== 1 ||
			selector === undefined ||
			!sameMatch(selector.match, entry.match)
		) {
			throw new ExecutionDispatchError(
				"INVALID_DISPATCH",
				`execution dispatch case ${entry.case_id} does not match exactly one input selector`,
			);
		}
		for (const previous of cases.slice(0, index)) {
			if (matchesOverlap(previous.match, entry.match)) {
				throw new ExecutionDispatchError(
					"INVALID_DISPATCH",
					`execution dispatch cases overlap: ${previous.case_id}/${entry.case_id}`,
				);
			}
		}
	}
}

/** Resolve one workflow dispatch case from the immutable input context. */
export function selectExecutionDispatch(
	cases: readonly ExecutionDispatchCase[],
	context: Readonly<Record<string, unknown>>,
): ExecutionDispatchCase {
	validateDispatchCases(cases);
	const matches = cases.filter((entry) => matchCase(entry, context));
	if (matches.length === 0) {
		throw new ExecutionDispatchError(
			"NO_MATCH",
			`execution dispatch has no case for context ${JSON.stringify(context)}`,
		);
	}
	if (matches.length > 1) {
		throw new ExecutionDispatchError(
			"AMBIGUOUS",
			`execution dispatch has multiple cases for context ${JSON.stringify(context)}: ${matches
				.map((entry) => entry.case_id)
				.join(", ")}`,
		);
	}
	const selected = matches[0];
	try {
		selectExecutionInputSelector(selected.input_contract, context);
	} catch (error) {
		if (error instanceof ExecutionDispatchError) throw error;
		const code = (error as ExecutionInputContractError).code;
		throw new ExecutionDispatchError(
			code === "AMBIGUOUS" ? "AMBIGUOUS" : code === "NO_MATCH" ? "NO_MATCH" : "INVALID_DISPATCH",
			error instanceof Error ? error.message : String(error),
		);
	}
	return selected;
}
