from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def _coerce_tempo(raw_tempo: Any) -> float:
    if hasattr(raw_tempo, "item"):
        try:
            return float(raw_tempo.item())
        except ValueError:
            pass
    if isinstance(raw_tempo, (list, tuple)) and raw_tempo:
        return float(raw_tempo[0])
    return float(raw_tempo)


@dataclass
class AnalysisResult:
    tempo: float
    beats: list[int]
    beat_times_sec: list[float]

    def __post_init__(self) -> None:
        if not self.beats:
            raise ValueError("beats must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    """Wrapper for the minimal librosa onset/beat analysis path."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model_dir = Path(__file__).resolve().parent
        self.artifacts_dir = self.model_dir / "artifacts"
        self.python_bin = Path(
            self.config.get("python_bin", "/tmp/sure_eval_librosa/.venv/bin/python")
        )
        self.default_fixture = Path(
            self.config.get(
                "default_fixture", "tests/fixtures/shared/mir/rhythm_22k_15s.wav"
            )
        )
        self._loaded = False

    def load(self) -> None:
        if not self.python_bin.exists():
            raise RuntimeError(f"Runtime python not found: {self.python_bin}")
        command = [
            str(self.python_bin),
            "-c",
            "import json, librosa; print(json.dumps({'version': librosa.__version__}))",
        ]
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            cwd=self.model_dir.parents[3],
        )
        payload = json.loads(completed.stdout.strip())
        if not payload.get("version"):
            raise RuntimeError("librosa version check returned an empty payload")
        self._loaded = True

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self.python_bin.exists() else "error",
            "message": f"python_bin={self.python_bin}",
            "model_loaded": self._loaded,
        }

    def predict(self, input_data: str | Path) -> AnalysisResult:
        if not self._loaded:
            self.load()
        input_path = Path(input_data).resolve()
        if not input_path.exists():
            raise FileNotFoundError(f"Input audio not found: {input_path}")
        command = [
            str(self.python_bin),
            "-c",
            (
                "import json, librosa, sys; "
                "audio_path = sys.argv[1]; "
                "y, sr = librosa.load(audio_path, sr=None); "
                "onset_env = librosa.onset.onset_strength(y=y, sr=sr); "
                "tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr); "
                "tempo_value = float(tempo.item() if hasattr(tempo, 'item') else tempo[0] if hasattr(tempo, '__len__') else tempo); "
                "beat_list = [int(x) for x in beats.tolist()]; "
                "beat_times = [round(float(x), 6) for x in librosa.frames_to_time(beats, sr=sr).tolist()]; "
                "print(json.dumps({'tempo': tempo_value, 'beats': beat_list, 'beat_times_sec': beat_times}))"
            ),
            str(input_path),
        ]
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            cwd=self.model_dir.parents[3],
        )
        payload = json.loads(completed.stdout.strip())
        return AnalysisResult(
            tempo=float(payload["tempo"]),
            beats=[int(x) for x in payload["beats"]],
            beat_times_sec=[float(x) for x in payload["beat_times_sec"]],
        )


def contract_result_to_json(result: AnalysisResult) -> str:
    return json.dumps(result.to_dict())
