from __future__ import annotations

from pathlib import Path

from sure_eval.evaluation.rps import EvaluationDatabase, EvaluationRecord, RPSCalculator


def test_rps_calculator_uses_sota_baseline() -> None:
    calculator = RPSCalculator()

    # aishell1 SOTA in reports/sota/sota_baseline.yaml is CER 0.80, lower is better.
    assert calculator.calculate("aishell1", 1.6) == 0.5


def test_rps_calculator_handles_zero_error_as_better_than_sota() -> None:
    calculator = RPSCalculator()

    assert calculator.calculate("aishell1", 0.0) == float("inf")


def test_evaluation_database_ranking_uses_latest_result(tmp_path: Path) -> None:
    db = EvaluationDatabase(tmp_path / "evaluations.json")
    db.add_record(EvaluationRecord(tool_name="tool_a", model_name=None, dataset="aishell1", metric="cer", score=1.0, rps=0.8, timestamp="2026-01-01T00:00:00"))
    db.add_record(EvaluationRecord(tool_name="tool_a", model_name=None, dataset="aishell1", metric="cer", score=2.0, rps=0.4, timestamp="2026-02-01T00:00:00"))
    db.add_record(EvaluationRecord(tool_name="tool_b", model_name=None, dataset="aishell1", metric="cer", score=1.2, rps=0.7, timestamp="2026-01-15T00:00:00"))

    assert db.get_best_tool("aishell1") == ("tool_b", 0.7)
    assert db.get_tool_ranking("aishell1") == [("tool_b", 0.7), ("tool_a", 0.4)]
