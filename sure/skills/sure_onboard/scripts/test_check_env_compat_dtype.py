from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from check_env_compat import dtype_contract_violation


class DtypeGateTests(unittest.TestCase):
    def _run(self, artifact: dict, *, config: str = "model:\n  dtype: float32\n") -> tuple[Path, Path]:
        root = Path(tempfile.mkdtemp())
        run_dir = root / "run"
        model_dir = root / "model"
        (run_dir / "artifacts").mkdir(parents=True)
        model_dir.mkdir()
        (model_dir / "config.yaml").write_text(config, encoding="utf-8")
        (run_dir / "artifacts" / "model_input_resolved.json").write_text(
            json.dumps({"model_dir": str(model_dir), "deployment_type": "local", "device": "cpu"}),
            encoding="utf-8",
        )
        return run_dir, model_dir

    def test_declared_dtype_requires_runtime_evidence(self) -> None:
        run_dir, _ = self._run({})
        self.assertIn("declared_dtype", dtype_contract_violation({"model_dir": str(run_dir.parent / "model")}, run_dir) or "")

    def test_matching_dtype_and_probe_pass(self) -> None:
        run_dir, model_dir = self._run({})
        data = {
            "model_dir": str(model_dir),
            "device": "cpu",
            "resolved_device": "cpu",
            "declared_dtype": "float32",
            "loaded_dtype": "float32",
            "dtype_supported": True,
            "device_map": None,
            "probe": {"status": "passed", "operation": "same_dtype_matmul"},
        }
        self.assertIsNone(dtype_contract_violation(data, run_dir))

    def test_mismatched_loaded_dtype_is_rejected(self) -> None:
        run_dir, model_dir = self._run({})
        data = {
            "model_dir": str(model_dir),
            "device": "cpu",
            "resolved_device": "cpu",
            "declared_dtype": "float32",
            "loaded_dtype": "float16",
            "dtype_supported": True,
            "device_map": None,
            "probe": {"status": "passed"},
        }
        self.assertIn("loaded_dtype", dtype_contract_violation(data, run_dir) or "")


if __name__ == "__main__":
    unittest.main()
