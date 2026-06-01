from __future__ import annotations

import contextlib
import io
import json
import math
import os
import tarfile
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_MODEL_ID = "Wespeaker/wespeaker-voxceleb-resnet221-LM"
DEFAULT_RUNTIME_MODEL_NAME = "english"
DEFAULT_ARCHIVE_NAME = "voxceleb_resnet221_LM.tar.gz"
TASK_NAME = "speaker_verification"


class ConfigurationError(ValueError):
    pass


class ModelLoadError(RuntimeError):
    pass


class InferenceError(RuntimeError):
    pass


@dataclass
class SpeakerVerificationResult:
    model_name: str
    task: str
    enroll_audio: str
    trial_audio: str
    score: float
    score_is_finite: bool
    device: str
    error_code: str | None
    backend: str | None = None
    weight_id: str | None = None
    embedding_dim: int | None = None
    sample_rate: int | None = None

    def __post_init__(self) -> None:
        if not self.model_name:
            raise ValueError("model_name must be non-empty")
        if self.task != TASK_NAME:
            raise ValueError(f"task must be {TASK_NAME}")
        if not self.enroll_audio or not self.trial_audio:
            raise ValueError("enroll_audio and trial_audio must be non-empty")
        if not isinstance(self.score, (int, float)):
            raise ValueError("score must be numeric")
        self.score = float(self.score)
        self.score_is_finite = math.isfinite(self.score)
        if not self.score_is_finite:
            raise ValueError("score must be finite")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class ModelWrapper:
    """Thin wrapper around the validated WeSpeaker similarity path."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model_root = Path(__file__).resolve().parent
        self.runtime_model_name = str(
            self.config.get("runtime_model_name", DEFAULT_RUNTIME_MODEL_NAME)
        )
        self.model_id = str(self.config.get("model_id", DEFAULT_MODEL_ID))
        self.device = str(self.config.get("device", os.getenv("DEVICE", "cpu")))
        self.backend = str(self.config.get("backend", "pip"))
        self.checkpoints_root = Path(
            self.config.get("checkpoints_root", self.model_root / "checkpoints")
        ).resolve()
        self.archive_name = str(
            self.config.get("archive_name", DEFAULT_ARCHIVE_NAME)
        )
        self._model = None
        self._embedding_dim: int | None = None

    def healthcheck(self) -> dict[str, Any]:
        model_dir = self._model_dir()
        return {
            "status": "ready" if self._model is not None else "loading",
            "message": f"runtime_model_name={self.runtime_model_name} device={self.device}",
            "model_loaded": self._model is not None,
            "checkpoints_root": str(self.checkpoints_root),
            "model_dir": str(model_dir),
            "weights_present": (model_dir / "avg_model.pt").exists(),
            "config_present": (model_dir / "config.yaml").exists(),
        }

    def load(self) -> None:
        if self._model is not None:
            return
        self._ensure_local_weights_ready()
        os.environ["WESPEAKER_HOME"] = str(self.checkpoints_root)
        try:
            import wespeaker

            with contextlib.redirect_stdout(io.StringIO()):
                model = wespeaker.load_model(self.runtime_model_name)
            model.set_device(self.device)
        except Exception as exc:  # noqa: BLE001
            raise ModelLoadError(str(exc)) from exc
        self._model = model
        self._embedding_dim = self._read_embedding_dim()

    def predict(self, input_data: Any) -> SpeakerVerificationResult:
        if self._model is None:
            self.load()
        enroll_audio, trial_audio = self._normalize_input(input_data)
        try:
            score = float(self._model.compute_similarity(enroll_audio, trial_audio))
        except Exception as exc:  # noqa: BLE001
            raise InferenceError(str(exc)) from exc
        return SpeakerVerificationResult(
            model_name="wespeaker",
            task=TASK_NAME,
            enroll_audio=enroll_audio,
            trial_audio=trial_audio,
            score=score,
            score_is_finite=math.isfinite(score),
            device=self.device,
            error_code=None,
            backend=self.backend,
            weight_id=self.model_id,
            embedding_dim=self._embedding_dim,
            sample_rate=self._read_sample_rate(enroll_audio),
        )

    def _normalize_input(self, input_data: Any) -> tuple[str, str]:
        if isinstance(input_data, dict):
            enroll_audio = input_data.get("enroll_audio") or input_data.get(
                "enrollment_audio"
            )
            trial_audio = input_data.get("trial_audio")
        elif isinstance(input_data, (list, tuple)) and len(input_data) == 2:
            enroll_audio, trial_audio = input_data
        else:
            raise ConfigurationError(
                "input_data must be a dict with enroll_audio/trial_audio or a 2-item sequence"
            )

        if not enroll_audio or not trial_audio:
            raise ConfigurationError("Both enroll_audio and trial_audio are required")

        enroll_path = Path(enroll_audio).resolve()
        trial_path = Path(trial_audio).resolve()
        if not enroll_path.exists():
            raise FileNotFoundError(f"Enrollment audio not found: {enroll_path}")
        if not trial_path.exists():
            raise FileNotFoundError(f"Trial audio not found: {trial_path}")
        return str(enroll_path), str(trial_path)

    def _ensure_local_weights_ready(self) -> None:
        model_dir = self._model_dir()
        model_dir.mkdir(parents=True, exist_ok=True)
        required = {"avg_model.pt", "config.yaml"}
        existing = {path.name for path in model_dir.iterdir() if path.is_file()}
        missing = required - existing
        if not missing:
            return

        archive_path = model_dir / self.archive_name
        if not archive_path.exists():
            raise ModelLoadError(
                f"Missing local weight files {sorted(missing)} and archive {archive_path}"
            )

        members = {
            "avg_model.pt": f"voxceleb_resnet221_LM/avg_model.pt",
            "config.yaml": f"voxceleb_resnet221_LM/config.yaml",
        }
        with tarfile.open(archive_path, "r:gz") as archive:
            for filename in sorted(missing):
                member = archive.getmember(members[filename])
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ModelLoadError(f"Unable to extract {filename} from {archive_path}")
                (model_dir / filename).write_bytes(extracted.read())

    def _model_dir(self) -> Path:
        return self.checkpoints_root / self.runtime_model_name

    def _read_embedding_dim(self) -> int | None:
        config_path = self._model_dir() / "config.yaml"
        if not config_path.exists():
            return None
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            return int(data.get("model_args", {}).get("embed_dim"))
        except Exception:  # noqa: BLE001
            return None

    def _read_sample_rate(self, audio_path: str) -> int | None:
        try:
            with wave.open(audio_path, "rb") as handle:
                return int(handle.getframerate())
        except Exception:  # noqa: BLE001
            return None


def contract_result_to_json(result: SpeakerVerificationResult) -> str:
    return result.to_json()
