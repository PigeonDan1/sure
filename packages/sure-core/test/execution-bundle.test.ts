import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
	canonicalJsonDigest,
	dispatchExecutor,
	type ExecutionContractBundle,
	type ExecutionContractRecord,
	type ExecutionLifecycle,
	type ExecutionReceipt,
	ExecutorRegistry,
	validateExecutionContractBundle,
	validateExecutionContractHistory,
} from "../src/index.ts";
import { FIXTURE_NOW, vcAdapterFixture } from "./external-adapter-fixture.ts";

interface BundleDifferentialCase {
	id: string;
	mutation: string;
	expected: {
		history_valid: boolean;
		outcome: string;
		reason_code: string;
		python_errors: boolean;
		pi_bundle: "absent";
		formal_eligible: false;
	};
}

const BUNDLE_DIFFERENTIAL_FIXTURE = JSON.parse(
	readFileSync(
		new URL("../../../sure/canonical/fixtures/execution-contract-bundle-differential.v1.json", import.meta.url),
		"utf8",
	),
) as {
	schema: string;
	version: number;
	rules: Record<string, boolean>;
	cases: BundleDifferentialCase[];
};

function registeredFixture() {
	const fixture = vcAdapterFixture();
	const registry = new ExecutorRegistry();
	registry.registerExternal(fixture.port, fixture.binding);
	return { fixture, registry };
}

function contractFor(bundle: Omit<ExecutionContractBundle, "contract">, alias: "latest" | "immutable") {
	const validation = validateExecutionContractBundle(bundle, {
		require_receipt: true,
		require_admission: bundle.admission !== undefined,
	});
	return {
		schema: "sure.execution_compatibility.v1",
		version: 1,
		request_path: `/${alias}/execution_request.json`,
		receipt_path: `/${alias}/execution_receipt.json`,
		...(bundle.admission === undefined ? {} : { admission_path: `/${alias}/execution_admission.json` }),
		request_digest: canonicalJsonDigest(bundle.request as never),
		...(bundle.receipt === undefined ? {} : { receipt_digest: canonicalJsonDigest(bundle.receipt as never) }),
		...(bundle.admission === undefined ? {} : { admission_digest: canonicalJsonDigest(bundle.admission as never) }),
		admission_instrumentation: bundle.admission === undefined ? "legacy-uninstrumented" : "admission-v1",
		contract_valid: validation.valid,
	};
}

function withContracts(
	latest: Omit<ExecutionContractBundle, "contract">,
	immutable: Omit<ExecutionContractBundle, "contract"> = structuredClone(latest),
): { latest: ExecutionContractBundle; immutable: ExecutionContractBundle } {
	return {
		latest: { ...latest, contract: contractFor(latest, "latest") },
		immutable: { ...immutable, contract: contractFor(immutable, "immutable") },
	};
}

