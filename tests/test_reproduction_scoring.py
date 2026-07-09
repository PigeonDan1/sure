from __future__ import annotations

import json
from pathlib import Path

import pytest

from sure_eval.reproduction.scoring import (
    compute_reproduction_score,
    normalize_metric_name,
    score_metric_item,
)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_higher_is_better_single_metric() -> None:
    item = score_metric_item("MELD", "WA", 50, 45)
    assert item.match_score == pytest.approx(90.0)
    assert item.local_better_than_paper is False

    better = score_metric_item("MELD", "WA", 50, 55)
    assert better.match_score == pytest.approx(100.0)
    assert better.local_better_than_paper is True


def test_lower_is_better_single_metric() -> None:
    item = score_metric_item("LibriSpeech", "WER", 10, 12)
    assert item.match_score == pytest.approx(83.3333333333)
    assert item.local_better_than_paper is False

    better = score_metric_item("LibriSpeech", "WER", 10, 8)
    assert better.match_score == pytest.approx(100.0)
    assert better.local_better_than_paper is True


def test_meld_three_metrics_equal_weight(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 90})
    _write_json(
        run / "paper_value_comparison.json",
        {
            "dataset": "MELD",
            "metrics": [
                {"metric": "WA", "paper_value": 51.88, "local_value": 47.78},
                {"metric": "UA", "paper_value": 28.03, "local_value": 26.02},
                {"metric": "WF1", "paper_value": 48.70, "local_value": 45.28},
            ],
        },
    )

    report = compute_reproduction_score(run)
    expected = ((47.78 / 51.88) + (26.02 / 28.03) + (45.28 / 48.70)) / 3 * 100
    assert report.metric_agreement_score == pytest.approx(expected)
    assert report.metric_agreement_score == pytest.approx(92.635, abs=1e-3)


def test_meld_three_metrics_weighted(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 90})
    _write_json(
        run / "paper_value_comparison.json",
        {
            "dataset": "MELD",
            "metrics": [
                {"metric": "WA", "paper_value": 51.88, "local_value": 47.78},
                {"metric": "UA", "paper_value": 28.03, "local_value": 26.02},
                {"metric": "WF1", "paper_value": 48.70, "local_value": 45.28},
            ],
        },
    )

    report = compute_reproduction_score(run, metric_weights={"WF1": 0.5, "WA": 0.25, "UA": 0.25})
    expected = (
        0.25 * (47.78 / 51.88 * 100)
        + 0.25 * (26.02 / 28.03 * 100)
        + 0.5 * (45.28 / 48.70 * 100)
    )
    assert report.metric_agreement_score == pytest.approx(expected)


def test_single_metric_object_without_metrics_array(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(run / "paper_confidence_report.json", {"score": 0.8})
    _write_json(
        run / "paper_value_comparison.json",
        {
            "metric": "WER",
            "paper_value": 1.38,
            "local_value": 1.63,
            "dataset": "LibriSpeech test-clean",
        },
    )

    report = compute_reproduction_score(run)
    assert report.paper_side_score == pytest.approx(80.0)
    assert report.metric_agreement_score == pytest.approx(1.38 / 1.63 * 100)
    assert report.dataset_scores[0].dataset == "LibriSpeech test-clean"


def test_multi_dataset_aggregates_dataset_first(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 90})
    _write_json(
        run / "paper_value_comparison.json",
        {
            "datasets": [
                {
                    "dataset": "MELD",
                    "metrics": [
                        {"metric": "WA", "paper_value": 50, "local_value": 40},
                        {"metric": "UA", "paper_value": 50, "local_value": 50},
                    ],
                },
                {
                    "dataset": "IEMOCAP",
                    "metrics": [
                        {"metric": "WF1", "paper_value": 80, "local_value": 40},
                    ],
                },
            ]
        },
    )

    report = compute_reproduction_score(run)
    assert report.dataset_scores[0].metric_agreement_score == pytest.approx(90.0)
    assert report.dataset_scores[1].metric_agreement_score == pytest.approx(50.0)
    assert report.metric_agreement_score == pytest.approx(70.0)


