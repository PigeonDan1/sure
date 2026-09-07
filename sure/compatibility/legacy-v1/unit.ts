import type { ExecutionOperation, WorkflowUnit } from "../../../packages/sure-core/src/index.ts";

export interface LegacyProjectedUnit {
	id: string;
	label: string;
	kind: "linear" | "gate";
	produces: string;
	schemaRef?: string;
	requiredFields?: string[];
	allowedValues?: Record<string, unknown[]>;
	forbiddenFields?: string[];
	gateScript?: string;
	validatorScriptId?: string;
	validatorScriptArgs?: string[];
	executionOperationId?: string;
	executionRequestOperation?: ExecutionOperation;
	gateScriptArgs?: () => string[];
	helperScripts?: string[];
	ownedScripts?: string[];
	gateInputs?: string[];
}

/** Project a pure Core unit into the camelCase shape consumed by legacy Pi hooks. */
export function projectUnit(unit: WorkflowUnit): LegacyProjectedUnit {
	const gateArgs = unit.gate?.script_args;
	return {
		id: unit.id,
		label: unit.label,
		kind: unit.kind,
		produces: unit.produces,
		...(unit.schema_ref === undefined ? {} : { schemaRef: unit.schema_ref }),
		...(unit.required_fields === undefined ? {} : { requiredFields: [...unit.required_fields] }),
		...(unit.allowed_values === undefined
			? {}
			: {
					allowedValues: Object.fromEntries(
						Object.entries(unit.allowed_values).map(([key, values]) => [key, [...values]]),
					),
				}),
		...(unit.forbidden_fields === undefined ? {} : { forbiddenFields: [...unit.forbidden_fields] }),
		...(unit.gate?.script_id === undefined ? {} : { gateScript: unit.gate.script_id }),
		...(unit.gate?.validator_script_id === undefined ? {} : { validatorScriptId: unit.gate.validator_script_id }),
		...(unit.gate?.validator_script_args === undefined
			? {}
			: { validatorScriptArgs: [...unit.gate.validator_script_args] }),
		...(unit.gate?.execution_operation_id === undefined
			? {}
			: { executionOperationId: unit.gate.execution_operation_id }),
		...(unit.gate?.execution_request_operation === undefined
			? {}
			: { executionRequestOperation: unit.gate.execution_request_operation }),
		...(gateArgs === undefined ? {} : { gateScriptArgs: () => [...gateArgs] }),
		...(unit.helper_scripts === undefined ? {} : { helperScripts: [...unit.helper_scripts] }),
		...(unit.owned_scripts === undefined ? {} : { ownedScripts: [...unit.owned_scripts] }),
		...(unit.gate?.gate_inputs === undefined ? {} : { gateInputs: [...unit.gate.gate_inputs] }),
	};
}

export function projectBranchUnits(units: readonly WorkflowUnit[]): LegacyProjectedUnit[] {
	return units.map(projectUnit);
}
