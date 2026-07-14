import type {
	SureDisplayArtifact,
	SureDisplayCheckpoint,
	SureDisplayDiagnostic,
	SureDisplayPhase,
	SureDisplayPhaseStatus,
	SureDisplayState,
} from "./types.ts";

const PHASE_STATUSES = new Set<SureDisplayPhaseStatus>([
	"pending",
	"running",
	"success",
	"failed",
	"incomplete",
	"skipped",
	"blocked",
]);

const DIAGNOSTIC_SEVERITIES = new Set(["info", "warning", "error"]);
const ARTIFACT_STATUSES = new Set(["draft", "ready", "failed", "incomplete"]);

export interface SureStateValidationResult {
	ok: boolean;
	state?: SureDisplayState;
	message?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
	return typeof value === "number" && Number.isFinite(value);
}

function parseStringArray(value: unknown, path: string): { value?: string[]; message?: string } {
	if (value === undefined) {
		return {};
	}
	if (!Array.isArray(value)) {
		return { message: `${path} must be an array of strings.` };
	}
	const parsed: string[] = [];
	for (const [index, item] of value.entries()) {
		if (typeof item !== "string") {
			return { message: `${path}[${index}] must be a string.` };
		}
		parsed.push(item);
	}
	return { value: parsed };
}

function parseStringRecord(value: unknown, path: string): { value?: Record<string, number>; message?: string } {
	if (value === undefined) {
		return {};
	}
	if (!isRecord(value)) {
		return { message: `${path} must be an object with numeric values.` };
	}
	const parsed: Record<string, number> = {};
	for (const [key, item] of Object.entries(value)) {
		if (!isFiniteNumber(item)) {
			return { message: `${path}.${key} must be a finite number.` };
		}
		parsed[key] = item;
	}
	return { value: parsed };
}

function parsePhase(value: unknown): { value?: SureDisplayPhase; message?: string } {
	if (value === undefined) {
		return {};
	}
	if (!isRecord(value)) {
		return { message: "phase must be an object." };
	}
	const parsed: SureDisplayPhase = {};
	if (value.id !== undefined) {
		if (typeof value.id !== "string") {
			return { message: "phase.id must be a string." };
		}
		parsed.id = value.id;
	}
	if (value.label !== undefined) {
		if (typeof value.label !== "string") {
			return { message: "phase.label must be a string." };
		}
		parsed.label = value.label;
	}
	if (value.status !== undefined) {
		if (typeof value.status !== "string" || !PHASE_STATUSES.has(value.status as SureDisplayPhaseStatus)) {
			return { message: "phase.status must be a valid Sure display phase status." };
		}
		parsed.status = value.status as SureDisplayPhaseStatus;
	}
	if (value.progress !== undefined) {
		if (!isFiniteNumber(value.progress)) {
			return { message: "phase.progress must be a finite number." };
		}
		parsed.progress = value.progress;
	}
	return { value: parsed };
}

function parseDiagnostics(value: unknown): { value?: SureDisplayDiagnostic[]; message?: string } {
	if (value === undefined) {
		return {};
	}
	if (!Array.isArray(value)) {
		return { message: "diagnostics must be an array." };
	}
	const parsed: SureDisplayDiagnostic[] = [];
	for (const [index, item] of value.entries()) {
		if (!isRecord(item)) {
			return { message: `diagnostics[${index}] must be an object.` };
		}
		if (typeof item.message !== "string") {
			return { message: `diagnostics[${index}].message must be a string.` };
		}
		const diagnostic: SureDisplayDiagnostic = { message: item.message };
		if (item.severity !== undefined) {
			if (typeof item.severity !== "string" || !DIAGNOSTIC_SEVERITIES.has(item.severity)) {
				return { message: `diagnostics[${index}].severity must be info, warning, or error.` };
			}
			diagnostic.severity = item.severity as SureDisplayDiagnostic["severity"];
		}
		if (item.code !== undefined) {
			if (typeof item.code !== "string") {
				return { message: `diagnostics[${index}].code must be a string.` };
			}
			diagnostic.code = item.code;
		}
		if (item.repair !== undefined) {
			if (typeof item.repair !== "string") {
				return { message: `diagnostics[${index}].repair must be a string.` };
			}
			diagnostic.repair = item.repair;
		}
		diagnostic.data = item.data;
		parsed.push(diagnostic);
	}
	return { value: parsed };
}

