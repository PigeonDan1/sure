from __future__ import annotations

from pathlib import Path


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
