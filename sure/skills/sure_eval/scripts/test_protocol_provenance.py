#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_run_report  # noqa: E402
import evaluate_predictions  # noqa: E402
import generate_predictions_via_server  # noqa: E402
import import_prediction_source  # noqa: E402
from resolve_prediction_source import build_payload as resolve_prediction_source  # noqa: E402


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class ProtocolProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.model_dir = self.root / "sure" / "models" / "demo__asr"
        self.model_dir.mkdir(parents=True)
        (self.model_dir / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "model": {"name": "demo__asr", "task": "ASR"},
                    "server": {"command": ["python", "server.py"], "working_dir": ".", "env": {"MODEL_PATH": "checkpoints/demo"}},
                    "tools": [{"name": "transcribe_audio"}],
                    "protocols": {
                        "strict_core": {
                            "standard_params": {"beam_size": 1},
                            "model_params": {"temperature": 0},
                        }
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        artifacts = self.model_dir / "artifacts"
        artifacts.mkdir()
        write_json(
            artifacts / "runtime_inventory.json",
            {
                "schema": "sure.onboard.runtime_inventory.v1",
                "status": "ready",
                "runtime": {
                    "backend": "uv",
                    "python_executable": str(self.model_dir / ".venv" / "bin" / "python"),
                },
                "evidence": {"links_manifest": str(artifacts / "runtime_links_manifest.json")},
            },
        )
        write_json(artifacts / "runtime_links_manifest.json", {"schema": "sure.onboard.runtime_links_manifest.v1"})

    def test_protocol_yaml_prefers_generation_status_and_runtime_inventory(self) -> None:
        run_dir = self.root / "eval_run"
        run_dir.mkdir()
        write_json(
            run_dir / "prediction_generation_status.json",
            {
                "schema": "sure.eval.prediction_generation_status.v2",
                "runtime": {
                    "server_command": ["/model/python", "server.py"],
                    "server_working_dir": str(self.model_dir),
                    "model_python": "/model/python",
                    "server_config": {"timeout": 120, "env_keys": ["MODEL_PATH"]},
                },
                "environment": {
                    "safe_env_values": {"MODEL_PATH": "checkpoints/demo"},
                    "env_keys": ["MODEL_PATH", "SECRET_TOKEN"],
                    "redacted_env_keys": ["SECRET_TOKEN"],
                },
                "generation": {
                    "protocol_resolution": {
                        "status": "resolved",
                        "standard_params": {"beam_size": 4},
                        "model_params": {"temperature": 0.2},
                        "unmapped": {"extra": "x"},
                    },
                    "tool_args": {"max_new_tokens": 64},
                    "argument_policy": {
                        "argument_keys": ["audio_path", "language", "max_new_tokens"],
                        "dynamic_argument_fields": ["audio_path"],
                    },
                    "observed_raw_response": {"source_of_truth": False, "payload_keys": ["text"]},
                },
            },
        )
        evaluate_predictions._write_protocol_yaml(
            run_dir,
            "strict_core",
            self.model_dir,
            results=[],
            tool_name="transcribe_audio",
        )
        protocol = yaml.safe_load((run_dir / "protocol.yaml").read_text(encoding="utf-8"))
        self.assertEqual(protocol["model"]["server_config"]["command"], ["/model/python", "server.py"])
        self.assertEqual(protocol["protocol_selection"]["standard_params"]["beam_size"], 4)
        self.assertEqual(protocol["inference_parameters"]["explicit_tool_args"]["max_new_tokens"], 64)
        self.assertFalse(protocol["provenance"]["raw_response_source_of_truth"])
        self.assertEqual(protocol["inference_environment"]["runtime_inventory"]["status"], "ready")
        self.assertEqual(check_run_report._validate_protocol(run_dir), [])

    def test_reval_resolves_and_links_source_inference_provenance(self) -> None:
        source_run = self.root / "source_run"
        predictions = source_run / "predictions"
        predictions.mkdir(parents=True)
        (predictions / "demo_dataset.txt").write_text("utt1\thello\n", encoding="utf-8")
        (predictions / "demo_dataset.jsonl").write_text(
            json.dumps({"key": "utt1", "normalized_prediction": "hello"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        write_json(source_run / "prediction_generation_status.json", {"schema": "sure.eval.prediction_generation_status.v2"})
        (source_run / "protocol.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema": "sure.eval.inference_protocol.v1",
                    "protocol_id": "strict_core",
                    "run": {"run_id": "source", "run_dir": str(source_run), "created_at": "now"},
                    "model": {"model_name": "demo__asr", "model_dir": str(self.model_dir), "mcp_tool_name": "transcribe_audio", "server_config": {}},
                    "datasets": [{"name": "demo_dataset", "task": "ASR", "language": "en"}],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        payload = resolve_prediction_source(
            argparse.Namespace(
                source=str(source_run),
                model=None,
                model_dir=None,
                datasets=[],
                protocol_id=None,
                output=None,
            )
        )
        provenance = payload["source_inference_provenance"]
        self.assertEqual(provenance["source_protocol"], str((source_run / "protocol.yaml").resolve()))
        self.assertEqual(
            provenance["source_prediction_generation_status"],
            str((source_run / "prediction_generation_status.json").resolve()),
        )
        manifest = import_prediction_source._write_source_provenance_links(self.root / "reval_run", payload)
        self.assertFalse(manifest["policy"]["links_checkpoint_payloads"])
        self.assertIn("source_protocol", manifest["links"])
        self.assertIn("source_prediction_generation_status", manifest["links"])

        reval_run = self.root / "reval_run"
        write_json(
            reval_run / "prediction_reuse_manifest.json",
            {
                "schema": "sure.reval.prediction_reuse_manifest.v1",
                "source": {
                    "source_run_dir": str(source_run),
                    "source_inference_provenance": payload["source_inference_provenance"],
                    "old_evaluation_reused": False,
                },
                "source_inference_provenance": manifest,
            },
        )
        evaluate_predictions._write_protocol_yaml(
            reval_run,
            "strict_core",
            self.model_dir,
            results=[],
            tool_name="transcribe_audio",
        )
        protocol = yaml.safe_load((reval_run / "protocol.yaml").read_text(encoding="utf-8"))
        self.assertTrue(protocol["prediction_reuse"]["enabled"])
        self.assertEqual(protocol["prediction_reuse"]["generation_policy"], "reused_predictions_no_inference")
        self.assertEqual(protocol["prediction_reuse"]["source_protocol"], str((source_run / "protocol.yaml").resolve()))
        self.assertEqual(
            protocol["provenance"]["source_prediction_generation_status"],
            str((source_run / "prediction_generation_status.json").resolve()),
        )

    def test_generation_status_upsert_preserves_initial_generated_at(self) -> None:
        status_path = self.root / "eval_run" / "prediction_generation_status.json"
        write_json(
            status_path,
            {
                "schema": "sure.eval.prediction_generation_status.v2",
                "generated_at": "initial",
                "updated_at": "initial",
                "datasets": [{"dataset": "old", "status": "completed"}],
            },
        )
        payload, dataset = generate_predictions_via_server._upsert_dataset_status(
            status_path,
            {
                "schema": "sure.eval.prediction_generation_status.v2",
                "generated_at": "new",
                "updated_at": "new",
                "runtime": {"server_command": ["python", "server.py"]},
            },
            {"dataset": "new", "status": "running"},
        )
        self.assertEqual(payload["generated_at"], "initial")
        self.assertEqual(payload["updated_at"], "new")
        self.assertEqual(dataset["dataset"], "new")
        self.assertEqual({row["dataset"] for row in payload["datasets"]}, {"old", "new"})


if __name__ == "__main__":
    unittest.main()
