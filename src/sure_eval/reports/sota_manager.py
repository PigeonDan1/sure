"""SOTA (State of the Art) baseline management.

This module manages SOTA baselines for all datasets in SURE Benchmark.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import yaml

from sure_eval.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SOTABaseline:
    """SOTA baseline for a dataset."""
    
    dataset: str
    metric: str
    score: float
    higher_is_better: bool
    sota_model: str
    description: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SOTABaseline:
        """Create from dictionary."""
        return cls(**data)


class SOTAManager:
    """Manager for SOTA baselines.
    
    This class loads and manages SOTA baselines from the reports/sota directory.
    It integrates with the RPS calculator to provide baseline scores.
    
    Attributes:
        sota_file: Path to the SOTA baseline YAML file
        baselines: Dictionary of dataset name to SOTABaseline
    """
    
    DEFAULT_SOTA_FILE = Path(__file__).parent.parent.parent.parent / "reports" / "sota" / "sota_baseline.yaml"
    
    def __init__(self, sota_file: str | Path | None = None) -> None:
        """Initialize SOTA manager.
        
        Args:
            sota_file: Path to SOTA baseline file. If None, uses default.
        """
        self.sota_file = Path(sota_file) if sota_file else self.DEFAULT_SOTA_FILE
        self._baselines: dict[str, SOTABaseline] = {}
        self._load_baselines()
    
    def _load_baselines(self) -> None:
        """Load baselines from YAML file."""
        if not self.sota_file.exists():
            logger.warning(f"SOTA file not found: {self.sota_file}")
            return
        
        try:
            with open(self.sota_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            
            if not data:
                logger.warning("SOTA file is empty")
                return
            
            for dataset_name, baseline_data in data.items():
                # Skip non-dataset keys (like comments section)
                if dataset_name.startswith("#") or not isinstance(baseline_data, dict):
                    continue
                
                # Ensure required fields
                if "metric" not in baseline_data or "score" not in baseline_data:
                    continue
                
                baseline = SOTABaseline(
                    dataset=dataset_name,
                    metric=baseline_data["metric"],
                    score=baseline_data["score"],
                    higher_is_better=baseline_data.get("higher_is_better", False),
                    sota_model=baseline_data.get("sota_model", "Unknown"),
                    description=baseline_data.get("description", ""),
                )
                self._baselines[dataset_name] = baseline
            
            logger.info(f"Loaded {len(self._baselines)} SOTA baselines")
            
        except Exception as e:
            logger.error(f"Failed to load SOTA baselines: {e}")
    
    def get_baseline(self, dataset: str) -> SOTABaseline | None:
        """Get SOTA baseline for a dataset.
        
        Args:
            dataset: Dataset name
            
        Returns:
            SOTABaseline if found, None otherwise
        """
        return self._baselines.get(dataset)

    def get_metric(self, dataset: str) -> str | None:
        """Get the canonical baseline metric for a dataset."""
        baseline = self.get_baseline(dataset)
        return baseline.metric if baseline else None
    
    def get_all_baselines(self) -> dict[str, SOTABaseline]:
        """Get all baselines.
        
        Returns:
            Dictionary of dataset name to SOTABaseline
        """
        return self._baselines.copy()
    
    def list_datasets(self) -> list[str]:
        """List all datasets with SOTA baselines.
        
        Returns:
            List of dataset names
        """
        return sorted(self._baselines.keys())
    
    def calculate_rps(self, dataset: str, score: float) -> float | None:
        """Calculate RPS for a score against SOTA.
        
        Args:
            dataset: Dataset name
            score: Score to evaluate
            
        Returns:
            RPS value (1.0 = SOTA, <1.0 = worse than SOTA, >1.0 = better than SOTA)
            None if no baseline exists for the dataset
        """
        baseline = self.get_baseline(dataset)
        if not baseline:
            return None
        
        if baseline.higher_is_better:
            # For metrics where higher is better (accuracy, BLEU)
            rps = score / baseline.score if baseline.score > 0 else 0.0
        else:
            # For metrics where lower is better (WER, CER, MER)
            if score == 0:
                rps = float("inf") if baseline.score > 0 else 1.0
            else:
                rps = baseline.score / score
        
        return rps
    
    def get_baseline_for_config(self, dataset: str) -> dict[str, Any] | None:
        """Get baseline in config-compatible format.
        
        This format is used by the RPS calculator in evaluation/rps.py
        
        Args:
            dataset: Dataset name
            
        Returns:
            Dictionary with metric, score, higher_is_better
        """
        baseline = self.get_baseline(dataset)
        if not baseline:
            return None
        
        return {
            "metric": baseline.metric,
            "score": baseline.score,
            "higher_is_better": baseline.higher_is_better,
        }
    
    def update_baseline(
        self,
        dataset: str,
        metric: str,
        score: float,
        higher_is_better: bool,
        sota_model: str,
        description: str = "",
    ) -> None:
        """Update or add a SOTA baseline.
        
        Args:
            dataset: Dataset name
            metric: Metric name (wer, cer, bleu, accuracy, etc.)
            score: SOTA score
            higher_is_better: Whether higher scores are better
            sota_model: Name of the SOTA model
            description: Optional description
        """
        baseline = SOTABaseline(
            dataset=dataset,
            metric=metric,
            score=score,
            higher_is_better=higher_is_better,
            sota_model=sota_model,
            description=description,
        )
        self._baselines[dataset] = baseline
        logger.info(f"Updated SOTA baseline for {dataset}: {score} ({metric})")
    
    def save_baselines(self) -> None:
        """Save baselines back to YAML file."""
        try:
            # Convert to YAML format
            data = {}
            for name, baseline in sorted(self._baselines.items()):
                data[name] = {
                    "metric": baseline.metric,
                    "score": baseline.score,
                    "higher_is_better": baseline.higher_is_better,
                    "sota_model": baseline.sota_model,
                    "description": baseline.description,
                }
            
            with open(self.sota_file, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=True)
            
            logger.info(f"Saved {len(self._baselines)} baselines to {self.sota_file}")
            
        except Exception as e:
            logger.error(f"Failed to save baselines: {e}")
    
    def get_sota_models(self) -> dict[str, str]:
        """Get mapping of dataset to SOTA model.
        
        Returns:
            Dictionary of dataset name to SOTA model name
        """
        return {
            name: baseline.sota_model
            for name, baseline in self._baselines.items()
        }
    
    def print_summary(self) -> None:
        """Print summary of SOTA baselines."""
        from rich.console import Console
        from rich.table import Table
        
        console = Console()
        console.print("\n[bold blue]SOTA Baselines Summary[/bold blue]\n")
        
        table = Table()
        table.add_column("Dataset", style="cyan")
        table.add_column("Metric", style="green")
        table.add_column("SOTA Score", style="yellow", justify="right")
        table.add_column("Direction", style="blue")
        table.add_column("SOTA Model", style="magenta")
        
        for name, baseline in sorted(self._baselines.items()):
            direction = "↑ higher" if baseline.higher_is_better else "↓ lower"
            table.add_row(
                name,
                baseline.metric.upper(),
                f"{baseline.score:.2f}",
                direction,
                baseline.sota_model,
            )
        
        console.print(table)
        console.print(f"\nTotal: {len(self._baselines)} datasets\n")
