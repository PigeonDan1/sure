from __future__ import annotations

from pathlib import Path

import pytest


def _write_key_text(path: Path, rows: list[tuple[str, str]]) -> None:
    path.write_text("".join(f"{key}\t{text}\n" for key, text in rows), encoding="utf-8")


def test_asr_metric_modules_match_sure_evaluator(tmp_path: Path) -> None:
    from sure_eval.evaluation.asr.metrics import CERMetric, WERMetric
    from sure_eval.evaluation.registry import MetricRegistry
    from sure_eval.evaluation.sure_evaluator import SUREEvaluator

    ref_zh = tmp_path / "ref_zh.txt"
    hyp_zh = tmp_path / "hyp_zh.txt"
    _write_key_text(ref_zh, [("utt1", "你好世界")])
    _write_key_text(hyp_zh, [("utt1", "你好世")])
    evaluator_cer = SUREEvaluator(language="zh").evaluate("ASR", str(ref_zh), str(hyp_zh), tochar=True)
    registry_cer = MetricRegistry.get_metric("cer").calculate("你好世", "你好世界", language="zh")
    task_cer = CERMetric().calculate("你好世", "你好世界", language="zh")
    assert task_cer == registry_cer
    assert task_cer.score == evaluator_cer["score"]

    ref_en = tmp_path / "ref_en.txt"
    hyp_en = tmp_path / "hyp_en.txt"
    _write_key_text(ref_en, [("utt1", "hello world")])
    _write_key_text(hyp_en, [("utt1", "hello brave world")])
    evaluator_wer = SUREEvaluator(language="en").evaluate("ASR", str(ref_en), str(hyp_en))
    registry_wer = MetricRegistry.get_metric("wer").calculate("hello brave world", "hello world")
    task_wer = WERMetric().calculate("hello brave world", "hello world")
    assert task_wer == registry_wer
    assert task_wer.score == evaluator_wer["score"]


def test_asr_ier_uses_corpus_level_insertions() -> None:
    from sure_eval.evaluation.asr.metrics import compute_asr_error_counts, compute_ier

    counts = compute_asr_error_counts("a b c", "a x b c")
    assert counts["insertions"] == 1
    assert counts["reference_words"] == 3
    assert compute_ier("a b c", "a x b c") == pytest.approx(33.3333, abs=1e-4)

    counts = compute_asr_error_counts("a b c", "a b c")
    assert counts["insertions"] == 0
    assert counts["reference_words"] == 3
    assert compute_ier("a b c", "a b c") == 0

    counts = compute_asr_error_counts("a b c", "a b c x y")
    assert counts["insertions"] == 2
    assert counts["reference_words"] == 3
    assert compute_ier("a b c", "a b c x y") == pytest.approx(66.6667, abs=1e-4)

    refs = ["a b c", "d e"]
    hyps = ["a x b c", "d e y"]
    counts = compute_asr_error_counts(refs, hyps)
    assert counts["insertions"] == 2
    assert counts["reference_words"] == 5
    assert compute_ier(refs, hyps) == pytest.approx(40.0)


def test_classification_metric_module_matches_legacy_metrics() -> None:
    from sure_eval.evaluation.classification.metrics import AccuracyMetric
    from sure_eval.evaluation.registry import MetricRegistry

    registry = MetricRegistry.get_metric("accuracy").calculate_batch(["happy", "female"], ["hap", "woman"])
    task = AccuracyMetric().calculate_batch(["happy", "female"], ["hap", "woman"])
    assert task == registry


def test_s2tt_metric_module_is_registry_metric() -> None:
    from sure_eval.evaluation.registry import MetricRegistry
    from sure_eval.evaluation.s2tt.metrics import BLEUMetric

    assert isinstance(MetricRegistry.get_metric("bleu"), BLEUMetric)
