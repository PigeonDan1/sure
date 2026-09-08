import { createHash } from "node:crypto";
import { lstatSync, readFileSync } from "node:fs";
import type { ExecutionAdmissionTrace, ExecutionReceipt, ExecutionRequest } from "@earendil-works/sure-core";
import { validateExecutionAdmissionReceiptBinding, validateExecutionAdmissionTrace } from "@earendil-works/sure-core";

const DIGEST = /^(?:sha256:)?[0-9a-f]{64}$/i;

export interface PersistedExecutionAdmissionResult {
	path: string;
	digest?: string;
	trace?: ExecutionAdmissionTrace;
	errors: readonly string[];
}

function sameDigest(left: string, right: string): boolean {
	return left.replace(/^sha256:/i, "").toLowerCase() === right.replace(/^sha256:/i, "").toLowerCase();
}

function fileDigest(path: string): string {
	return `sha256:${createHash("sha256").update(readFileSync(path)).digest("hex")}`;
}

function record(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Read and bind a host-written admission trace without trusting state paths. */
export function readAndValidateExecutionAdmission(
	path: string,
	request: ExecutionRequest,
	options: {
		expected_digest?: string;
		receipt?: ExecutionReceipt;
		receipt_valid?: boolean;
		capability_admitted?: boolean;
	} = {},
): PersistedExecutionAdmissionResult {
	const errors: string[] = [];
	let digest: string | undefined;
	let trace: ExecutionAdmissionTrace | undefined;
	try {
		const stat = lstatSync(path);
		if (!stat.isFile() || stat.isSymbolicLink())
			throw new Error(`Execution admission is not a regular file: ${path}`);
		digest = fileDigest(path);
		if (options.expected_digest !== undefined) {
			if (!DIGEST.test(options.expected_digest) || !sameDigest(digest, options.expected_digest)) {
				errors.push("execution admission digest does not match its persisted evidence");
			}
		}
		const value = JSON.parse(readFileSync(path, "utf8")) as unknown;
		if (!record(value)) {
			errors.push("execution admission must be a JSON object");
		} else {
			const shapeErrors = validateExecutionAdmissionTrace(value);
			errors.push(...shapeErrors);
			if (shapeErrors.length === 0) {
				trace = value as unknown as ExecutionAdmissionTrace;
				errors.push(
					...validateExecutionAdmissionReceiptBinding(request, trace, {
						receipt: options.receipt,
						receipt_valid: options.receipt_valid,
						capability_admitted: options.capability_admitted,
					}),
				);
			}
		}
	} catch (error) {
		errors.push(error instanceof Error ? error.message : String(error));
	}
	return { path, ...(digest === undefined ? {} : { digest }), ...(trace === undefined ? {} : { trace }), errors };
}
