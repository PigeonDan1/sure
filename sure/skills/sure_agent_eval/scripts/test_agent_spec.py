#!/usr/bin/env python3
"""Tests for the agent spec loader/validator.

Run directly:
    cd sure/skills/sure_agent_eval/scripts && python3 -m unittest test_agent_spec.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_spec  # noqa: E402


def valid_spec() -> dict:
    return {
        "agent": {"name": "demo_s2tt", "task": "s2tt", "input": "speech", "output": "text"},
        "stages": [
            {"id": "asr", "model": "asr_model"},
            {"id": "translate", "model": "llm_model", "prompt_template": "Translate to {target_language}: {text}"},
        ],
    }


class LoadAgentSpecTests(unittest.TestCase):
    def test_missing_file_raises(self) -> None:
        with self.assertRaises(agent_spec.AgentSpecError):
            agent_spec.load_agent_spec(Path("/nonexistent/agent.yaml"))

    def test_non_mapping_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.yaml"
            path.write_text("- just\n- a\n- list\n", encoding="utf-8")
            with self.assertRaises(agent_spec.AgentSpecError):
                agent_spec.load_agent_spec(path)


class ValidateAgentSpecTests(unittest.TestCase):
    def test_valid_spec_has_no_errors(self) -> None:
        self.assertEqual(agent_spec.validate_agent_spec(valid_spec()), [])

    def test_missing_agent_mapping(self) -> None:
        self.assertTrue(agent_spec.validate_agent_spec({"stages": [{"id": "a", "model": "m"}]}))

    def test_missing_agent_fields(self) -> None:
        spec = valid_spec()
        del spec["agent"]["task"]
        errors = agent_spec.validate_agent_spec(spec)
        self.assertTrue(any("agent.task" in error for error in errors))

    def test_empty_stages(self) -> None:
        spec = valid_spec()
        spec["stages"] = []
        self.assertTrue(agent_spec.validate_agent_spec(spec))

    def test_duplicate_stage_ids(self) -> None:
        spec = valid_spec()
        spec["stages"][1]["id"] = "asr"
        errors = agent_spec.validate_agent_spec(spec)
        self.assertTrue(any("duplicate stage id" in error for error in errors))

    def test_model_path_is_rejected(self) -> None:
        spec = valid_spec()
        spec["stages"][0]["model"] = "../somewhere/model"
        errors = agent_spec.validate_agent_spec(spec)
        self.assertTrue(any("bare approved model name" in error for error in errors))

    def test_bad_agent_name(self) -> None:
        spec = valid_spec()
        spec["agent"]["name"] = "bad name!"
        self.assertTrue(agent_spec.validate_agent_spec(spec))


class StageModeTests(unittest.TestCase):
    def test_api_mode_when_base_url_present(self) -> None:
        self.assertEqual(agent_spec.stage_mode({"api": {"base_url": "https://example/v1"}}), "api")

    def test_mcp_mode_by_default(self) -> None:
        self.assertEqual(agent_spec.stage_mode({"server": {"command": ["python", "server.py"]}}), "mcp_tool")
        self.assertEqual(agent_spec.stage_mode({}), "mcp_tool")


class RenderPromptTests(unittest.TestCase):
    def test_default_template(self) -> None:
        prompt = agent_spec.render_prompt(
            "", text="你好", target_language="en", source_language="zh", dataset="ds", key="k1"
        )
        self.assertEqual(prompt, "Translate to en: 你好")

    def test_all_placeholders(self) -> None:
        prompt = agent_spec.render_prompt(
            "{key}|{dataset}|{source_language}|{target_language}|{text}",
            text="t",
            target_language="en",
            source_language="zh",
            dataset="d",
            key="k",
        )
        self.assertEqual(prompt, "k|d|zh|en|t")


if __name__ == "__main__":
    unittest.main()
