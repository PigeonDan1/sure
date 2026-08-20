export type SureRunStatus = "pending" | "running" | "success" | "failed" | "incomplete" | "cancelled";

export type SureHookPoint =
	| "pre_start"
	| "pre_tool_call"
	| "post_tool_result"
	| "pre_finish"
	| "post_finish"
	| "on_error";

export interface SureHookDeclaration {
	module: string;
	handler?: string;
}

export interface SureArtifactRequirement {
	type?: string;
	path?: string;
	required?: boolean;
	description?: string;
}

export interface SureSkillUiHints {
	primaryCounters?: string[];
	artifactTypes?: string[];
	defaultExpandedSections?: string[];
}

export interface SureSkillManifest {
	name: string;
	command: string;
	description?: string;
	prompt: string;
	hooks?: Partial<Record<SureHookPoint, SureHookDeclaration[]>>;
	artifacts?: SureArtifactRequirement[];
	ui?: SureSkillUiHints;
}

export interface SureManifestArtifact {
	type?: string;
	path?: string;
}

export interface SureManifestEnvelope {
	schema_version: string;
	run_id: string;
	skill_name: string;
	status: SureRunStatus;
	created_at: string;
	inputs: Record<string, unknown>;
	outputs: Record<string, unknown>;
	validation: Record<string, unknown>;
	artifacts?: SureManifestArtifact[];
}

export interface SureRunRecord {
	runId: string;
	skillName: string;
	command: string;
	status: SureRunStatus;
	cwd: string;
	packageDir: string;
	runDir: string;
	args: string;
	outputDir?: string;
	startedAt: string;
	updatedAt: string;
	finishedAt?: string;
	manifestPath?: string;
	summary?: string;
	errorSummary?: string;
	lastRepair?: string;
	artifacts?: unknown;
}

export type SureDisplayPhaseStatus =
	| "pending"
	| "running"
	| "success"
	| "failed"
	| "incomplete"
	| "skipped"
	| "blocked";

export interface SureDisplayPhase {
	id?: string;
	label?: string;
	status?: SureDisplayPhaseStatus;
	progress?: number;
}

export interface SureDisplayDiagnostic {
	severity?: "info" | "warning" | "error";
	code?: string;
	message: string;
	repair?: string;
	data?: unknown;
}

export interface SureDisplayArtifact {
	type?: string;
	name?: string;
	path?: string;
	status?: "draft" | "ready" | "failed" | "incomplete";
	summary?: string;
	metadata?: Record<string, unknown>;
}

export interface SureDisplayCheckpoint {
	id?: string;
	label?: string;
	resumable?: boolean;
	resume_hint?: string;
	data?: unknown;
}

export interface SureDisplayState {
	phase?: SureDisplayPhase;
	message?: string;
	progress?: number;
	counters?: Record<string, number>;
	diagnostics?: SureDisplayDiagnostic[];
	artifacts?: SureDisplayArtifact[];
	checkpoint?: SureDisplayCheckpoint;
	next_actions?: string[];
}

export interface SureHookContext {
	point: SureHookPoint;
	run: SureRunRecord;
	skill: SureSkillManifest;
	cwd: string;
	packageDir: string;
	runDir: string;
	args: string;
	event?: unknown;
}

export interface SureHookResult {
	ok?: boolean;
	message?: string;
	repair?: string;
	patch?: unknown;
	artifacts?: unknown;
	diagnostics?: unknown;
	state_patch?: unknown;
}

export interface SureFinishParams {
	status: "success" | "incomplete" | "failed";
	manifest_path: string;
	summary: string;
	artifacts?: unknown;
	error_summary?: string;
	next_actions?: string[];
}

export interface SureFinishDetails {
	runId?: string;
	status?: SureRunStatus;
	accepted: boolean;
	repair?: string;
	diagnostics?: unknown;
}

export interface SureUpdateStateDetails {
	runId?: string;
	accepted: boolean;
	state?: SureDisplayState;
	repair?: string;
}
