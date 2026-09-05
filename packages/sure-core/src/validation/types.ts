import type { JsonValue } from "../contracts/types.ts";

export type ValidatorAuthority = "structural" | "semantic" | "advisory";
export type ValidatorOperation = "validate" | "execute" | "publish" | "memory";

export interface ValidatorDescriptor {
	id: string;
	version: string;
	authority: ValidatorAuthority;
	operation: ValidatorOperation;
	deterministic: boolean;
	skill_id?: string;
	branch_id?: string;
	unit_id?: string;
	/** Name used by the legacy hook/state-machine implementation. */
	legacy_id?: string;
	script_args?: readonly string[];
	resource_path?: string;
	resource_digest?: string;
	input_contract?: string;
	output_contract?: string;
}

export interface ValidatorRegistrySnapshot {
	schema: "sure.validator.registry.v1";
	validators: readonly ValidatorDescriptor[];
	digest: string;
}

/** Lower-case wire value used by an individual validator evidence record. */
export type ValidatorEvidenceVerdict = "pass" | "fail" | "not_executed";

export interface ValidatorEvidence {
	validator_id: string;
	verdict: ValidatorEvidenceVerdict;
	reason_code?: string;
	diagnostics?: readonly string[];
	artifacts?: readonly JsonValue[];
}
