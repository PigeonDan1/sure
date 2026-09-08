import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import {
	auditCheckpointTransition,
	decodeLegacyCheckpoint,
	type ExecutionRequestDispatcher,
	initialCheckpoint,
	type WorkflowCheckpoint,
	type WorkflowDefinition,
} from "@earendil-works/sure-core";
import {
	createPiExecutionProvenanceHostForContext,
	type PiExecutionProvenanceContextOptions,
} from "./execution-provenance.ts";
import { isSureWorkflowId, type SureWorkflowId, workflowDefinitionForSkill } from "./generated-workflows.ts";
import { type SureGateResult, SureHookRunner } from "./hooks.ts";
import type { SureHookContext, SureHookPoint, SureSkillPackage } from "./types.ts";

export type PiHookEventKind = "tool_call" | "tool_result" | "finish" | "session_shutdown" | "agent_error" | "lifecycle";

export interface CoreHookEvent {
	point: SureHookPoint;
	kind: PiHookEventKind;
	tool_name?: string;
	tool_call_id?: string;
	is_error?: boolean;
	payload: unknown;
}

export interface SureHookDispatcher {
	run(point: SureHookPoint, context: Omit<SureHookContext, "point">): Promise<SureGateResult>;
}

/** Host-only extensions; omitted callers retain the legacy cooperative path. */
export interface PiSureControllerOptions {
	readonly executionDispatcherForContext?: (
		context: Omit<SureHookContext, "point">,
	) => ExecutionRequestDispatcher | undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function skillIdForPackage(skillPackage: SureSkillPackage): SureWorkflowId | undefined {
	const name = skillPackage.manifest.name;
	return isSureWorkflowId(name) ? name : undefined;
}

function toolNameFromEvent(event: unknown): string | undefined {
	if (!isRecord(event)) return undefined;
	if (typeof event.toolName === "string") return event.toolName;
	const call = isRecord(event.toolCall) ? event.toolCall : undefined;
	return call && typeof call.name === "string" ? call.name : undefined;
}

function toolCallIdFromEvent(event: unknown): string | undefined {
	if (!isRecord(event)) return undefined;
	return typeof event.toolCallId === "string" ? event.toolCallId : undefined;
}

/** Convert a Pi event into a host-neutral event without removing legacy fields. */
export function translatePiEvent(point: SureHookPoint, event: unknown): CoreHookEvent {
	const toolName = toolNameFromEvent(event);
	const toolCallId = toolCallIdFromEvent(event);
	const kind: PiHookEventKind =
		point === "pre_tool_call"
			? "tool_call"
			: point === "post_tool_result"
				? "tool_result"
				: point === "pre_finish" || point === "post_finish"
					? "finish"
					: point === "on_error"
						? isRecord(event) && event.reason === "session_shutdown"
							? "session_shutdown"
							: "agent_error"
						: "lifecycle";
	return {
		point,
		kind,
		...(toolName === undefined ? {} : { tool_name: toolName }),
		...(toolCallId === undefined ? {} : { tool_call_id: toolCallId }),
		...(isRecord(event) && typeof event.isError === "boolean" ? { is_error: event.isError } : {}),
		payload: event,
	};
}

function withCoreEvent(event: unknown, coreEvent: CoreHookEvent): unknown {
	if (!isRecord(event)) return { payload: event, coreEvent };
	return { ...event, coreEvent };
}

function checkpointFromRaw(
	definition: WorkflowDefinition,
	skillId: SureWorkflowId,
	raw: unknown,
): WorkflowCheckpoint | undefined {
	if (!isRecord(raw) || !isRecord(raw.checkpoint) || !isRecord(raw.checkpoint.data)) return undefined;
	const data = raw.checkpoint.data;
	const mode = data.mode === "approve" ? "approve" : "audit";
	const branchId = skillId === "sure_approve" ? mode : definition.default_branch_id;
	const decoded = decodeLegacyCheckpoint(definition, raw, {
		id: definition.checkpoint_id ?? "main_flow",
		label: definition.checkpoint_label ?? definition.workflow_id,
		branch_id: branchId,
		include_failed_artifact_digests: true,
	});
	const initial = initialCheckpoint(definition, branchId, {
		id: definition.checkpoint_id ?? "main_flow",
		label: definition.checkpoint_label ?? definition.workflow_id,
		mode: typeof data.mode === "string" ? data.mode : undefined,
	});
	return {
		...initial,
		data: decoded.data,
		branch_id: decoded.branch_id,
	};
}

function checkpointFromPatch(
	definition: WorkflowDefinition,
	skillId: SureWorkflowId,
	patch: unknown,
): WorkflowCheckpoint | undefined {
	if (!isRecord(patch) || !isRecord(patch.checkpoint)) return undefined;
	return checkpointFromRaw(definition, skillId, { checkpoint: patch.checkpoint });
}

function transitionFailure(result: SureGateResult, reason: string): SureGateResult {
	return {
		ok: false,
		message: `Core rejected a Pi checkpoint transition: ${reason}`,
		repair: `The Pi hook produced an illegal checkpoint transition. ${reason}`,
		diagnostics: [
			...(Array.isArray(result.diagnostics) ? result.diagnostics : []),
			{ code: "CORE_CHECKPOINT_TRANSITION_REJECTED", message: reason },
		],
		state_patch: {
			phase: { id: "core_transition", label: "Core transition audit blocked", status: "blocked" },
			message: reason,
			diagnostics: [{ severity: "error", code: "CORE_CHECKPOINT_TRANSITION_REJECTED", message: reason }],
		},
	};
}

/**
 * Pi-only enforcement adapter. It owns module loading and lifecycle strength;
 * canonical workflow legality is audited by the host-neutral Core kernel.
 */
export class PiSureController {
	private readonly definition?: WorkflowDefinition;
	private readonly skillId?: SureWorkflowId;
	private readonly hookRunner: SureHookDispatcher;
	private readonly hostOptions: PiSureControllerOptions;

