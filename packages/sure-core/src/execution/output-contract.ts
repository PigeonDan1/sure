import { createHash } from "node:crypto";
import { lstatSync, readdirSync, readFileSync, realpathSync } from "node:fs";
import { posix as posixPath, resolve } from "node:path";
import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type {
	ArtifactRef,
	ExecutionOutputContract,
	ExecutionOutputKind,
	ExecutionOutputResidual,
	ExecutionOutputSpec,
	ExecutionReceipt,
	ExecutionRequest,
	JsonValue,
} from "../contracts/types.ts";

const DIGEST = /^(?:sha256:)?[0-9a-f]{64}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

function object(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validDigest(value: unknown): value is string {
	return typeof value === "string" && DIGEST.test(value);
}

function validId(value: unknown): value is string {
	return typeof value === "string" && ID.test(value);
}

function relativePath(value: unknown, field: string, errors: string[]): string | undefined {
	if (typeof value !== "string" || value.length === 0 || value.startsWith("/") || value.includes("\\")) {
		errors.push(`${field} must be a non-empty relative POSIX path`);
		return undefined;
	}
	const normalized = posixPath.normalize(value);
	if (normalized === "." || normalized === ".." || normalized.startsWith("../") || normalized !== value) {
		errors.push(`${field} must be normalized and non-escaping: ${value}`);
		return undefined;
	}
	return normalized;
}

function outputKind(value: unknown, field: string, errors: string[]): value is ExecutionOutputKind {
	if (value !== "file" && value !== "directory") {
		errors.push(`${field} must be file or directory`);
		return false;
	}
	return true;
}

function digestKindFor(kind: ExecutionOutputKind): "file_sha256" | "tree_sha256" {
	return kind === "directory" ? "tree_sha256" : "file_sha256";
}

function sameDigest(left: string, right: string): boolean {
	return left.replace(/^sha256:/, "").toLowerCase() === right.replace(/^sha256:/, "").toLowerCase();
}

function isTerminal(lifecycle: ExecutionReceipt["lifecycle"]): boolean {
	return ["SUCCEEDED", "FAILED", "PARTIAL", "CANCELLED"].includes(lifecycle);
}

function isWithinRelative(candidate: string, root: string): boolean {
	return candidate === root || candidate.startsWith(`${root}/`);
}

export interface OutputContractValidation {
	valid: boolean;
	errors: readonly string[];
}

/** Validate only the declarative contract, without inspecting the filesystem. */
export function validateExecutionOutputContract(value: unknown): OutputContractValidation {
	const errors: string[] = [];
	if (!object(value)) return { valid: false, errors: ["execution output contract must be an object"] };
	if (value.schema !== "sure.execution_output_contract.v1") errors.push("output contract schema is unsupported");
	if (value.mode !== "preexisting" && value.mode !== "mutating" && value.mode !== "producing")
		errors.push("output contract mode is invalid");
	if (!Array.isArray(value.outputs) || value.outputs.length === 0) {
		errors.push("output contract must declare at least one output");
	} else {
		const ids = new Set<string>();
		const paths = new Set<string>();
		value.outputs.forEach((raw, index) => {
			const field = `output_contract.outputs[${index}]`;
			if (!object(raw)) {
				errors.push(`${field} must be an object`);
				return;
			}
			if (!validId(raw.artifact_id)) errors.push(`${field}.artifact_id is invalid`);
			else if (ids.has(raw.artifact_id)) errors.push(`${field}.artifact_id is duplicated`);
			else ids.add(raw.artifact_id);
			const path = relativePath(raw.path, `${field}.path`, errors);
			if (path !== undefined) {
				if (paths.has(path)) errors.push(`${field}.path is duplicated`);
				else paths.add(path);
			}
			outputKind(raw.kind, `${field}.kind`, errors);
			if (typeof raw.required !== "boolean") errors.push(`${field}.required must be boolean`);
		});
		if (value.mode === "producing" && !value.outputs.some((raw) => object(raw) && raw.required === true)) {
			errors.push("producing output contract must declare a required output");
		}
	}
	if (!Array.isArray(value.temporary_paths)) {
		errors.push("output_contract.temporary_paths must be an array");
	} else {
		const paths = new Set<string>();
		value.temporary_paths.forEach((raw, index) => {
			const path = relativePath(raw, `output_contract.temporary_paths[${index}]`, errors);
			if (path !== undefined) {
				if (paths.has(path)) errors.push(`output_contract.temporary_paths[${index}] is duplicated`);
				else paths.add(path);
			}
		});
	}
	if (typeof value.allow_missing_on_failure !== "boolean")
		errors.push("output_contract.allow_missing_on_failure must be boolean");
	if (typeof value.retain_failed_outputs !== "boolean")
		errors.push("output_contract.retain_failed_outputs must be boolean");
	return { valid: errors.length === 0, errors };
}

export function executionOutputContractDigest(contract: ExecutionOutputContract): string {
	return canonicalJsonDigest(contract as unknown as JsonValue);
}

/** Digest the exact output and residual set, including bound paths and metadata. */
export function executionOutputSetDigest(
	outputs: readonly ArtifactRef[],
	residuals: readonly ExecutionOutputResidual[] = [],
): string {
	const sortedOutputs = [...outputs].sort((left, right) => {
		const a = `${left.artifact_id}\0${left.resolved_path}`;
		const b = `${right.artifact_id}\0${right.resolved_path}`;
		return a < b ? -1 : a > b ? 1 : 0;
	});
	const sortedResiduals = [...residuals].sort((left, right) => {
		const a = `${left.path}\0${left.resolved_path}`;
		const b = `${right.path}\0${right.resolved_path}`;
		return a < b ? -1 : a > b ? 1 : 0;
	});
	return canonicalJsonDigest({
		schema: "sure.execution.output-set.v1",
		outputs: sortedOutputs,
		residuals: sortedResiduals,
	} as unknown as JsonValue);
}

function expectedOutputPath(request: ExecutionRequest, spec: ExecutionOutputSpec): string {
	return resolve(request.output_root.resolved_path, ...spec.path.split("/"));
}

function outputRelativePath(
	request: ExecutionRequest,
	path: unknown,
	rootKey: "path" | "resolved_path" = "resolved_path",
): string | undefined {
	if (typeof path !== "string" || path.length === 0) return undefined;
	const root = resolve(request.output_root[rootKey]);
	const candidate = resolve(path);
	const relation = posixPath.relative(root, candidate).replaceAll("\\", "/");
	if (relation === "" || relation === ".." || relation.startsWith("../") || relation.startsWith("/")) return undefined;
	return relation;
}

function validateArtifactDigestShape(
	artifact: ArtifactRef,
	field: string,
	errors: string[],
	kind: ExecutionOutputKind,
): void {
	const actualKind = artifact.kind ?? "file";
	if (actualKind !== kind) errors.push(`${field}.kind does not match declared output kind`);
	const expectedDigestKind = digestKindFor(kind);
	const actualDigestKind = artifact.digest_kind ?? "file_sha256";
	if (actualDigestKind !== expectedDigestKind)
		errors.push(`${field}.digest_kind must be ${expectedDigestKind} for a ${kind} output`);
	if (kind === "directory" && artifact.media_type !== "inode/directory")
		errors.push(`${field}.media_type must be inode/directory for a directory output`);
}

function validateResidual(
	residual: unknown,
	index: number,
	request: ExecutionRequest,
	contract: ExecutionOutputContract,
	errors: string[],
): void {
	const field = `receipt.residuals[${index}]`;
	if (!object(residual)) {
		errors.push(`${field} must be an object`);
		return;
	}
	for (const key of ["path", "resolved_path"] as const) {
		if (typeof residual[key] !== "string" || !residual[key])
			errors.push(`${field}.${key} must be a non-empty string`);
	}
	if (!outputKind(residual.kind, `${field}.kind`, errors)) return;
	if (residual.status !== "present" && residual.status !== "missing")
		errors.push(`${field}.status must be present or missing`);
	const relative =
		typeof residual.resolved_path === "string"
			? outputRelativePath(request, residual.resolved_path, "resolved_path")
			: undefined;
	if (relative === undefined) {
		errors.push(`${field}.resolved_path is outside the output root`);
	} else if (!contract.temporary_paths.some((root) => isWithinRelative(relative, root))) {
		errors.push(`${field}.resolved_path is not declared as a temporary path`);
	}
	if (typeof residual.path === "string" && typeof residual.resolved_path === "string") {
		const lexicalRelative = outputRelativePath(request, residual.path, "path");
		if (lexicalRelative === undefined || relative === undefined || lexicalRelative !== relative)
			errors.push(`${field}.path does not resolve to resolved_path within the output root`);
	}
	const digestKind = digestKindFor(residual.kind);
	if (residual.status === "present") {
		if (!validDigest(residual.sha256)) errors.push(`${field}.sha256 is required for present residuals`);
		if (residual.digest_kind !== digestKind)
			errors.push(`${field}.digest_kind must be ${digestKind} for a present residual`);
		if (typeof residual.size !== "number" || !Number.isSafeInteger(residual.size) || residual.size < 0)
			errors.push(`${field}.size is required for present residuals`);
	} else if (residual.sha256 !== undefined || residual.digest_kind !== undefined || residual.size !== undefined) {
		errors.push(`${field} missing residuals cannot carry digest or size`);
	}
}

/** Validate a receipt's output set against its request-bound output contract. */
export function validateExecutionOutputBinding(
	request: ExecutionRequest,
	receipt: ExecutionReceipt,
): readonly string[] {
	const contract = request.output_contract;
	const hasReceiptExtension =
		receipt.output_contract_digest !== undefined ||
		receipt.output_set_digest !== undefined ||
		receipt.residuals !== undefined;
	if (contract === undefined) {
		return hasReceiptExtension ? ["receipt carries output-contract fields without a request output_contract"] : [];
	}
	const contractValidation = validateExecutionOutputContract(contract);
	const errors = [...contractValidation.errors];
	if (!contractValidation.valid) return errors;
	const contractDigest = executionOutputContractDigest(contract);
	if (!validDigest(receipt.output_contract_digest)) {
		errors.push("receipt.output_contract_digest is required when request.output_contract is present");
	} else if (!sameDigest(receipt.output_contract_digest, contractDigest)) {
		errors.push("receipt.output_contract_digest does not match request.output_contract");
	}
	const outputs = Array.isArray(receipt.outputs) ? receipt.outputs : [];
	const specs = Array.isArray(contract.outputs)
		? contract.outputs.filter((candidate): candidate is ExecutionOutputSpec => object(candidate))
		: [];
	const seenIds = new Set<string>();
	const seenPaths = new Set<string>();
	for (const [index, rawOutput] of outputs.entries()) {
		const field = `receipt.outputs[${index}]`;
		if (!object(rawOutput)) continue;
		const output = rawOutput as unknown as ArtifactRef;
		if (seenIds.has(output.artifact_id)) errors.push(`${field}.artifact_id is duplicated`);
		seenIds.add(output.artifact_id);
		const spec = specs.find((candidate) => candidate.artifact_id === output.artifact_id);
		if (spec === undefined) {
			errors.push(`${field}.artifact_id is not declared by output_contract`);
			continue;
		}
		const relative = outputRelativePath(request, output.resolved_path);
		if (relative === undefined) {
			errors.push(`${field}.resolved_path is outside the output root`);
		} else {
			if (seenPaths.has(relative)) errors.push(`${field}.resolved_path is duplicated`);
			seenPaths.add(relative);
			if (relative !== spec.path)
				errors.push(`${field}.resolved_path does not match output_contract path ${spec.path}`);
		}
		const lexicalRelative = outputRelativePath(request, output.path, "path");
		if (lexicalRelative === undefined || lexicalRelative !== spec.path)
			errors.push(`${field}.path does not match output_contract path ${spec.path}`);
		if (
			typeof output.resolved_path === "string" &&
			resolve(output.resolved_path) !== expectedOutputPath(request, spec)
		)
			errors.push(`${field}.resolved_path is not the declared output path`);
		validateArtifactDigestShape(output, field, errors, spec.kind);
	}
	const terminal = isTerminal(receipt.lifecycle);
	for (const spec of specs) {
		if (!spec.required || seenIds.has(spec.artifact_id)) continue;
		if (receipt.lifecycle === "SUCCEEDED" || (terminal && !contract.allow_missing_on_failure)) {
			errors.push(`required output ${spec.artifact_id} is missing from receipt`);
		}
	}
	const residuals = Array.isArray(receipt.residuals) ? receipt.residuals : [];
	if (receipt.lifecycle === "SUCCEEDED" && residuals.length > 0)
		errors.push("successful execution cannot retain output residuals");
	if (residuals.length > 0 && !contract.retain_failed_outputs)
		errors.push("receipt residuals are not permitted by output_contract");
	const residualPaths = new Set<string>();
	for (const [index, residual] of residuals.entries()) {
		if (object(residual)) {
			const key = `${String(residual.path)}\0${String(residual.resolved_path)}`;
			if (residualPaths.has(key)) errors.push(`receipt.residuals[${index}] is duplicated`);
			residualPaths.add(key);
		}
		validateResidual(residual, index, request, contract, errors);
	}
	let outputSetDigest: string | undefined;
	try {
		outputSetDigest = executionOutputSetDigest(outputs, residuals);
	} catch (error) {
		errors.push(
			`receipt output set cannot be canonicalized: ${error instanceof Error ? error.message : String(error)}`,
		);
	}
	if (!validDigest(receipt.output_set_digest)) {
		errors.push("receipt.output_set_digest is required when request.output_contract is present");
	} else if (outputSetDigest !== undefined && !sameDigest(receipt.output_set_digest, outputSetDigest)) {
		errors.push("receipt.output_set_digest does not match observed outputs and residuals");
	}
	return errors;
}

function digestFile(path: string): string {
	return `sha256:${createHash("sha256").update(readFileSync(path)).digest("hex")}`;
}

interface TreeEntry {
	relativePath: string;
	kind: "file" | "directory";
	digest?: string;
	size?: number;
}

function collectTreeEntries(root: string, current: string, entries: TreeEntry[]): number {
	const stat = lstatSync(current);
	if (stat.isSymbolicLink()) throw new Error(`output path contains a symlink: ${current}`);
	if (stat.isFile()) {
		entries.push({
			relativePath: posixPath.relative(root, current) || ".",
			kind: "file",
			digest: digestFile(current),
			size: stat.size,
		});
		return stat.size;
	}
	if (!stat.isDirectory()) throw new Error(`output path is not a regular file or directory: ${current}`);
	const relative = posixPath.relative(root, current) || ".";
	entries.push({ relativePath: relative, kind: "directory" });
	let size = 0;
	for (const entry of readdirSync(current, { withFileTypes: true }).sort((left, right) =>
		left.name < right.name ? -1 : left.name > right.name ? 1 : 0,
	)) {
		size += collectTreeEntries(root, `${current}/${entry.name}`, entries);
	}
	return size;
}

/** Inspect a regular file or directory using a deterministic tree digest. */
export function inspectExecutionArtifact(
	path: string,
): Pick<ArtifactRef, "sha256" | "size" | "kind" | "digest_kind" | "media_type"> {
	const lexical = resolve(path);
	const stat = lstatSync(lexical);
	if (stat.isSymbolicLink()) throw new Error(`output path is a symlink: ${lexical}`);
	const resolved = resolve(realpathSync.native(lexical));
	const resolvedStat = lstatSync(resolved);
	if (resolvedStat.isSymbolicLink()) throw new Error(`output path is a symlink: ${resolved}`);
	if (resolvedStat.isFile()) {
		return {
			sha256: digestFile(resolved),
			size: resolvedStat.size,
			kind: "file",
			digest_kind: "file_sha256",
			media_type: "application/octet-stream",
		};
	}
	if (!resolvedStat.isDirectory()) throw new Error(`output path is not a regular file or directory: ${resolved}`);
	const entries: TreeEntry[] = [];
	const size = collectTreeEntries(resolved, resolved, entries);
	const rows = entries
		.sort((left, right) =>
			left.relativePath < right.relativePath ? -1 : left.relativePath > right.relativePath ? 1 : 0,
		)
		.map((entry) =>
			entry.kind === "directory"
				? `${entry.relativePath}\0directory\n`
				: `${entry.relativePath}\0file\0${entry.digest}\0${entry.size}\n`,
		)
		.join("");
	return {
		sha256: `sha256:${createHash("sha256").update(rows, "utf8").digest("hex")}`,
		size,
		kind: "directory",
		digest_kind: "tree_sha256",
		media_type: "inode/directory",
	};
}
