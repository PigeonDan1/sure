"""RPS (Relative Performance Score) management for SURE-EVAL."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sure_eval.core.config import Config
from sure_eval.core.logging import get_logger
from sure_eval.reports.sota_manager import SOTAManager

logger = get_logger(__name__)


@dataclass
class EvaluationRecord:
    """Record of a single evaluation."""
    tool_name: str
    model_name: str | None
    dataset: str
    metric: str
    score: float
    rps: float | None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


class RPSCalculator:
    """Calculator for Relative Performance Score."""
    
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config.from_env()
        self.sota_manager = SOTAManager()
    
    def calculate(self, dataset: str, score: float) -> float | None:
        """Calculate RPS for a score on a dataset."""
        baseline = self.sota_manager.get_baseline(dataset)
        if baseline:
            normalized_score = self.sota_manager.normalize_evaluator_score_for_rps(
                baseline.metric,
                score,
                higher_is_better=baseline.higher_is_better,
            )
            normalized_baseline = self.sota_manager.normalize_baseline_score_for_rps(
                baseline.metric,
                baseline.score,
                higher_is_better=baseline.higher_is_better,
            )
            if baseline.higher_is_better:
                return normalized_score / normalized_baseline if normalized_baseline > 0 else 0.0
            if normalized_score == 0:
                return float("inf") if normalized_baseline > 0 else 1.0
            return normalized_baseline / normalized_score

        legacy_baseline = self.config.get_baseline(dataset)
        if not legacy_baseline:
            logger.warning("No baseline for dataset", dataset=dataset)
            return None

        normalized_score = SOTAManager.normalize_evaluator_score_for_rps(
            legacy_baseline.metric,
            score,
            higher_is_better=legacy_baseline.higher_is_better,
        )
        normalized_baseline = SOTAManager.normalize_baseline_score_for_rps(
            legacy_baseline.metric,
            legacy_baseline.score,
            higher_is_better=legacy_baseline.higher_is_better,
        )
        
        if legacy_baseline.higher_is_better:
            return normalized_score / normalized_baseline if normalized_baseline > 0 else 0.0
        if normalized_score == 0:
            return float("inf") if normalized_baseline > 0 else 1.0
        return normalized_baseline / normalized_score
    
    def get_baseline_info(self, dataset: str) -> dict[str, Any] | None:
        """Get baseline information for a dataset."""
        baseline = self.sota_manager.get_baseline(dataset)
        if baseline:
            return {
                "dataset": dataset,
                "metric": baseline.metric,
                "score": baseline.score,
                "higher_is_better": baseline.higher_is_better,
            }

        legacy_baseline = self.config.get_baseline(dataset)
        if not legacy_baseline:
            return None
        
        return {
            "dataset": dataset,
            "metric": legacy_baseline.metric,
            "score": legacy_baseline.score,
            "higher_is_better": legacy_baseline.higher_is_better,
        }


class EvaluationDatabase:
    """Database for evaluation results."""
    
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or "./results/evaluations.json")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[EvaluationRecord] = []
        self._load()
    
    def _load(self) -> None:
        """Load records from disk."""
        if not self.db_path.exists():
            return
        
        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            self._records = [EvaluationRecord(**record) for record in data]
            logger.info("Loaded evaluation records", count=len(self._records))
        except Exception as e:
            logger.error("Failed to load records", error=str(e))
    
    def _save(self) -> None:
        """Save records to disk."""
        try:
            with open(self.db_path, "w", encoding="utf-8") as f:
                json.dump(
                    [self._record_to_dict(r) for r in self._records],
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
        except Exception as e:
            logger.error("Failed to save records", error=str(e))
    
    def _record_to_dict(self, record: EvaluationRecord) -> dict[str, Any]:
        """Convert record to dictionary."""
        return {
            "tool_name": record.tool_name,
            "model_name": record.model_name,
            "dataset": record.dataset,
            "metric": record.metric,
            "score": record.score,
            "rps": record.rps,
            "timestamp": record.timestamp,
            "metadata": record.metadata,
        }
    
    def add_record(self, record: EvaluationRecord) -> None:
        """Add a new record."""
        self._records.append(record)
        self._save()
        logger.info(
            "Added evaluation record",
            tool=record.tool_name,
            dataset=record.dataset,
            rps=record.rps,
        )
    
    def get_records(
        self,
        tool_name: str | None = None,
        dataset: str | None = None,
        model_name: str | None = None,
    ) -> list[EvaluationRecord]:
        """Get records with optional filtering."""
        records = self._records
        
        if tool_name:
            records = [r for r in records if r.tool_name == tool_name]
        if dataset:
            records = [r for r in records if r.dataset == dataset]
        if model_name:
            records = [r for r in records if r.model_name == model_name]
        
        return records
    
    def get_best_tool(self, dataset: str) -> tuple[str, float] | None:
        """Get the best tool for a dataset by RPS."""
        latest_records = self._latest_records_by_tool(dataset)
        if not latest_records:
            return None
        
        best = max(latest_records.values(), key=lambda r: r.rps or 0)
        return (best.tool_name, best.rps)
    
    def get_tool_ranking(self, dataset: str) -> list[tuple[str, float]]:
        """Get tool ranking for a dataset using each tool's latest result."""
        latest_records = self._latest_records_by_tool(dataset)
        ranking = sorted(
            ((tool_name, record.rps) for tool_name, record in latest_records.items() if record.rps is not None),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranking

    def _latest_records_by_tool(self, dataset: str) -> dict[str, EvaluationRecord]:
        """Get the latest record for each tool on a dataset."""
        latest_records: dict[str, EvaluationRecord] = {}
        for record in self.get_records(dataset=dataset):
            if record.rps is None:
                continue
            existing = latest_records.get(record.tool_name)
            if existing is None or record.timestamp > existing.timestamp:
                latest_records[record.tool_name] = record
        return latest_records


class RPSManager:
    """Manager for RPS calculations and records."""
    
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config.from_env()
        self.calculator = RPSCalculator(config)
        self.database = EvaluationDatabase()
    
    def evaluate_and_record(
        self,
        tool_name: str,
        dataset: str,
        score: float,
        metric: str | None = None,
        model_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EvaluationRecord:
        """Evaluate and record result."""
        # Get metric if not provided
        if not metric:
            metric = self.config.get_default_metric(
                self._get_task_from_dataset(dataset)
            )
        
        # Calculate RPS
        rps = self.calculator.calculate(dataset, score)
        
        # Create record
        record = EvaluationRecord(
            tool_name=tool_name,
            model_name=model_name,
            dataset=dataset,
            metric=metric,
            score=score,
            rps=rps,
            metadata=metadata or {},
        )
        
        # Add to database
        self.database.add_record(record)
        
        return record
    
    def _get_task_from_dataset(self, dataset: str) -> str:
        """Get task type from dataset name."""
        dataset_def = self.config.get_dataset(dataset)
        if dataset_def:
            return dataset_def.task
        
        # Guess from name
        if "asr" in dataset.lower():
            return "ASR"
        elif "s2tt" in dataset.lower() or "covost" in dataset.lower():
            return "S2TT"
        elif "sd" in dataset.lower():
            return "SD"
        elif "ser" in dataset.lower() or "emotion" in dataset.lower():
            return "SER"
        
        return "ASR"  # Default
    
    def get_recommendation(self, dataset: str) -> dict[str, Any]:
        """Get tool recommendation for a dataset."""
        best = self.database.get_best_tool(dataset)
        ranking = self.database.get_tool_ranking(dataset)
        baseline = self.calculator.get_baseline_info(dataset)
        
        return {
            "dataset": dataset,
            "best_tool": best[0] if best else None,
            "best_rps": best[1] if best else None,
            "ranking": ranking,
            "baseline": baseline,
        }
