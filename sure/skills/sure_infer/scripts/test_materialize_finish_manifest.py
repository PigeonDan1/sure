import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from check_run_report import main as check_run_report
from materialize_finish_manifest import materialize_finish_manifest


PACKAGE_DIR = Path(__file__).resolve().parent.parent


class MaterializeFinishManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run_dir = self.root / "20261024-test"
        self.artifacts_dir = self.run_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True)
        self.product_dir = self.root / "product"
        self.product_dir.mkdir()
        self._write("run.json", {
            "runId": self.run_dir.name,
            "runDir": str(self.run_dir),
            "skillName": "sure_infer",
            "startedAt": "2026-10-24T08:23:43Z",
        })
        self._write("artifacts/eval_input_resolved.json", {
            "user_input": {"model": "owner__demo", "datasets": ["/datasets/one"], "metrics": ["cer"], "execution": "local"},
        })
        self._write("artifacts/execution_result.json", {
            "job_status": "succeeded", "exit_code": 0, "product_dir": str(self.product_dir),
        })
        self._write("artifacts/main_agent_run_report.json", {
            "status": "success", "report_persisted": True, "execution_path_actual": "local_python",
        })
        self._write("artifacts/dataset_decision.json", {})
        self._write("artifacts/execution_surface.json", {})

    def _write(self, path: str, value: dict) -> None:
        (self.run_dir / path).write_text(json.dumps(value), encoding="utf-8")

    def test_replaces_broken_manifest_with_verified_evidence(self) -> None:
        manifest_path = self.run_dir / "manifest.json"
        manifest_path.write_text('{"artifacts": [{', encoding="utf-8")

        materialize_finish_manifest(self.run_dir, PACKAGE_DIR, self.product_dir)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["run_id"], self.run_dir.name)
        self.assertEqual(manifest["inputs"]["model"], "owner__demo")
        self.assertEqual(manifest["outputs"]["product_dir"], str(self.product_dir))
        self.assertEqual(
            {artifact["type"] for artifact in manifest["artifacts"]},
            {"dataset_decision", "execution_surface", "execution_result", "run_report"},
        )
        self.assertTrue(all(Path(artifact["path"]).is_file() for artifact in manifest["artifacts"]))
        self.assertEqual(list(self.run_dir.glob(".manifest-*.tmp")), [])

    def test_does_not_replace_manifest_if_required_artifact_is_missing(self) -> None:
        (self.artifacts_dir / "execution_surface.json").unlink()
        manifest_path = self.run_dir / "manifest.json"
        manifest_path.write_text("untouched", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "execution_surface.json"):
            materialize_finish_manifest(self.run_dir, PACKAGE_DIR, self.product_dir)

        self.assertEqual(manifest_path.read_text(encoding="utf-8"), "untouched")

    def test_rejects_failed_execution_and_report(self) -> None:
        self._write("artifacts/execution_result.json", {
            "job_status": "failed", "exit_code": 1, "product_dir": str(self.product_dir),
        })
        with self.assertRaisesRegex(ValueError, "successful execution_result"):
            materialize_finish_manifest(self.run_dir, PACKAGE_DIR, self.product_dir)

        self._write("artifacts/execution_result.json", {
            "job_status": "succeeded", "exit_code": 0, "product_dir": str(self.product_dir),
        })
        self._write("artifacts/main_agent_run_report.json", {
            "status": "failed", "report_persisted": True, "execution_path_actual": "local_python",
        })
        with self.assertRaisesRegex(ValueError, "successful run report"):
            materialize_finish_manifest(self.run_dir, PACKAGE_DIR, self.product_dir)

    def test_optional_resolved_input_may_be_missing(self) -> None:
        (self.artifacts_dir / "eval_input_resolved.json").unlink()
        self._write("run.json", {
            "runId": self.run_dir.name,
            "runDir": str(self.run_dir),
            "skillName": "sure_infer",
            "startedAt": "2026-10-24T08:23:43Z",
            "args": "model=owner__demo",
        })
        self._write("artifacts/main_agent_run_report.json", {
            "status": "succeeded", "report_persisted": True, "execution_path_actual": "local_python",
        })

        manifest_path = materialize_finish_manifest(self.run_dir, PACKAGE_DIR, self.product_dir)

        self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8"))["inputs"], {"request": "model=owner__demo"})

    def test_rejects_mismatched_product_and_out_of_run_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "product directory disagrees"):
            materialize_finish_manifest(self.run_dir, PACKAGE_DIR, self.root)

        (self.artifacts_dir / "execution_surface.json").unlink()
        (self.artifacts_dir / "execution_surface.json").symlink_to(self.root / "outside.json")
        (self.root / "outside.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "outside the run"):
            materialize_finish_manifest(self.run_dir, PACKAGE_DIR, self.product_dir)

    def test_report_gate_repairs_manifest_only_after_product_validation(self) -> None:
        (self.product_dir / "prediction_generation_status.json").write_text(json.dumps({
            "datasets": [{"dataset": "one", "status": "completed", "num_expected_samples": 1}],
        }), encoding="utf-8")
        (self.product_dir / "protocol.yaml").write_text("schema: sure.eval.inference_protocol.v1\n", encoding="utf-8")
        predictions = self.product_dir / "predictions"
        predictions.mkdir()
        (predictions / "one.txt").write_text("sample\ttext\n", encoding="utf-8")
        references = self.product_dir / "references" / "sure_benchmark" / "jsonl"
        references.mkdir(parents=True)
        (references / "one.jsonl").write_text("{}\n", encoding="utf-8")
        manifest_path = self.run_dir / "manifest.json"
        manifest_path.write_text("broken", encoding="utf-8")
        arguments = [
            "check_run_report.py", "--profile", "infer", "--run-dir", str(self.run_dir),
            "--produces", str(self.artifacts_dir / "main_agent_run_report.json"),
        ]

        with patch("sys.argv", arguments):
            self.assertEqual(check_run_report(), 0)
        self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8"))["status"], "success")

        manifest_path.write_text("broken again", encoding="utf-8")
        (predictions / "one.txt").unlink()
        with patch("sys.argv", arguments):
            self.assertEqual(check_run_report(), 1)
        self.assertEqual(manifest_path.read_text(encoding="utf-8"), "broken again")


if __name__ == "__main__":
    unittest.main()
