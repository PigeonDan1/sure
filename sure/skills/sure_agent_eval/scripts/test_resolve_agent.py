#!/usr/bin/env python3
"""Tests for resolve_agent.py: spec validation, stage resolution, dataset resolution.

Run directly (needs the Harness Python for yaml/pydantic):
    cd sure/skills/sure_agent_eval/scripts && python3 -m unittest test_resolve_agent.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import resolve_agent  # noqa: E402
from sure_eval.datasets import source_resolver  # noqa: E402


def write_model(root: Path, name: str, config: dict, verdict_status: str = "success") -> Path:
    import yaml

    model_dir = root / name
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    (model_dir / "verdict.json").write_text(json.dumps({"status": verdict_status}), encoding="utf-8")
    return model_dir


def write_flat_s2tt_source(root: Path, name: str) -> Path:
    dataset_root = root / name
    dataset_root.mkdir(parents=True)
    audio = dataset_root / "utt1.wav"
    audio.write_bytes(b"RIFFxxxx")
    (dataset_root / "sample.jsonl").write_text(
        json.dumps(
            {
                "sample_id": "utt1",
                "attribute": {"path": "utt1.wav", "sample_rate": 16000},
                "annotation": [
                    {"transcription": {"text": ["你好"]}},
                    {"translation": {"text": ["hello"]}},
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (dataset_root / "ds.jsonl").write_text(
        '{"audio": {"speech": {"language": "zh", "translation_language": "en"}}}\n', encoding="utf-8"
    )
    return dataset_root


ASR_CONFIG = {
    "model": {"task": "ASR"},
    "server": {"command": [".venv/bin/python", "server.py"]},
    "tools": [{"name": "asr_transcribe"}],
}
LLM_CONFIG = {
    "model": {"task": "LLM", "name": "qwen-mt"},
    "api": {
        "base_url": "https://example.invalid/v1",
        "api_key_env": "DEMO_API_KEY",
        "timeout": 60,
        "retry": 2,
        "temperature": 0,
        "top_p": 1,
        "max_tokens": 256,
    },
}

AGENT_YAML = """\
agent:
  name: demo_s2tt
  task: s2tt
  input: speech
  output: text
stages:
  - id: asr
    model: asr_model
  - id: translate
    model: llm_model
    prompt_template: "Translate to {target_language}: {text}"
"""

TEXT_AGENT_YAML = """\
agent:
  name: demo_mt
  task: mt
  input: text
  output: text
stages:
  - id: translate
    model: llm_model
