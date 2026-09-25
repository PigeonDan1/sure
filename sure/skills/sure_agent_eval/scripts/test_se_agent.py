from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_runner
from check_agent_execution import gate_errors


class SEAgentTests(unittest.TestCase):
    def run_case(self, root: Path, *, missing_audio: bool = False) -> tuple[dict, dict]:
        projection = root / "source.jsonl"
        projection.write_text(json.dumps({"key": "utterance", "path": "noisy.wav", "task": "SE"}) + "\n", encoding="utf-8")
        spec = {
            "agent": {"name": "gtcrn", "task": "se", "input": "speech", "output": "audio", "spec_sha256": "0" * 64},
            "stages": [{"id": "enhance", "model": "gtcrn", "mode": "mcp_tool", "task": "SE"}],
            "datasets": [{"dataset": "smoke__unversioned", "source_root": str(root), "version_id": "unversioned", "task": "SE", "language": "en"}],
            "runtime": {"product_dir": str(root / "product  with spaces"), "dataset_source_key": "default"},
        }
        artifacts = root / "run" / "artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "agent_spec_resolved.json").write_text(json.dumps(spec), encoding="utf-8")

        def enhance(arguments: dict) -> dict:
            self.assertEqual(set(arguments), {"audio_path", "output_path"})
            output = Path(arguments["output_path"])
            if not missing_audio:
                output.write_bytes(b"generated audio payload")
            return {"enhanced_audio": str(output), "sample_rate": 16000}

        manager = Mock()
        manager.download_and_convert.return_value = projection
        with patch.object(agent_runner, "_dataset_manager", return_value=manager), patch.object(agent_runner, "_projection_root", return_value=root):
            result = agent_runner.run_agent(spec, root / "run", mcp_caller_factory=lambda stage: enhance)
        return spec, result

    def test_audio_output_is_preserved_and_gate_checks_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec, result = self.run_case(root)
            self.assertEqual(result["job_status"], "succeeded", result)
            result_path = root / "run" / "artifacts" / "execution_result.json"
            self.assertEqual(gate_errors(root / "run", result_path), [])
            structured = Path(spec["runtime"]["product_dir"]) / "predictions" / "smoke__unversioned.jsonl"
            row = json.loads(structured.read_text(encoding="utf-8"))
            self.assertIn("  ", row["normalized_prediction"])
            Path(row["prediction"]["audio_path"]).unlink()
            self.assertTrue(any("missing or empty" in error for error in gate_errors(root / "run", result_path)))

    def test_claiming_a_nonexistent_audio_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _spec, result = self.run_case(Path(directory), missing_audio=True)
            self.assertEqual(result["job_status"], "failed")
            self.assertEqual(result["failed_stage"], "enhance")


if __name__ == "__main__":
    unittest.main()
