#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_predictions_via_server  # noqa: E402


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class GeneratePredictionsAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model_dir = self.root / "models" / "demo_api_asr"
        self.model_dir.mkdir(parents=True)
        self.run_dir = self.root / "run"
        self.dataset_root = self.root / "data" / "datasets"
        self.jsonl_dir = self.dataset_root / "sure_benchmark" / "jsonl"
        self.jsonl_dir.mkdir(parents=True)
        self.audio = self.root / "sample.wav"
        self.audio.write_bytes(b"RIFFxxxx")
        (self.jsonl_dir / "demo_librispeech.jsonl").write_text(
            json.dumps(
                {
                    "key": "utt1",
                    "path": str(self.audio),
                    "target": "lobster a la newberg",
                    "task": "ASR",
                    "language": "en",
                    "dataset": "demo_librispeech",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        self.config_path = self.root / "config.yaml"
        self.config_path.write_text(
            yaml.safe_dump(
                {
                    "data": {
                        "root": str(self.root / "data"),
                        "cache": str(self.root / "data" / "cache"),
                        "models": str(self.root / "data" / "models"),
                        "datasets": str(self.dataset_root),
                        "results": str(self.root / "results"),
                    }
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (self.model_dir / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "demo_api_asr",
                    "task": "ASR",
                    "deployment_type": "api",
                    "api": {
                        "provider": "test",
                        "endpoint": "https://example.test/transcribe",
                        "api_key_env": "DASHSCOPE_API_KEY",
                        "model_id": "demo-api-asr",
                        "wrapper_module": "model",
                        "wrapper_class": "DemoAPIWrapper",
                        "predict_method": "predict",
                    },
                    "tools": [
                        {
                            "name": "transcribe_audio",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "audio_path": {"type": "string"},
                                    "language": {"type": "string"},
                                },
                            },
                        }
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (self.model_dir / "model.py").write_text(
            "\n".join(
                [
                    "import os",
                    "",
                    "class DemoAPIWrapper:",
                    "    def load(self):",
                    "        if not os.environ.get('DASHSCOPE_API_KEY'):",
                    "            raise RuntimeError('missing key')",
                    "        return self",
                    "",
                    "    def predict(self, payload):",
                    "        return {",
                    "            'text': 'lobster a la newberg',",
                    "            'language': payload.get('language'),",
                    "            'audio_path': payload.get('audio_path'),",
                    "        }",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        write_json(
            self.model_dir / "artifacts" / "runtime_inventory.json",
            {
                "schema": "sure.onboard.runtime_inventory.v2",
                "status": "api_ready",
                "container_runtime": {"required": False},
                "local_runtime": {"backend": "api", "eligible_for_eval": False},
                "policy": {
                    "eval_runtime": "api_only",
                    "host_python_fallback": False,
                    "image_override_allowed": False,
                    "nfs_models_mutable_by_eval": False,
                },
            },
        )

    def test_generates_predictions_via_api_wrapper(self) -> None:
        argv = [
            "generate_predictions_via_server.py",
            "--model-dir",
            str(self.model_dir),
            "--dataset",
            "demo_librispeech",
            "--run-dir",
            str(self.run_dir),
            "--config",
            str(self.config_path),
            "--tool-name",
            "transcribe_audio",
            "--language",
            "en",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.dict(
                os.environ,
                {
                    "DASHSCOPE_API_KEY": "secret",
                    "SURE_EVAL_EXECUTION_PATH": "api",
                    "SURE_EVAL_DEPLOYMENT_MODE": "api_only",
                    "SURE_EVAL_API_KEY_ENV": "DASHSCOPE_API_KEY",
                },
            ),
        ):
            self.assertEqual(generate_predictions_via_server.main(), 0)

        prediction = (self.run_dir / "predictions" / "demo_librispeech.txt").read_text(encoding="utf-8")
        self.assertEqual(prediction.strip(), "utt1\tlobster a la newberg")
        structured = json.loads((self.run_dir / "predictions" / "demo_librispeech.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(structured["raw_response"]["text"], "lobster a la newberg")
        status = json.loads((self.run_dir / "prediction_generation_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["inference_call_mode"], "direct_api")
        self.assertIn("DASHSCOPE_API_KEY", status["environment"]["redacted_env_keys"])
        self.assertEqual(status["environment"]["safe_env_values"]["SURE_EVAL_API_KEY_ENV"], "DASHSCOPE_API_KEY")
        conversion = json.loads((self.run_dir / "predictions" / "conversion_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(conversion["datasets"][0]["source_format"], "api_wrapper_response")


if __name__ == "__main__":
    unittest.main()