def test_local_better_than_paper_capped_to_100(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 90})
    _write_json(run / "paper_value_comparison.json", {"metric": "Accuracy", "paper_value": 80, "local_value": 90})

    report = compute_reproduction_score(run)
    item = report.dataset_scores[0].metric_items[0]
    assert item.match_score == pytest.approx(100.0)
    assert item.local_better_than_paper is True


def test_missing_local_value_excluded_and_warning_recorded(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 90})
    _write_json(
        run / "paper_value_comparison.json",
        {
            "dataset": "MELD",
            "metrics": [
                {"metric": "WA", "paper_value": 50, "local_value": 45},
                {"metric": "UA", "paper_value": 30},
            ],
        },
    )

    report = compute_reproduction_score(run)
    assert report.metric_agreement_score == pytest.approx(90.0)
    assert report.dataset_scores[0].metric_items[1].status == "not_evaluable"
    assert any("UA excluded" in warning for warning in report.warnings)


def test_readiness_full_and_partial_scores(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 90})
    _write_json(run / "paper_value_comparison.json", {"metric": "WA", "paper_value": 50, "local_value": 50})
    _write_json(
        run / "runtime_readiness.json",
        {
            "import": True,
            "load": "passed",
            "infer": "success",
            "contract": "ready",
            "smoke_test": "ok",
        },
    )
    assert compute_reproduction_score(run).runtime_readiness_score == pytest.approx(100.0)

    _write_json(
        run / "runtime_readiness.json",
        {
            "import": True,
            "load": False,
            "infer": "success",
            "contract": "unknown",
            "smoke_test": "blocked",
        },
    )
    report = compute_reproduction_score(run)
    assert report.runtime_readiness_score == pytest.approx(45.0)
    assert report.readiness_breakdown["load"]["passed"] is False


def test_readiness_evidence_array_check_scores(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 90})
    _write_json(run / "paper_value_comparison.json", {"metric": "WA", "paper_value": 50, "local_value": 50})
    _write_json(
        run / "tool_readiness_routing.json",
        {
            "evidence": [
                {"check": "validate_import", "status": "success"},
                {"check": "validate_load", "passed": True},
                {"check": "validate_infer", "status": "passed"},
                {"check": "io_contract", "status": "ok"},
                {"check": "smoke_test", "status": "success"},
            ]
        },
    )

    report = compute_reproduction_score(run)

    assert report.runtime_readiness_score == pytest.approx(100.0)
    assert report.readiness_breakdown["load"]["evidence_scope"] == "run_local"
    assert report.readiness_breakdown["load"]["reason"] in {"evidence_array", "object_check_status"}


def test_readiness_completed_array_semantics_score(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 90})
    _write_json(run / "paper_value_comparison.json", {"metric": "WA", "paper_value": 50, "local_value": 50})
    _write_json(
        run / "tool_readiness_routing.json",
        {
            "completed": [
                "validate import passed",
                "validate load succeeded",
                "validate infer completed",
                "io contract validated",
                "offline smoke succeeded",
            ]
        },
    )

    report = compute_reproduction_score(run)

    assert report.runtime_readiness_score == pytest.approx(100.0)
    assert report.readiness_breakdown["infer"]["reason"] == "completed_array_semantic_match"


def test_server_ready_true_does_not_imply_full_readiness(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 90})
    _write_json(run / "paper_value_comparison.json", {"metric": "WA", "paper_value": 50, "local_value": 50})
    _write_json(
        run / "tool_readiness_routing.json",
        {
            "server_ready": True,
            "tool_readiness_state": "server_ready",
        },
    )

    report = compute_reproduction_score(run)

    assert report.runtime_readiness_score == pytest.approx(0.0)
    assert all(item["status"] == "unknown" for item in report.readiness_breakdown.values())


