from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import check_eval_run_report as gate
from sure.runtime.execution_bridge import (
    build_receipt,
    build_request,
    derive_execution_admission_trace,
    digest_json,
    write_contract_bundle,
)


class ExecutionContractConsumerTests(unittest.TestCase):
    def test_orphan_admission_trace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "execution_admission.json").write_text("{}\n", encoding="utf-8")
            self.assertEqual(
                gate.execution_contract_errors(root),
                ["execution_admission.json requires execution_request.json and execution_receipt.json"],
            )

    def test_legacy_bundle_without_contract_files_remains_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(gate.execution_contract_errors(Path(temporary)), [])

    def test_immutable_execution_history_is_rechecked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = build_request(
                run_id="eval-history",
                unit_id="run_evaluation",
                operation="formal_evaluation",
                entrypoint={"executable": "/usr/bin/python3", "argv": ["-c", "pass"]},
                output_root=root,
                subject={
                    "bundle_manifest_path": str(root / "bundle.json"),
                    "bundle_digest": digest_json({"bundle": 1}),
                    "runtime_identity_digest": digest_json({"runtime": 1}),
                },
                policy_digest=digest_json({"policy": 1}),
                reference_snapshot_digest=digest_json({"snapshot": 1}),
            )
            receipt = build_receipt(request, lifecycle="SUCCEEDED", executor_kind="python", exit_code=0)
            admission = derive_execution_admission_trace(request, receipt)
            write_contract_bundle(root, request, receipt, admission_trace=admission)
            self.assertEqual(gate.execution_contract_errors(root), [])

            history_receipt = root / "execution_contracts" / f"{request['request_id']}.receipt.json"
            forged = receipt | {"policy_digest": digest_json({"forged": True})}
            history_receipt.write_text(json.dumps(forged), encoding="utf-8")
            self.assertTrue(any("immutable" in error for error in gate.execution_contract_errors(root)))


if __name__ == "__main__":
    unittest.main()
