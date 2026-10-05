from __future__ import annotations

import unittest

from model_wrapper_mcp_server import _tool_schema


class SeToolContractTests(unittest.TestCase):
    def test_se_tool_accepts_generation_output_path(self) -> None:
        schema = _tool_schema("SE")
        self.assertEqual(schema["required"], ["audio_path"])
        self.assertIn("noisy_audio_path", schema["properties"])
        self.assertIn("output_path", schema["properties"])


if __name__ == "__main__":
    unittest.main()
