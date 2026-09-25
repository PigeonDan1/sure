from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_runner
from resolve_agent import _validate_agent_task
from sure_eval.datasets.source_resolver import SOURCE_ROOT_ENV


class SeAgentTests(unittest.TestCase):
    def test_resolver_rejects_mismatched_se_contracts(self) -> None:
        agent = {"task": "se", "input": "speech", "output": "speech"}
        stages = [{"mode": "mcp_tool", "task": "SE", "tool_names": ["enhance_speech"]}]
        datasets = [{"task": "SE"}]
        _validate_agent_task(agent, stages, datasets)
        with self.assertRaisesRegex(ValueError, "one approved SE MCP-tool stage"):
            _validate_agent_task(agent, stages + stages, datasets)
        with self.assertRaisesRegex(ValueError, "SE datasets"):
            _validate_agent_task(agent, stages, [{"task": "ASR"}])

    def test_se_agent_writes_audio_predictions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projected = root / "projected.jsonl"
            projected.write_text(
                json.dumps({"key": "sample", "task": "SE", "path": str(root / "noisy.wav")}) + "\n",
                encoding="utf-8",
            )
            (root / "noisy.wav").write_bytes(b"noisy")
            product = root / "product"
            spec = {
                "agent": {"name": "metricgan", "task": "se", "input": "speech", "output": "speech", "spec_sha256": "0" * 64},
                "stages": [{"id": "enhance", "model": "metricgan", "mode": "mcp_tool", "task": "SE", "tool_names": ["enhance_speech"]}],
                "datasets": [{"dataset": "smoke__unversioned__se", "task": "SE", "source_root": str(root), "version_id": "unversioned"}],
                "runtime": {"product_dir": str(product), "dataset_source_key": "default"},
            }

            class Manager:
                def download_and_convert(self, entry: str) -> Path:
                    return projected

            def caller(stage: dict):
                def enhance(arguments: dict) -> dict:
                    self.assertEqual(arguments["audio_path"], arguments["noisy_audio_path"])
                    destination = Path(arguments["output_path"])
                    destination.write_bytes(b"RIFFenhanced")
                    return {"audio_path": str(destination)}

                return enhance

            with patch.object(agent_runner, "_dataset_manager", return_value=Manager()):
                result = agent_runner.run_agent(spec, root / "run", mcp_caller_factory=caller)
            self.assertEqual(result["job_status"], "succeeded", result["error"])
            output = product / "predictions_audio/smoke__unversioned__se/000000.wav"
            self.assertEqual(output.read_bytes(), b"RIFFenhanced")
            prediction = (product / "predictions/smoke__unversioned__se.txt").read_text(encoding="utf-8")
            self.assertEqual(prediction, f"sample\t{output}\n")
            structured = json.loads((product / "predictions/smoke__unversioned__se.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(structured["prediction"]["audio_path"], str(output))

    def test_se_result_must_use_the_requested_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            requested = root / "requested.wav"
            requested.write_bytes(b"RIFF")
            with self.assertRaisesRegex(ValueError, "requested output_path"):
                agent_runner.extract_audio_path({"audio_path": str(root / "other.wav")}, requested)
            requested.write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "empty enhanced audio"):
                agent_runner.extract_audio_path({"audio_path": str(requested)}, requested)

    def test_se_source_projection_through_agent_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset_root = root / "se_dataset"
            dataset_root.mkdir()
            (dataset_root / "noisy.wav").write_bytes(b"RIFFnoisy")
            clean = dataset_root / "clean.wav"
            clean.write_bytes(b"RIFFclean")
            (dataset_root / "ds.jsonl").write_text(
                json.dumps({"supported_tasks": ["SE"], "audio": {"speech": {"language": "en"}}}) + "\n",
                encoding="utf-8",
            )
            (dataset_root / "sample.jsonl").write_text(
                json.dumps({"sample_id": "sample", "attribute": {"path": "noisy.wav"}, "reference_audio": "clean.wav"}) + "\n",
                encoding="utf-8",
            )
            dataset_id = "se_dataset__unversioned__se"
            product = root / "product"
            spec = {
                "agent": {"name": "metricgan", "task": "se", "input": "speech", "output": "speech", "spec_sha256": "0" * 64},
                "stages": [{"id": "enhance", "model": "metricgan", "mode": "mcp_tool", "task": "SE", "tool_names": ["enhance_speech"]}],
                "datasets": [{"dataset": dataset_id, "task": "SE", "source_root": str(dataset_root), "version_id": "unversioned"}],
                "runtime": {"product_dir": str(product), "dataset_source_key": "default"},
            }

            def caller(_stage: dict):
                def enhance(arguments: dict) -> dict:
                    destination = Path(arguments["output_path"])
                    destination.write_bytes(b"RIFFenhanced")
                    return {"audio_path": str(destination)}

                return enhance

            with patch.dict(os.environ, {SOURCE_ROOT_ENV: str(root)}), patch.object(
                agent_runner, "_projection_root", return_value=root / "projections"
            ):
                result = agent_runner.run_agent(spec, root / "run", mcp_caller_factory=caller)
            self.assertEqual(result["job_status"], "succeeded", result["error"])
            reference_jsonl = product / "references" / "sure_benchmark" / "jsonl" / f"{dataset_id}.jsonl"
            row = json.loads(reference_jsonl.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["reference_audio"], str(clean))
            self.assertEqual(row["noisy_audio"], str(dataset_root / "noisy.wav"))
            structured = json.loads((product / "predictions" / f"{dataset_id}.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(structured["task"], "SE")


if __name__ == "__main__":
    unittest.main()