function parseArtifacts(value: unknown): { value?: SureDisplayArtifact[]; message?: string } {
	if (value === undefined) {
		return {};
	}
	if (!Array.isArray(value)) {
		return { message: "artifacts must be an array." };
	}
	const parsed: SureDisplayArtifact[] = [];
	for (const [index, item] of value.entries()) {
		if (!isRecord(item)) {
			return { message: `artifacts[${index}] must be an object.` };
		}
		const artifact: SureDisplayArtifact = {};
		if (item.type !== undefined) {
			if (typeof item.type !== "string") {
				return { message: `artifacts[${index}].type must be a string.` };
			}
			artifact.type = item.type;
		}
		if (item.name !== undefined) {
			if (typeof item.name !== "string") {
				return { message: `artifacts[${index}].name must be a string.` };
			}
			artifact.name = item.name;
		}
		if (item.path !== undefined) {
			if (typeof item.path !== "string") {
				return { message: `artifacts[${index}].path must be a string.` };
			}
			artifact.path = item.path;
		}
		if (item.status !== undefined) {
			if (typeof item.status !== "string" || !ARTIFACT_STATUSES.has(item.status)) {
				return { message: `artifacts[${index}].status must be draft, ready, failed, or incomplete.` };
			}
			artifact.status = item.status as SureDisplayArtifact["status"];
		}
		if (item.summary !== undefined) {
			if (typeof item.summary !== "string") {
				return { message: `artifacts[${index}].summary must be a string.` };
			}
			artifact.summary = item.summary;
		}
		if (item.metadata !== undefined) {
			if (!isRecord(item.metadata)) {
				return { message: `artifacts[${index}].metadata must be an object.` };
			}
			artifact.metadata = item.metadata;
		}
		parsed.push(artifact);
	}
	return { value: parsed };
}

function parseCheckpoint(value: unknown): { value?: SureDisplayCheckpoint; message?: string } {
	if (value === undefined) {
		return {};
	}
	if (!isRecord(value)) {
		return { message: "checkpoint must be an object." };
	}
	const checkpoint: SureDisplayCheckpoint = {};
	if (value.id !== undefined) {
		if (typeof value.id !== "string") {
			return { message: "checkpoint.id must be a string." };
		}
		checkpoint.id = value.id;
	}
	if (value.label !== undefined) {
		if (typeof value.label !== "string") {
			return { message: "checkpoint.label must be a string." };
		}
		checkpoint.label = value.label;
	}
	if (value.resumable !== undefined) {
		if (typeof value.resumable !== "boolean") {
			return { message: "checkpoint.resumable must be a boolean." };
		}
		checkpoint.resumable = value.resumable;
	}
	if (value.resume_hint !== undefined) {
		if (typeof value.resume_hint !== "string") {
			return { message: "checkpoint.resume_hint must be a string." };
		}
		checkpoint.resume_hint = value.resume_hint;
	}
	checkpoint.data = value.data;
	return { value: checkpoint };
}

export function normalizeSureDisplayStatePatch(value: unknown): SureStateValidationResult {
	if (!isRecord(value)) {
		return { ok: false, message: "state_patch must be an object." };
	}

	const phase = parsePhase(value.phase);
	if (phase.message) {
		return { ok: false, message: phase.message };
	}
	const counters = parseStringRecord(value.counters, "counters");
	if (counters.message) {
		return { ok: false, message: counters.message };
	}
	const diagnostics = parseDiagnostics(value.diagnostics);
	if (diagnostics.message) {
		return { ok: false, message: diagnostics.message };
	}
	const artifacts = parseArtifacts(value.artifacts);
	if (artifacts.message) {
		return { ok: false, message: artifacts.message };
	}
	const checkpoint = parseCheckpoint(value.checkpoint);
	if (checkpoint.message) {
		return { ok: false, message: checkpoint.message };
	}
	const nextActions = parseStringArray(value.next_actions, "next_actions");
	if (nextActions.message) {
		return { ok: false, message: nextActions.message };
	}

	const state: SureDisplayState = {};
	if (phase.value) {
		state.phase = phase.value;
	}
	if (value.message !== undefined) {
		if (typeof value.message !== "string") {
			return { ok: false, message: "message must be a string." };
		}
		state.message = value.message;
	}
	if (value.progress !== undefined) {
		if (!isFiniteNumber(value.progress)) {
			return { ok: false, message: "progress must be a finite number." };
		}
		state.progress = value.progress;
	}
	if (counters.value) {
		state.counters = counters.value;
	}
	if (diagnostics.value) {
		state.diagnostics = diagnostics.value;
	}
	if (artifacts.value) {
		state.artifacts = artifacts.value;
	}
	if (checkpoint.value) {
		state.checkpoint = checkpoint.value;
	}
	if (nextActions.value) {
		state.next_actions = nextActions.value;
	}
	return { ok: true, state };
}

export function mergeSureDisplayState(
	previous: SureDisplayState | undefined,
	patch: SureDisplayState,
): SureDisplayState {
	return {
		...previous,
		...patch,
		phase: patch.phase ? { ...previous?.phase, ...patch.phase } : previous?.phase,
		counters: patch.counters ? { ...previous?.counters, ...patch.counters } : previous?.counters,
		checkpoint: patch.checkpoint ? { ...previous?.checkpoint, ...patch.checkpoint } : previous?.checkpoint,
	};
}

export function formatSureDisplayStatus(command: string, runId: string, state?: SureDisplayState): string {
	const phaseText = state?.phase?.label ?? state?.phase?.id;
	const messageText = state?.message;
	const suffix = [phaseText, messageText].filter((entry) => entry && entry.trim() !== "").join(" - ");
	return suffix ? `Sure ${command} ${runId}: ${suffix}` : `Sure ${command} ${runId}`;
}
