from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sure_feed.providers.base import synthesize_model_input


class SpeechEnhancementFeedTests(unittest.TestCase):
    def test_sherpa_strategy_uses_the_matched_task(self) -> None:
        candidate = {
            "model_id": "example/gtcrn",
            "source": "github",
            "repo": "https://github.com/example/gtcrn",
            "weights_source": "release_or_pypi",
            "model_card_text": "Speech enhancement using sherpa-onnx on CPU.",
        }
        model_input, _missing, evidence = synthesize_model_input(candidate, "se")
        self.assertEqual(model_input["task_type"], "se")
        self.assertIn("SE inference", model_input["entrypoints"]["infer_test"])
        self.assertNotIn("ASR", model_input["runtime_strategy"]["inference_surface"])
        self.assertTrue(model_input["fixture"]["reference_audio"])
        strategy_evidence = next(item for item in evidence if item.get("model_input_field") == "runtime_strategy")
        self.assertEqual(strategy_evidence["value"], model_input["runtime_strategy"])


if __name__ == "__main__":
    unittest.main()
