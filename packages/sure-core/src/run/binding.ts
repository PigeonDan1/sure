import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type { JsonValue } from "../contracts/types.ts";
import type { ResumeBinding } from "./types.ts";

export type RunBindingIdentity = Pick<
	ResumeBinding,
	"workflowDigest" | "validatorDigest" | "executorDigest" | "policyDigest" | "policySnapshotDigest"
>;

/**
 * Canonical run binding shared by portable and Pi hosts.
 *
 * The exact field set preserves the existing Pi digest. Core implementation
 * identity is bound separately by formal assurance rather than silently
 * changing already persisted run-binding semantics.
 */
export function runBindingDigest(binding: RunBindingIdentity): string {
	return canonicalJsonDigest({
		workflowDigest: binding.workflowDigest,
		validatorDigest: binding.validatorDigest,
		executorDigest: binding.executorDigest,
		policyDigest: binding.policyDigest,
		policySnapshotDigest: binding.policySnapshotDigest ?? null,
	} as unknown as JsonValue);
}
