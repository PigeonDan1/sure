from __future__ import annotations

from pathlib import Path

import pytest

from sure_eval.agent.evaluator import AutonomousEvaluator
from sure_eval.core.config import Config
from sure_eval.evaluation.sure_evaluator import SUREEvaluator


def _make_config(tmp_path: Path) -> Config:
    config = Config.from_env()
    config.data.datasets = str(tmp_path / "datasets")
    config.data.results = str(tmp_path / "results")
    return config


def test_asr_char_metric_is_reported_as_cer(tmp_path: Path) -> None:
    ref_file = tmp_path / "ref.txt"
    hyp_file = tmp_path / "hyp.txt"
    ref_file.write_text("utt1\tabc\n", encoding="utf-8")
    hyp_file.write_text("utt1\tadc\n", encoding="utf-8")

    result = SUREEvaluator(language="en").evaluate("ASR", str(ref_file), str(hyp_file), tochar=True)

    assert "cer" in result
    assert "wer" not in result
    assert result["score"] == result["cer"]


def test_extract_score_respects_requested_metric(tmp_path: Path) -> None:
    evaluator = AutonomousEvaluator(_make_config(tmp_path))

    assert evaluator._extract_score({"cer": 0.25, "wer": 0.5}, "ASR", "cer") == 0.25
    assert evaluator._extract_score({"cer": 0.25, "wer": 0.5}, "ASR", "wer") == 0.5
    assert evaluator._extract_score({"bleu": 18.0, "chrf": 42.0}, "S2TT", "chrf") == 42.0


def test_validate_metric_rejects_unsupported_metric(tmp_path: Path) -> None:
    evaluator = AutonomousEvaluator(_make_config(tmp_path))

    with pytest.raises(ValueError, match="Unsupported metric"):
        evaluator._validate_metric("ASR", "bleu")


def test_extract_score_supports_bleu_char_alias(tmp_path: Path) -> None:
    evaluator = AutonomousEvaluator(_make_config(tmp_path))

    assert evaluator._extract_score({"bleu": 18.0, "bleu_char": 19.5}, "S2TT", "bleu_char") == 19.5


def test_default_metric_prefers_dataset_sota_metric(tmp_path: Path) -> None:
    evaluator = AutonomousEvaluator(_make_config(tmp_path))

    jsonl_path = tmp_path / "covost2_en2zh.jsonl"
    jsonl_path.write_text(
        '{"key":"utt1","path":"sample.wav","target":"hello","task":"S2TT","language":"en","dataset":"covost2_en2zh"}\n',
        encoding="utf-8",
    )
    pred_file = tmp_path / "pred.txt"
    pred_file.write_text("utt1\thello\n", encoding="utf-8")

    evaluator._load_dataset = lambda dataset: (jsonl_path, {"task": "S2TT", "language": "en"})  # type: ignore[method-assign]
    evaluator._load_samples = lambda path, max_samples: [{"key": "utt1", "path": "sample.wav", "target": "hello"}]  # type: ignore[method-assign]
    evaluator._run_tool = lambda tool_name, samples, task: ["hello"]  # type: ignore[method-assign]
    evaluator._save_predictions = lambda samples, predictions: str(pred_file)  # type: ignore[method-assign]
    evaluator._evaluate_with_sure_evaluator = lambda *args: {"bleu_char": 46.25, "score": 46.25}  # type: ignore[method-assign]
    evaluator.rps_manager.evaluate_and_record = lambda **kwargs: None  # type: ignore[method-assign]

    result = evaluator.evaluate_tool("demo_tool", "covost2_en2zh")

    assert result.metric == "bleu_char"
