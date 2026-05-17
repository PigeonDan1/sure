from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class EnhancementResult:
    output_path: str
    model_name: str = "DeepFilterNet3"

    def __post_init__(self) -> None:
        if not self.output_path:
            raise ValueError("output_path must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    """Thin wrapper around the repo-native deepFilter CLI."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model_dir = Path(__file__).resolve().parent
        self.artifacts_dir = self.model_dir / "artifacts"
        self.runtime_root = Path(
            self.config.get("runtime_root", "/private/tmp/sure_eval_deepfilternet")
        )
        self.output_root = Path(
            self.config.get("output_root", self.runtime_root / "wrapper_outputs")
        )
        self.cache_root = Path(
            self.config.get("cache_root", self.runtime_root / "cache")
        )
        self.hf_home = Path(
            self.config.get("hf_home", self.cache_root / "hf_home")
        )
        self.venv_bin = Path(
            self.config.get("venv_bin", self.model_dir / ".venv" / "bin")
        )
        self.cli_path = Path(
            self.config.get("cli_path", self.venv_bin / "deepFilter")
        )
        self.device = self.config.get("device", "cpu")
        self._loaded = False
        self._model: Any | None = None
        self._df_state: Any | None = None
        self._suffix: str | None = None

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(self.cache_root / "home")
        env["HF_HOME"] = str(self.hf_home)
        env["XDG_CACHE_HOME"] = str(self.cache_root)
        env["PATH"] = f"{self.venv_bin}:{env.get('PATH', '')}"
        if self.device == "cpu":
            env["CUDA_VISIBLE_DEVICES"] = ""
        return env

    def _apply_env(self) -> None:
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        os.environ.update(self._env())

    def load(self) -> None:
        if not self.cli_path.exists():
            raise RuntimeError(f"deepFilter CLI not found: {self.cli_path}")
        completed = subprocess.run(
            [str(self.cli_path), "--version"],
            check=True,
            capture_output=True,
            text=True,
            env=self._env(),
        )
        if "DeepFilterNet" not in completed.stdout:
            raise RuntimeError(f"Unexpected version output: {completed.stdout.strip()}")
        self._apply_env()
        from df.enhance import init_df

        self._model, self._df_state, self._suffix = init_df(
            log_level="ERROR",
            log_file=None,
        )
        self._loaded = True

    def healthcheck(self) -> dict[str, Any]:
        status = "ready" if self.cli_path.exists() else "error"
        cache_dir = (
            self.cache_root
            / "home"
            / "Library"
            / "Caches"
            / "DeepFilterNet"
            / "DeepFilterNet3"
        )
        return {
            "status": status,
            "message": f"CLI path: {self.cli_path}",
            "model_loaded": self._loaded,
            "cache_present": cache_dir.exists(),
        }

    def predict(self, input_data: str | os.PathLike[str]) -> EnhancementResult:
        if not self._loaded:
            self.load()
        input_path = Path(input_data).resolve()
        if not input_path.exists():
            raise FileNotFoundError(f"Input audio not found: {input_path}")
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._apply_env()
        from df.enhance import enhance
        from df.io import load_audio, resample, save_audio
        from df.model import ModelParams

        existing = {p.resolve() for p in self.output_root.glob("*.wav")}
        audio, metadata = load_audio(str(input_path), ModelParams().sr, "cpu")
        enhanced = enhance(
            self._model,
            self._df_state,
            audio,
            pad=True,
            atten_lim_db=None,
        )
        enhanced = resample(enhanced.to("cpu"), ModelParams().sr, metadata.sample_rate)
        save_audio(
            str(input_path),
            enhanced,
            sr=metadata.sample_rate,
            output_dir=str(self.output_root),
            suffix=self._suffix,
            log=False,
        )
        produced = sorted(
            {p.resolve() for p in self.output_root.glob("*.wav")} - existing
        )
        if not produced:
            stem_matches = sorted(self.output_root.glob(f"{input_path.stem}*.wav"))
            produced = [p.resolve() for p in stem_matches]
        if not produced:
            raise RuntimeError("deepFilter finished without producing an output wav")
        output_path = produced[-1]
        if output_path.stat().st_size == 0:
            raise RuntimeError(f"Output file is empty: {output_path}")
        return EnhancementResult(output_path=str(output_path))


def contract_result_to_json(result: EnhancementResult) -> str:
    return json.dumps(result.to_dict())
