#!/usr/bin/env python3
"""Check that active sure_onboard references keep critical experience assets."""

from __future__ import annotations

from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1]
REFERENCES = PACKAGE_DIR / "references"


CHECKS: dict[str, list[str]] = {
    "contracts/fixture_policy.md": [
        "MeetEval",
        "RTTM",
        "STM",
        "CTM",
        "SegLST",
    ],
    "task_playbooks/ASR.md": [
        "CERMetric",
        "WERMetric",
        "asr_metric_bypass.md",
        "sample_output.json",
    ],
    "task_playbooks/KWS.md": [
        "run_kws_metric_pipeline.py",
        "false_alarm_per_hour",
        "det_curve",
        "wekws_score_ctc",
        "wekws_frame_score",
    ],
    "task_playbooks/SPEECH_UNDERSTANDING.md": [
        "SD, SA-ASR",
        "speech_understanding_metric_report.json",
        "sa_asr__cpwer",
        "prompt_norm",
        "classify",
        "kimi_audio_leading_bang_token.md",
        "sacrebleu",
    ],
    "task_playbooks/TTS.md": [
        "TTSSample",
        "TTSMetricPipeline",
        "sure-eval metric describe tts",
        "tts_metric_report.json",
        "tts_metric_bypass.md",
    ],
    "task_playbooks/VC.md": [
        "converted_audio",
        "source_audio",
        "reference_audio",
        "vc_metric_bypass.md",
        "vc_cer",
        "dnsmos",
    ],
    "playbooks/env_uv.md": [
        "sacrebleu",
        "download-r2.pytorch.org",
        "pyarrow<21",
        "UV_CACHE_DIR",
        "MPLCONFIGDIR",
        "pipefail",
    ],
    "contracts/minimal_validation.md": [
        "sample_output.json",
        "explicit pass signal",
        "docker_smoke_false_pass.md",
    ],
    "templates/validate_metric_enrichment.md": [
        "metric_reports",
        "pipeline_description.json",
        "do not rerun model inference",
        "asr_metric_bypass.md",
        "tts_metric_bypass.md",
        "vc_metric_bypass.md",
    ],
    "experience_loss_register.md": [
        "P0 Restores",
        "Not Restored As Default Gate",
    ],
}


def main() -> int:
    missing: list[str] = []
    for rel_path, terms in CHECKS.items():
        path = REFERENCES / rel_path
        if not path.exists():
            missing.append(f"{rel_path}: file missing")
            continue
        text = path.read_text(encoding="utf-8")
        for term in terms:
            if term not in text:
                missing.append(f"{rel_path}: missing {term!r}")

    if missing:
        print("check_experience_assets FAILED")
        for item in missing:
            print(f"- {item}")
        return 1

    print(f"check_experience_assets OK ({len(CHECKS)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
