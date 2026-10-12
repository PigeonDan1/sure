#!/usr/bin/env python3
"""A tool a client cannot filter arguments for must not reach approval.

FireRedVAD's approved `config.yaml` declared `tools: [{name: vad}]` and stopped
there. `/sure_infer` filters the arguments it sends against
`input_schema.properties`, found none, and refused -- correctly, but only after
the bundle had been packaged, approved and published. These cases pin the rule
that now runs at onboarding and again at approval, so the refusal happens while
the model directory can still be fixed.

Run directly:
    cd sure/runtime/model_server && python test_tool_contract.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # sure/runtime
from model_server.tool_contract import ToolContractError, select_tool, tool_contracts

VAD_SCHEMA = {
    "type": "object",
    "properties": {"audio_path": {"type": "string"}},
    "required": ["audio_path"],
}


def config(**tool: object) -> dict:
    return {"tools": [{"name": "vad", **tool}]}


class ToolContractTests(unittest.TestCase):
    def test_a_declared_tool_reports_the_fields_a_client_filters_on(self) -> None:
        contract = select_tool(config(input_schema=VAD_SCHEMA), "vad")
        self.assertEqual(contract.allowed, {"audio_path"})
        self.assertEqual(contract.required, ["audio_path"])
        self.assertEqual(contract.essential()["properties"], ["audio_path"])

    def test_the_shape_that_failed_is_refused(self) -> None:
        with self.assertRaisesRegex(ToolContractError, "no input_schema mapping"):
            tool_contracts(config(description="Voice activity detection returning speech_segments."))

    def test_an_empty_properties_mapping_is_refused(self) -> None:
        with self.assertRaisesRegex(ToolContractError, "no input_schema.properties"):
            tool_contracts(config(input_schema={"type": "object", "properties": {}}))

    def test_a_required_field_the_properties_do_not_declare_is_refused(self) -> None:
        with self.assertRaisesRegex(ToolContractError, "requires 'audio_path'"):
            tool_contracts(config(input_schema={"properties": {"text": {"type": "string"}},
                                               "required": ["audio_path"]}))

    def test_a_contradicting_schema_type_is_refused(self) -> None:
        with self.assertRaisesRegex(ToolContractError, "input_schema.type"):
            tool_contracts(config(input_schema={"type": "array", "properties": {"a": {}}}))

    def test_an_omitted_schema_type_still_describes_an_object(self) -> None:
        # Declaring `type` is optional and plenty of working configs omit it;
        # only a type that contradicts the object shape is a defect.
        contract = select_tool(config(input_schema={"properties": {"audio_path": {"type": "string"}}}), "vad")
        self.assertEqual(contract.essential()["type"], "object")

    def test_a_repeated_tool_name_is_refused(self) -> None:
        with self.assertRaisesRegex(ToolContractError, "more than once"):
            tool_contracts({"tools": [{"name": "vad", "input_schema": VAD_SCHEMA}] * 2})

    def test_a_config_with_no_tools_is_refused(self) -> None:
        with self.assertRaisesRegex(ToolContractError, "no tools"):
            tool_contracts({"tools": []})

    def test_a_tool_that_is_not_declared_is_refused_by_name(self) -> None:
        with self.assertRaisesRegex(ToolContractError, "not declared"):
            select_tool(config(input_schema=VAD_SCHEMA), "asr")

    def test_the_fingerprint_ignores_decorations_a_server_may_add(self) -> None:
        # tools/list may add titles and descriptions; those change nothing about
        # which arguments a client may send, so they must not read as drift.
        bare = select_tool(config(input_schema=VAD_SCHEMA), "vad")
        decorated = select_tool(config(input_schema={
            "title": "VAD",
            "properties": {"audio_path": {"type": "string", "description": "16 kHz mono wav"}},
            "required": ["audio_path"],
        }), "vad")
        self.assertEqual(bare.digest(), decorated.digest())


if __name__ == "__main__":
    unittest.main()
