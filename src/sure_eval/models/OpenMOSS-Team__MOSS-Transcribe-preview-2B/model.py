"""MOSS-Transcribe-preview-2B wrapper for SURE-EVAL."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MODEL_ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL_ID = "OpenMOSS-Team/MOSS-Transcribe-preview-2B"
DEFAULT_REVISION = "c98175cb20e48bd9be4e95f6c85f2af18899f780"


class ConfigurationError(ValueError):
    """Raised when wrapper configuration is invalid."""


class ModelLoadError(RuntimeError):
    """Raised when model loading fails."""


class InferenceError(RuntimeError):
    """Raised when inference fails."""


@dataclass
class TranscriptionResult:
    text: str
    raw: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        if not self.text.strip():
            raise ValueError("text must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    """Lazy local wrapper around the repo-native Transformers MOSS path."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model_id = str(self.config.get("model_id") or os.environ.get("MODEL_ID", DEFAULT_MODEL_ID))
        self.revision = str(
            self.config.get("revision") or os.environ.get("MODEL_REVISION", DEFAULT_REVISION)
        )
        self.device = str(self.config.get("device") or os.environ.get("DEVICE", "cuda:0"))
        self.max_new_tokens = int(self.config.get("max_new_tokens") or os.environ.get("MAX_NEW_TOKENS", "512"))
        self._model = None
        self._tokenizer = None
        self._processor = None
        self._template_path: str | None = None
        self._configure_model_local_env()

    def _configure_model_local_env(self) -> None:
        runtime = MODEL_ROOT / ".runtime"
        defaults = {
            "HF_HOME": runtime / "hf-home",
            "HF_HUB_CACHE": runtime / "hf-home" / "hub",
            "TRANSFORMERS_CACHE": runtime / "hf-home" / "hub",
            "MPLCONFIGDIR": runtime / "matplotlib",
            "TMPDIR": runtime / "tmp",
        }
        for key, value in defaults.items():
            os.environ.setdefault(key, str(value))
            Path(os.environ[key]).mkdir(parents=True, exist_ok=True)

    def _resolved_snapshot_path(self) -> Path:
        return (
            Path(os.environ.get("HF_HUB_CACHE", str(MODEL_ROOT / ".runtime" / "hf-home" / "hub")))
            / "models--OpenMOSS-Team--MOSS-Transcribe-preview-2B"
            / "snapshots"
            / self.revision
        )

    def _resolve_model_path(self) -> str:
        explicit = self.config.get("model_path") or os.environ.get("MOSS_MODEL_PATH")
        if explicit:
            return str(Path(explicit).expanduser().resolve())
        snapshot = self._resolved_snapshot_path()
        if snapshot.exists():
            return str(snapshot)
        return self.model_id

    def _validate_weights_present(self) -> None:
        path = Path(self._resolve_model_path())
        if not path.exists():
            return
        required = [
            "config.json",
            "model.safetensors.index.json",
            "model-00000-of-00001.safetensors",
            "modeling_Moss.py",
            "processing_Moss.py",
            "chat_template_default.py",
            "tokenizer.json",
            "tokenizer_config.json",
        ]
        missing = [name for name in required if not (path / name).exists()]
        if missing:
            raise ModelLoadError(f"Missing MOSS local checkpoint files: {missing}")

    def _device(self) -> str:
        try:
            import torch
        except Exception as exc:
            raise ModelLoadError(f"Cannot import torch: {exc}") from exc
        if self.device.startswith("cuda") and not torch.cuda.is_available():
            raise ModelLoadError("CUDA device requested but torch.cuda.is_available() is false")
        return self.device

    def load(self) -> None:
        if self._model is not None:
            return
        self._validate_weights_present()
        device = self._device()
        try:
            import torch
            from huggingface_hub import hf_hub_download
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from transformers.dynamic_module_utils import get_class_from_dynamic_module

            model_path = self._resolve_model_path()
            self._model = AutoModelForCausalLM.from_pretrained(
                model_path,
                revision=None if Path(model_path).exists() else self.revision,
                dtype=torch.bfloat16,
                trust_remote_code=True,
                local_files_only=Path(model_path).exists(),
            ).to(device).eval()
            self._tokenizer = AutoTokenizer.from_pretrained(
                model_path,
                revision=None if Path(model_path).exists() else self.revision,
                trust_remote_code=True,
                local_files_only=Path(model_path).exists(),
            )
            dynamic_repo = model_path if Path(model_path).exists() else self.model_id
            dynamic_revision = None if Path(model_path).exists() else self.revision
            MossProcessor = get_class_from_dynamic_module(
                "processing_Moss.MossProcessor",
                dynamic_repo,
                revision=dynamic_revision,
                local_files_only=Path(model_path).exists(),
            )
            MelConfig = get_class_from_dynamic_module(
                "processing_Moss.MelConfig",
                dynamic_repo,
                revision=dynamic_revision,
                local_files_only=Path(model_path).exists(),
            )
            mel_cfg = MelConfig(
                mel_sr=16000,
                mel_dim=128,
                mel_n_fft=400,
                mel_hop_length=160,
            )
            self._processor = MossProcessor(
                self._tokenizer,
                config=mel_cfg,
                enable_time_marker=False,
            )
            if Path(model_path).exists():
                template_path = str(Path(model_path) / "chat_template_default.py")
            else:
                template_path = hf_hub_download(
                    repo_id=self.model_id,
                    filename="chat_template_default.py",
                    revision=self.revision,
                )
            self._processor.load_template(template_path)
            self._template_path = template_path
        except Exception as exc:
            raise ModelLoadError(f"Failed to load MOSS-Transcribe from {self._resolve_model_path()}: {exc}") from exc

    def predict(self, input_data: str | dict[str, Any]) -> TranscriptionResult:
        if self._model is None:
            self.load()
        if self._processor is None:
            raise InferenceError("Processor is not loaded")
        audio_path = input_data.get("audio_path") if isinstance(input_data, dict) else input_data
        if not audio_path:
            raise ConfigurationError("audio_path is required")
        try:
            import librosa
            import torch

            waveform, _ = librosa.load(str(audio_path), sr=16000, mono=True)
            model_inputs = self._processor(audio=waveform, return_tensors="pt").to(self.device)
            model_inputs["audio_data"] = model_inputs["audio_data"].to(self._model.dtype)
            with torch.no_grad():
                output_ids = self._model.generate(
                    **model_inputs,
                    max_new_tokens=self.max_new_tokens,
                    do_sample=False,
                    num_beams=1,
                    use_cache=True,
                    eos_token_id=[self._processor.end_token_id],
                )
            generated_ids = output_ids[:, model_inputs["input_ids"].shape[1] :]
            transcript = self._processor.batch_decode(
                generated_ids,
                skip_special_tokens=True,
            )[0].strip()
            return TranscriptionResult(
                text=transcript,
                raw={
                    "model_id": self.model_id,
                    "revision": self.revision,
                    "device": self.device,
                    "template_path": self._template_path,
                },
            )
        except Exception as exc:
            raise InferenceError(f"MOSS inference failed: {exc}") from exc

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._model is not None else "not_loaded",
            "model_loaded": self._model is not None,
            "model_id": self.model_id,
            "revision": self.revision,
            "device": self.device,
            "resolved_model_path": self._resolve_model_path(),
        }

    def health(self) -> dict[str, Any]:
        return self.healthcheck()
