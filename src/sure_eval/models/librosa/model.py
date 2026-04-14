from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class FeatureExtractionResult:
    sample_rate: int
    num_samples: int
    duration_sec: float
    feature_type: str
    shape: list[int]
    mean: float
    std: float

    def __post_init__(self) -> None:
        if not self.feature_type:
            raise ValueError("feature_type must be non-empty")
        if not self.shape:
            raise ValueError("shape must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    """Minimal librosa wrapper for MFCC feature extraction."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model_dir = Path(__file__).resolve().parent
        self.repo_root = self.model_dir.parents[3]
        self.python_bin = Path(
            self.config.get("python_bin", self.model_dir / ".venv-phase1" / "bin" / "python")
        )
        self.default_fixture = Path(
            self.config.get(
                "default_fixture",
                self.repo_root / "tests" / "fixtures" / "shared" / "asr" / "en_16k_10s.wav",
            )
        )
        self._loaded = False

    def _run_python(self, code: str, *args: str) -> dict[str, Any]:
        if not self.python_bin.exists():
            raise RuntimeError(f"Runtime python not found: {self.python_bin}")
        completed = subprocess.run(
            [str(self.python_bin), "-c", code, *args],
            cwd=self.repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout.strip())

    def load(self) -> None:
        payload = self._run_python(
            (
                "import json, librosa; "
                "callable_ready = callable(librosa.feature.mfcc); "
                "print(json.dumps({'librosa_version': librosa.__version__, 'callable_ready': callable_ready, "
                "'load_semantics': 'explicit model object not applicable; minimal feature extraction callable constructed'}))"
            )
        )
        if not payload.get("callable_ready"):
            raise RuntimeError("librosa.feature.mfcc is not callable")
        self._loaded = True

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self.python_bin.exists() else "error",
            "message": f"python_bin={self.python_bin}",
            "model_loaded": self._loaded,
        }

    def predict(self, input_data: str | Path | None = None) -> FeatureExtractionResult:
        if not self._loaded:
            self.load()

        audio_path = Path(input_data or self.default_fixture).resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"Input audio not found: {audio_path}")

        payload = self._run_python(
            (
                "import json, numpy as np, librosa, sys; "
                "audio_path = sys.argv[1]; "
                "y, sr = librosa.load(audio_path, sr=None); "
                "mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13); "
                "result = {"
                "'sample_rate': int(sr), "
                "'num_samples': int(len(y)), "
                "'duration_sec': float(len(y) / sr), "
                "'feature_type': 'mfcc', "
                "'shape': [int(mfcc.shape[0]), int(mfcc.shape[1])], "
                "'mean': float(np.mean(mfcc)), "
                "'std': float(np.std(mfcc))}; "
                "print(json.dumps(result, ensure_ascii=False))"
            ),
            str(audio_path),
        )
        return FeatureExtractionResult(**payload)


def contract_result_to_json(result: FeatureExtractionResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False)
umps(result.to_dict())
