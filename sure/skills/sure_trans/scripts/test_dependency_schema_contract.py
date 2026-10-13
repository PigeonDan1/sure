from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import inspect_dependencies


class DependencySchemaContractTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