def test_model_local_verdict_fallback_scores_when_model_dir_declared(tmp_path: Path) -> None:
    run = tmp_path / "run"
    model_dir = tmp_path / "models" / "demo_model"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 90})
    _write_json(
        run / "paper_value_comparison.json",
        {
            "model_dir": str(model_dir),
            "metric": "WA",
            "paper_value": 50,
            "local_value": 50,
        },
    )
    _write_json(
        model_dir / "artifacts" / "verdict.json",
        {
            "status": "PASSED",
            "checks": {
                "validate_import": "passed",
                "validate_load": "passed",
                "validate_infer": "passed",
                "validate_contract": "passed",
                "server_declaration_smoke": "passed",
            },
        },
    )

    report = compute_reproduction_score(run)

    assert report.runtime_readiness_score == pytest.approx(100.0)
    assert report.readiness_breakdown["import"]["evidence_scope"] == "model_local_onboarding"
    assert str(model_dir / "artifacts" / "verdict.json") in report.evidence_files


def test_run_local_failure_beats_model_local_success(tmp_path: Path) -> None:
    run = tmp_path / "run"
    model_dir = tmp_path / "models" / "demo_model"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 90})
    _write_json(
        run / "paper_value_comparison.json",
        {
            "model_dir": str(model_dir),
            "metric": "WA",
            "paper_value": 50,
            "local_value": 50,
        },
    )
    _write_json(run / "runtime_readiness.json", {"load": False})
    _write_json(
        model_dir / "artifacts" / "verdict.json",
        {
            "checks": {
                "validate_import": "passed",
                "validate_load": "passed",
                "validate_infer": "passed",
                "validate_contract": "passed",
                "server_declaration_smoke": "passed",
            },
        },
    )

    report = compute_reproduction_score(run)

    assert report.runtime_readiness_score == pytest.approx(80.0)
    assert report.readiness_breakdown["load"]["passed"] is False
    assert report.readiness_breakdown["load"]["evidence_scope"] == "run_local"


def test_comparability_factor_clamped_and_warns(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 90})
    _write_json(run / "paper_value_comparison.json", {"metric": "WA", "paper_value": 50, "local_value": 50})

    report = compute_reproduction_score(run, comparability_factor=1.5)
    assert report.comparability_factor == pytest.approx(1.0)
    assert any("comparability_factor" in warning for warning in report.warnings)

    report = compute_reproduction_score(run, comparability_factor=-1)
    assert report.comparability_factor == pytest.approx(0.01)
    assert any("clamped" in warning for warning in report.warnings)


def test_normalize_metric_name_handles_variants() -> None:
    assert normalize_metric_name("#Ins.&Del.") == normalize_metric_name("ins del")
    assert normalize_metric_name("SER-error") == normalize_metric_name("ser_error")


def test_excluded_metric_is_reported_but_not_scored(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 90})
    _write_json(
        run / "paper_value_comparison.json",
        {
            "dataset": "AMI",
            "metrics": [
                {"metric": "WER", "paper_value": 10, "local_value": 12},
                {"metric": "IER", "paper_value": 5, "local_value": 10},
                {"metric": "5-Dup", "paper_value": 3, "local_value": 100},
            ],
        },
    )

    report = compute_reproduction_score(run, excluded_metrics=["5-Dup"])
    expected = ((10 / 12 * 100) + (5 / 10 * 100)) / 2
    assert report.metric_agreement_score == pytest.approx(expected)
    items = {item.metric: item for item in report.dataset_scores[0].metric_items}
    assert items["5-Dup"].status == "excluded_from_score"
    assert items["5-Dup"].reason == "excluded_by_user"
    assert items["5-Dup"].paper_value == pytest.approx(3.0)
    assert items["5-Dup"].local_value == pytest.approx(100.0)
    assert items["5-Dup"].direction == "lower_is_better"


