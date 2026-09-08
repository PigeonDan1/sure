from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sure.runtime.execution_bridge import (
    artifact_ref,
    build_receipt,
    build_request,
    capability_evidence,
    create_execution_admission_trace,
    derive_execution_admission_trace,
    digest_json,
    digest_tree,
    output_set_digest,
    execution_outcome_projection,
    map_vc_job_result_to_receipt,
    project_execution_evidence,
    validate_adapter_route,
    validate_capability_evidence,
    validate_capability_evidence_list,
    validate_contract_bundle,
    validate_contract_pair,
    validate_execution_admission_binding,
    validate_execution_admission_receipt_binding,
    validate_execution_admission_trace,
    validate_output_binding,
    validate_output_contract,
    write_contract_bundle,
)


class ExecutionBridgeTests(unittest.TestCase):
    VC_EXECUTOR = {
        "executor_id": "sure.external.vc.mock",
        "executor_version": "1.0.0",
        "executor_digest_value": "sha256:" + "1" * 64,
        "executor_trust_level": "host_enforced",
    }

    def request(
        self,
        root: Path,
        *,
        requirements: list[dict] | None = None,
        runtime_requirements: dict | None = None,
        adapter_manifest_digest: str | None = None,
        policy_snapshot_digest: str | None = None,
        output_contract: dict | None = None,
    ) -> dict:
        return build_request(
            run_id="bridge-test",
            unit_id="execute_inference",
            operation="inference",
            entrypoint={"executable": "/usr/bin/python3", "argv": ["-c", "pass"]},
            output_root=root,
            subject={
                "bundle_manifest_path": str(root / "bundle.json"),
                "bundle_digest": digest_json({"bundle": 1}),
                "runtime_identity_digest": digest_json({"runtime": 1}),
            },
            capability_requirements=requirements or [],
            runtime_requirements=runtime_requirements,
            adapter_manifest_digest=adapter_manifest_digest,
            policy_snapshot_digest=policy_snapshot_digest,
            output_contract=output_contract,
            reference_snapshot_digest=digest_json({"inputs": []}),
            policy_digest=digest_json({"policy": 1}),
        )

    def test_success_receipt_is_bound_to_request_and_never_advances_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(root)
            receipt = build_receipt(
                request,
                lifecycle="SUCCEEDED",
                executor_kind="python",
                exit_code=0,
            )
            self.assertEqual(validate_contract_pair(request, receipt), [])
            contract = write_contract_bundle(root, request, receipt)
            self.assertTrue(contract["contract_valid"])
            self.assertEqual(contract["admission_instrumentation"], "legacy-uninstrumented")
            self.assertTrue((root / "execution_request.json").is_file())
            self.assertTrue((root / "execution_receipt.json").is_file())
            self.assertTrue((root / "execution_contract.json").is_file())

    def test_contract_bundle_consumer_rechecks_all_persisted_digests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(root)
            receipt = build_receipt(request, lifecycle="SUCCEEDED", executor_kind="python", exit_code=0)
            trace = derive_execution_admission_trace(request, receipt)
            contract = write_contract_bundle(root, request, receipt, admission_trace=trace)
            self.assertEqual(
                validate_contract_bundle(
                    request,
                    receipt,
                    trace,
                    contract,
                    require_receipt=True,
                    require_admission=True,
                ),
                [],
            )

            tampered = validate_contract_bundle(
                request,
                {**receipt, "policy_digest": digest_json({"tampered": True})},
                {**trace, "request_digest": digest_json({"tampered": True})},
                {**contract, "receipt_digest": digest_json({"tampered": True})},
                require_receipt=True,
                require_admission=True,
            )
            self.assertTrue(any("receipt.policy_digest" in error for error in tampered))
            self.assertTrue(any("admission.request_digest" in error for error in tampered))
            self.assertTrue(any("execution contract receipt_digest" in error for error in tampered))

    def test_contract_bundle_allows_explicit_missing_capability_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(root)
            trace = create_execution_admission_trace(
                request,
                observed_at="2026-01-01T00:00:00Z",
                outcome_reason_code="CAPABILITY_MISSING",
                probe_invoked=False,
                execute_invoked=False,
            )
            self.assertEqual(
                validate_contract_bundle(request, None, trace, require_receipt=True),
                [],
            )

    def test_contract_bundle_can_reject_legacy_instrumentation_for_formal_consumers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(root)
            receipt = build_receipt(request, lifecycle="SUCCEEDED", executor_kind="python", exit_code=0)
            contract = write_contract_bundle(root, request, receipt)
            errors = validate_contract_bundle(
                request,
                receipt,
                None,
                contract,
                require_receipt=True,
                accept_legacy_uninstrumented=False,
            )
            self.assertIn("legacy-uninstrumented execution contract is not accepted by this consumer", errors)

    def test_contract_bundle_preserves_non_success_lifecycles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(root)
            for lifecycle, exit_code in (("FAILED", 7), ("PARTIAL", 7), ("CANCELLED", None)):
                receipt = build_receipt(
                    request,
                    lifecycle=lifecycle,
                    executor_kind="python",
                    exit_code=exit_code,
                )
                trace = derive_execution_admission_trace(request, receipt)
                contract_root = root / lifecycle.lower()
                contract = write_contract_bundle(
                    contract_root,
                    request,
                    receipt,
                    admission_trace=trace,
                )
                self.assertEqual(
                    validate_contract_bundle(
                        request,
                        receipt,
                        trace,
                        contract,
                        require_receipt=True,
                        require_admission=True,
                    ),
                    [],
                    lifecycle,
                )

    def test_missing_capability_is_not_a_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(
                root,
                requirements=[
                    {
                        "capability_id": "sure.execution.gpu",
                        "capability_class": "execution_capability",
                        "required": True,
                    }
                ],
            )
            receipt = build_receipt(
                request,
                lifecycle="NOT_STARTED",
                executor_kind="docker",
                capability_evidence_values=[
                    capability_evidence("sure.execution.gpu", status="MISSING", details={"probe": "nvidia-smi"})
                ],
                diagnostics=[{"code": "CAPABILITY_MISSING", "message": "GPU is unavailable"}],
            )
            errors = validate_contract_pair(request, receipt)
            self.assertTrue(any("sure.execution.gpu" in error for error in errors))
            self.assertNotEqual(receipt["lifecycle"], "SUCCEEDED")

    def test_admission_trace_distinguishes_preflight_from_legacy_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(root)
            trace = create_execution_admission_trace(
                request,
                observed_at="2026-01-01T00:00:00Z",
                outcome_reason_code="VALIDATION_PENDING",
                probe_invoked=True,
                execute_invoked=True,
            )
            self.assertEqual(trace["schema"], "sure.execution_admission.v1")
            self.assertEqual(trace["status"], "ADMITTED")
            self.assertFalse(trace["receipt_present"])
            self.assertFalse(trace["receipt_valid"])
            self.assertEqual(validate_execution_admission_trace(trace), [])
            self.assertEqual(validate_execution_admission_binding(request, trace), [])
            direct = create_execution_admission_trace(
                request,
                observed_at="2026-01-01T00:00:00Z",
                outcome_reason_code="VALIDATION_PENDING",
                probe_invoked=False,
                execute_invoked=True,
            )
            self.assertEqual(direct["status"], "ADMITTED")

    def test_admission_trace_rejects_forged_receipt_flag_and_rebinding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(root)
            missing = create_execution_admission_trace(
                request,
                observed_at="2026-01-01T00:00:00Z",
                outcome_reason_code="CAPABILITY_MISSING",
                probe_invoked=False,
                execute_invoked=False,
            )
            rejected = create_execution_admission_trace(
                request,
                observed_at="2026-01-01T00:00:00Z",
                outcome_reason_code="INVALID_CONTRACT",
                probe_invoked=False,
                execute_invoked=False,
            )
            self.assertEqual(missing["status"], "CAPABILITY_MISSING")
            self.assertEqual(rejected["status"], "REJECTED")
            forged = {**missing, "receipt_valid": True, "receipt_present": False}
            self.assertIn("admission.receipt_valid requires receipt_present", validate_execution_admission_trace(forged))
            self.assertIn(
                "ADMITTED admission requires probe_invoked or execute_invoked",
                validate_execution_admission_trace({**missing, "status": "ADMITTED"}),
            )
            self.assertIn(
                "REJECTED admission cannot invoke execute",
                validate_execution_admission_trace({**missing, "status": "REJECTED", "execute_invoked": True}),
            )
            rebound = {**missing, "request_digest": digest_json({"forged": True})}
            self.assertIn("admission.request_digest does not match request", validate_execution_admission_binding(request, rebound))
            bound_request = self.request(
                root,
                runtime_requirements={"executor_kind": "remote", "execution_surface": "remote"},
                adapter_manifest_digest=digest_json({"manifest": True}),
            )
            bound = create_execution_admission_trace(
                bound_request,
                observed_at="2026-01-01T00:00:00Z",
                outcome_reason_code="INVALID_CONTRACT",
                probe_invoked=False,
                execute_invoked=False,
            )
            self.assertIn(
                "admission.request_id is missing from request binding",
                validate_execution_admission_binding(bound_request, {**bound, "request_id": None}),
            )
            self.assertIn(
                "admission.requested_executor_kind is missing from request binding",
                validate_execution_admission_binding(bound_request, {**bound, "requested_executor_kind": None}),
            )
            self.assertIn(
                "admission.execution_surface is missing from request binding",
                validate_execution_admission_binding(bound_request, {**bound, "execution_surface": None}),
            )
            self.assertIn(
                "admission.adapter_manifest_digest is missing from request binding",
                validate_execution_admission_binding(bound_request, {**bound, "adapter_manifest_digest": None}),
            )

    def test_admission_trace_binds_receipt_lifecycle_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(root)
            receipt = build_receipt(request, lifecycle="SUCCEEDED", executor_kind="python", exit_code=0)
            trace = create_execution_admission_trace(
                request,
                observed_at="2026-01-01T00:00:00Z",
                outcome_reason_code="VALIDATION_PENDING",
                probe_invoked=True,
                execute_invoked=True,
                receipt=receipt,
                receipt_valid=True,
            )
            self.assertEqual(
                validate_execution_admission_receipt_binding(
                    request,
                    trace,
                    receipt=receipt,
                    receipt_valid=True,
                    capability_admitted=True,
                ),
                [],
            )
            forged = {**trace, "status": "CAPABILITY_MISSING"}
            errors = validate_execution_admission_receipt_binding(
                request,
                forged,
                receipt=receipt,
                receipt_valid=True,
                capability_admitted=False,
            )
            self.assertIn("CAPABILITY_MISSING admission cannot invoke execute", errors)
            self.assertIn("successful receipt requires ADMITTED admission status", errors)
            self.assertIn("successful receipt cannot have missing capability", errors)
            self.assertIn(
                "admission.status CAPABILITY_MISSING conflicts with an admitted capability result",
                validate_execution_admission_receipt_binding(
                    request,
                    forged,
                    receipt=receipt,
                    receipt_valid=True,
                    capability_admitted=True,
                ),
            )

    def test_contract_bundle_persists_admission_trace_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(root)
            receipt = build_receipt(request, lifecycle="NOT_STARTED", executor_kind="python")
            trace = create_execution_admission_trace(
                request,
                observed_at="2026-01-01T00:00:00Z",
                outcome_reason_code="CAPABILITY_MISSING",
                probe_invoked=False,
                execute_invoked=False,
                receipt=receipt,
                receipt_valid=True,
            )
            contract = write_contract_bundle(root, request, receipt, admission_trace=trace)
            self.assertTrue(contract["contract_valid"])
            self.assertEqual(contract["admission_instrumentation"], "admission-v1")
            self.assertEqual(json.loads((root / "execution_admission.json").read_text(encoding="utf-8")), trace)
            self.assertTrue((root / "execution_contracts" / f"{request['request_id']}.admission.json").is_file())
            self.assertEqual(contract["admission_digest"], digest_json(trace))

            forged = {**trace, "request_digest": digest_json({"forged": True})}
            invalid_root = root / "invalid"
            invalid_root.mkdir()
            invalid_request = {**request, "request_id": "bridge-test-invalid"}
            invalid = write_contract_bundle(invalid_root, invalid_request, receipt, admission_trace=forged)
            self.assertFalse(invalid["contract_valid"])
            self.assertIn("admission.request_digest does not match request", invalid["diagnostics"])

            success_receipt = build_receipt(request, lifecycle="SUCCEEDED", executor_kind="python", exit_code=0)
            success_trace = derive_execution_admission_trace(request, success_receipt)
            forged_success = {**success_trace, "status": "CAPABILITY_MISSING"}
            invalid_success_root = root / "invalid-success"
            invalid_success_root.mkdir()
            invalid_success = write_contract_bundle(
                invalid_success_root,
                request,
                success_receipt,
                admission_trace=forged_success,
            )
            self.assertFalse(invalid_success["contract_valid"])
            self.assertIn("successful receipt requires ADMITTED admission status", invalid_success["diagnostics"])

    def test_legacy_admission_derivation_is_conservative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(root)
            missing_receipt = build_receipt(
                request,
                lifecycle="NOT_STARTED",
                executor_kind="python",
                diagnostics=[{"code": "CAPABILITY_MISSING", "message": "python is unavailable"}],
            )
            missing = derive_execution_admission_trace(request, missing_receipt)
            self.assertEqual(missing["status"], "CAPABILITY_MISSING")
            self.assertFalse(missing["execute_invoked"])
            self.assertTrue(missing["receipt_valid"])

            success_receipt = build_receipt(request, lifecycle="SUCCEEDED", executor_kind="python", exit_code=0)
            success = derive_execution_admission_trace(request, success_receipt)
            self.assertEqual(success["status"], "ADMITTED")
            self.assertTrue(success["execute_invoked"])
            self.assertTrue(success["receipt_valid"])

            spawn_failure = build_receipt(
                request,
                lifecycle="NOT_STARTED",
                executor_kind="python",
                diagnostics=[{"code": "EXECUTOR_SPAWN_FAILED", "message": "spawn failed"}],
            )
            attempted = derive_execution_admission_trace(request, spawn_failure)
            self.assertEqual(attempted["status"], "ADMITTED")
            self.assertTrue(attempted["execute_invoked"])

    def test_external_adapter_route_is_mirrored_and_fail_closed(self) -> None:
        self.assertEqual(
            validate_adapter_route(
                {
                    "execution_surface": "vc",
                    "executor_kind": "remote",
                    "vc_project": "sure-test",
                    "vc_partition": "gpu-test",
                    "vc_gpus": 1,
                }
            ),
            [],
        )
        self.assertIn(
            "runtime_requirements.executor_kind must be remote for execution_surface=remote",
            validate_adapter_route({"execution_surface": "remote", "executor_kind": "python"}),
        )
        self.assertIn(
            "runtime_requirements.vc_partition must be a non-empty string",
            validate_adapter_route({"execution_surface": "vc", "executor_kind": "remote", "vc_project": "sure-test"}),
        )
        self.assertEqual(
            validate_adapter_route({"executor_kind": "remote"}),
            ["external executor kind requires runtime_requirements.execution_surface"],
        )
        self.assertIn(
            "runtime_requirements.adapter_timeouts.wait_seconds exceeds the maximum allowed value",
            validate_adapter_route(
                {
                    "execution_surface": "remote",
                    "executor_kind": "remote",
                    "adapter_timeouts": {"wait_seconds": 604801},
                }
            ),
        )
        self.assertEqual(
            validate_adapter_route(
                {
                    "execution_surface": "remote",
                    "executor_kind": "remote",
                    "adapter_timeouts": {"wait_seconds": 10, "command_seconds": 604801},
                }
            ),
            [
                "runtime_requirements.adapter_timeouts.command_seconds exceeds the maximum allowed value"
            ],
        )

    def test_contract_pair_rejects_invalid_external_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(root)
            request["runtime_requirements"] = {
                "execution_surface": "vc",
                "executor_kind": "python",
                "vc_project": "sure-test",
                "vc_partition": "gpu-test",
            }
            receipt = build_receipt(request, lifecycle="FAILED", executor_kind="python", exit_code=23)
            errors = validate_contract_pair(request, receipt)
            self.assertIn(
                "runtime_requirements.executor_kind must be remote or trusted for execution_surface=vc",
                errors,
            )

    def test_external_contract_requires_policy_and_adapter_manifest_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(root)
            request["runtime_requirements"] = {
                "execution_surface": "vc",
                "executor_kind": "remote",
                "vc_project": "sure-test",
                "vc_partition": "gpu-test",
            }
            receipt = build_receipt(request, lifecycle="FAILED", executor_kind="remote", exit_code=23)
            errors = validate_contract_pair(request, receipt)
            self.assertIn("external execution requires request.policy_snapshot_digest", errors)
            self.assertIn("external execution requires request.adapter_manifest_digest", errors)

    def test_external_differential_projections_match_canonical_fixture(self) -> None:
        fixture_path = Path(__file__).resolve().parents[2] / "sure" / "canonical" / "fixtures" / "external-adapter-differential-traces.v1.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(fixture["schema"], "sure.execution.external_adapter_differential_traces.v1")
        self.assertEqual(len(fixture["cases"]), 5)
        for item in fixture["cases"]:
            surectl = item["surectl"]
            admission = item["admission"]
            self.assertIn(admission["status"], {"ADMITTED", "CAPABILITY_MISSING", "REJECTED"})
            self.assertIsInstance(admission["probe_invoked"], bool)
            self.assertIsInstance(admission["execute_invoked"], bool)
            self.assertIsInstance(admission["receipt_present"], bool)
            self.assertIsInstance(admission["receipt_valid"], bool)
            self.assertFalse(admission["receipt_valid"] and not admission["receipt_present"])
            self.assertEqual(
                project_execution_evidence(
                    lifecycle=surectl["receipt_lifecycle"],
                    receipt_valid=surectl["receipt_valid"],
                    capability_admitted=surectl["capability_admitted"],
                    outcome_reason_code=item["canonical"]["reason_code"],
                ),
                {
                    "verdict": item["python"]["evidence_verdict"],
                    "reason_code": item["python"]["evidence_reason_code"],
                },
            )
            if item["id"] in {"admitted_executor_failure", "success_waits_for_validation"}:
                self.assertEqual(
                    execution_outcome_projection(item["canonical"]["receipt_lifecycle"]),
                    {
                        "validator_verdict": item["canonical"]["validator_verdict"],
                        "workflow_disposition": item["canonical"]["workflow_disposition"],
                        "outcome": item["canonical"]["outcome"],
                        "reason_code": item["canonical"]["reason_code"],
                        "execution_lifecycle": item["canonical"]["receipt_lifecycle"],
                    },
                )

    def test_external_differential_contracts_keep_policy_and_receipt_tamper_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = digest_json({"site": "sure-test"})
            adapter = digest_json({"adapter": "sure-test"})
            request = build_request(
                run_id="external-differential",
                unit_id="execute",
                operation="inference",
                entrypoint={"executable": "adapter-entrypoint", "argv": []},
                output_root=root,
                policy_digest=digest_json({"policy": 1}),
                policy_snapshot_digest=snapshot,
                adapter_manifest_digest=adapter,
                reference_snapshot_digest=digest_json({"inputs": []}),
                runtime_requirements={
                    "execution_surface": "vc",
                    "executor_kind": "remote",
                    "vc_project": "sure-test",
                    "vc_partition": "gpu-test",
                },
            )
            failed = build_receipt(request, lifecycle="FAILED", executor_kind="remote", exit_code=7)
            self.assertEqual(validate_contract_pair(request, failed), [])
            self.assertEqual(
                project_execution_evidence(
                    lifecycle=failed["lifecycle"],
                    receipt_valid=True,
                    capability_admitted=True,
                    outcome_reason_code="EXECUTION_FAILED",
                ),
                {"verdict": "FAIL", "reason_code": "EXECUTION_FAILED"},
            )

            tampered = build_receipt(request, lifecycle="SUCCEEDED", executor_kind="remote", exit_code=0)
            tampered["adapter_manifest_digest"] = digest_json({"adapter": "forged"})
            self.assertIn("receipt.adapter_manifest_digest does not match request", validate_contract_pair(request, tampered))
            self.assertEqual(
                project_execution_evidence(
                    lifecycle=tampered["lifecycle"],
                    receipt_valid=False,
                    capability_admitted=True,
                    outcome_reason_code="INVALID_CONTRACT",
                ),
                {"verdict": "NOT_EXECUTED", "reason_code": "INVALID_CONTRACT"},
            )

            drifted = dict(request)
            drifted["runtime_requirements"] = {
                "execution_surface": "vc",
                "executor_kind": "python",
                "vc_project": "sure-test",
                "vc_partition": "gpu-test",
            }
            drifted_receipt = build_receipt(drifted, lifecycle="NOT_STARTED", executor_kind="python")
            self.assertTrue(
                any(
                    "runtime_requirements.executor_kind must be remote or trusted" in error
                    for error in validate_contract_pair(drifted, drifted_receipt)
                )
            )

    def test_build_request_and_receipt_carry_external_binding_digests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = digest_json({"site": "sure-test"})
            adapter_manifest = digest_json({"adapter": "sure-test"})
            request = build_request(
                run_id="bridge-test",
                unit_id="execute",
                operation="inference",
                entrypoint={"executable": "/usr/bin/python3", "argv": ["-c", "pass"]},
                output_root=root,
                policy_digest=digest_json({"policy": 1}),
                policy_snapshot_digest=snapshot,
                adapter_manifest_digest=adapter_manifest,
                reference_snapshot_digest=digest_json({"inputs": []}),
                runtime_requirements={"execution_surface": "remote", "executor_kind": "remote"},
            )
            self.assertEqual(request["policy_snapshot_digest"], snapshot)
            self.assertEqual(request["adapter_manifest_digest"], adapter_manifest)
            receipt = build_receipt(request, lifecycle="FAILED", executor_kind="remote", exit_code=23)
            self.assertEqual(receipt["policy_snapshot_digest"], snapshot)
            self.assertEqual(receipt["adapter_manifest_digest"], adapter_manifest)
            self.assertEqual(validate_contract_pair(request, receipt), [])

    def test_build_request_does_not_coerce_an_invalid_policy_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = build_request(
                run_id="bridge-test",
                unit_id="execute",
                operation="inference",
                entrypoint={"executable": "/usr/bin/python3", "argv": ["-c", "pass"]},
                output_root=root,
                policy_digest=digest_json({"policy": 1}),
                policy_snapshot_digest="not-a-digest",
                adapter_manifest_digest=digest_json({"adapter": "sure-test"}),
                reference_snapshot_digest=digest_json({"inputs": []}),
                runtime_requirements={"execution_surface": "remote", "executor_kind": "remote"},
            )
            self.assertEqual(request["policy_snapshot_digest"], "not-a-digest")
            receipt = build_receipt(request, lifecycle="FAILED", executor_kind="remote", exit_code=23)
            errors = validate_contract_pair(request, receipt)
            self.assertTrue(any("policy_snapshot_digest" in error for error in errors))

    def test_external_receipt_snapshot_must_match_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = digest_json({"site": "sure-test"})
            request = self.request(root)
            request["policy_snapshot_digest"] = snapshot
            request["adapter_manifest_digest"] = digest_json({"adapter": "sure-test"})
            request["runtime_requirements"] = {
                "execution_surface": "remote",
                "executor_kind": "remote",
            }
            receipt = build_receipt(request, lifecycle="FAILED", executor_kind="remote", exit_code=23)
            receipt["policy_snapshot_digest"] = digest_json({"site": "forged"})
            errors = validate_contract_pair(request, receipt)
            self.assertIn("receipt.policy_snapshot_digest does not match request", errors)

    def test_external_receipt_adapter_manifest_must_match_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(root)
            request["policy_snapshot_digest"] = digest_json({"site": "sure-test"})
            request["adapter_manifest_digest"] = digest_json({"adapter": "sure-test"})
            request["runtime_requirements"] = {
                "execution_surface": "remote",
                "executor_kind": "remote",
            }
            receipt = build_receipt(request, lifecycle="FAILED", executor_kind="remote", exit_code=23)
            receipt["adapter_manifest_digest"] = digest_json({"adapter": "forged"})
            errors = validate_contract_pair(request, receipt)
            self.assertIn("receipt.adapter_manifest_digest does not match request", errors)

    def test_capability_evidence_rejects_arbitrary_authority_sources(self) -> None:
        evidence = capability_evidence("sure.execution.gpu", status="AVAILABLE")
        evidence["source"] = "remote_daemon"
        self.assertIn("capability_evidence.source is invalid", validate_capability_evidence(evidence))
        self.assertIn(
            "receipt.capability_evidence[0].source is invalid",
            validate_capability_evidence_list([evidence], field="receipt.capability_evidence"),
        )

    def test_contract_pair_rejects_agent_execution_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(
                root,
                requirements=[
                    {
                        "capability_id": "sure.execution.gpu",
                        "capability_class": "execution_capability",
                        "required": True,
                    }
                ],
            )
            receipt = build_receipt(
                request,
                lifecycle="SUCCEEDED",
                executor_kind="python",
                exit_code=0,
                capability_evidence_values=[
                    {
                        **capability_evidence("sure.execution.gpu", status="AVAILABLE"),
                        "source": "agent",
                    }
                ],
            )
            errors = validate_contract_pair(request, receipt)
            self.assertTrue(any("source agent cannot satisfy" in error for error in errors))

    def test_receipt_digest_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(root)
            receipt = build_receipt(request, lifecycle="FAILED", executor_kind="python", exit_code=23)
            receipt["request_digest"] = digest_json({"forged": True})
            errors = validate_contract_pair(request, receipt)
            self.assertIn("receipt.request_digest does not match canonical request digest", errors)

    def test_reference_artifact_carries_snapshot_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.json"
            reference.write_text("{}\n", encoding="utf-8")
            artifact = artifact_ref(
                reference,
                origin="read_only_reference",
                source_root=root,
                reference_snapshot_digest=digest_json({"snapshot": 1}),
            )
            self.assertEqual(artifact["origin"], "read_only_reference")
            self.assertTrue(str(artifact["reference_snapshot_digest"]).startswith("sha256:"))
            self.assertNotIn("kind", artifact)
            self.assertNotIn("digest_kind", artifact)

    def test_fixed_views_are_aliases_and_history_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.request(root)
            second = self.request(root)
            second["request_id"] = "bridge-test-second"
            write_contract_bundle(
                root,
                first,
                build_receipt(first, lifecycle="SUCCEEDED", executor_kind="python", exit_code=0),
            )
            write_contract_bundle(
                root,
                second,
                build_receipt(second, lifecycle="SUCCEEDED", executor_kind="python", exit_code=0),
            )
            history = root / "execution_contracts"
            self.assertEqual(len(list(history.glob("*.request.json"))), 2)
            self.assertEqual(len(list(history.glob("*.receipt.json"))), 2)
            latest = json.loads((root / "execution_request.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["request_id"], "bridge-test-second")

    def test_vc_job_result_mapping_is_fail_closed_for_timeout_capability_and_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(
                root,
                requirements=[
                    {"capability_id": "sure.execution.remote", "capability_class": "execution_capability", "required": True},
                    {"capability_id": "sure.execution.vc", "capability_class": "execution_capability", "required": True},
                ],
                runtime_requirements={
                    "execution_surface": "vc",
                    "executor_kind": "remote",
                    "vc_project": "project",
                    "vc_partition": "gpu-test",
                },
                adapter_manifest_digest=digest_json({"manifest": "vc"}),
                policy_snapshot_digest=digest_json({"snapshot": "site"}),
            )
            base = {
                "job_id": "job-123",
                "partition": "gpu-test",
                "submit_command": ["vc", "submit"],
                "duration_ms": 12.5,
                "timed_out": False,
                "log_dir": str(root / "logs"),
                "vc_diagnostics": "info",
                "exit_code": 0,
            }
            success = map_vc_job_result_to_receipt(
                request, base, observed_at="2026-01-01T00:00:00Z", **self.VC_EXECUTOR
            )
            self.assertEqual(success["lifecycle"], "SUCCEEDED")
            self.assertEqual(validate_contract_pair(request, success), [])

            failed = map_vc_job_result_to_receipt(request, {**base, "exit_code": 17}, **self.VC_EXECUTOR)
            self.assertEqual(failed["lifecycle"], "FAILED")
            self.assertEqual(validate_contract_pair(request, failed), [])

            timeout = map_vc_job_result_to_receipt(
                request,
                {**base, "exit_code": None, "timed_out": True},
                cancellation_confirmed=False,
                **self.VC_EXECUTOR,
            )
            self.assertEqual(timeout["lifecycle"], "CANCELLED")
            timeout_codes = {item["code"] for item in timeout["diagnostics"]}
            self.assertIn("EXECUTOR_TIMEOUT", timeout_codes)
            self.assertIn("CANCEL_UNCONFIRMED", timeout_codes)
            self.assertEqual(validate_contract_pair(request, timeout), [])

            missing = map_vc_job_result_to_receipt(
                request, base, capability_available=False, **self.VC_EXECUTOR
            )
            self.assertEqual(missing["lifecycle"], "NOT_STARTED")
            self.assertIn("CAPABILITY_MISSING", {item["code"] for item in missing["diagnostics"]})
            self.assertTrue(any("required capability" in error for error in validate_contract_pair(request, missing)))

            missing_before_submit = map_vc_job_result_to_receipt(
                request,
                {"exit_code": None, "timed_out": False},
                capability_available=False,
                submitted=False,
                **self.VC_EXECUTOR,
            )
            self.assertEqual(missing_before_submit["lifecycle"], "NOT_STARTED")
            self.assertNotIn("job_id", next(item for item in missing_before_submit["diagnostics"] if item["code"] == "VC_JOB_METADATA")["details"])
            with self.assertRaises(ValueError):
                map_vc_job_result_to_receipt(
                    request, {"exit_code": 0, "timed_out": False}, **self.VC_EXECUTOR
                )

            escaped = map_vc_job_result_to_receipt(
                request,
                base,
                **self.VC_EXECUTOR,
                outputs=[
                    {
                        "artifact_id": "escaped",
                        "path": "/outside/result.json",
                        "resolved_path": "/outside/result.json",
                        "sha256": "sha256:" + "a" * 64,
                        "size": 1,
                        "media_type": "application/json",
                        "origin": "generated",
                        "source_root": "/outside",
                    }
                ],
            )
            self.assertTrue(any("escapes output root" in error for error in validate_contract_pair(request, escaped)))

    def test_vc_mapping_fixture_is_replayed_without_semantic_drift(self) -> None:
        fixture_path = Path(__file__).resolve().parents[1] / "canonical" / "fixtures" / "external-adapter-receipt-mapping.v1.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path("/tmp/sure-mapping")
            profile = fixture["request_profile"]
            request = self.request(
                root,
                requirements=[
                    {"capability_id": capability_id, "capability_class": "execution_capability", "required": True}
                    for capability_id in profile["capability_ids"]
                ],
                runtime_requirements={
                    "execution_surface": profile["execution_surface"],
                    "executor_kind": profile["executor_kind"],
                    "vc_project": "project",
                    "vc_partition": "gpu-test",
                },
                adapter_manifest_digest=profile["adapter_manifest_digest"],
                policy_snapshot_digest=profile["policy_snapshot_digest"],
                output_contract=profile["output_contract"],
            )
            for case in fixture["cases"]:
                result = map_vc_job_result_to_receipt(
                    request,
                    case["result"],
                    executor_id=profile["executor"]["executor_id"],
                    executor_version=profile["executor"]["version"],
                    executor_digest_value=profile["executor"]["digest"],
                    executor_trust_level=profile["executor"]["trust_level"],
                    capability_available=case.get("capability_available", True),
                    submitted=case.get("submitted", True),
                    cancellation_confirmed=case.get("cancellation_confirmed"),
                    outputs=case.get("outputs", []),
                    residuals=case.get("residuals", []),
                )
                expected = case["expected"]
                self.assertEqual(result["lifecycle"], expected["lifecycle"], case["id"])
                codes = {item["code"] for item in result.get("diagnostics", [])}
                self.assertTrue(set(expected["diagnostic_codes"]).issubset(codes), case["id"])
                contract_valid = not validate_contract_pair(request, result)
                self.assertEqual(contract_valid, expected["contract_valid"], case["id"])
                metadata = next(item for item in result["diagnostics"] if item["code"] == "VC_JOB_METADATA")
                self.assertTrue(str(metadata["details"]["result_digest"]).startswith("sha256:"), case["id"])
                self.assertTrue(str(metadata["details"]["request_runtime_digest"]).startswith("sha256:"), case["id"])
                self.assertTrue(str(metadata["details"]["runtime_identity_digest"]).startswith("sha256:"), case["id"])
                if case["id"] == "timeout-cancel-unconfirmed":
                    self.assertIs(metadata["details"]["cancellation_confirmed"], False)

    def test_vc_mapping_requires_external_identity_and_normalized_result_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(
                root,
                runtime_requirements={
                    "execution_surface": "vc",
                    "executor_kind": "remote",
                    "vc_project": "project",
                    "vc_partition": "gpu-test",
                },
                adapter_manifest_digest=digest_json({"manifest": "vc"}),
                policy_snapshot_digest=digest_json({"snapshot": "site"}),
            )
            result = {"job_id": "job", "partition": "gpu-test", "exit_code": 0, "timed_out": False}
            with self.assertRaisesRegex(ValueError, "explicit executor_id"):
                map_vc_job_result_to_receipt(request, result, executor_digest_value=digest_json({"e": 1}))
            with self.assertRaisesRegex(ValueError, "timed_out must be boolean"):
                map_vc_job_result_to_receipt(
                    request,
                    {**result, "timed_out": "false"},
                    **self.VC_EXECUTOR,
                )
            with self.assertRaisesRegex(ValueError, "partial VC result requires"):
                map_vc_job_result_to_receipt(
                    request,
                    {**result, "partial": True, "exit_code": None},
                    **self.VC_EXECUTOR,
                )
            with self.assertRaisesRegex(ValueError, "residuals require request.output_contract"):
                map_vc_job_result_to_receipt(
                    request,
                    result,
                    residuals=[{"path": "partial.bin"}],
                    **self.VC_EXECUTOR,
                )
            with self.assertRaisesRegex(ValueError, "partition does not match"):
                map_vc_job_result_to_receipt(
                    request,
                    {**result, "partition": "other-queue"},
                    **self.VC_EXECUTOR,
                )
            with self.assertRaisesRegex(ValueError, "project does not match"):
                map_vc_job_result_to_receipt(
                    request,
                    {**result, "project": "other-project"},
                    **self.VC_EXECUTOR,
                )

    def output_contract(self, mode: str = "producing") -> dict:
        return {
            "schema": "sure.execution_output_contract.v1",
            "mode": mode,
            "outputs": [
                {"artifact_id": "manifest", "path": "manifest.json", "kind": "file", "required": True},
                {"artifact_id": "bundle", "path": "bundle", "kind": "directory", "required": False},
            ],
            "temporary_paths": [".staging"],
            "allow_missing_on_failure": True,
            "retain_failed_outputs": True,
        }

    def test_output_contract_allows_failure_residue_and_multiple_output_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = build_request(
                run_id="bridge-output",
                unit_id="produce",
                operation="package",
                entrypoint={"executable": "/usr/bin/python3", "argv": ["-c", "pass"]},
                output_root=root,
                subject={"bundle_manifest_path": str(root / "bundle.json")},
                output_contract=self.output_contract(),
            )
            (root / ".staging").mkdir()
            (root / ".staging" / "partial.bin").write_bytes(b"x")
            residual_digest, residual_size = digest_tree(root / ".staging")
            residual = {
                "path": str(root / ".staging"),
                "resolved_path": str((root / ".staging").resolve()),
                "kind": "directory",
                "status": "present",
                "sha256": residual_digest,
                "digest_kind": "tree_sha256",
                "size": residual_size,
            }
            receipt = build_receipt(
                request,
                lifecycle="FAILED",
                executor_kind="python",
                exit_code=23,
                residuals=[residual],
            )
            self.assertEqual(validate_output_contract(request["output_contract"]), [])
            self.assertEqual(validate_output_binding(request, receipt), [])
            self.assertEqual(validate_contract_pair(request, receipt), [])

    def test_output_contract_rejects_success_without_required_output_or_tampered_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.request(root)
            request["output_contract"] = self.output_contract()
            receipt = build_receipt(request, lifecycle="SUCCEEDED", executor_kind="python", exit_code=0)
            self.assertIn("required output manifest is missing from receipt", validate_output_binding(request, receipt))
            receipt["output_set_digest"] = digest_json({"forged": True})
            self.assertIn("receipt.output_set_digest does not match observed outputs and residuals", validate_output_binding(request, receipt))

    def test_output_set_digest_matches_typescript_utf16_ordering(self) -> None:
        outputs = [
            {
                "artifact_id": "z",
                "path": "/tmp/根/z",
                "resolved_path": "/tmp/根/z",
                "sha256": "sha256:" + "a" * 64,
                "size": 1,
                "media_type": "application/octet-stream",
                "origin": "generated",
                "source_root": "/tmp/根",
            },
            {
                "artifact_id": "a",
                "path": "/tmp/根/😀",
                "resolved_path": "/tmp/根/😀",
                "sha256": "sha256:" + "b" * 64,
                "size": 2,
                "media_type": "application/octet-stream",
                "origin": "generated",
                "source_root": "/tmp/根",
            },
        ]
        self.assertEqual(
            output_set_digest(outputs),
            "sha256:a67a440e2802ca59ec5977ed45820d99c5d69ef303fffc49f4f8378f4551056d",
        )

    def test_directory_digest_is_deterministic_and_changes_with_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            root.mkdir()
            (root / "a.txt").write_text("a", encoding="utf-8")
            first, first_size = digest_tree(root)
            (root / "b.txt").write_text("b", encoding="utf-8")
            second, second_size = digest_tree(root)
            self.assertNotEqual(first, second)
            self.assertEqual(first_size, 1)
            self.assertEqual(second_size, 2)


if __name__ == "__main__":
    unittest.main()
