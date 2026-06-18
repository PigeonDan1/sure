from __future__ import annotations

from sure_eval.paper_to_userspec.extractor import extract_user_spec


def test_target_model_ignores_related_work_and_baselines() -> None:
    text = """# TargetNet ASR

## Abstract
We introduce TargetNet-ASR for automatic speech recognition.

## Related Work
Baseline-Whisper is a strong baseline in prior work.

## Experiments
We compare against Baseline-Whisper and report WER on LibriSpeech.
"""
    spec = extract_user_spec(case_id="targetnet", paper_text=text, raw_goal="onboard")
    assert spec["model"]["name"] == "TargetNet"
    assert "Baseline-Whisper" not in spec["model"]["name"]


def test_repo_url_paper_evidence_takes_priority_over_cli_value() -> None:
    text = """# Repo ASR

## Abstract
Repo-ASR is an automatic speech recognition model.

## Availability
Code is available at https://github.com/paper/repo-asr.

## Experiments
We report WER on LibriSpeech.
"""
    spec = extract_user_spec(
        case_id="repo",
        paper_text=text,
        raw_goal="onboard",
        repo_url="https://github.com/user/provided",
    )
    assert spec["source"]["repo_url"] == "https://github.com/paper/repo-asr"
    span = next(span for span in spec["evidence_spans"] if span["field"] == "source.repo_url")
    assert span["source"] == "paper_text"


def test_task_metric_and_dataset_basic_extraction() -> None:
    text = """# Basic ASR

## Abstract
Basic-ASR targets automatic speech recognition.

## Experiments
We evaluate on LibriSpeech and Common Voice and report WER and CER.
"""
    spec = extract_user_spec(case_id="basic", paper_text=text, raw_goal="onboard")
    assert spec["task"]["primary_task"] == "ASR"
    assert {"WER", "CER"} <= set(spec["evaluation"]["metrics"])
    assert {"LibriSpeech", "Common Voice"} <= set(spec["data"]["eval_datasets"])


def test_references_false_positive_filtering() -> None:
    text = """# Clean ASR

## Abstract
Clean-ASR is an automatic speech recognition model.

## Experiments
We report WER on LibriSpeech.

## References
[1] Music IR systems.
[2] https://huggingface.co/blog/asr-chunking
"""
    spec = extract_user_spec(case_id="clean", paper_text=text, raw_goal="onboard")
    assert spec["task"]["primary_task"] == "ASR"
    assert spec["source"]["model_card_url"] is None
    warnings = spec["confidence"]["extraction_warnings"]
    assert "ignored_task_candidate_from_references" in warnings
    assert "ignored_model_card_blog_url" in warnings
