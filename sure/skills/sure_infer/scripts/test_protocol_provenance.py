#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_run_report  # noqa: E402
import evaluate_predictions  # noqa: E402
import generate_predictions_via_server  # noqa: E402
import import_prediction_source  # noqa: E402
import protocol_writer  # noqa: E402


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
                "schema": "sure.onboard.runtime_inventory.v2",
                "status": "ready",
                "local_runtime": {
                    "backend": "uv",
                    "eligible_for_eval": False,
                },
                "container_runtime": {
                    "target_image": "registry.example.com/sure/demo:v1",
                    "target_image_digest": "sha256:" + "a" * 64,
                    "target_image_ref": "registry.example.com/sure/demo@sha256:" + "a" * 64,
                    "python_executable": "python",
                    "working_dir": "/workspace/model",
                    "mount_policy": {
                        "nfs_models_read_only": True,
                        "result_workspace": {"read_only": False},
                    },
                },
                "policy": {"eval_runtime": "container_only", "host_python_fallback": False},
                "evidence": {"links_manifest": str(artifacts / "runtime_links_manifest.json")},
            },
        )
        write_json(artifacts / "deployment_ready.json", {"schema": "sure.onboard.deployment_ready.v1"})
        write_json(artifacts / "package_gate.json", {"schema": "sure.onboard.package_gate.v2"})
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
                    "harness_runtime": {
                        "schema": "sure.harness.runtime.binding.v1",
                        "runtime_id": "sure-harness-v1-py311-demo",
                        "runtime_type": "harness_python",
                        "python_executable": "/harness/bin/python",
                        "process_python_executable": "/harness/base/bin/python3.11",
                        "lock_sha256": "c" * 64,
                        "manifest_path": "/harness/runtime-manifest.json",
                        "runtime_root": "/harness",
                    },
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
        with patch.dict("os.environ", {"RUN_ID": "published-run-id"}):
            evaluate_predictions._write_protocol_yaml(
                run_dir,
                "strict_core",
                self.model_dir,
                results=[],
                tool_name="transcribe_audio",
            )
        protocol = yaml.safe_load((run_dir / "protocol.yaml").read_text(encoding="utf-8"))
        self.assertEqual(protocol["run"]["run_id"], "published-run-id")
        self.assertEqual(protocol["model"]["server_config"]["command"], ["/model/python", "server.py"])
        self.assertEqual(protocol["protocol_selection"]["standard_params"]["beam_size"], 4)
        self.assertEqual(protocol["inference_parameters"]["explicit_tool_args"]["max_new_tokens"], 64)
        self.assertFalse(protocol["provenance"]["raw_response_source_of_truth"])
        self.assertEqual(protocol["inference_environment"]["runtime_inventory"]["status"], "ready")
        self.assertEqual(
            protocol["inference_environment"]["container"]["image_ref"],
            "registry.example.com/sure/demo@sha256:" + "a" * 64,
        )
        self.assertFalse(protocol["inference_environment"]["container"]["host_python_fallback"])
        self.assertIn("harness_commit", protocol["provenance"])
        self.assertIn("evaluation_engine", protocol["provenance"])
        self.assertEqual(
            protocol["inference_environment"]["harness_runtime"]["runtime_id"],
            "sure-harness-v1-py311-demo",
        )
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
        payload = {
            "schema": "sure.reval.approved_prediction_source.v2",
            "source_kind": "approved_nfs_results",
            "source_results_dir": str(source_run),
            "source_protocol": str((source_run / "protocol.yaml").resolve()),
            "protocol_id": "strict_core",
            "model_name": "demo__asr",
        }
        manifest = import_prediction_source._write_source_provenance_links(self.root / "reval_run", payload)
        self.assertFalse(manifest["policy"]["links_checkpoint_payloads"])
        self.assertIn("source_protocol", manifest["links"])
        # The evidence must be a regular file: the scratch tree is later persisted
        # into the result bundle by a copier that refuses symlinks.
        linked_protocol = Path(manifest["links"]["source_protocol"]["path"])
        self.assertEqual(manifest["links"]["source_protocol"]["mode"], "copy")
        self.assertFalse(linked_protocol.is_symlink())
        self.assertTrue(linked_protocol.is_file())

        reval_run = self.root / "reval_run"
        write_json(
            reval_run / "prediction_reuse_manifest.json",
            {
                "schema": "sure.reval.prediction_reuse_manifest.v1",
                "source": {
                    "source_run_dir": str(source_run),
                    "source_inference_provenance": {},
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
        self.assertIsNone(protocol["provenance"].get("source_prediction_generation_status"))

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


class ProtocolWriterModuleTests(unittest.TestCase):
    def test_write_protocol_yaml_without_results(self) -> None:
        # The inference entrypoint writes protocol.yaml before any evaluation
        # exists: no results rows, no model_dir, only the generation status.
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "infer_run"
            run_dir.mkdir()
            write_json(
                run_dir / "prediction_generation_status.json",
                {"schema": "sure.eval.prediction_generation_status.v2", "runtime": {}, "generation": {}},
            )
            protocol_writer.write_protocol_yaml(
                run_dir, "standard_system", None, results=None, tool_name="transcribe_audio"
            )
            protocol = yaml.safe_load((run_dir / "protocol.yaml").read_text(encoding="utf-8"))
        self.assertEqual(protocol["schema"], "sure.eval.inference_protocol.v1")
        self.assertEqual(protocol["protocol_id"], "standard_system")
        self.assertEqual(protocol["model"]["mcp_tool_name"], "transcribe_audio")
        self.assertFalse(protocol["prediction_reuse"]["enabled"])
        self.assertEqual(protocol["prediction_reuse"]["generation_policy"], "generated_by_model_server")
        self.assertEqual(
            protocol["provenance"]["prediction_generation_status"],
            str(run_dir / "prediction_generation_status.json"),
        )
        self.assertEqual(
            protocol["provenance"]["prediction_generation_status_schema"],
            "sure.eval.prediction_generation_status.v2",
        )
        self.assertIsNone(protocol["provenance"]["evaluation_engine"]["root"])
        self.assertEqual(len(protocol["execution_surface"]["template_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
