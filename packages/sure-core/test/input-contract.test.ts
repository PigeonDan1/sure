import { describe, expect, it } from "vitest";
import {
	bindExecutionInputs,
	type ExecutionInputContract,
	ExecutionInputContractError,
	executionInputBindingDigest,
	executionInputContractDigest,
	selectExecutionInputSelector,
	validateExecutionInputBinding,
	validateExecutionInputContract,
} from "../src/execution/input-contract.ts";

const contract: ExecutionInputContract = {
	schema: "sure.execution_input_contract.v1",
	context_artifact: "trans_input_resolved.json",
	selection: "exactly_one",
	selectors: [
		{
			selector_id: "python-none",
			match: { source_kind: "python", package_profile: "none" },
			inputs: [
				{ input_id: "resolved", locator_kind: "run_artifact", path: "trans_input_resolved.json", required: true },
				{ input_id: "lock", locator_kind: "resolved_input_field", path: "lockfile", required: true },
			],
		},
		{
			selector_id: "python-registry",
			match: { source_kind: "python", package_profile: "docker-registry" },
			inputs: [
				{ input_id: "resolved", locator_kind: "run_artifact", path: "trans_input_resolved.json", required: true },
				{ input_id: "adapter", locator_kind: "run_artifact", path: "adapter_manifest.json", required: true },
			],
		},
	],
};

describe("execution input contracts", () => {
	it("validates and selects one exact runtime branch", () => {
		expect(validateExecutionInputContract(contract)).toEqual({ valid: true, errors: [] });
		expect(
			selectExecutionInputSelector(contract, { source_kind: "python", package_profile: "none" }).selector_id,
		).toBe("python-none");
		expect(
			selectExecutionInputSelector(contract, { source_kind: "python", package_profile: "docker-registry" }).inputs,
		).toHaveLength(2);
	});

	it("rejects an unsupported profile instead of falling back to another branch", () => {
		expect(() =>
			selectExecutionInputSelector(contract, { source_kind: "docker", package_profile: "docker-registry" }),
		).toThrow(/has no selector/);
		try {
			selectExecutionInputSelector(contract, { source_kind: "docker", package_profile: "docker-registry" });
		} catch (error) {
			expect(error).toBeInstanceOf(ExecutionInputContractError);
			expect((error as ExecutionInputContractError).code).toBe("NO_MATCH");
		}
	});

	it("rejects overlapping selectors and path escapes", () => {
		const overlapping = {
			...contract,
			selectors: [
				...contract.selectors,
				{
					selector_id: "broad",
					match: { source_kind: "python" },
					inputs: contract.selectors[0].inputs,
				},
			],
		};
		expect(validateExecutionInputContract(overlapping).errors.join(" ")).toMatch(/overlap/);
		expect(
			validateExecutionInputContract({
				...contract,
				selectors: [
					{
						...contract.selectors[0],
						inputs: [
							{
								...contract.selectors[0].inputs[0],
								path: "../outside.json",
							},
						],
					},
				],
			}).valid,
		).toBe(false);
	});

	it("has a stable canonical digest", () => {
		expect(executionInputContractDigest(contract)).toBe(executionInputContractDigest(structuredClone(contract)));
	});

	it("binds multiple inputs in contract order and fails closed for missing required inputs", () => {
		const artifact = (artifactId: string, path: string) => ({
			artifact_id: artifactId,
			path: `/run/artifacts/${path}`,
			resolved_path: `/run/artifacts/${path}`,
			sha256: `sha256:${artifactId === "lock" ? "b" : "a"}`.padEnd(71, "0"),
			size: 1,
			media_type: "application/json",
			origin: "local_staging" as const,
			source_root: "/run",
		});
		const binding = bindExecutionInputs({
			contract,
			context: { source_kind: "python", package_profile: "none", lockfile: "/site/requirements.lock" },
			context_digest: `sha256:${"c".repeat(64)}`,
			resolver: {
				resolveRunArtifact: (path) => artifact(path.replace(".json", ""), path),
				resolveRunPath: () => undefined,
				resolveResolvedInputField: (_path, value) => artifact("lock", value.slice(1).replaceAll("/", "-")),
			},
		});
		expect(binding.selector_id).toBe("python-none");
		expect(binding.inputs.map((entry) => entry.input_id)).toEqual(["resolved", "lock"]);
		expect(binding.binding_digest).toBe(executionInputBindingDigest(binding));
		expect(
			validateExecutionInputBinding(
				binding,
				binding.inputs.map((entry) => entry.artifact),
			).valid,
		).toBe(true);

		expect(() =>
			bindExecutionInputs({
				contract,
				context: { source_kind: "python", package_profile: "none", lockfile: "/site/requirements.lock" },
				context_digest: `sha256:${"c".repeat(64)}`,
				resolver: {
					resolveRunArtifact: () => undefined,
					resolveRunPath: () => undefined,
					resolveResolvedInputField: () => undefined,
				},
			}),
		).toThrow(/required execution input/);
	});

	it("rejects a binding whose artifact mapping or digest was changed", () => {
		const binding = bindExecutionInputs({
			contract,
			context: { source_kind: "python", package_profile: "none", lockfile: "/site/requirements.lock" },
			context_digest: `sha256:${"d".repeat(64)}`,
			resolver: {
				resolveRunArtifact: (path) => ({
					artifact_id: "resolved",
					path: `/run/${path}`,
					resolved_path: `/run/${path}`,
					sha256: `sha256:${"e".repeat(64)}`,
					size: 1,
					media_type: "application/json",
					origin: "local_staging",
					source_root: "/run",
				}),
				resolveRunPath: () => undefined,
				resolveResolvedInputField: () => ({
					artifact_id: "lock",
					path: "/site/requirements.lock",
					resolved_path: "/site/requirements.lock",
					sha256: `sha256:${"f".repeat(64)}`,
					size: 1,
					media_type: "text/plain",
					origin: "read_only_reference",
					source_root: "/site",
					reference_snapshot_digest: `sha256:${"a".repeat(64)}`,
				}),
			},
		});
		const tampered = structuredClone(binding);
		tampered.inputs[0].artifact.sha256 = `sha256:${"0".repeat(64)}`;
		expect(
			validateExecutionInputBinding(
				tampered,
				binding.inputs.map((entry) => entry.artifact),
			).valid,
		).toBe(false);
		tampered.inputs[0].artifact.sha256 = binding.inputs[0].artifact.sha256;
		tampered.binding_digest = `sha256:${"0".repeat(64)}`;
		expect(validateExecutionInputBinding(tampered).errors.join(" ")).toMatch(/binding_digest/);
		const empty = structuredClone(binding);
		empty.inputs = [];
		empty.binding_digest = executionInputBindingDigest(empty);
		expect(validateExecutionInputBinding(empty).errors.join(" ")).toMatch(/must not be empty/);
	});
});
