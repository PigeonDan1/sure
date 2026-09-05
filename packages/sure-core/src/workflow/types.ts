import type { JsonValue } from "../contracts/types.ts";

export type UnitKind = "linear" | "gate";

export interface GateDefinition {
	validator_id: string;
	/** Additional in-process or cross-artifact validators that must also pass. */
	auxiliary_validator_ids?: readonly string[];
	script_id?: string;
	script_args?: readonly string[];
	gate_inputs?: readonly string[];
}

export interface WorkflowUnit {
	id: string;
	label: string;
	kind: UnitKind;
	produces: string;
	schema_ref?: string;
	required_fields?: readonly string[];
	allowed_values?: Readonly<Record<string, readonly JsonValue[]>>;
	forbidden_fields?: readonly string[];
	gate?: GateDefinition;
	helper_scripts?: readonly string[];
	owned_scripts?: readonly string[];
}

export interface WorkflowBranch {
	id: string;
	units: readonly WorkflowUnit[];
	initial_unit_id: string;
	terminal_unit_id: string;
}

export interface RetryPolicy {
	default_max_retries: number;
	exempt_from_exhaustion?: readonly string[];
}

export interface WorkflowDefinition {
	schema: "sure.workflow.definition.v1";
	workflow_id: string;
	version: string;
	/** Legacy-compatible checkpoint presentation; it has no transition authority. */
	checkpoint_id?: string;
	checkpoint_label?: string;
	branches: readonly WorkflowBranch[];
	default_branch_id: string;
	retry_policy: RetryPolicy;
}

export interface WorkflowCheckpointData<TMemory = unknown> {
	currentUnit: string;
	completedUnits: readonly string[];
	retries: Readonly<Record<string, number>>;
	blocks?: number;
	failedArtifactDigests?: Readonly<Record<string, string>>;
	/** Compatibility field retained by the approval skill's legacy checkpoint. */
	mode?: string;
	memory?: TMemory;
}

export interface WorkflowCheckpoint<TMemory = unknown> {
	id: string;
	label: string;
	resumable: boolean;
	resume_hint: string;
	branch_id: string;
	data: WorkflowCheckpointData<TMemory>;
}

export type ValidationSignal =
	| { kind: "missing"; reason?: string }
	| { kind: "pass"; artifact_digest?: string }
	| { kind: "fail"; reason: string; artifact_digest?: string };

export type TransitionAction = "advanced" | "missing" | "retry" | "unchanged" | "exhausted" | "rejected" | "terminal";

export interface TransitionResult<TMemory = unknown> {
	accepted: boolean;
	action: TransitionAction;
	checkpoint: WorkflowCheckpoint<TMemory>;
	retry_consumed: boolean;
	exhausted: boolean;
	reason?: string;
}
