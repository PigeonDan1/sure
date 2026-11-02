from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluate_predictions import _write_external_audio_samples_jsonl
from generate_predictions_via_server import _build_tool_arguments


class TTAToolArgumentTests(unittest.TestCase):
    def test_tta_arguments_use_caption_without_input_audio(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            arguments = _build_tool_arguments(
                repo_root=root,
                sample={"key": "tta-001", "caption": "A dog barking loudly in a park"},
                task="TTA",
                language="any",
                argument_name="audio_path",
                audio_path=None,
                output_audio_dir=root / "outputs",
            )
            self.assertEqual(arguments["text"], "A dog barking loudly in a park")
            self.assertEqual(arguments["caption"], "A dog barking loudly in a park")
            self.assertTrue(arguments["output_path"].endswith("tta-001.wav"))
            self.assertEqual(arguments["audio_path"], arguments["output_path"])

    def test_tta_arguments_reject_a_missing_caption(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            with self.assertRaisesRegex(ValueError, "no text caption"):
                _build_tool_arguments(
                    repo_root=Path(raw_root),
                    sample={"key": "tta-001"},
                    task="TTA",
                    language="any",
                    argument_name="audio_path",
                    audio_path=None,
                    output_audio_dir=Path(raw_root) / "outputs",
                )


class TTAEvaluationBridgeTests(unittest.TestCase):
    def test_tta_rows_use_prediction_audio_caption_and_reference_audio(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            prediction = root / "generated.wav"
            reference = root / "reference.wav"
            prediction.write_bytes(b"prediction")
            reference.write_bytes(b"reference")
            output = root / "samples.jsonl"

            _write_external_audio_samples_jsonl(
                task="TTA",
                dataset_jsonl_path=root / "dataset.jsonl",
                samples=[
                    {
                        "key": "tta-001",
                        "caption": "A dog barking loudly in a park",
                        "reference_audio": reference.name,
                        "language": "any",
                    }
                ],
                structured_predictions={
                    "tta-001": {
                        "prediction": {"audio_path": prediction.name},
                    }
                },
                structured_prediction_path=root / "predictions.jsonl",
                output_path=output,
                required_roles=set(),
            )

            row = json.loads(output.read_text(encoding="utf-8").strip())
            self.assertEqual(row["sample_id"], "tta-001")
            self.assertEqual(row["prediction_audio"], str(prediction.resolve()))
            self.assertEqual(row["reference_audio"], str(reference.resolve()))
            self.assertEqual(row["caption"], "A dog barking loudly in a park")
            self.assertEqual(row["language"], "any")

    def test_tta_bridge_reports_missing_caption_role(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            prediction = root / "generated.wav"
            prediction.write_bytes(b"prediction")

            with self.assertRaisesRegex(ValueError, "caption"):
                _write_external_audio_samples_jsonl(
                    task="TTA",
                    dataset_jsonl_path=root / "dataset.jsonl",
                    samples=[{"key": "tta-001"}],
                    structured_predictions={
                        "tta-001": {"prediction": {"audio_path": prediction.name}}
                    },
                    structured_prediction_path=root / "predictions.jsonl",
                    output_path=root / "samples.jsonl",
                    required_roles={"caption"},
                )


if __name__ == "__main__":
    unittest.main()
