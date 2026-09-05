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
    validate_contract_pair,
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


if __name__ == "__main__":
    unittest.main()
