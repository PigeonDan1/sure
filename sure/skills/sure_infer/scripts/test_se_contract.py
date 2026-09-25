from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_predictions_via_server as gp
import model_wrapper_mcp_server as server


class SpeechEnhancementContractTests(unittest.TestCase):
    def test_engine_audio_field_survives_normalization(self) -> None:
        for task in ("SE", "speech-enhancement", "speech_enhancement"):
            for payload in (
                {"enhanced_audio": "enhanced.wav", "sample_rate": 16000},
                {"prediction": {"enhanced_audio": "enhanced.wav", "sample_rate": 16000}},
            ):
                with self.subTest(task=task, payload=payload):
                    path, normalized = gp._normalize_prediction_payload(payload, task=task)
                    self.assertEqual(path, "enhanced.wav")
                    self.assertEqual(normalized["audio_path"], path)
                    self.assertEqual(normalized["enhanced_audio"], path)
                    self.assertEqual(normalized["sample_rate"], 16000)

    def test_missing_audio_is_a_failure_not_an_empty_success(self) -> None:
        for payload in ({}, {"text": "done"}, {"enhanced_audio": " "}):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "SE.*audio"):
                    gp._normalize_prediction_payload(payload, task="SE")

    def test_enhancement_gets_no_clean_reference_or_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            arguments = gp._build_tool_arguments(
                repo_root=root,
                sample={"key": "sample", "reference_audio": "clean.wav", "reference_text": "secret transcript"},
                task="speech-enhancement",
                language="en",
                argument_name="audio_path",
                audio_path=root / "noisy.wav",
                output_audio_dir=root / "outputs",
            )
            self.assertEqual(arguments["audio_path"], str(root / "noisy.wav"))
            self.assertEqual(arguments["output_path"], str(root / "outputs" / "sample.wav"))
            self.assertFalse({"prompt_text", "ref_text", "reference_audio", "reference_audio_path"} & arguments.keys())

    def test_mcp_schema_preserves_the_requested_output_path(self) -> None:
        schema = server._tool_schema("SE")
        arguments = gp._filter_tool_arguments(
            {"audio_path": "noisy.wav", "output_path": "run/enhanced.wav"},
            set(schema["properties"]), set(schema["required"]),
        )
        self.assertEqual(arguments["output_path"], "run/enhanced.wav")


if __name__ == "__main__":
    unittest.main()
