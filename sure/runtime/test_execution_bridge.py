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
    digest_json,
    digest_tree,
    output_set_digest,
    execution_outcome_projection,
    project_execution_evidence,
    validate_adapter_route,
    validate_capability_evidence,
    validate_capability_evidence_list,
    validate_contract_pair,
    validate_output_binding,
    validate_output_contract,
    write_contract_bundle,
)


class ExecutionBridgeTests(unittest.TestCase):
    def request(self, root: Path, *, requirements: list[dict] | None = None) -> dict:
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
            self.assertTrue((root / "execution_request.json").is_file())
            self.assertTrue((root / "execution_receipt.json").is_file())
            self.assertTrue((root / "execution_contract.json").is_file())

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
