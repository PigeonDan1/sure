from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from check_env_compat import dtype_contract_violation, run_dtype_probe


class DtypeGateTests(unittest.TestCase):
    def _run(self, artifact: dict, *, config: str = "model:\n  dtype: float32\n") -> tuple[Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
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
            "probe": {"status": "passed", "operation": "same_dtype_matmul", "dtype": "float32", "device": "cpu"},
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

    def test_api_dtype_does_not_require_local_loading_or_probe(self):
        run_dir, model_dir = self._run({})
        (run_dir / "artifacts" / "model_input_resolved.json").write_text(
            json.dumps({"deployment_type": "api", "model_dir": str(model_dir)})
        )
        with patch("check_env_compat.subprocess.run") as run:
            run_dtype_probe({}, run_dir)
            self.assertIsNone(dtype_contract_violation({}, run_dir))
        run.assert_not_called()

    def test_undeclared_dtype_does_not_probe(self):
        run_dir, model_dir = self._run({}, config="model: {}\n")
        with patch("check_env_compat.subprocess.run") as run:
            run_dtype_probe({"model_dir": str(model_dir)}, run_dir)
            self.assertIsNone(dtype_contract_violation({}, run_dir))
        run.assert_not_called()

    def test_gate_generates_probe_with_selected_python(self):
        run_dir, model_dir = self._run({})
        probe = {"status": "passed", "operation": "same_dtype_matmul", "dtype": "float32", "device": "cpu"}
        data = {"model_dir": str(model_dir), "python_executable": sys.executable, "device": "cpu",
                "declared_dtype": "float32", "loaded_dtype": "float32"}
        with patch("check_env_compat.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(probe), "")) as run:
            run_dtype_probe(data, run_dir)
        self.assertEqual(run.call_args.args[0][0], sys.executable)
        self.assertEqual(data["probe"], probe)
        self.assertTrue(data["dtype_supported"])
        self.assertIsNone(dtype_contract_violation(data, run_dir))
        data["probe"]["dtype"] = "float16"
        self.assertIn("probe dtype/device/operation", dtype_contract_violation(data, run_dir))

    def test_failed_probe_invalidates_stale_success(self):
        run_dir, model_dir = self._run({})
        data = {"model_dir": str(model_dir), "python_executable": sys.executable, "device": "cpu",
                "dtype_supported": True, "probe": {"status": "passed"}}
        with patch("check_env_compat.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "unsupported hardware")):
            with self.assertRaisesRegex(ValueError, "unsupported hardware"):
                run_dtype_probe(data, run_dir)
        self.assertFalse(data["dtype_supported"])

    def test_declared_dtype_never_falls_back_to_harness_python(self):
        run_dir, model_dir = self._run({})
        with self.assertRaisesRegex(ValueError, "selected Model Python"):
            run_dtype_probe({"model_dir": str(model_dir), "device": "cpu"}, run_dir)

    def test_docker_probe_uses_image_runtime_and_only_requests_gpu_for_cuda(self):
        run_dir, model_dir = self._run({})
        (run_dir / "artifacts" / "build_env_result.json").write_text(
            json.dumps({"backend": "docker", "docker_image": "model:test", "python_executable": "/model/bin/python"})
        )
        for device in ("cpu", "cuda:1"):
            probe = {"status": "passed", "device": device, "dtype": "float32", "operation": "same_dtype_matmul"}
            with patch("check_env_compat.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(probe), "")) as run:
                run_dtype_probe({"model_dir": str(model_dir), "device": device}, run_dir)
            command = run.call_args.args[0]
            self.assertEqual(command[:3], ["docker", "run", "--rm"])
            self.assertIn("model:test", command)
            self.assertEqual(command[command.index("--entrypoint") + 1], "/model/bin/python")
            self.assertEqual("--gpus" in command, device.startswith("cuda"))


if __name__ == "__main__":
    unittest.main()
