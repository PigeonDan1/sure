from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import torch


MODEL_DIR = Path(__file__).resolve().parents[1]
MODEL_PATH = MODEL_DIR / "model.py"


def load_model_module():
    spec = importlib.util.spec_from_file_location("granite_speech_model", MODEL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeTensorBatch(dict):
    def to(self, device: str):
        self["moved_to"] = device
        return self


class FakeTokenizer:
    def apply_chat_template(self, chat, tokenize=False, add_generation_prompt=True):
        assert tokenize is False
        assert add_generation_prompt is True
        assert chat == [
            {
                "role": "user",
                "content": "<|audio|>transcribe the speech with proper punctuation and capitalization.",
            }
        ]
        return "formatted prompt"

    def batch_decode(self, generated_ids, add_special_tokens=False, skip_special_tokens=True):
        assert add_special_tokens is False
        assert skip_special_tokens is True
        return ["Lobster a la Newberg."]


class FakeProcessor:
    def __init__(self):
        self.tokenizer = FakeTokenizer()
        self.calls = []

    def __call__(self, prompt, wav, device, return_tensors):
        self.calls.append(
            {
                "prompt": prompt,
                "shape": tuple(wav.shape),
                "device": device,
                "return_tensors": return_tensors,
            }
        )
        return FakeTensorBatch({"input_ids": torch.tensor([[1, 2, 3]])})


class FakeModel:
    def __init__(self):
        self.generate_kwargs = None

    def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        return torch.tensor([[1, 2, 3, 9, 10]])


def test_predict_returns_nonempty_json_serializable_text(monkeypatch, tmp_path):
    module = load_model_module()
    fake_processor = FakeProcessor()
    fake_model = FakeModel()
    fixture = tmp_path / "sample.wav"
    fixture.write_bytes(b"placeholder wav")

    monkeypatch.setattr(
        module,
        "_load_audio_16k_mono",
        lambda path: (torch.ones(1, 16000), 16000),
    )

    wrapper = module.ModelWrapper(
        {
            "processor": fake_processor,
            "model": fake_model,
            "device": "cpu",
        }
    )

    result = wrapper.predict({"audio_path": str(fixture)})

    assert result["text"] == "Lobster a la Newberg."
    assert json.loads(json.dumps(result)) == result
    assert fake_processor.calls == [
        {
            "prompt": "formatted prompt",
            "shape": (1, 16000),
            "device": "cpu",
            "return_tensors": "pt",
        }
    ]
    assert fake_model.generate_kwargs["max_new_tokens"] == 200
    assert fake_model.generate_kwargs["do_sample"] is False
    assert fake_model.generate_kwargs["num_beams"] == 1
