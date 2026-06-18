from __future__ import annotations

import json
from pathlib import Path

from scripts.run_reproduction_workflow import run
from sure_eval.core.config import Config
from sure_eval.datasets.dataset_manager import DatasetManager
from sure_eval.reproduction.schema import DatasetTarget, LocalEval, ModelTarget, PaperClaim, ReproductionTarget
from sure_eval.reproduction.workflow import compare_paper_and_local, default_metric_direction


def _claim(metric: str, direction: str, value: float) -> PaperClaim:
    return PaperClaim(
        paper_id="paper_x",
        paper_title="Generic Paper",
        source_pdf=None,
        evidence_page=None,
        evidence_table="Table 1",
        evidence_text="A generic result row.",
        model_name="model_x",
        dataset="dataset_x",
        split="test",
        task="ASR" if metric.lower() in {"wer", "cer"} else "SER",
        metric=metric,
        metric_direction=direction,  # type: ignore[arg-type]
        paper_value=value,
        paper_value_unit="percent",
    )


def _local(metric: str, score: float, task: str = "SER") -> LocalEval:
    return LocalEval(
        protocol_id="fixture",
        prediction_file=None,
        eval_result_file=None,
        metric=metric,
        score=score,
        score_unit="percent",
        num_samples=1,
        evaluator_version="test",
        model_name="model_x",
        dataset="dataset_x",
        split="test",
        task=task,
    )


def _make_config(tmp_path: Path) -> Config:
    config = Config.from_env()
    config.data.datasets = str(tmp_path / "datasets")
    return config


def test_metric_direction_higher_is_better() -> None:
    assert default_metric_direction("Accuracy") == "higher_is_better"
    assert default_metric_direction("F1") == "higher_is_better"
    assert default_metric_direction("UAR") == "higher_is_better"
    assert default_metric_direction("BLEU") == "higher_is_better"

    comparison = compare_paper_and_local(_claim("F1", "higher_is_better", 48.7), _local("F1", 50.0))
    assert comparison.status == "slightly_different"
    assert "better" in comparison.reason


def test_metric_direction_lower_is_better() -> None:
    assert default_metric_direction("WER") == "lower_is_better"
    assert default_metric_direction("CER") == "lower_is_better"
    assert default_metric_direction("DER") == "lower_is_better"
    assert default_metric_direction("JER") == "lower_is_better"

    comparison = compare_paper_and_local(_claim("WER", "lower_is_better", 10.0), _local("WER", 9.0, task="ASR"))
    assert comparison.status == "significantly_different"
    assert "better" in comparison.reason
    assert comparison.absolute_delta == 1.0
    assert comparison.relative_delta == 0.1


def test_reproduction_target_schema_generic() -> None:
    target = ReproductionTarget(
        paper_claim=_claim("BLEU", "higher_is_better", 22.5),
        model_target=ModelTarget(model_name="any_supported_model", model_dir="src/sure_eval/models/any_supported_model"),
        dataset_target=DatasetTarget(
            dataset_name="any_dataset",
            jsonl_path="data/datasets/any_dataset.jsonl",
            source_format="jsonl",
            task="S2TT",
            split="test",
            label_schema={"reference_fields": ["translation", "reference_text"]},
            num_samples=2,
        ),
    )

    data = target.to_dict()
    assert data["paper_claim"]["model_name"] == "model_x"
    assert data["dataset_target"]["task"] == "S2TT"
    assert "MELD" not in json.dumps(data)
    assert "emotion2vec" not in json.dumps(data)


def test_meld_emotion2vec_still_supported_as_case(tmp_path: Path) -> None:
    final = run(
        Path("examples/reproduction/meld_emotion2vec_case.json"),
        tmp_path / "meld_case",
    )

    assert final["paper_claims"][0]["dataset"] == "MELD"
    assert final["paper_claims"][0]["model_name"] == "ser_emotion2vec_plus_base"
    assert final["paper_claims"][0]["task"] == "SER"
    assert (tmp_path / "meld_case" / "final_reproduction_report.json").exists()


def test_ser_label_field_not_forced_to_text(tmp_path: Path) -> None:
    manager = DatasetManager(_make_config(tmp_path))
    csv_dir = manager.sure_dir / "SURE_Test_csv"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_path = csv_dir / "IEMOCAP_SER_test.csv"
    csv_path.write_text("audio,label\nIEMOCAP_SER_test/sample.wav,hap\n", encoding="utf-8")

    jsonl_path = manager._convert_csv_to_jsonl(csv_path)
    row = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])

    assert row["task"] == "SER"
    assert row["target"] == "hap"
    assert row["label"] == "hap"
    assert row["emotion"] == "hap"
    assert "text" not in row


def test_asr_text_field_still_supported(tmp_path: Path) -> None:
    manager = DatasetManager(_make_config(tmp_path))
    csv_dir = manager.sure_dir / "SURE_Test_csv"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_path = csv_dir / "aishell1-test_ASR.csv"
    csv_path.write_text("audio,text\naishell-1-test/sample.wav,你好\n", encoding="utf-8")

    jsonl_path = manager._convert_csv_to_jsonl(csv_path)
    row = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])

    assert row["task"] == "ASR"
    assert row["target"] == "你好"
    assert row["text"] == "你好"
    assert row["transcript"] == "你好"
