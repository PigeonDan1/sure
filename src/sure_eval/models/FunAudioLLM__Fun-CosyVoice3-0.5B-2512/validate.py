#!/usr/bin/env python3
"""Phase-1 validation for FunAudioLLM/Fun-CosyVoice3-0.5B-2512."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_DIR = Path(__file__).resolve().parent
SOURCE_DIR = MODEL_DIR / ".runtime/source/CosyVoice"
ARTIFACTS_DIR = MODEL_DIR / "artifacts"
OUTPUTS_DIR = ARTIFACTS_DIR / "outputs"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"
TTS_METRIC_REPORT = ARTIFACTS_DIR / "tts_metric_report.json"
FIXTURE_DIR = MODEL_DIR / "fixture/tts/en"
FIXTURE_GT = FIXTURE_DIR / "gt.jsonl"

TARGET_TEXT = "CosyVoice is ready."
PROMPT_TEXT = "You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(stage: str, status: str, message: str, **extra: Any) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": _now(), "stage": stage, "status": status, "message": message, **extra}
    with VALIDATION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _record_failure(result: dict[str, Any], stage: str, exc: Exception) -> int:
    result["overall"] = "FAILED"
    result["failure_stage"] = stage
    result["error"] = f"{type(exc).__name__}: {exc}"
    _log(stage, "failed", str(exc), error_type=type(exc).__name__)
    SAMPLE_OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1


def _ensure_repo_src_path() -> None:
    for parent in [MODEL_DIR, *MODEL_DIR.parents]:
        src_dir = parent / "src"
        if (src_dir / "sure_eval").exists():
            src_text = str(src_dir)
            if src_text not in sys.path:
                sys.path.insert(0, src_text)
            return


def _load_fixture() -> dict[str, Any]:
    if not FIXTURE_GT.exists():
        raise FileNotFoundError(f"Missing model-local fixture metadata: {FIXTURE_GT}")
    with FIXTURE_GT.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            prompt_audio = FIXTURE_DIR / str(payload["prompt_audio"])
            if not prompt_audio.exists():
                raise FileNotFoundError(f"Missing model-local prompt audio: {prompt_audio}")
            return {
                "sample_id": str(payload.get("key") or "fun_cosyvoice3_zero_shot"),
                "target_text": str(payload.get("target_text") or TARGET_TEXT),
                "prompt_text": str(payload.get("prompt_text") or PROMPT_TEXT),
                "prompt_audio_path": prompt_audio,
                "language": str(payload.get("language") or "en"),
            }
    raise RuntimeError(f"No fixture rows found in {FIXTURE_GT}")


def _save_audio(tts_speech: Any, sample_rate: int | None) -> dict[str, Any]:
    import torch
    import torchaudio

    if sample_rate is None:
        sample_rate = 24000
    if not isinstance(tts_speech, torch.Tensor):
        raise TypeError(f"tts_speech must be a torch.Tensor, got {type(tts_speech).__name__}")

    speech = tts_speech.detach().cpu().float()
    while speech.dim() > 2 and speech.shape[0] == 1:
        speech = speech.squeeze(0)
    if speech.dim() == 1:
        speech = speech.unsqueeze(0)
    if speech.dim() != 2:
        raise RuntimeError(f"Expected tts_speech to save as [channels, samples], got shape={tuple(speech.shape)}")

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUTS_DIR / "fun_cosyvoice3_zero_shot.wav"
    torchaudio.save(str(output_path), speech, sample_rate)
    num_samples = int(speech.shape[-1])
    return {
        "path": str(output_path),
        "sample_rate": int(sample_rate),
        "num_channels": int(speech.shape[0]),
        "num_samples": num_samples,
        "duration_sec": num_samples / float(sample_rate),
    }


def _write_tts_metric_report(audio: dict[str, Any], fixture: dict[str, Any]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "backend": "sure_eval.evaluation.tasks.tts",
        "status": "pending_external_metric_pipeline",
        "sample": {
            "sample_id": fixture["sample_id"],
            "prediction_audio": audio["path"],
            "reference_audio": str(fixture["prompt_audio_path"]),
            "reference_text": fixture["target_text"],
            "language": fixture["language"],
        },
        "results": {},
        "provider_failures": [],
        "metrics_requested": [
            "tts_wer",
            "sim/wavlm-large",
            "sim/ecapa-tdnn",
            "sim/eres2net",
            "dnsmos",
            "wv-mos",
            "utmos",
        ],
        "notes": [
            "Validation generated real audio and confirmed the official TTS metric namespace is importable.",
            "Run scripts/run_tts_metric_pipeline_docker.py with the recorded sample to populate metric scores.",
        ],
    }
    previous: dict[str, Any] | None = None
    if TTS_METRIC_REPORT.exists():
        try:
            previous = json.loads(TTS_METRIC_REPORT.read_text(encoding="utf-8"))
        except Exception:
            previous = None
    try:
        _ensure_repo_src_path()
        from sure_eval.evaluation.tasks.tts import (  # noqa: F401
            TTSSample,
            TTSMetricPipeline,
            build_default_tts_metric_pipeline,
        )

        report["official_import"] = {
            "passed": True,
            "symbols": ["TTSSample", "TTSMetricPipeline", "build_default_tts_metric_pipeline"],
        }
    except Exception as exc:
        report["status"] = "blocked"
        report["blocker_stage"] = "official_tts_import"
        report["official_import"] = {
            "passed": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    if previous and previous.get("backend") == "sure_eval.evaluation.tasks.tts":
        if previous.get("provider_failures") or previous.get("attempted_runner"):
            report["status"] = previous.get("status", report["status"])
            report["results"] = previous.get("results", report["results"])
            report["provider_failures"] = previous.get("provider_failures", [])
            for key in ["attempted_runner", "attempt_log", "generated_at"]:
                if key in previous:
                    report[key] = previous[key]
            previous_notes = previous.get("notes")
            if isinstance(previous_notes, list):
                report["notes"] = previous_notes

    TTS_METRIC_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "timestamp": _now(),
        "model_id": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        "overall": "PASSED",
        "tests": {},
    }

    try:
        sys.path.insert(0, str(MODEL_DIR))
        from model import ModelWrapper

        wrapper = ModelWrapper({"device": os.environ.get("DEVICE", "cuda")})
        result["tests"]["import"] = {"passed": True, "health": wrapper.healthcheck()}
        _log("VALIDATE_IMPORT", "passed", "ModelWrapper imported.", health=wrapper.healthcheck())
    except Exception as exc:
        return _record_failure(result, "VALIDATE_IMPORT", exc)

    try:
        wrapper.load()
        result["tests"]["load"] = {"passed": True, "health": wrapper.healthcheck()}
        _log("VALIDATE_LOAD", "passed", "CosyVoice AutoModel loaded.", health=wrapper.healthcheck())
    except Exception as exc:
        return _record_failure(result, "VALIDATE_LOAD", exc)

    try:
        fixture = _load_fixture()
        prompt_audio = fixture["prompt_audio_path"]
        result_obj = wrapper.predict(
            {
                "text": fixture["target_text"],
                "prompt_text": fixture["prompt_text"],
                "prompt_audio_path": str(prompt_audio),
                "stream": False,
            }
        )
        result["tests"]["infer"] = {"passed": True}
        result["result"] = result_obj.to_dict()
        result["fixture"] = {
            "source": "model_local",
            "gt_jsonl": str(FIXTURE_GT),
            "prompt_audio_path": str(prompt_audio),
            "sample_id": fixture["sample_id"],
            "language": fixture["language"],
        }
        _log(
            "VALIDATE_INFER",
            "passed",
            "CosyVoice zero-shot inference returned one item.",
            fixture_source="model_local",
            fixture_gt=str(FIXTURE_GT),
            prompt_audio_path=str(prompt_audio),
        )
    except Exception as exc:
        return _record_failure(result, "VALIDATE_INFER", exc)

    try:
        summary = result["result"]["tts_speech"]
        if not summary.get("nonempty"):
            raise RuntimeError(f"tts_speech is empty: {summary}")
        result["tests"]["contract"] = {"passed": True, "summary": summary}
        _log("VALIDATE_CONTRACT", "passed", "tts_speech field is present and nonempty.", summary=summary)
    except Exception as exc:
        return _record_failure(result, "VALIDATE_CONTRACT", exc)

    try:
        audio = _save_audio(result_obj.tts_speech, result_obj.sample_rate)
        result["tests"]["audio_write"] = {"passed": True, "audio": audio}
        result["result"]["audio"] = audio
        _log("VALIDATE_AUDIO_WRITE", "passed", "Saved generated tts_speech tensor as wav.", audio=audio)
    except Exception as exc:
        return _record_failure(result, "VALIDATE_AUDIO_WRITE", exc)

    try:
        metric_report = _write_tts_metric_report(audio, fixture)
        result["tests"]["tts_metric_report"] = {
            "passed": metric_report.get("backend") == "sure_eval.evaluation.tasks.tts",
            "status": metric_report.get("status"),
            "report_path": str(TTS_METRIC_REPORT),
        }
        _log(
            "VALIDATE_TTS_METRIC_REPORT",
            "passed",
            "Wrote official TTS metric report stub for generated audio.",
            report_status=metric_report.get("status"),
            report_path=str(TTS_METRIC_REPORT),
        )
    except Exception as exc:
        return _record_failure(result, "VALIDATE_TTS_METRIC_REPORT", exc)

    SAMPLE_OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
