"""
Granite Speech 4.1 2B wrapper for SURE-EVAL.

Entry points:
- ModelWrapper: load and run local ASR inference.
- TranscriptionResult: JSON-serializable ASR result type.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MODEL_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ID = "ibm-granite/granite-speech-4.1-2b"
DEFAULT_REVISION = "de575db64086f84fdc79da4932d1076e965bc546"
DEFAULT_PROMPT = "<|audio|>transcribe the speech with proper punctuation and capitalization."


class ModelLoadError(RuntimeError):
    """Raised when the Granite Speech model cannot be loaded."""


class InferenceError(RuntimeError):
    """Raised when Granite Speech inference fails."""


class ConfigurationError(ValueError):
    """Raised when wrapper configuration is invalid."""


@dataclass
class TranscriptionResult:
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if not self.text.strip():
            raise ValueError("text must be non-empty")
        self.text = self.text.strip()

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _load_audio_16k_mono(audio_path: str | os.PathLike[str]):
    import torchaudio

    wav, sample_rate = torchaudio.load(str(audio_path), normalize=True)
    if wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)
    if sample_rate != 16000:
        wav = torchaudio.functional.resample(wav, orig_freq=sample_rate, new_freq=16000)
        sample_rate = 16000
    return wav, sample_rate


class ModelWrapper:
    def __init__(self, config: dict[str, Any] | None = None, **kwargs: Any):
        cfg = dict(config or {})
        cfg.update(kwargs)

        self.repo_id = cfg.get("repo_id") or os.environ.get("GRANITE_SPEECH_REPO_ID") or DEFAULT_REPO_ID
        self.revision = cfg.get("revision") or os.environ.get("GRANITE_SPEECH_REVISION") or DEFAULT_REVISION
        self.model_path = cfg.get("model_path") or os.environ.get("MODEL_PATH") or self._resolve_model_path()
        self.device = cfg.get("device") or os.environ.get("DEVICE") or "auto"
        self.torch_dtype = cfg.get("torch_dtype") or os.environ.get("TORCH_DTYPE") or "bfloat16"

        self.processor = cfg.get("processor")
        self.tokenizer = cfg.get("tokenizer") or getattr(self.processor, "tokenizer", None)
        self.model = cfg.get("model")
        self._loaded = self.processor is not None and self.model is not None
        self._resolved_device: str | None = "cpu" if self._loaded and self.device == "cpu" else None

    def _resolve_model_path(self) -> str:
        manifest_path = MODEL_DIR / "artifacts" / "weights_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                resolved = manifest.get("resolved_local_model_path")
                if resolved and Path(resolved).exists():
                    return str(Path(resolved).resolve())
            except json.JSONDecodeError:
                pass
        snapshot_root = MODEL_DIR / ".runtime" / "hf-home" / "hub" / "models--ibm-granite--granite-speech-4.1-2b" / "snapshots" / self.revision
        if snapshot_root.exists():
            return str(snapshot_root.resolve())
        checkpoint_root = MODEL_DIR / "checkpoints" / "granite-speech-4.1-2b"
        if checkpoint_root.exists():
            return str(checkpoint_root.resolve())
        return self.repo_id

    def _select_device(self) -> str:
        if self.device != "auto":
            return str(self.device)
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"

    def _select_dtype(self):
        import torch

        dtype_map = {
            "auto": "auto",
            "float16": torch.float16,
            "fp16": torch.float16,
            "bfloat16": torch.bfloat16,
            "bf16": torch.bfloat16,
            "float32": torch.float32,
            "fp32": torch.float32,
        }
        if self.torch_dtype not in dtype_map:
            raise ConfigurationError(f"Unsupported TORCH_DTYPE={self.torch_dtype!r}")
        return dtype_map[self.torch_dtype]

    def _processor_kwargs(self) -> dict[str, Any]:
        return {
            "revision": None if Path(str(self.model_path)).exists() else self.revision,
            "local_files_only": Path(str(self.model_path)).exists(),
            "fix_mistral_regex": True,
        }

    def load(self) -> None:
        if self._loaded:
            if self.tokenizer is None and self.processor is not None:
                self.tokenizer = self.processor.tokenizer
            return

        try:
            import torch
            from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
        except ImportError as exc:
            raise ModelLoadError(f"Required runtime dependency is missing: {exc}") from exc

        device = self._select_device()
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise ModelLoadError(f"DEVICE={device} requested but CUDA is not available")

        try:
            self.processor = AutoProcessor.from_pretrained(self.model_path, **self._processor_kwargs())
            self.tokenizer = self.processor.tokenizer
            self.model = AutoModelForSpeechSeq2Seq.from_pretrained(
                self.model_path,
                revision=None if Path(str(self.model_path)).exists() else self.revision,
                dtype=self._select_dtype(),
                local_files_only=Path(str(self.model_path)).exists(),
            ).to(device).eval()
            self._resolved_device = device
            self._loaded = True
        except Exception as exc:  # noqa: BLE001 - preserve upstream load error context
            raise ModelLoadError(f"Failed to load Granite Speech from {self.model_path}: {exc}") from exc

    def predict(self, input_data: str | os.PathLike[str] | dict[str, Any]) -> dict[str, str]:
        if isinstance(input_data, (str, os.PathLike)):
            audio_path = str(input_data)
        elif isinstance(input_data, dict):
            audio_path = input_data.get("audio_path")
        else:
            raise InferenceError("input_data must be an audio path or {'audio_path': ...}")

        if not audio_path:
            raise InferenceError("audio_path is required")
        if not Path(audio_path).exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        self.load()
        assert self.processor is not None
        assert self.tokenizer is not None
        assert self.model is not None

        try:
            import torch

            device = self._resolved_device or self._select_device()
            wav, _sample_rate = _load_audio_16k_mono(audio_path)
            user_prompt = DEFAULT_PROMPT
            chat = [{"role": "user", "content": user_prompt}]
            prompt = self.tokenizer.apply_chat_template(
                chat,
                tokenize=False,
                add_generation_prompt=True,
            )
            model_inputs = self.processor(
                prompt,
                wav,
                device=device,
                return_tensors="pt",
            ).to(device)

            with torch.no_grad():
                output_ids = self.model.generate(
                    **model_inputs,
                    max_new_tokens=200,
                    do_sample=False,
                    num_beams=1,
                )

            input_length = model_inputs["input_ids"].shape[-1]
            generated_ids = output_ids[0, input_length:].unsqueeze(0)
            transcript = self.tokenizer.batch_decode(
                generated_ids,
                add_special_tokens=False,
                skip_special_tokens=True,
            )[0].strip()
            return TranscriptionResult(text=transcript).to_dict()
        except Exception as exc:
            if isinstance(exc, (FileNotFoundError, InferenceError)):
                raise
            raise InferenceError(f"Granite Speech inference failed: {exc}") from exc

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._loaded else "loading",
            "message": "model loaded" if self._loaded else "model not loaded",
            "model_loaded": self._loaded,
            "model_path": str(self.model_path),
            "device": self._resolved_device or self.device,
        }

    def health(self) -> dict[str, Any]:
        return self.healthcheck()
