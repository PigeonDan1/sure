import { isAbsolute, normalize, relative } from "node:path/posix";
import { type CoreOutcome, createOutcome, type ReasonCode } from "../workflow/outcome.ts";

export interface ResolvedRoot {
	path: string;
	resolved_path: string;
}

export interface PathBoundaryInput {
	candidate_path: string;
	candidate_resolved_path: string;
	allowed_roots: readonly ResolvedRoot[];
	forbidden_roots?: readonly ResolvedRoot[];
}

export interface PathBoundaryEvaluation {
	admitted: boolean;
	reason_code?: ReasonCode;
	blocking_outcome?: CoreOutcome;
}

function isInside(root: string, candidate: string): boolean {
	const relation = relative(normalize(root), normalize(candidate));
	return relation === "" || (relation !== ".." && !relation.startsWith("../") && !isAbsolute(relation));
}

function blocked(reasonCode: ReasonCode, message: string): PathBoundaryEvaluation {
	return {
		admitted: false,
		reason_code: reasonCode,
		blocking_outcome: createOutcome({
			validatorVerdict: "NOT_EXECUTED",
			workflowDisposition: "BLOCK",
			reasonCode,
			diagnostics: [{ code: reasonCode, message }],
		}),
	};
}

export function evaluatePathBoundary(input: PathBoundaryInput): PathBoundaryEvaluation {
	const allPaths = [
		input.candidate_path,
		input.candidate_resolved_path,
		...input.allowed_roots.flatMap((root) => [root.path, root.resolved_path]),
		...(input.forbidden_roots ?? []).flatMap((root) => [root.path, root.resolved_path]),
	];
	if (allPaths.some((path) => !isAbsolute(path))) {
		return blocked("INVALID_CONTRACT", "Path boundary checks require absolute lexical and resolved paths");
	}

	for (const root of input.forbidden_roots ?? []) {
		if (isInside(root.path, input.candidate_path) || isInside(root.resolved_path, input.candidate_resolved_path)) {
			return blocked("READ_ONLY_REFERENCE", `Path ${input.candidate_path} overlaps a read-only reference root`);
		}
	}

	const lexicalRoots = input.allowed_roots.filter((root) => isInside(root.path, input.candidate_path));
	if (lexicalRoots.length === 0) {
		return blocked("PATH_OUT_OF_SCOPE", `Path ${input.candidate_path} is outside every allowed root`);
	}
	if (!lexicalRoots.some((root) => isInside(root.resolved_path, input.candidate_resolved_path))) {
		return blocked("SYMLINK_ESCAPE", `Resolved path ${input.candidate_resolved_path} escapes its allowed root`);
	}
	return { admitted: true };
}
