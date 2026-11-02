from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluate_predictions import _write_external_audio_samples_jsonl


class RIREvaluationBridgeTests(unittest.TestCase):
    def test_rir_rows_use_rir_path_and_reference_rir(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            prediction = root / "estimated.wav"
            reference = root / "reference.wav"
            prediction.write_bytes(b"prediction")
            reference.write_bytes(b"reference")
            output = root / "samples.jsonl"

            _write_external_audio_samples_jsonl(
                task="RIR",
                dataset_jsonl_path=root / "dataset.jsonl",
                samples=[
                    {
                        "key": "rir-001",
                        "reference_rir": reference.name,
                        "sample_rate": 16000,
                        "channel_layout": "binaural_lr",
                        "metadata": {"microphone_spacing_m": 0.18},
                    }
                ],
                structured_predictions={
                    "rir-001": {
                        "prediction": {"rir_path": prediction.name},
                    }
                },
                structured_prediction_path=root / "predictions.jsonl",
                output_path=output,
                required_roles=set(),
            )

            row = json.loads(output.read_text(encoding="utf-8").strip())
            self.assertEqual(row["sample_id"], "rir-001")
            self.assertEqual(row["prediction_rir"], str(prediction.resolve()))
            self.assertEqual(row["reference_rir"], str(reference.resolve()))
            self.assertEqual(row["metadata"]["microphone_spacing_m"], 0.18)

    def test_rir_bridge_reports_missing_reference_role(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            prediction = root / "estimated.wav"
            prediction.write_bytes(b"prediction")

            with self.assertRaisesRegex(ValueError, "reference_rir"):
                _write_external_audio_samples_jsonl(
                    task="RIR",
                    dataset_jsonl_path=root / "dataset.jsonl",
                    samples=[{"key": "rir-001"}],
                    structured_predictions={
                        "rir-001": {"prediction": {"rir_path": prediction.name}}
                    },
                    structured_prediction_path=root / "predictions.jsonl",
                    output_path=root / "samples.jsonl",
                    required_roles=set(),
                )


if __name__ == "__main__":
    unittest.main()
