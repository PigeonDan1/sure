from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import check_artifact
import inspect_dependencies


class DependencySchemaContractTest(unittest.TestCase):
    def _run_dependency_gate(self, run_dir: Path, expected_error: str | None = None) -> int | None:
        argv = [
            "check_artifact.py",
            "--run-dir",
            str(run_dir),
            "--produces",
            str(run_dir / "artifacts" / "inference_dependency_report.json"),
            "--kind",
            "dependencies",
        ]
        with mock.patch.object(sys, "argv", argv):
            if expected_error is None:
                return check_artifact.main()
            with self.assertRaisesRegex(ValueError, expected_error):
                check_artifact.main()
        return None

    def test_inspector_output_matches_declared_schema_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            source = root / "source"
            source.mkdir()
            entrypoint = source / "infer.py"
            entrypoint.write_text("import torch\n", encoding="utf-8")
            dependency_file = source / "requirements.lock"
            dependency_file.write_text("torch==1.0\n", encoding="utf-8")
            model_path = root / "model"
            model_path.mkdir()
            (artifacts / "trans_input_resolved.json").write_text(
                json.dumps(
                    {
                        "source_kind": "python",
                        "build_context": str(source),
                        "inference_entrypoint": str(entrypoint),
                        "model_path": str(model_path),
                        "dependency_file": str(dependency_file),
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(sys, "argv", ["inspect_dependencies.py", "--run-dir", str(run_dir)]):
                self.assertEqual(inspect_dependencies.main(), 0)

            report = json.loads((artifacts / "inference_dependency_report.json").read_text(encoding="utf-8"))
            schema_path = Path(__file__).resolve().parents[1] / "schemas" / "inference_dependency_report.schema.json"
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "ready")
            self.assertEqual(schema["properties"]["schema"]["const"], "sure.trans.dependencies.v2")
            self.assertEqual(report["schema"], schema["properties"]["schema"]["const"])
            self.assertNotIn("backend_signals", report)
            self.assertNotIn("dependency_file", report)
            self.assertTrue(set(schema["required"]) <= set(report))
            if schema.get("additionalProperties") is False:
                self.assertTrue(set(report) <= set(schema["properties"]))

            self.assertEqual(self._run_dependency_gate(run_dir), 0)

    def test_dependency_gate_rejects_legacy_report_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            report = artifacts / "inference_dependency_report.json"
            report.write_text(
                json.dumps(
                    {
                        "schema": "sure.trans.dependencies.v1",
                        "entrypoint": "infer.py",
                        "build_context": ".",
                        "docker_copy_sources": [],
                        "python_imports": [],
                        "backend_signals": {},
                        "dependency_file": None,
                        "support_paths": [],
                        "model_path": "model",
                        "unresolved": [],
                        "external_paths": [],
                        "dynamic_validation_required": False,
                        "status": "ready",
                    }
                ),
                encoding="utf-8",
            )

            self._run_dependency_gate(run_dir, expected_error="dependency report schema is incompatible")


if __name__ == "__main__":
    unittest.main()
