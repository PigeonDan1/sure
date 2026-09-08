from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import check_eval_run_report as gate


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


if __name__ == "__main__":
    unittest.main()
