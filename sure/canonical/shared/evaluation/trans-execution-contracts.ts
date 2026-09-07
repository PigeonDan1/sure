import type { ExecutionInputContract } from "../../../../packages/sure-core/src/execution/input-contract.ts";
import type { ExecutionDispatchCase } from "../../../../packages/sure-core/src/workflow/types.ts";

function transInputContract(selectors: ExecutionInputContract["selectors"]): ExecutionInputContract {
	return {
		schema: "sure.execution_input_contract.v1",
		context_artifact: "trans_input_resolved.json",
		selection: "exactly_one",
		selectors,
	};
}

function transDispatchCase(
	caseId: string,
	operationId: string,
	inputContract: ExecutionInputContract,
): ExecutionDispatchCase {
	const selector = inputContract.selectors.find((entry) => entry.selector_id === caseId);
	if (selector === undefined) throw new Error(`Missing TRANS input selector: ${caseId}`);
	return {
		case_id: caseId,
		match: selector.match,
		operation_id: operationId,
		input_contract: inputContract,
	};
}

export const SURE_TRANS_PYTHON_ADAPTER_INPUT_CONTRACT = transInputContract([
	{
		selector_id: "python-source",
		match: { source_kind: "python" },
		inputs: [
			{ input_id: "trans-input", locator_kind: "run_artifact", path: "trans_input_resolved.json", required: true },
			{ input_id: "adapter-manifest", locator_kind: "run_artifact", path: "adapter_manifest.json", required: true },
			{ input_id: "lockfile", locator_kind: "resolved_input_field", path: "lockfile", required: true },
		],
	},
]);

export const SURE_TRANS_DOCKER_ADAPTER_INPUT_CONTRACT = transInputContract([
	{
		selector_id: "docker-source",
		match: { source_kind: "docker" },
		inputs: [
			{ input_id: "trans-input", locator_kind: "run_artifact", path: "trans_input_resolved.json", required: true },
			{ input_id: "adapter-manifest", locator_kind: "run_artifact", path: "adapter_manifest.json", required: true },
			{ input_id: "source-image", locator_kind: "run_artifact", path: "source_image_result.json", required: true },
			{
				input_id: "model-payload",
				locator_kind: "run_artifact",
				path: "model_payload_manifest.json",
				required: true,
			},
			{ input_id: "adapter-dockerfile", locator_kind: "run_path", path: "adapter/Dockerfile.sure", required: true },
		],
	},
]);

export const SURE_TRANS_PYTHON_PACKAGE_INPUT_CONTRACT = transInputContract([
	{
		selector_id: "python-none",
		match: { source_kind: "python", package_profile: "none" },
		inputs: [
			{ input_id: "trans-input", locator_kind: "run_artifact", path: "trans_input_resolved.json", required: true },
			{ input_id: "adapter-manifest", locator_kind: "run_artifact", path: "adapter_manifest.json", required: true },
			{ input_id: "mcp-result", locator_kind: "run_artifact", path: "mcp_result.json", required: true },
			{ input_id: "lockfile", locator_kind: "resolved_input_field", path: "lockfile", required: true },
		],
	},
]);

export const SURE_TRANS_DOCKER_PACKAGE_INPUT_CONTRACT = transInputContract([
	{
		selector_id: "docker-registry",
		match: { source_kind: "docker", package_profile: "docker-registry" },
		inputs: [
			{ input_id: "trans-input", locator_kind: "run_artifact", path: "trans_input_resolved.json", required: true },
			{ input_id: "source-image", locator_kind: "run_artifact", path: "source_image_result.json", required: true },
			{
				input_id: "model-payload",
				locator_kind: "run_artifact",
				path: "model_payload_manifest.json",
				required: true,
			},
			{ input_id: "adapter-manifest", locator_kind: "run_artifact", path: "adapter_manifest.json", required: true },
			{ input_id: "adapter-image", locator_kind: "run_artifact", path: "adapter_image_result.json", required: true },
			{ input_id: "mcp-result", locator_kind: "run_artifact", path: "mcp_result.json", required: true },
			{ input_id: "adapter-dockerfile", locator_kind: "run_path", path: "adapter/Dockerfile.sure", required: true },
		],
	},
]);

export const SURE_TRANS_BUILD_ADAPTER_DISPATCH: readonly ExecutionDispatchCase[] = [
	transDispatchCase(
		"python-source",
		"sure.trans.execute_adapter_image.python",
		SURE_TRANS_PYTHON_ADAPTER_INPUT_CONTRACT,
	),
	transDispatchCase(
		"docker-source",
		"sure.trans.execute_adapter_image.docker",
		SURE_TRANS_DOCKER_ADAPTER_INPUT_CONTRACT,
	),
];

export const SURE_TRANS_PACKAGE_DISPATCH: readonly ExecutionDispatchCase[] = [
	transDispatchCase(
		"python-none",
		"sure.trans.execute_package_container.python",
		SURE_TRANS_PYTHON_PACKAGE_INPUT_CONTRACT,
	),
	transDispatchCase(
		"docker-registry",
		"sure.trans.execute_package_container.docker",
		SURE_TRANS_DOCKER_PACKAGE_INPUT_CONTRACT,
	),
];
