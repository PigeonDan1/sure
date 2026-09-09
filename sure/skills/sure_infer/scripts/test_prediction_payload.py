#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_predictions_via_server as gp  # noqa: E402


class AsrPayloadNormalizationTests(unittest.TestCase):
    def test_single_element_text_list_is_unwrapped(self) -> None:
        prediction, normalized = gp._normalize_prediction_payload(
            {"text": [" 二零二二年冬奥会在北京举行"]}, task="ASR"
        )
        self.assertEqual(prediction, " 二零二二年冬奥会在北京举行")
        self.assertEqual(normalized, {"text": " 二零二二年冬奥会在北京举行"})

    def test_single_element_text_tuple_is_unwrapped(self) -> None:
        prediction, normalized = gp._normalize_prediction_payload({"text": ("hello",)}, task="S2TT")
        self.assertEqual(prediction, "hello")
        self.assertEqual(normalized, {"translation": "hello", "text": "hello"})

    def test_nested_prediction_text_list_is_unwrapped(self) -> None:
        prediction, _ = gp._normalize_prediction_payload(
            {"prediction": {"text": ["nested"]}}, task="ASR"
        )
        self.assertEqual(prediction, "nested")

    def test_plain_string_text_is_untouched(self) -> None:
        prediction, normalized = gp._normalize_prediction_payload({"text": "严浩出演的电影有什么"}, task="ASR")
        self.assertEqual(prediction, "严浩出演的电影有什么")
        self.assertEqual(normalized, {"text": "严浩出演的电影有什么"})

    def test_empty_text_list_stays_empty(self) -> None:
        prediction, normalized = gp._normalize_prediction_payload({"text": []}, task="ASR")
        self.assertEqual(prediction, "")
        self.assertEqual(normalized, {"text": ""})

    def test_structured_task_payloads_keep_engine_fields(self) -> None:
        cases = [
            ({"label": "happy"}, "SER", {"label": "happy"}),
            ({"text": "activate_lights"}, "SLU", {"answer": "activate_lights", "text": "activate_lights"}),
            ({"detected": False, "score": 0.1}, "KWS", {"detected": False, "score": 0.1}),
            ({"speech_segments": [{"start": 0.5, "end": 1.0}]}, "VAD", {"speech_segments": [{"start": 0.5, "end": 1.0}]}),
            ({"embedding": [0.1, 0.2]}, "SV", {"embedding": [0.1, 0.2]}),
        ]
        for payload, task, expected in cases:
            with self.subTest(task=task):
                _projection, normalized = gp._normalize_prediction_payload(payload, task=task)
                self.assertEqual(normalized, expected)

    def test_audio_task_payloads_use_task_specific_engine_fields(self) -> None:
        cases = [
            ("SE", "enhanced_audio"),
            ("TSE", "prediction_audio"),
            ("VC", "converted_audio"),
        ]
        for task, field in cases:
            with self.subTest(task=task):
                prediction, normalized = gp._normalize_prediction_payload("generated.wav", task=task)
                self.assertEqual(prediction, "generated.wav")
                self.assertEqual(normalized["audio_path"], "generated.wav")
                self.assertEqual(normalized[field], "generated.wav")


if __name__ == "__main__":
    unittest.main()
