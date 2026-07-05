#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_ID = "nvidia/parakeet-rnnt-1.1b"
MODEL_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODEL_DIR.parents[3]
ARTIFACTS_DIR = MODEL_DIR / "artifacts"
FIXTURE_PATH = MODEL_DIR / "fixture" / "asr" / "asr_en" / "sample_1.wav"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"
WEIGHTS_MANIFEST = ARTIFACTS_DIR / "weights_manifest.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def configure_runtime() -> None:
    runtime_dir = MODEL_DIR / ".runtime"
    defaults = {
        "HF_HOME": runtime_dir / "hf-home",
        "HUGGINGFACE_HUB_CACHE": runtime_dir / "hf-home" / "hub",
        "NEMO_CACHE_DIR": runtime_dir / "nemo-cache",
        "XDG_CACHE_HOME": runtime_dir / "xdg-cache",
        "MPLCONFIGDIR": runtime_dir / "matplotlib",
        "TMPDIR": runtime_dir / "tmp",
    }
    for key, path in defaults.items():
        os.environ.setdefault(key, str(path))
        Path(os.environ[key]).mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
    repo_src = str(REPO_ROOT / "src")
    if repo_src not in sys.path:
        sys.path.insert(0, repo_src)
    if str(MODEL_DIR) not in sys.path:
        sys.path.insert(0, str(MODEL_DIR))


def append_log(stage: str, status: str, message: str, extra: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "timestamp": now_iso(),
        "stage": stage,
        "status": status,
        "message": message,
    }
    if extra:
        payload.update(extra)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    with VALIDATION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def timed_stage(stage: str, fn: Any) -> tuple[Any, float]:
    started = time.time()
    result = fn()
    return result, round((time.time() - started) * 1000, 3)


def text_from_hypothesis(hypothesis: Any) -> str:
    text = hypothesis.text if hasattr(hypothesis, "text") else str(hypothesis)
    return text.strip()


def resolve_model_identity() -> str:
    if WEIGHTS_MANIFEST.exists():
        manifest = json.loads(WEIGHTS_MANIFEST.read_text(encoding="utf-8"))
        for key in ["runtime_load_identity", "resolved_local_model_path"]:
            value = manifest.get(key)
            if value:
                return str(value)
    return MODEL_ID


def load_asr_model(nemo_asr: Any, model_identity: str) -> Any:
    model_path = Path(model_identity).expanduser()
    if model_path.suffix == ".nemo" and model_path.exists():
        return nemo_asr.models.EncDecRNNTBPEModel.restore_from(
            restore_path=str(model_path.resolve())
        )
    return nemo_asr.models.EncDecRNNTBPEModel.from_pretrained(model_name=model_identity)


def validate_contract(result: dict[str, Any]) -> None:
    json.dumps(result, ensure_ascii=False)
    if "text" not in result:
        raise AssertionError("Missing required field: text")
    if not isinstance(result["text"], str) or not result["text"].strip():
        raise AssertionError("Required field text is empty")


def main() -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    configure_runtime()
    if not FIXTURE_PATH.exists():
        raise FileNotFoundError(f"Missing fixture: {FIXTURE_PATH}")

    stage_results: dict[str, Any] = {}
    output: dict[str, Any] | None = None
    asr_model: Any | None = None
    model_identity = resolve_model_identity()

    try:
        def import_test() -> Any:
            import json as _json
            import nemo.collections.asr as nemo_asr
            return _json, nemo_asr

        (_, nemo_asr), duration = timed_stage("VALIDATE_IMPORT", import_test)
        stage_results["import"] = {"passed": True, "duration_ms": duration}
        append_log("VALIDATE_IMPORT", "passed", "Imported json and nemo.collections.asr.", {"duration_ms": duration})

        def load_test() -> Any:
            model = load_asr_model(nemo_asr, model_identity)
            model.eval()
            return model

        asr_model, duration = timed_stage("VALIDATE_LOAD", load_test)
        stage_results["load"] = {"passed": True, "duration_ms": duration}
        append_log(
            "VALIDATE_LOAD",
            "passed",
            "Loaded EncDecRNNTBPEModel in eval mode.",
            {
                "duration_ms": duration,
                "model_id": MODEL_ID,
                "model_identity": model_identity,
                "hf_home": os.environ.get("HF_HOME"),
                "nemo_cache_dir": os.environ.get("NEMO_CACHE_DIR"),
            },
        )

        def infer_test() -> dict[str, Any]:
            hypotheses = asr_model.transcribe(
                audio=[str(FIXTURE_PATH)],
                batch_size=1,
                return_hypotheses=True,
            )
            transcript = text_from_hypothesis(hypotheses[0])
            assert transcript, "Parakeet RNNT returned an empty transcription"
            return {"text": transcript}

        output, duration = timed_stage("VALIDATE_INFER", infer_test)
        stage_results["infer"] = {"passed": True, "duration_ms": duration}
        append_log(
            "VALIDATE_INFER",
            "passed",
            "Transcribed one short ASR fixture.",
            {"duration_ms": duration, "fixture": str(FIXTURE_PATH), "output": output},
        )

        _, duration = timed_stage("VALIDATE_CONTRACT", lambda: validate_contract(output))
        stage_results["contract"] = {"passed": True, "duration_ms": duration}
        append_log("VALIDATE_CONTRACT", "passed", "Output satisfied JSON/text contract.", {"duration_ms": duration})
        overall = "PASSED"
        error = None
    except Exception as exc:
        overall = "FAILED"
        error = repr(exc)
        append_log("VALIDATE_RUN", "failed", str(exc), {"error_type": type(exc).__name__})
        raise
    finally:
        payload = {
            "timestamp": now_iso(),
            "model_id": MODEL_ID,
            "model_identity": model_identity,
            "overall": overall,
            "fixture": {
                "path": str(FIXTURE_PATH),
                "source": "model_local_copy_of_tests/fixtures/shared/asr/en_16k_10s.wav",
                "language": "en",
                "sample_rate_hz": 16000,
                "channels": 1,
            },
            "tests": stage_results,
            "result": output,
            "error": error,
        }
        SAMPLE_OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