def test_included_in_score_false_auto_excludes_metric(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 90})
    _write_json(
        run / "paper_value_comparison.json",
        {
            "dataset": "AMI",
            "metrics": [
                {"metric": "WER", "paper_value": 10, "local_value": 10},
                {
                    "metric": "5-Dup",
                    "paper_value": 3,
                    "local_value": 100,
                    "included_in_score": False,
                },
            ],
        },
    )

    report = compute_reproduction_score(run)
    assert report.metric_agreement_score == pytest.approx(100.0)
    items = {item.metric: item for item in report.dataset_scores[0].metric_items}
    assert items["5-Dup"].status == "excluded_from_score"
    assert items["5-Dup"].reason == "auxiliary_reported_only"


def test_whisperx_paper_local_shape_excludes_five_dup(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 86})
    _write_json(
        run / "paper_value_comparison.json",
        {
            "paper": {
                "dataset": "TED-LIUM",
                "wer_percent": 9.7,
                "ier_percent": 6.7,
                "five_dup": 189,
            },
            "local": {
                "dataset_id": "tedlium_release3_legacy_test_longform",
                "wer_percent": 14.695743932465705,
                "ier_percent": 4.030953218431234,
                "five_dup": None,
            },
            "old_run_values_if_found": {
                "wer_percent": 15.656,
                "ier_percent": 3.8938,
                "five_dup_total": 170,
            },
        },
    )

    report = compute_reproduction_score(run, excluded_metrics=["5-Dup"])

    expected = ((9.7 / 15.656 * 100) + 100.0) / 2
    assert report.metric_agreement_score == pytest.approx(expected)
    items = {item.metric: item for item in report.dataset_scores[0].metric_items}
    assert items["WER"].status == "evaluated"
    assert items["WER"].direction == "lower_is_better"
    assert items["WER"].local_value == pytest.approx(15.656)
    assert items["IER"].status == "evaluated"
    assert items["IER"].direction == "lower_is_better"
    assert items["IER"].local_better_than_paper is True
    assert items["5-Dup"].status == "excluded_from_score"
    assert items["5-Dup"].weight == pytest.approx(0.0)
    assert items["5-Dup"].local_value == pytest.approx(170.0)


def test_f5_comparison_shape_scores_wer_and_sim_excludes_rtf(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_json(run / "paper_confidence_report.json", {"overall_percent": 65})
    _write_json(
        run / "paper_value_comparison.json",
        {
            "full_core_wavlm_metrics": {
                "status": "success",
                "num_success": 1127,
            },
            "previous_full_mos_attempt": {
                "status": "partial",
                "num_success": 26,
                "note": "Historical partial MOS/all-metrics attempt only; not used for the final WER/WavLM-only comparison.",
            },
            "comparisons": [
                {
                    "paper_metric": "WER",
                    "dataset": "LibriSpeech-PC test-clean",
                    "paper_value": 2.42,
                    "local_value": 2.079051800392883,
                    "direction": "lower_is_better",
                    "status": "evaluated",
                },
                {
                    "paper_metric": "SIM-o",
                    "dataset": "LibriSpeech-PC test-clean",
                    "paper_value": 0.66,
                    "local_value": 0.623810023058421,
                    "direction": "higher_is_better",
                    "status": "evaluated_via_wavlm_large",
                },
                {
                    "paper_metric": "RTF",
                    "dataset": "LibriSpeech-PC test-clean",
                    "paper_value": 0.31,
                    "local_value": None,
                    "direction": "lower_is_better",
                    "status": "not_evaluated",
                },
            ],
        },
    )

    report = compute_reproduction_score(run)

    expected = ((100.0) + (0.623810023058421 / 0.66 * 100)) / 2
    assert report.metric_agreement_score == pytest.approx(expected)
    items = {item.metric: item for item in report.dataset_scores[0].metric_items}
    assert items["WER"].status == "evaluated"
    assert items["WER"].direction == "lower_is_better"
    assert items["WER"].match_score == pytest.approx(100.0)
    assert items["SIM-o"].status == "evaluated"
    assert items["SIM-o"].direction == "higher_is_better"
    assert items["RTF"].status == "excluded_from_score"
    assert items["RTF"].reason == "not_evaluated"
    assert items["RTF"].weight == pytest.approx(0.0)