describe("execution contract bundle audit", () => {
	it("revalidates request, receipt, admission, and contract digests together", async () => {
		const { fixture, registry } = registeredFixture();
		const dispatched = await dispatchExecutor(registry, fixture.request, { now: () => FIXTURE_NOW });
		if (!dispatched.receipt) throw new Error("fixture did not produce a receipt");
		const contract = {
			schema: "sure.execution_compatibility.v1",
			version: 1,
			request_digest: canonicalJsonDigest(fixture.request as never),
			receipt_digest: canonicalJsonDigest(dispatched.receipt as never),
			admission_digest: canonicalJsonDigest(dispatched.admission_trace as never),
			admission_instrumentation: "admission-v1",
			contract_valid: true,
		};
		const audited = validateExecutionContractBundle(
			{ request: fixture.request, receipt: dispatched.receipt, admission: dispatched.admission_trace, contract },
			{ require_receipt: true, require_admission: true, require_contract_record: true },
		);
		expect(audited.valid).toBe(true);
		expect(audited.errors).toEqual([]);
		expect(audited.outcome).toMatchObject({ reason_code: "VALIDATION_PENDING", workflow_disposition: "WAIT" });
	});

	it("rejects receipt/admission/contract tampering without changing lifecycle semantics", async () => {
		const { fixture, registry } = registeredFixture();
		const dispatched = await dispatchExecutor(registry, fixture.request, { now: () => FIXTURE_NOW });
		if (!dispatched.receipt) throw new Error("fixture did not produce a receipt");
		const contract = {
			schema: "sure.execution_compatibility.v1",
			version: 1,
			request_digest: canonicalJsonDigest(fixture.request as never),
			receipt_digest: canonicalJsonDigest(dispatched.receipt as never),
			admission_digest: canonicalJsonDigest(dispatched.admission_trace as never),
			admission_instrumentation: "admission-v1",
			contract_valid: true,
		};
		const tampered = validateExecutionContractBundle(
			{
				request: fixture.request,
				receipt: { ...dispatched.receipt, policy_digest: `sha256:${"f".repeat(64)}` },
				admission: { ...dispatched.admission_trace, request_digest: `sha256:${"e".repeat(64)}` },
				contract: { ...contract, receipt_digest: `sha256:${"d".repeat(64)}` },
			},
			{ require_receipt: true, require_admission: true, require_contract_record: true },
		);
		expect(tampered.valid).toBe(false);
		expect(tampered.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		expect(tampered.errors.join(" ")).toMatch(/receipt\.policy_digest|admission\.request_digest|contract .*digest/);
	});

	it("turns metadata-only tampering into an invalid-contract outcome", async () => {
		const { fixture, registry } = registeredFixture();
		const dispatched = await dispatchExecutor(registry, fixture.request, { now: () => FIXTURE_NOW });
		if (!dispatched.receipt) throw new Error("fixture did not produce a receipt");
		const audited = validateExecutionContractBundle(
			{
				request: fixture.request,
				receipt: dispatched.receipt,
				admission: dispatched.admission_trace,
				contract: {
					schema: "sure.execution_compatibility.v1",
					version: 1,
					request_digest: canonicalJsonDigest(fixture.request as never),
					receipt_digest: `sha256:${"0".repeat(64)}`,
					admission_digest: canonicalJsonDigest(dispatched.admission_trace as never),
					admission_instrumentation: "admission-v1",
					contract_valid: true,
				},
			},
			{ require_receipt: true, require_admission: true, require_contract_record: true },
		);
		expect(audited.valid).toBe(false);
		expect(audited.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
	});

	it.each([
		["FAILED", "EXECUTION_FAILED", "RETRY"],
		["PARTIAL", "EXECUTION_PARTIAL", "BLOCKED"],
		["CANCELLED", "EXECUTION_CANCELLED", "NOT_EXECUTED"],
	] as const)("preserves the %s lifecycle while auditing the whole bundle", async (lifecycle, reasonCode, outcome) => {
		const defaults = vcAdapterFixture();
		const fixture = vcAdapterFixture({
			execute: async (request) => {
				const receipt = (await defaults.port.execute(request)) as ExecutionReceipt;
				return {
					...receipt,
					lifecycle: lifecycle as ExecutionLifecycle,
					...(lifecycle === "CANCELLED" ? {} : { exit_code: 7 }),
				};
			},
		});
		const registry = new ExecutorRegistry();
		registry.registerExternal(fixture.port, fixture.binding);
		const dispatched = await dispatchExecutor(registry, fixture.request, { now: () => FIXTURE_NOW });
		if (!dispatched.receipt) throw new Error("fixture did not produce a receipt");
		const audited = validateExecutionContractBundle({
			request: fixture.request,
			receipt: dispatched.receipt,
			admission: dispatched.admission_trace,
		});
		expect(audited.valid).toBe(true);
		expect(audited.outcome).toMatchObject({ outcome, reason_code: reasonCode });
	});

	it("preserves valid rejected and capability-missing preflight records without executing", async () => {
		const policy = vcAdapterFixture();
		const policyRequest = structuredClone(policy.request);
		policyRequest.runtime_requirements.vc_project = "not-authorized";
		const policyRegistry = new ExecutorRegistry();
		policyRegistry.registerExternal(policy.port, policy.binding);
		const rejected = await dispatchExecutor(policyRegistry, policyRequest, { now: () => FIXTURE_NOW });
		const rejectedAudit = validateExecutionContractBundle(
			{ request: policyRequest, admission: rejected.admission_trace },
			{ require_receipt: true, require_admission: true },
		);
		expect(rejectedAudit.valid).toBe(true);
		expect(rejectedAudit.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });

		const missing = vcAdapterFixture();
		const missingResult = await dispatchExecutor(new ExecutorRegistry(), missing.request, { now: () => FIXTURE_NOW });
		const missingAudit = validateExecutionContractBundle(
			{ request: missing.request, admission: missingResult.admission_trace },
			{ require_receipt: true, require_admission: true },
		);
		expect(missingAudit.valid).toBe(true);
		expect(missingAudit.outcome).toMatchObject({ outcome: "NOT_EXECUTED", reason_code: "CAPABILITY_MISSING" });
	});

	it("allows an explicit capability-missing preflight without inventing a receipt", () => {
		const { fixture } = registeredFixture();
		const admission = {
			...fixture.request,
			status: "CAPABILITY_MISSING" as const,
			request_digest: canonicalJsonDigest(fixture.request as never),
			request_id: fixture.request.request_id,
			requested_executor_kind: "remote",
			execution_surface: "vc" as const,
			adapter_manifest_digest: fixture.request.adapter_manifest_digest,
			reason_code: "CAPABILITY_MISSING" as const,
			observed_at: FIXTURE_NOW,
			probe_invoked: false,
			execute_invoked: false,
			receipt_present: false,
			receipt_valid: false,
		};
		// Keep only the admission fields; the spread above intentionally makes
		// this test fail if a future refactor accidentally accepts extra fields.
		const trace = {
			schema: "sure.execution_admission.v1" as const,
			request_digest: admission.request_digest,
			request_id: admission.request_id,
			requested_executor_kind: admission.requested_executor_kind,
			execution_surface: admission.execution_surface,
			adapter_manifest_digest: admission.adapter_manifest_digest,
			status: admission.status,
			reason_code: admission.reason_code,
			observed_at: admission.observed_at,
			probe_invoked: admission.probe_invoked,
			execute_invoked: admission.execute_invoked,
			receipt_present: admission.receipt_present,
			receipt_valid: admission.receipt_valid,
		};
		const audited = validateExecutionContractBundle(
			{ request: fixture.request, admission: trace },
			{ require_receipt: true },
		);
		expect(audited.valid).toBe(true);
		expect(audited.outcome).toMatchObject({ reason_code: "CAPABILITY_MISSING", outcome: "NOT_EXECUTED" });
	});

	it("treats an unexplained missing receipt as invalid before checking contract_valid", () => {
		const { fixture } = registeredFixture();
		const audited = validateExecutionContractBundle(
			{
				request: fixture.request,
				contract: {
					schema: "sure.execution_compatibility.v1",
					version: 1,
					request_digest: canonicalJsonDigest(fixture.request as never),
					admission_instrumentation: "legacy-uninstrumented",
					contract_valid: false,
				},
			},
			{ require_receipt: true, require_contract_record: true },
		);
		expect(audited.valid).toBe(false);
		expect(audited.errors).toContain("execution receipt is required for final bundle validation");
		expect(audited.contract_errors).toEqual([]);
		expect(audited.outcome).toMatchObject({ reason_code: "INVALID_CONTRACT", outcome: "NOT_EXECUTED" });
	});

	it("keeps legacy uninstrumented bundles readable but rejectable by formal consumers", async () => {
		const { fixture, registry } = registeredFixture();
		const dispatched = await dispatchExecutor(registry, fixture.request, { now: () => FIXTURE_NOW });
		if (!dispatched.receipt) throw new Error("fixture did not produce a receipt");
		const base = {
			schema: "sure.execution_compatibility.v1",
			version: 1,
			request_digest: canonicalJsonDigest(fixture.request as never),
			receipt_digest: canonicalJsonDigest(dispatched.receipt as never),
			admission_instrumentation: "legacy-uninstrumented",
			contract_valid: true,
		};
		const compatible = validateExecutionContractBundle(
			{ request: fixture.request, receipt: dispatched.receipt, contract: base },
			{ require_receipt: true, require_contract_record: true },
		);
		expect(compatible.valid).toBe(true);
		const formal = validateExecutionContractBundle(
			{ request: fixture.request, receipt: dispatched.receipt, contract: base },
			{ require_receipt: true, require_contract_record: true, accept_legacy_uninstrumented: false },
		);
		expect(formal.valid).toBe(false);
		expect(formal.errors).toContain("legacy-uninstrumented execution contract is not accepted by this consumer");
	});

	it("replays the canonical latest/history differential cases without semantic drift", async () => {
		expect(BUNDLE_DIFFERENTIAL_FIXTURE.schema).toBe("sure.execution.contract_bundle_differential.v1");
		expect(BUNDLE_DIFFERENTIAL_FIXTURE.version).toBe(1);
		expect(BUNDLE_DIFFERENTIAL_FIXTURE.rules.history_is_derived_from_validated_request_id).toBe(true);
		expect(BUNDLE_DIFFERENTIAL_FIXTURE.rules.embedded_history_paths_are_authoritative).toBe(false);
		expect(BUNDLE_DIFFERENTIAL_FIXTURE.rules.compatibility_record_can_advance_workflow).toBe(false);

		const { fixture, registry } = registeredFixture();
		const dispatched = await dispatchExecutor(registry, fixture.request, { now: () => FIXTURE_NOW });
		if (!dispatched.receipt) throw new Error("fixture did not produce a receipt");
		const base = {
			request: fixture.request,
			receipt: dispatched.receipt,
			admission: dispatched.admission_trace,
		};

		for (const item of BUNDLE_DIFFERENTIAL_FIXTURE.cases) {
			let bundles: { latest: ExecutionContractBundle; immutable: ExecutionContractBundle };
			switch (item.mutation) {
				case "none":
					bundles = withContracts(base);
					break;
				case "capability_missing": {
					const missing = await dispatchExecutor(new ExecutorRegistry(), fixture.request, {
						now: () => FIXTURE_NOW,
					});
					bundles = withContracts({ request: fixture.request, admission: missing.admission_trace });
					break;
				}
				case "latest_request_policy_digest": {
					const latest = structuredClone(base);
					latest.request.policy_digest = `sha256:${"e".repeat(64)}`;
					bundles = withContracts(latest, base);
					break;
				}
				case "cancelled_receipt": {
					const receipt = structuredClone(base.receipt);
					delete receipt.exit_code;
					receipt.lifecycle = "CANCELLED";
					const cancelled = {
						...base,
						receipt,
					};
					bundles = withContracts(cancelled);
					break;
				}
				case "partial_receipt": {
					const partial = {
						...base,
						receipt: { ...base.receipt, lifecycle: "PARTIAL" as const, exit_code: 7 },
					};
					bundles = withContracts(partial);
					break;
				}
				case "escaped_output": {
					const escaped = {
						...base,
						admission: { ...base.admission, receipt_valid: false },
						receipt: {
							...base.receipt,
							outputs: [
								{
									artifact_id: "escaped",
									path: "/outside/result.json",
									resolved_path: "/outside/result.json",
									sha256: `sha256:${"a".repeat(64)}`,
									size: 1,
									media_type: "application/json",
									origin: "generated" as const,
									source_root: "/outside",
								},
							],
						},
					};
					bundles = withContracts(escaped);
					break;
				}
				case "latest_receipt_policy_digest": {
					const latest = {
						...base,
						receipt: { ...base.receipt, policy_digest: `sha256:${"d".repeat(64)}` },
					};
					bundles = withContracts(latest, base);
					break;
				}
				case "latest_contract_receipt_digest": {
					bundles = withContracts(base);
					bundles.latest = {
						...bundles.latest,
						contract: {
							...bundles.latest.contract,
							receipt_digest: `sha256:${"c".repeat(64)}`,
						} as ExecutionContractRecord,
					};
					break;
				}
				default:
					throw new Error(`unknown bundle mutation: ${item.mutation}`);
			}
			const audited = validateExecutionContractHistory(bundles, {
				require_receipt: true,
				require_admission: true,
				require_contract_record: true,
			});
			expect(audited.valid, item.id).toBe(item.expected.history_valid);
			expect(audited.outcome, item.id).toMatchObject({
				outcome: item.expected.outcome,
				reason_code: item.expected.reason_code,
			});
		}
	});

	it("allows only location aliases to differ between latest and immutable contract records", async () => {
		const { fixture, registry } = registeredFixture();
		const dispatched = await dispatchExecutor(registry, fixture.request, { now: () => FIXTURE_NOW });
		if (!dispatched.receipt) throw new Error("fixture did not produce a receipt");
		const bundles = withContracts({
			request: fixture.request,
			receipt: dispatched.receipt,
			admission: dispatched.admission_trace,
		});
		const valid = validateExecutionContractHistory(bundles, {
			require_receipt: true,
			require_admission: true,
		});
		expect(valid.valid).toBe(true);

		const tampered = validateExecutionContractHistory(
			{
				...bundles,
				immutable: {
					...bundles.immutable,
					contract: { ...bundles.immutable.contract, lifecycle: "FAILED" },
				},
			},
			{ require_receipt: true, require_admission: true },
		);
		expect(tampered.valid).toBe(false);
		expect(tampered.history_errors).toContain(
			"immutable execution contract metadata does not match latest execution contract metadata",
		);
		expect(tampered.outcome).toMatchObject({ reason_code: "INVALID_CONTRACT", outcome: "NOT_EXECUTED" });
	});
});
