import type { ExecutorKind, JsonValue } from "../contracts/types.ts";

/** External transport surfaces that require a registered adapter port. */
export const EXECUTION_ADAPTER_SURFACES = ["vc", "remote", "trusted"] as const;
export type ExecutionAdapterSurface = (typeof EXECUTION_ADAPTER_SURFACES)[number];

export interface ExecutionAdapterRoute {
	surface: ExecutionAdapterSurface;
	capability_id: `sure.execution.${ExecutionAdapterSurface}`;
	allowed_executor_kinds: readonly ExecutorKind[];
}

export interface ExecutionAdapterRouteValidation {
	valid: boolean;
	errors: readonly string[];
	route?: ExecutionAdapterRoute;
}

const ROUTES: Readonly<Record<ExecutionAdapterSurface, ExecutionAdapterRoute>> = {
	vc: {
		surface: "vc",
		capability_id: "sure.execution.vc",
		// VC is a site/queue transport. It must never be represented as a
		// local executor, even when the submitted payload is a container.
		allowed_executor_kinds: ["remote", "trusted"],
	},
	remote: {
		surface: "remote",
		capability_id: "sure.execution.remote",
		allowed_executor_kinds: ["remote"],
	},
	trusted: {
		surface: "trusted",
		capability_id: "sure.execution.trusted",
		allowed_executor_kinds: ["trusted"],
	},
};

function nonEmptyString(value: JsonValue | undefined, field: string, errors: string[]): void {
	if (typeof value !== "string" || value.trim() === "") errors.push(`${field} must be a non-empty string`);
}

function positiveInteger(value: JsonValue | undefined, field: string, errors: string[]): void {
	if (value !== undefined && (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0)) {
		errors.push(`${field} must be a positive integer when present`);
	}
}

/**
 * Parse the optional external execution route carried by a v1 request.
 * Legacy requests omit `execution_surface` and remain valid. When present,
 * the route is intentionally strict so a VC/remote/trusted request cannot be
 * silently reinterpreted as a local Python or Docker invocation.
 */
export function parseExecutionAdapterRoute(
	runtimeRequirements: Record<string, JsonValue>,
): ExecutionAdapterRouteValidation {
	const rawSurface = runtimeRequirements.execution_surface;
	if (rawSurface === undefined) return { valid: true, errors: [] };
	const errors: string[] = [];
	if (typeof rawSurface !== "string" || !EXECUTION_ADAPTER_SURFACES.includes(rawSurface as ExecutionAdapterSurface)) {
		errors.push("runtime_requirements.execution_surface must be vc, remote, or trusted");
		return { valid: false, errors };
	}
	const route = ROUTES[rawSurface as ExecutionAdapterSurface];
	const executorKind = runtimeRequirements.executor_kind;
	if (typeof executorKind !== "string" || !route.allowed_executor_kinds.includes(executorKind as ExecutorKind)) {
		errors.push(
			`runtime_requirements.executor_kind must be ${route.allowed_executor_kinds.join(" or ")} for execution_surface=${route.surface}`,
		);
	}
	if (route.surface === "vc") {
		// Resolve defaults in the site adapter before constructing the request;
		// carrying them here makes the queue/project decision reproducible.
		nonEmptyString(runtimeRequirements.vc_project, "runtime_requirements.vc_project", errors);
		nonEmptyString(runtimeRequirements.vc_partition, "runtime_requirements.vc_partition", errors);
		positiveInteger(runtimeRequirements.vc_gpus, "runtime_requirements.vc_gpus", errors);
		positiveInteger(runtimeRequirements.vc_memory_gb, "runtime_requirements.vc_memory_gb", errors);
		positiveInteger(runtimeRequirements.vc_cpus, "runtime_requirements.vc_cpus", errors);
	}
	return errors.length === 0 ? { valid: true, errors: [], route } : { valid: false, errors };
}

export function executionAdapterRouteFor(
	runtimeRequirements: Record<string, JsonValue>,
): ExecutionAdapterRoute | undefined {
	const parsed = parseExecutionAdapterRoute(runtimeRequirements);
	return parsed.valid ? parsed.route : undefined;
}
