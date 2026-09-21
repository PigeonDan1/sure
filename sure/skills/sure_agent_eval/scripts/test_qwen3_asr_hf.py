"""Unit tests for the Qwen3-ASR Hugging Face wrapper."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

MODEL_DIR = Path(__file__).resolve().parents[5] / "sure-data" / "models" / "qwen3_asr_1_7b_hf"


class FakeTensor:
    def __init__(self, shape):
        self.shape = shape

    def __getitem__(self, item):
        return self


class Batch(dict):
    def to(self, *args):
        return self


class FakeProcessor:
    def __init__(self):
        self.requests = []
        self.decode_calls = []

    def apply_transcription_request(self, **kwargs):
        self.requests.append(kwargs)
        return Batch(input_ids=FakeTensor((1, 2)))

    def decode(self, generated_ids, **kwargs):
        self.decode_calls.append((generated_ids, kwargs))
        return ["decoded transcript"]


class FakeModel:
    device = "cuda:0"
    dtype = "float16"

    def generate(self, **kwargs):
        return FakeTensor((1, 5))


class FakeInferenceMode:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@unittest.skipUnless((MODEL_DIR / "model.py").is_file(), f"requires the Qwen3-ASR wrapper at {MODEL_DIR}")
class Qwen3ASRHFTests(unittest.TestCase):
    def load_wrapper(self):
        spec = importlib.util.spec_from_file_location("qwen3_asr_hf_model", MODEL_DIR / "model.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module.Qwen3ASRHF

    def test_processor_request_and_transcription_only_decode(self):
        processor = FakeProcessor()
        model = FakeModel()
        fake_transformers = types.SimpleNamespace(
            AutoProcessor=types.SimpleNamespace(from_pretrained=mock.Mock(return_value=processor)),
            AutoModelForMultimodalLM=types.SimpleNamespace(from_pretrained=mock.Mock(return_value=model)),
        )
        fake_torch = types.SimpleNamespace(inference_mode=FakeInferenceMode)
        Wrapper = self.load_wrapper()
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio, mock.patch.dict(
            sys.modules, {"transformers": fake_transformers, "torch": fake_torch}
        ):
            wrapper = Wrapper(model_path="/models/qwen3-asr", device="auto", language="Chinese")
            result = wrapper.predict({"audio_path": audio.name, "language": "zh"})
        self.assertEqual(result, {"text": "decoded transcript", "language": "zh"})
        self.assertEqual(
            processor.requests,
            [{"audio": audio.name, "language": "zh", "processor_kwargs": {"load_audio_backend": "librosa"}}],
        )
        self.assertEqual(processor.decode_calls[0][1], {"return_format": "transcription_only"})
        fake_transformers.AutoProcessor.from_pretrained.assert_called_once_with("/models/qwen3-asr")
        fake_transformers.AutoModelForMultimodalLM.from_pretrained.assert_called_once_with(
            "/models/qwen3-asr", device_map="auto"
        )

    def test_empty_transcription_is_rejected(self):
        processor = FakeProcessor()
        processor.decode = mock.Mock(return_value=[""])
        fake_transformers = types.SimpleNamespace(
            AutoProcessor=types.SimpleNamespace(from_pretrained=mock.Mock(return_value=processor)),
            AutoModelForMultimodalLM=types.SimpleNamespace(from_pretrained=mock.Mock(return_value=FakeModel())),
        )
        fake_torch = types.SimpleNamespace(inference_mode=FakeInferenceMode)
        Wrapper = self.load_wrapper()
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio, mock.patch.dict(
            sys.modules, {"transformers": fake_transformers, "torch": fake_torch}
        ):
            with self.assertRaisesRegex(RuntimeError, "empty transcription"):
                Wrapper(model_path="/models/qwen3-asr").predict(audio.name)


if __name__ == "__main__":
    unittest.main()