	constructor(
		skillPackage: SureSkillPackage,
		hookRunner?: SureHookDispatcher,
		hostOptions: PiSureControllerOptions = {},
	) {
		this.hookRunner = hookRunner ?? new SureHookRunner(skillPackage);
		this.hostOptions = hostOptions;
		this.skillId = skillIdForPackage(skillPackage);
		this.definition = this.skillId ? workflowDefinitionForSkill(this.skillId) : undefined;
	}

	async run(point: SureHookPoint, context: Omit<SureHookContext, "point">): Promise<SureGateResult> {
		const coreEvent = translatePiEvent(point, context.event);
		// The caller can construct a context object, but it cannot supply the
		// provenance issuer.  The controller derives it from the host-owned run
		// and generated package binding on every lifecycle entry.
		const { executionProvenance: _callerProvenance, ...callerContext } = context;
		const baseContext: Omit<SureHookContext, "point"> = {
			...callerContext,
			run: Object.freeze({ ...context.run }),
			skill: Object.freeze({ ...context.skill }),
		};
		const forwarded: Omit<SureHookContext, "point"> = {
			...baseContext,
			event: withCoreEvent(context.event, coreEvent),
		};
		const executionDispatcher = this.hostOptions.executionDispatcherForContext?.(baseContext);
		const provenanceOptions: PiExecutionProvenanceContextOptions =
			executionDispatcher === undefined ? {} : { execution_dispatcher: executionDispatcher };
		const host = createPiExecutionProvenanceHostForContext(baseContext, provenanceOptions);
		if (host !== undefined) {
			Object.defineProperty(forwarded, "executionProvenance", {
				value: host,
				enumerable: false,
				writable: false,
				configurable: false,
			});
		}
		const result = await this.hookRunner.run(point, forwarded);
		if (!this.definition || !this.skillId || result.state_patch === undefined) return result;

		const statePath = join(context.runDir, "state.json");
		if (!existsSync(statePath)) return result;
		let raw: unknown;
		try {
			raw = JSON.parse(readFileSync(statePath, "utf8")) as unknown;
		} catch {
			return result;
		}
		const before = checkpointFromRaw(this.definition, this.skillId, raw);
		const after = checkpointFromPatch(this.definition, this.skillId, result.state_patch);
		if (!before || !after) return result;
		const audit = auditCheckpointTransition(this.definition, before, after);
		return audit.ok ? result : transitionFailure(result, audit.reason ?? "Unknown checkpoint transition error.");
	}
}

export function createPiSureController(
	skillPackage: SureSkillPackage,
	hostOptions: PiSureControllerOptions = {},
): PiSureController {
	return new PiSureController(skillPackage, undefined, hostOptions);
}
