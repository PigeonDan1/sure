import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { HarnessRuntimeContract } from "./harness/resolve.ts";

const RUNTIME_BINDING_SCHEMA = "sure.skill.runtime_binding.v1";

interface RequiredRuntimeSlot {
	required: true;
	role: string;
	binding: Record<string, unknown>;
}

interface OmittedRuntimeSlot {
	required: false;
	reason: string;
}

export interface SkillRuntimeBindingOptions {
	runDir: string;
	skill: string;
	harnessRuntime: HarnessRuntimeContract;
	harnessRole: string;
	modelRuntimeReason: string;
	evaluationRuntime:
		| { binding: Record<string, unknown>; role: string }
		| { reason: string };
}

function harnessBinding(contract: HarnessRuntimeContract): Record<string, unknown> {
	return {
		...contract,
		schema: "sure.harness.runtime.binding.v1",
		runtime_type: "harness_python",
	};
}

/** Write the skill's complete three-runtime responsibility declaration atomically. */
export function writeSkillRuntimeBinding(options: SkillRuntimeBindingOptions): string {
	const evaluation: RequiredRuntimeSlot | OmittedRuntimeSlot =
		"binding" in options.evaluationRuntime
			? {
					required: true,
					role: options.evaluationRuntime.role,
					binding: options.evaluationRuntime.binding,
				}
			: { required: false, reason: options.evaluationRuntime.reason };
	const payload = {
		schema: RUNTIME_BINDING_SCHEMA,
		skill: options.skill,
		generated_at: new Date().toISOString(),
		runtimes: {
			harness: {
				required: true,
				role: options.harnessRole,
				binding: harnessBinding(options.harnessRuntime),
			},
			model: { required: false, reason: options.modelRuntimeReason },
			evaluation,
		},
	};
	const output = join(options.runDir, "artifacts", "runtime_binding.json");
	mkdirSync(dirname(output), { recursive: true });
	const temporary = `${output}.tmp`;
	writeFileSync(temporary, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");
	renameSync(temporary, output);
	return output;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readRecord(path: string): Record<string, unknown> | undefined {
	try {
		const payload: unknown = JSON.parse(readFileSync(path, "utf-8"));
		return isRecord(payload) ? payload : undefined;
	} catch {
		return undefined;
	}
}

function validateMaterializedBinding(binding: Record<string, unknown>, label: string): string | undefined {
	for (const key of ["runtime_id", "runtime_type", "python_executable", "manifest_path"]) {
		if (typeof binding[key] !== "string" || binding[key] === "") {
			return `${label} binding is missing ${key}`;
		}
	}
	const python = String(binding.python_executable);
	const manifestPath = String(binding.manifest_path);
	if (!existsSync(python) || !existsSync(manifestPath)) {
		return `${label} executable or runtime manifest is missing`;
	}
	const manifest = readRecord(manifestPath);
	if (!manifest) return `${label} runtime manifest is not valid JSON`;
	if (manifest.runtime_id !== binding.runtime_id) {
		return `${label} runtime_id differs from its materialized manifest`;
	}
	if (typeof binding.lock_sha256 === "string" && manifest.lock_sha256 !== binding.lock_sha256) {
		return `${label} lock_sha256 differs from its materialized manifest`;
	}
	return undefined;
}

function requiredSlot(value: unknown, label: string): { binding?: Record<string, unknown>; error?: string } {
	if (!isRecord(value) || value.required !== true || typeof value.role !== "string" || !isRecord(value.binding)) {
		return { error: `${label} runtime slot must be required and carry a role plus binding` };
	}
	return { binding: value.binding };
}

/** Validate a terminal runtime declaration and the materialized manifests it references. */
export function validateSkillRuntimeBinding(
	path: string,
	expectedSkill: string,
	evaluationRequired: boolean,
): string | undefined {
	const payload = readRecord(path);
	if (!payload) return "runtime_binding.json is missing or invalid JSON";
	if (payload.schema !== RUNTIME_BINDING_SCHEMA || payload.skill !== expectedSkill) {
		return `runtime binding must use ${RUNTIME_BINDING_SCHEMA} for ${expectedSkill}`;
	}
	if (!isRecord(payload.runtimes)) return "runtime binding has no runtimes object";
	const harness = requiredSlot(payload.runtimes.harness, "Harness");
	if (harness.error || !harness.binding) return harness.error;
	if (
		harness.binding.schema !== "sure.harness.runtime.binding.v1" ||
		harness.binding.runtime_type !== "harness_python"
	) {
		return "Harness runtime binding schema or type is invalid";
	}
	const harnessError = validateMaterializedBinding(harness.binding, "Harness");
	if (harnessError) return harnessError;

	const model = payload.runtimes.model;
	if (!isRecord(model) || model.required !== false || typeof model.reason !== "string" || model.reason === "") {
		return "Model runtime slot must explicitly declare why it is not required";
	}

	if (!evaluationRequired) {
		const evaluation = payload.runtimes.evaluation;
		if (
			!isRecord(evaluation) ||
			evaluation.required !== false ||
			typeof evaluation.reason !== "string" ||
			evaluation.reason === ""
		) {
			return "Evaluation runtime slot must explicitly declare why it is not required";
		}
		return undefined;
	}
	const evaluation = requiredSlot(payload.runtimes.evaluation, "Evaluation");
	if (evaluation.error || !evaluation.binding) return evaluation.error;
	if (
		evaluation.binding.schema !== "sure.evaluation.runtime.binding.v1" ||
		evaluation.binding.runtime_type !== "evaluation_python"
	) {
		return "Evaluation runtime binding schema or type is invalid";
	}
	if (evaluation.binding.harness_runtime_id !== harness.binding.runtime_id) {
		return "Evaluation Runtime is not bound to the declared Harness Runtime";
	}
	return validateMaterializedBinding(evaluation.binding, "Evaluation");
}