"""


class ResolveAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.models_root = self.tmp / "models"
        write_model(self.models_root, "asr_model", ASR_CONFIG)
        write_model(self.models_root, "llm_model", LLM_CONFIG)
        self.source_root = self.tmp / "src"
        self.dataset_root = write_flat_s2tt_source(self.source_root, "mini_s2tt")
        self.spec_path = self.tmp / "agent.yaml"
        self.spec_path.write_text(AGENT_YAML, encoding="utf-8")
        self._env = mock.patch.dict(os.environ, {source_resolver.SOURCE_ROOT_ENV: str(self.source_root)})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def make_args(self, **overrides) -> argparse.Namespace:
        values = {
            "agent": str(self.spec_path),
            "datasets": str(self.dataset_root),
            "metrics": "bleu,chrf",
            "run_id": "run_test",
            "dataset_source_key": None,
            "output_dir": None,
            "output": None,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_resolves_a_two_stage_agent(self) -> None:
        payload = resolve_agent.resolve_agent(self.make_args(), approved_root=self.models_root)
        self.assertEqual(payload["schema"], "sure.agent_eval.spec_resolved.v1")
        self.assertEqual(payload["agent"]["name"], "demo_s2tt")
        self.assertEqual(len(payload["agent"]["spec_sha256"]), 64)
        asr, translate = payload["stages"]
        self.assertEqual(asr["mode"], "mcp_tool")
        self.assertEqual(asr["task"], "ASR")
        self.assertEqual(asr["tool_names"], ["asr_transcribe"])
        self.assertEqual(asr["server_command"], [".venv/bin/python", "server.py"])
        self.assertIsNone(asr["api"])
        self.assertFalse(asr["deployment_bound"])
        self.assertTrue(asr["deployment_error"])
        self.assertEqual(translate["mode"], "api")
        # Credential red line: the variable NAME is recorded, never a value.
        self.assertEqual(translate["api"]["api_key_env"], "DEMO_API_KEY")
        self.assertEqual(translate["api"]["model"], "qwen-mt")
        self.assertEqual(translate["api"]["temperature"], 0.0)
        self.assertEqual(translate["api"]["top_p"], 1.0)
        self.assertEqual(translate["api"]["max_tokens"], 256)
        self.assertEqual(translate["prompt_template"], "Translate to {target_language}: {text}")
        (dataset,) = payload["datasets"]
        self.assertEqual(dataset["dataset"], "mini_s2tt__unversioned")
        self.assertEqual(dataset["task"], "S2TT")
        self.assertEqual(dataset["language"], "zh")
        self.assertEqual(dataset["translation_language"], "en")
        self.assertEqual(dataset["num_samples"], 1)
        self.assertEqual(payload["metrics"], ["bleu", "chrf"])
        self.assertIn("demo_s2tt", payload["runtime"]["product_dir"])

    def test_unknown_stage_model_fails(self) -> None:
        spec = AGENT_YAML.replace("model: asr_model", "model: missing_model")
        self.spec_path.write_text(spec, encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            resolve_agent.resolve_agent(self.make_args(), approved_root=self.models_root)
        self.assertIn("missing_model", str(ctx.exception))

    def test_failed_verdict_fails(self) -> None:
        write_model(self.models_root, "asr_model", ASR_CONFIG, verdict_status="failed")
        with self.assertRaises(ValueError):
            resolve_agent.resolve_agent(self.make_args(), approved_root=self.models_root)

    def test_first_stage_api_model_is_rejected_for_speech_input(self) -> None:
        # only the first stage is swapped to the api-mode model; chaining the
        # second replace() turned both stages back into asr_model and the
        # rejection under test never ran.
        spec = AGENT_YAML.replace("model: asr_model", "model: llm_model", 1)
        self.spec_path.write_text(spec, encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            resolve_agent.resolve_agent(self.make_args(), approved_root=self.models_root)
        self.assertIn("the first stage must be an MCP-tool model", str(ctx.exception))

    def test_first_stage_api_model_is_rejected_for_any_input(self) -> None:
        # agent_runner.py always drives position 0 through the MCP server, so a
        # non-speech agent used to resolve here and only die in the runner.
        self.spec_path.write_text(TEXT_AGENT_YAML, encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            resolve_agent.resolve_agent(self.make_args(), approved_root=self.models_root)
        self.assertIn("the first stage must be an MCP-tool model", str(ctx.exception))

    def test_later_stage_mcp_model_is_rejected(self) -> None:
        spec = AGENT_YAML.replace("model: llm_model", "model: asr_model")
        self.spec_path.write_text(spec, encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            resolve_agent.resolve_agent(self.make_args(), approved_root=self.models_root)
        self.assertIn("only the first stage", str(ctx.exception))

    def test_invalid_spec_fails(self) -> None:
        self.spec_path.write_text("agent:\n  name: demo\nstages: []\n", encoding="utf-8")
        with self.assertRaises(resolve_agent.AgentSpecError):
            resolve_agent.resolve_agent(self.make_args(), approved_root=self.models_root)

    def test_dataset_outside_source_root_fails(self) -> None:
        with self.assertRaises(ValueError):
            resolve_agent.resolve_agent(
                self.make_args(datasets=str(self.tmp / "elsewhere")), approved_root=self.models_root
            )

    def test_relative_dataset_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            resolve_agent.resolve_agent(self.make_args(datasets="aishell1"), approved_root=self.models_root)
        self.assertIn("not a source path", str(ctx.exception))

    def test_output_dir_becomes_product_dir(self) -> None:
        out = self.tmp / "out"
        payload = resolve_agent.resolve_agent(self.make_args(output_dir=str(out)), approved_root=self.models_root)
        self.assertEqual(payload["runtime"]["product_dir"], str(out))
        self.assertEqual(payload["runtime"]["output_dir"], str(out))

    def test_invalid_generation_parameters_fail(self) -> None:
        write_model(
            self.models_root,
            "llm_model",
            {**LLM_CONFIG, "api": {**LLM_CONFIG["api"], "top_p": 0}},
        )
        with self.assertRaisesRegex(ValueError, "top_p"):
            resolve_agent.resolve_agent(self.make_args(), approved_root=self.models_root)


if __name__ == "__main__":
    unittest.main()
