from __future__ import annotations

import unittest

from sure_feed.providers.base import infer_task, synthesize_model_input


class SeFeedTests(unittest.TestCase):
    def candidate(self):
        return {
            "model_id": "speechbrain/metricgan-plus-voicebank",
            "source": "huggingface",
            "repo": "https://huggingface.co/speechbrain/metricgan-plus-voicebank",
            "pipeline_tag": "audio-to-audio",
            "tags": ["audio-to-audio", "speech-enhancement"],
            "model_card_text": '''```python
from speechbrain.inference.enhancement import SpectralMaskEnhancement
enhance_model = SpectralMaskEnhancement.from_hparams(
    source="speechbrain/metricgan-plus-voicebank",
    savedir="pretrained_models/metricgan-plus-voicebank",
)
enhanced = enhance_model.enhance_batch(noisy, lengths=torch.tensor([1.]))
```
```bash
python train.py hparams/train.yaml --data_folder=your_data_folder
```
''',
        }

    def test_audio_to_audio_enhancement_is_not_voice_conversion(self):
        matched, task, _, evidence, _ = infer_task(self.candidate(), "auto")
        self.assertTrue(matched)
        self.assertEqual(task, "se")
        self.assertTrue(any(item["field"] == "task_narrowing.final_task" for item in evidence))
        self.assertFalse(infer_task(self.candidate(), "vc")[0])

    def test_speechbrain_entrypoints_and_se_fixture_are_actionable(self):
        model_input, missing, _ = synthesize_model_input(self.candidate(), "se")
        self.assertIn(".from_hparams(", model_input["entrypoints"]["load_test"])
        self.assertIn(".enhance_batch(", model_input["entrypoints"]["infer_test"])
        self.assertFalse(any(field.startswith("missing:entrypoints") for field in missing))
        self.assertEqual(model_input["fixture"]["fixture_status"], "ready")
        self.assertIn("reference_audio", model_input["fixture"]["samples"][0])

    def test_training_command_cannot_fill_missing_inference(self):
        candidate = self.candidate()
        candidate["model_card_text"] = "```bash\npython train.py hparams/train.yaml\n```"
        model_input, missing, _ = synthesize_model_input(candidate, "se")
        self.assertIn("missing:entrypoints.infer_test", missing)
        self.assertTrue(model_input["entrypoints"]["infer_test"].startswith("UNRESOLVED"))

    def test_sepformer_enhancement_uses_separation_api(self):
        for method in ("separate_file", "separate_batch"):
            with self.subTest(method=method):
                candidate = self.candidate()
                candidate.update(
                    model_id="speechbrain/sepformer-wham16k-enhancement",
                    repo="https://huggingface.co/speechbrain/sepformer-wham16k-enhancement",
                    tags=["audio-to-audio", "Speech Enhancement", "SepFormer"],
                    model_card_text='''```python
from speechbrain.inference.separation import SepformerSeparation as separator
model = separator.from_hparams(source="speechbrain/sepformer-wham16k-enhancement")
est_sources = model.METHOD(noisy)
```'''.replace("METHOD", method),
                )
                matched, task, *_ = infer_task(candidate, "auto")
                self.assertTrue(matched)
                self.assertEqual(task, "se")
                model_input, missing, _ = synthesize_model_input(candidate, task)
                self.assertIn(f".{method}(", model_input["entrypoints"]["infer_test"])
                self.assertFalse(any(field.startswith("missing:entrypoints") for field in missing))
                self.assertNotIn("missing:phase1_runtime_target", missing)


if __name__ == "__main__":
    unittest.main()
