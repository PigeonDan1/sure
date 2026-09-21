"""Regression coverage for a renamed, isolated model mount and strict MCP inputs."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from sure_eval.models import registry

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_predictions_via_server as generation


class MountedModelGenerationTests(unittest.TestCase):
    def test_protocol_uses_only_mounted_config_and_preserves_strict_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model"
            model.mkdir()
            config_path = model / "config.yaml"
            config_path.write_text(yaml.safe_dump({"name": "owner__demo", "task": "VAD"}))
            with (
                patch.object(registry, "ModelRegistry") as model_registry,
                patch.object(generation, "_load_yaml", wraps=generation._load_yaml) as load_yaml,
            ):
                result = generation._resolve_protocol_parameters("standard_system", model, {})
                self.assertEqual(result["status"], "resolved")
                self.assertEqual(result["config_sources"][0]["path"], str(config_path))
                self.assertEqual(result["config_sources"][0]["sha256"], generation._sha256(config_path))
                with self.assertRaisesRegex(ValueError, "does not enable protocol"):
                    generation._resolve_protocol_parameters("strict_core", model, {})
                self.assertEqual(load_yaml.call_count, 2)
                self.assertTrue(all(call.args == (config_path,) for call in load_yaml.call_args_list))
                model_registry.assert_not_called()

    def test_schema_filter_removes_all_undeclared_arguments_and_checks_required_fields(self):
        arguments = generation._filter_tool_arguments(
            {"audio_path": "/tmp/audio.wav", "language": "en", "future_extra": 1},
            {"audio_path"},
            {"audio_path"},
        )
        self.assertEqual(arguments, {"audio_path": "/tmp/audio.wav"})
        with self.assertRaisesRegex(ValueError, "missing required schema fields: audio_path"):
            generation._filter_tool_arguments(
                {"language": "en", "future_extra": 1},
                {"audio_path"},
                {"audio_path"},
            )

    def test_tool_without_declared_properties_is_refused_instead_of_sending_nothing(self):
        # With nothing declared the filter would strip every argument, audio_path
        # included, and raise nothing because nothing is required.
        for schema in (None, {"type": "object"}, {"type": "object", "properties": {}}):
            with self.subTest(schema=schema):
                tool: dict = {"name": "detect"}
                if schema is not None:
                    tool["input_schema"] = schema
                with self.assertRaisesRegex(ValueError, "declares no input_schema.properties"):
                    generation._tool_argument_contract({"tools": [tool]}, "detect")

    def test_generation_sends_language_only_when_tool_declares_it(self):
        for accepts_language in (False, True):
            with self.subTest(accepts_language=accepts_language), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                model = root / "model"
                (model / "artifacts").mkdir(parents=True)
                properties = {"audio_path": {"type": "string"}}
                if accepts_language:
                    properties["language"] = {"type": "string"}
                # config.yaml carries a different name on purpose: the sealed
                # inventory, not the mounted config, is what names the model.
                (model / "config.yaml").write_text(yaml.safe_dump({
                    "name": "config-alias", "task": "VAD",
                    "tools": [{"name": "detect", "input_schema": {
                        "type": "object",
                        "properties": properties,
                        "required": ["audio_path"],
                        "additionalProperties": False,
                    }}],
                }))
                image = "registry.example.com/demo@sha256:" + "a" * 64
                (model / "artifacts/runtime_inventory.json").write_text(json.dumps({
                    "schema": "sure.onboard.runtime_inventory.v2", "status": "ready",
                    "model": {"name": "owner__demo"},
                    "policy": {"eval_runtime": "container_only", "host_python_fallback": False, "image_override_allowed": False},
                    "container_runtime": {"server_command": ["python", "server.py"], "working_dir": str(model), "target_image_ref": image, "tool_names": ["detect"]},
                }))
                dataset = root / "dataset.jsonl"
                dataset.write_text(json.dumps({"key": "sample", "path": str(root / "audio.wav"), "task": "VAD", "language": "en"}) + "\n")
                argv = ["generate", "--model-dir", str(model), "--dataset", "demo", "--run-dir", str(root / "result"), "--language", "en"]
                with (
                    patch.object(sys, "argv", argv),
                    patch.dict("os.environ", {"SURE_EVAL_CONTAINER_IMAGE": image}),
                    patch.object(generation.Config, "from_env"),
                    patch.object(generation, "DatasetManager") as manager,
                    patch.object(generation.subprocess, "Popen"),
                    patch.object(generation, "_send_request") as send,
                    patch("builtins.print"),
                ):
                    manager.return_value.expand_dataset_names.return_value = ["demo"]
                    manager.return_value.normalize_dataset_name.return_value = "demo"
                    manager.return_value.get_jsonl_path.return_value = dataset
                    send.side_effect = [
                        {"result": {}},
                        {"result": {"tools": [{"name": "detect"}]}},
                        {"result": {"speech_segments": [{"start": 0.1, "end": 0.2}]}},
                        {"result": {}},
                    ]
                    self.assertEqual(generation.main(), 0)
                    arguments = send.call_args_list[2].args[1]["params"]["arguments"]
                    expected = {"audio_path": str(root / "audio.wav")}
                    if accepts_language:
                        expected["language"] = "en"
                    self.assertEqual(arguments, expected)
                    status = json.loads((root / "result/prediction_generation_status.json").read_text())
                    manifest = json.loads((root / "result/predictions/manifest.json").read_text())
                    self.assertEqual(status["model_name"], "owner__demo")
                    self.assertEqual(manifest["model_name"], "owner__demo")


if __name__ == "__main__":
    unittest.main()
