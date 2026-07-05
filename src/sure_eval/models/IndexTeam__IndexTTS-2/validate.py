#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_DIR = Path(__file__).resolve().parent
ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", MODEL_DIR / "artifacts")).resolve()
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"


def _log(stage: str, status: str, message: str, **extra: object) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "status": status,
        "message": message,
        **extra,
    }
    with VALIDATION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _read_gt() -> list[dict[str, Any]]:
    path = MODEL_DIR / "fixture/tts/zh/gt.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _audio_info(path: Path) -> dict[str, Any]:
    import soundfile as sf

    data, sample_rate = sf.read(path)
    return {
        "path": str(path),
        "sample_rate": int(sample_rate),
        "num_samples": int(len(data)),
        "file_size": int(path.stat().st_size),
        "duration_seconds": float(len(data) / sample_rate) if sample_rate else 0.0,
    }


def _metric_result_to_dict(result: Any) -> dict[str, Any]:
    return {"metric_name": result.metric_name, "score": result.score, "details": result.details}


def _run_tts_evaluation(sample_output: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "backend": "sure_eval.evaluation.tts",
        "status": "not_started",
        "sample": {
            "prediction_audio": sample_output["output_audio"]["path"],
            "reference_text": sample_output["target_text"],
            "reference_audio": sample_output["prompt"]["audio_info"]["path"],
            "language": "zh",
            "sample_id": "indextts2_reonboard_smoke",
        },
        "results": {},
        "rows": [],
        "blockers": [],
    }
    try:
        from sure_eval.evaluation.tts import TTSSample, build_default_tts_metric_pipeline

        device = os.environ.get("TTS_EVAL_DEVICE", os.environ.get("DEVICE", "cuda:0"))
        cache_dir = os.environ.get("TTS_EVAL_CACHE_DIR", "/hpc_stor03/sjtu_home/junhao.du/.cache/sure-eval/tts-metrics")
        pipeline = build_default_tts_metric_pipeline(device=device, cache_dir=cache_dir)
        report = pipeline.evaluate(
            [
                TTSSample(
                    prediction_audio=payload["sample"]["prediction_audio"],
                    reference_text=payload["sample"]["reference_text"],
                    reference_audio=payload["sample"]["reference_audio"],
                    language=payload["sample"]["language"],
                    sample_id=payload["sample"]["sample_id"],
                    metadata={"model_name": "IndexTeam__IndexTTS-2"},
                )
            ]
        )
        payload["status"] = "passed"
        payload["device"] = device
        payload["cache_dir"] = cache_dir
        payload["results"] = {name: _metric_result_to_dict(result) for name, result in report.results.items()}
        payload["rows"] = report.rows
    except Exception as exc:
        payload["status"] = "blocked"
        payload["blockers"].append(
            {
                "stage": "sure_eval.evaluation.tts.pipeline.evaluate",
                "error_type": exc.__class__.__name__,
                "error": str(exc),
                "policy": "Do not replace official TTS metrics with ad hoc scores.",
            }
        )
    (ARTIFACTS_DIR / "tts_metric_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def validate_static() -> int:
    model_root = Path(os.environ.get("INDEXTTS2_MODEL_ROOT", MODEL_DIR / ".runtime/modelscope_cache/IndexTeam/IndexTTS-2")).resolve()
    source_root = Path(os.environ.get("INDEXTTS2_SOURCE_ROOT", MODEL_DIR / ".runtime/source/index-tts")).resolve()
    required = [
        "model.spec.yaml",
        "config.yaml",
        "model.py",
        "server.py",
        "__init__.py",
        "artifacts/backend_choice.json",
        "artifacts/build_plan.json",
        "artifacts/spec_validation.json",
        "artifacts/weights_manifest.json",
        "fixture/tts/zh/gt.jsonl",
    ]
    missing = [item for item in required if not (MODEL_DIR / item).exists()]
    for path in (
        model_root / "config.yaml",
        model_root / "gpt.pth",
        model_root / "s2mel.pth",
        model_root / "bpe.model",
        model_root / "wav2vec2bert_stats.pt",
        model_root / "feat1.pt",
        model_root / "feat2.pt",
        model_root / "qwen0.6bemo4-merge/config.json",
        source_root / "indextts/infer_v2.py",
    ):
        if not path.exists():
            missing.append(str(path))
    if not sorted((MODEL_DIR / "fixture/tts/zh").glob("*.mp3")):
        missing.append("fixture/tts/zh/*.mp3")
    if missing:
        _log("VALIDATE_SPEC", "failed", "Missing required files.", missing=missing)
        return 2
    _log("VALIDATE_SPEC", "passed", "Static onboarding files are present.", model_root=str(model_root), source_root=str(source_root))
    return 0


def main() -> int:
    os.environ.setdefault("MPLCONFIGDIR", str(MODEL_DIR / ".runtime/matplotlib"))
    os.environ.setdefault("HF_HOME", str((MODEL_DIR / ".runtime/huggingface").resolve()))
    os.environ.setdefault("HF_HUB_CACHE", str((MODEL_DIR / ".runtime/huggingface/hub").resolve()))
    if VALIDATION_LOG.exists():
        VALIDATION_LOG.unlink()
    status = validate_static()
    if status != 0:
        return status
    if os.environ.get("SURE_TTS_STATIC_ONLY", "0") == "1":
        return 0

    rows = _read_gt()
    prompt_row = next((item for item in rows if item.get("id") == "ZH_B00001_S00000_W000000"), rows[0])
    prompt_audio = MODEL_DIR / "fixture/tts/zh" / str(prompt_row.get("wav"))
    target_text = os.environ.get(
        "SURE_TTS_TARGET_TEXT",
        "这是一个新的本地语音合成验证样例，用来确认模型会根据输入文本重新生成语音，而不是返回参考音频本身。",
    )
    output_path = ARTIFACTS_DIR / "outputs/indextts2_reonboard_smoke.wav"

    sys.path.insert(0, str(MODEL_DIR))
    from model import ModelWrapper

    wrapper = ModelWrapper({"device": os.environ.get("DEVICE", "cuda:0")})
    _log("VALIDATE_IMPORT", "passed", "ModelWrapper imported.", health=wrapper.healthcheck())
    wrapper.load()
    _log("VALIDATE_LOAD", "passed", "IndexTTS-2 model loaded.", health=wrapper.healthcheck())
    result = wrapper.predict(
        {
            "prompt_audio_path": str(prompt_audio),
            "text": target_text,
            "output_path": str(output_path),
            "language": "zh",
        }
    )

    output = Path(result.audio_path)
    if not output.exists() or output.stat().st_size <= 0:
        _log("VALIDATE_INFER", "failed", "Missing or empty output audio.", audio_path=str(output))
        return 4
    output_info = _audio_info(output)
    prompt_info = _audio_info(prompt_audio)
    if output.resolve() == prompt_audio.resolve():
        _log("VALIDATE_CONTRACT", "failed", "Output path equals prompt audio path.", audio_path=str(output))
        return 5
    if output_info["file_size"] == prompt_info["file_size"] and output_info["num_samples"] == prompt_info["num_samples"]:
        _log("VALIDATE_CONTRACT", "failed", "Output audio appears identical in size and duration to prompt audio.", output=output_info, prompt=prompt_info)
        return 6

    sample_output = {
        "model_name": "IndexTeam__IndexTTS-2",
        "task": "TTS",
        "backend": "local_or_docker_validate",
        "prompt": {
            "audio_path": str(prompt_audio.relative_to(MODEL_DIR)),
            "text": str(prompt_row.get("text") or ""),
            "audio_info": prompt_info,
        },
        "target_text": target_text,
        "prediction": result.to_dict(),
        "output_audio": output_info,
        "contract": {
            "required_fields_present": bool(result.audio_path and result.text),
            "json_serializable": True,
            "output_is_new_file": output.resolve() != prompt_audio.resolve(),
            "output_differs_from_prompt_size_or_duration": True,
        },
    }
    (ARTIFACTS_DIR / "sample_output.json").write_text(
        json.dumps(sample_output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tts_metric_report = _run_tts_evaluation(sample_output)
    _log("VALIDATE_INFER", "passed", "IndexTTS-2 inference completed.", result=result.to_dict())
    _log("VALIDATE_CONTRACT", "passed", "TTS audio_path contract passed.", sample_output=sample_output)
    _log("VALIDATE_EVALUATE", tts_metric_report["status"], "TTS evaluation routed through sure_eval.evaluation.tts.", report=tts_metric_report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
