from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MODEL_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_ID = "nvidia/parakeet-rnnt-1.1b"


class ModelLoadError(RuntimeError):
    pass


class InferenceError(RuntimeError):
    pass


class ConfigurationError(ValueError):
    pass


@dataclass
class TranscriptionResult:
    text: str
    language: str | None = "en"

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def configure_model_local_runtime(model_dir: Path = MODEL_DIR) -> None:
    runtime_dir = model_dir / ".runtime"
    env_defaults = {
        "HF_HOME": runtime_dir / "hf-home",
        "HUGGINGFACE_HUB_CACHE": runtime_dir / "hf-home" / "hub",
        "NEMO_CACHE_DIR": runtime_dir / "nemo-cache",
        "XDG_CACHE_HOME": runtime_dir / "xdg-cache",
        "MPLCONFIGDIR": runtime_dir / "matplotlib",
        "TMPDIR": runtime_dir / "tmp",
    }
    for key, value in env_defaults.items():
        os.environ.setdefault(key, str(value))
        Path(os.environ[key]).mkdir(parents=True, exist_ok=True)


def _weights_manifest_model_id(model_dir: Path = MODEL_DIR) -> str | None:
    manifest_path = model_dir / "artifacts" / "weights_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    resolved = manifest.get("runtime_load_identity") or manifest.get("model_id")
    return str(resolved) if resolved else None


def _is_local_nemo_checkpoint(value: str) -> bool:
    path = Path(value).expanduser()
    return path.suffix == ".nemo" and path.exists()


class ModelWrapper:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        configure_model_local_runtime()
        self.model_id = (
            self.config.get("model_id")
            or _weights_manifest_model_id()
            or os.environ.get("MODEL_ID")
            or DEFAULT_MODEL_ID
        )
        self.device = self.config.get("device") or os.environ.get("DEVICE", "cpu")
        self._model: Any | None = None

    def load(self) -> None:
        if self._model is not None:
            return
        if self.device == "cpu":
            os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
        try:
            import nemo.collections.asr as nemo_asr
            import torch

            if _is_local_nemo_checkpoint(str(self.model_id)):
                model = nemo_asr.models.EncDecRNNTBPEModel.restore_from(
                    restore_path=str(Path(str(self.model_id)).expanduser().resolve())
                )
            else:
                model = nemo_asr.models.EncDecRNNTBPEModel.from_pretrained(
                    model_name=self.model_id
                )
            model.eval()
            if self.device.startswith("cuda"):
                if not torch.cuda.is_available():
                    raise ModelLoadError(
                        f"DEVICE={self.device} requested but torch.cuda.is_available() is false"
                    )
                model = model.to(self.device)
            self._model = model
        except ModelLoadError:
            raise
        except Exception as exc:  # pragma: no cover - exact NeMo failures are environment-specific.
            raise ModelLoadError(f"Failed to load {self.model_id}: {exc}") from exc

    def predict(self, input_data: str | dict[str, Any]) -> TranscriptionResult:
        if isinstance(input_data, dict):
            audio_path = input_data.get("audio_path")
        else:
            audio_path = input_data
        if not audio_path:
            raise ConfigurationError("audio_path is required")
        audio_file = Path(str(audio_path))
        if not audio_file.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_file}")

        if self._model is None:
            self.load()

        try:
            hypotheses = self._model.transcribe(
                audio=[str(audio_file)],
                batch_size=1,
                return_hypotheses=True,
            )
            if not hypotheses:
                raise InferenceError("transcribe returned no hypotheses")
            first = hypotheses[0]
            text = first.text if hasattr(first, "text") else str(first)
            return TranscriptionResult(text=text.strip(), language="en")
        except InferenceError:
            raise
        except Exception as exc:  # pragma: no cover - exact NeMo failures are environment-specific.
            raise InferenceError(f"Inference failed for {audio_file}: {exc}") from exc

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._model is not None else "loading",
            "message": "Model loaded" if self._model is not None else "Model not loaded",
            "model_loaded": self._model is not None,
            "model_id": self.model_id,
            "device": self.device,
            "hf_home": os.environ.get("HF_HOME"),
            "nemo_cache_dir": os.environ.get("NEMO_CACHE_DIR"),
        }
