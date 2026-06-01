from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np


class ModelLoadError(RuntimeError):
    pass


class InferenceError(RuntimeError):
    pass


@dataclass
class SongFormerSegment:
    start: float
    end: float
    label: str

    def __post_init__(self) -> None:
        self.start = float(self.start)
        self.end = float(self.end)
        self.label = str(self.label)
        if not self.label.strip():
            raise ValueError("SongFormer segment label must be non-empty")


@dataclass
class SongFormerResult:
    segments: list[SongFormerSegment]

    def to_dict(self) -> dict[str, Any]:
        return {"segments": [asdict(segment) for segment in self.segments]}


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


class ModelWrapper:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.model_id = self.config.get("model_id", "ASLP-lab/SongFormer")
        self.model_root = Path(self.config.get("model_root", Path(__file__).resolve().parent))
        self.snapshot_dir = Path(
            self.config.get("snapshot_dir")
            or os.environ.get("SONGFORMER_LOCAL_DIR")
            or self.model_root / "ckpts" / "SongFormer"
        )
        self.hf_home = Path(
            self.config.get("hf_home")
            or os.environ.get("HF_HOME")
            or self.model_root / "hf_cache"
        )
        requested_device = self.config.get("device") or os.environ.get("DEVICE", "auto")
        self.device = self._resolve_device(requested_device)
        self._model = None

    def _resolve_device(self, requested_device: str) -> str:
        if requested_device not in {"auto", "cpu", "cuda", "cuda:0"}:
            return requested_device
        if requested_device == "cpu":
            return "cpu"
        try:
            import torch

            return "cuda:0" if torch.cuda.is_available() and requested_device != "cpu" else "cpu"
        except Exception:
            return "cpu"

    def load(self) -> None:
        if self._model is not None:
            return

        os.environ["HF_HOME"] = str(self.hf_home)
        os.environ["HF_HUB_CACHE"] = str(self.hf_home / "hub")

        try:
            from huggingface_hub import snapshot_download
            from transformers import AutoModel

            local_dir = snapshot_download(
                repo_id=self.model_id,
                repo_type="model",
                local_dir=str(self.snapshot_dir),
                local_dir_use_symlinks=False,
                resume_download=True,
                allow_patterns="*",
                ignore_patterns=["SongFormer.pt", "SongFormer.safetensors"],
            )
            if local_dir in sys.path:
                sys.path.remove(local_dir)
            sys.path.insert(0, local_dir)
            existing_model_module = sys.modules.get("model")
            if existing_model_module is not None and getattr(existing_model_module, "__file__", None) == __file__:
                sys.modules.pop("model", None)
            os.environ["SONGFORMER_LOCAL_DIR"] = local_dir
            self._model = AutoModel.from_pretrained(
                local_dir,
                trust_remote_code=True,
                low_cpu_mem_usage=False,
            )
            self._model = self._model.to(self.device)
            self._model.eval()
        except Exception as exc:
            raise ModelLoadError(f"Failed to load SongFormer model: {exc}") from exc

    def predict(self, input_data: str) -> dict[str, Any]:
        if self._model is None:
            self.load()

        try:
            raw_result = self._model(str(input_data))
            normalized = _to_jsonable(raw_result)
            if not isinstance(normalized, list) or not normalized:
                raise InferenceError("SongFormer returned an empty or non-list result")
            segments = [SongFormerSegment(**segment) for segment in normalized]
            payload = SongFormerResult(segments=segments).to_dict()
            json.dumps(payload, ensure_ascii=False)
            return payload
        except InferenceError:
            raise
        except Exception as exc:
            raise InferenceError(f"SongFormer inference failed: {exc}") from exc

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._model is not None else "loading",
            "message": "Model loaded" if self._model is not None else "Model not loaded",
            "model_loaded": self._model is not None,
            "model_id": self.model_id,
            "device": self.device,
            "snapshot_dir": str(self.snapshot_dir),
            "hf_home": str(self.hf_home),
        }
