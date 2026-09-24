#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check_env.py"


class CheckEnvDockerTests(unittest.TestCase):
    def _run(self, run_dir: Path, produces: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--run-dir", str(run_dir), "--produces", str(produces)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_docker_backend_short_circuits(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            produces = artifacts / "build_env_result.json"
            produces.write_text(
                json.dumps(
                    {
                        "env_ready": True,
                        "backend": "docker",
                        "docker_image": "registry.example/demo:1",
                        "model_dir": str(Path(td) / "model"),
                    }
                ),
                encoding="utf-8",
            )
            result = self._run(run_dir, produces)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("backend=docker", result.stdout)

    def test_local_backend_with_docker_delivery_adds_hint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            produces = artifacts / "build_env_result.json"
            produces.write_text(
                json.dumps(
                    {
                        "env_ready": False,
                        "backend": "uv",
                        "failures": ["broken venv"],
                        "model_dir": str(Path(td) / "model"),
                    }
                ),
                encoding="utf-8",
            )
            (artifacts / "model_input_resolved.json").write_text(
                json.dumps({"package_profile": "docker-local"}),
                encoding="utf-8",
            )
            result = self._run(run_dir, produces)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Docker delivery must not be blocked", result.stderr)


if __name__ == "__main__":
    unittest.main()
