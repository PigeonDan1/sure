"""Model performance report management.

This module manages model performance reports from SURE Benchmark.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sure_eval.core.logging import get_logger
from sure_eval.reports.sota_manager import SOTAManager

logger = get_logger(__name__)


@dataclass
class DatasetResult:
    """Result on a specific dataset."""
    dataset: str
    task: str
    metric: str
    raw_score: float
    rps: float
    is_sota: bool = False
    note: str = ""


@dataclass
class ModelReport:
    """Performance report for a model."""
    
    model_name: str
    full_name: str
    organization: str
    model_size: str
    release_date: str
    supported_tasks: list[str] = field(default_factory=list)
    paper: str = ""
    results: dict[str, DatasetResult] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    
    def get_result(self, dataset: str) -> DatasetResult | None:
        """Get result for a specific dataset."""
        return self.results.get(dataset)
    
    def get_average_rps(self) -> float:
        """Calculate average RPS across all datasets."""
        if not self.results:
            return 0.0
        return sum(r.rps for r in self.results.values()) / len(self.results)
    
    def get_sota_count(self) -> int:
        """Get number of SOTA results."""
        return sum(1 for r in self.results.values() if r.is_sota)


class ReportManager:
    """Manager for model performance reports.
    
    This class loads and manages model performance reports from the
    reports/models directory. It provides utilities for comparing
    models and tracking performance over time.
    
    Attributes:
        report_file: Path to the model performance report JSON file
        models: Dictionary of model name to ModelReport
        sota_manager: SOTAManager instance for baseline lookups
    """
    
    DEFAULT_REPORT_FILE = Path(__file__).parent.parent.parent.parent / "reports" / "models" / "model_performance_report.json"
    
    def __init__(self, report_file: str | Path | None = None) -> None:
        """Initialize report manager.
        
        Args:
            report_file: Path to report file. If None, uses default.
        """
        self.report_file = Path(report_file) if report_file else self.DEFAULT_REPORT_FILE
        self._models: dict[str, ModelReport] = {}
        self.sota_manager = SOTAManager()
        self._load_reports()
    
    def _load_reports(self) -> None:
        """Load model reports from JSON file."""
        if not self.report_file.exists():
            logger.warning(f"Report file not found: {self.report_file}")
            return
        
        try:
            with open(self.report_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            models_data = data.get("models", {})
            
            for model_name, model_data in models_data.items():
                report = self._parse_model_report(model_name, model_data)
                self._models[model_name] = report
            
            logger.info(f"Loaded {len(self._models)} model reports")
            
        except Exception as e:
            logger.error(f"Failed to load model reports: {e}")
    
    def _parse_model_report(self, model_name: str, data: dict) -> ModelReport:
        """Parse model data into ModelReport."""
        results: dict[str, DatasetResult] = {}
        
        # Parse track I results (frontend)
        track_i = data.get("results", {}).get("track_i_frontend", {})
        for dataset_name, result_data in track_i.items():
            result = DatasetResult(
                dataset=result_data.get("dataset", dataset_name),
                task="ASR",  # Track I is all ASR
                metric=result_data.get("metric", ""),
                raw_score=result_data.get("raw_score", 0.0),
                rps=result_data.get("rps", 0.0),
                is_sota=result_data.get("is_sota", False),
                note=result_data.get("note", ""),
            )
            results[dataset_name] = result
        
        # Parse track II results (horizontal)
        track_ii = data.get("results", {}).get("track_ii_horizontal", {})
        for dataset_name, result_data in track_ii.items():
            result = DatasetResult(
                dataset=result_data.get("dataset", dataset_name),
                task=result_data.get("task", "ASR"),
                metric=result_data.get("metric", ""),
                raw_score=result_data.get("raw_score", 0.0),
                rps=result_data.get("rps", 0.0),
                is_sota=result_data.get("is_sota", False),
                note=result_data.get("note", ""),
            )
            results[dataset_name] = result
        
        return ModelReport(
            model_name=model_name,
            full_name=data.get("full_name", model_name),
            organization=data.get("organization", "Unknown"),
            model_size=data.get("model_size", "Unknown"),
            release_date=data.get("release_date", ""),
            supported_tasks=data.get("supported_tasks", []),
            paper=data.get("paper", ""),
            results=results,
            summary=data.get("summary", {}),
        )
    
    def get_model(self, model_name: str) -> ModelReport | None:
        """Get report for a specific model.
        
        Args:
            model_name: Model name (e.g., "Qwen3-Omni")
            
        Returns:
            ModelReport if found, None otherwise
        """
        return self._models.get(model_name)
    
    def get_all_models(self) -> dict[str, ModelReport]:
        """Get all model reports.
        
        Returns:
            Dictionary of model name to ModelReport
        """
        return self._models.copy()
    
    def list_models(self) -> list[str]:
        """List all models with reports.
        
        Returns:
            List of model names
        """
        return sorted(self._models.keys())
    
    def get_models_by_task(self, task: str) -> list[ModelReport]:
        """Get models that support a specific task.
        
        Args:
            task: Task name (e.g., "ASR", "S2TT")
            
        Returns:
            List of ModelReport for models supporting the task
        """
        return [
            report for report in self._models.values()
            if task in report.supported_tasks
        ]
    
    def get_results_for_dataset(self, dataset: str) -> list[tuple[str, DatasetResult]]:
        """Get all model results for a specific dataset.
        
        Args:
            dataset: Dataset name
            
        Returns:
            List of (model_name, DatasetResult) tuples
        """
        results = []
        for model_name, report in self._models.items():
            result = report.get_result(dataset)
            if result:
                results.append((model_name, result))
        
        # Sort by RPS descending
        results.sort(key=lambda x: x[1].rps, reverse=True)
        return results
    
    def get_sota_for_dataset(self, dataset: str) -> tuple[str, DatasetResult] | None:
        """Get SOTA model for a specific dataset.
        
        Args:
            dataset: Dataset name
            
        Returns:
            Tuple of (model_name, DatasetResult) for SOTA, or None
        """
        results = self.get_results_for_dataset(dataset)
        if not results:
            return None
        
        # Return the highest RPS (should be 1.0 for true SOTA)
        return results[0]
    
    def compare_models(self, model_names: list[str], dataset: str) -> dict[str, Any]:
        """Compare multiple models on a specific dataset.
        
        Args:
            model_names: List of model names to compare
            dataset: Dataset name
            
        Returns:
            Comparison dictionary with rankings and scores
        """
        comparison = {
            "dataset": dataset,
            "models": {},
            "ranking": [],
        }
        
        for model_name in model_names:
            report = self.get_model(model_name)
            if not report:
                comparison["models"][model_name] = {"error": "Model not found"}
                continue
            
            result = report.get_result(dataset)
            if not result:
                comparison["models"][model_name] = {"error": "No result for dataset"}
                continue
            
            comparison["models"][model_name] = {
                "raw_score": result.raw_score,
                "rps": result.rps,
                "is_sota": result.is_sota,
                "metric": result.metric,
            }
            comparison["ranking"].append((model_name, result.rps))
        
        # Sort ranking by RPS
        comparison["ranking"].sort(key=lambda x: x[1], reverse=True)
        
        return comparison
    
    def print_model_summary(self, model_name: str) -> None:
        """Print summary for a specific model.
        
        Args:
            model_name: Model name
        """
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        
        console = Console()
        report = self.get_model(model_name)
        
        if not report:
            console.print(f"[red]Model not found: {model_name}[/red]")
            return
        
        # Model info panel
        info = (
            f"[bold]Full Name:[/bold] {report.full_name}\n"
            f"[bold]Organization:[/bold] {report.organization}\n"
            f"[bold]Model Size:[/bold] {report.model_size}\n"
            f"[bold]Release Date:[/bold] {report.release_date}\n"
            f"[bold]Supported Tasks:[/bold] {', '.join(report.supported_tasks)}\n"
            f"[bold]Average RPS:[/bold] {report.get_average_rps():.2f}\n"
            f"[bold]SOTA Count:[/bold] {report.get_sota_count()}/{len(report.results)}"
        )
        
        console.print(Panel(info, title=f"[bold blue]{model_name}[/bold blue]"))
        
        # Results table
        if report.results:
            table = Table()
            table.add_column("Dataset", style="cyan")
            table.add_column("Task", style="green")
            table.add_column("Metric", style="blue")
            table.add_column("Score", style="yellow", justify="right")
            table.add_column("RPS", style="magenta", justify="right")
            table.add_column("SOTA", style="white")
            
            for dataset, result in sorted(report.results.items()):
                sota_mark = "✓" if result.is_sota else ""
                table.add_row(
                    result.dataset,
                    result.task,
                    result.metric.upper(),
                    f"{result.raw_score:.2f}",
                    f"{result.rps:.2f}",
                    sota_mark,
                )
            
            console.print(table)
        
        console.print()
    
    def print_leaderboard(self, dataset: str | None = None) -> None:
        """Print leaderboard for all models or a specific dataset.
        
        Args:
            dataset: If provided, show leaderboard for this dataset only
        """
        from rich.console import Console
        from rich.table import Table
        
        console = Console()
        
        if dataset:
            # Single dataset leaderboard
            console.print(f"\n[bold blue]Leaderboard: {dataset}[/bold blue]\n")
            
            results = self.get_results_for_dataset(dataset)
            if not results:
                console.print(f"[yellow]No results found for {dataset}[/yellow]")
                return
            
            table = Table()
            table.add_column("Rank", style="white", justify="right")
            table.add_column("Model", style="cyan")
            table.add_column("Score", style="yellow", justify="right")
            table.add_column("RPS", style="magenta", justify="right")
            table.add_column("SOTA", style="green")
            
            for rank, (model_name, result) in enumerate(results, 1):
                sota_mark = "🏆" if result.is_sota else ""
                table.add_row(
                    str(rank),
                    model_name,
                    f"{result.raw_score:.2f}",
                    f"{result.rps:.2f}",
                    sota_mark,
                )
            
            console.print(table)
            
        else:
            # Overall leaderboard (by average RPS)
            console.print("\n[bold blue]Overall Leaderboard (by Average RPS)[/bold blue]\n")
            
            models_with_rps = [
                (name, report.get_average_rps(), report.get_sota_count())
                for name, report in self._models.items()
            ]
            models_with_rps.sort(key=lambda x: x[1], reverse=True)
            
            table = Table()
            table.add_column("Rank", style="white", justify="right")
            table.add_column("Model", style="cyan")
            table.add_column("Avg RPS", style="yellow", justify="right")
            table.add_column("SOTAs", style="green", justify="right")
            table.add_column("Organization", style="magenta")
            
            for rank, (model_name, avg_rps, sota_count) in enumerate(models_with_rps, 1):
                report = self._models[model_name]
                table.add_row(
                    str(rank),
                    model_name,
                    f"{avg_rps:.2f}",
                    str(sota_count),
                    report.organization,
                )
            
            console.print(table)
        
        console.print()
    
    def generate_markdown_report(self, output_path: str | Path | None = None) -> str:
        """Generate a Markdown report of all models.
        
        Args:
            output_path: If provided, save report to this path
            
        Returns:
            Markdown report string
        """
        lines = [
            "# SURE Benchmark Model Performance Report\n",
            f"**Report Date:** {self._get_report_date()}\n",
            f"**Total Models:** {len(self._models)}\n",
            "## SOTA Summary\n",
        ]
        
        # SOTA summary table
        lines.append("| Dataset | Metric | SOTA Model | Score |")
        lines.append("|---------|--------|------------|-------|")
        
        for dataset in self.sota_manager.list_datasets():
            baseline = self.sota_manager.get_baseline(dataset)
            if baseline:
                lines.append(
                    f"| {dataset} | {baseline.metric.upper()} | "
                    f"{baseline.sota_model} | {baseline.score:.2f} |"
                )
        
        lines.append("\n## Model Details\n")
        
        # Individual model sections
        for model_name in sorted(self._models.keys()):
            report = self._models[model_name]
            lines.append(f"### {model_name}\n")
            lines.append(f"- **Full Name:** {report.full_name}")
            lines.append(f"- **Organization:** {report.organization}")
            lines.append(f"- **Model Size:** {report.model_size}")
            lines.append(f"- **Supported Tasks:** {', '.join(report.supported_tasks)}")
            lines.append(f"- **Average RPS:** {report.get_average_rps():.2f}")
            lines.append(f"- **SOTA Count:** {report.get_sota_count()}/{len(report.results)}\n")
            
            if report.results:
                lines.append("| Dataset | Task | Metric | Score | RPS | SOTA |")
                lines.append("|---------|------|--------|-------|-----|------|")
                
                for dataset, result in sorted(report.results.items()):
                    sota_mark = "✓" if result.is_sota else ""
                    lines.append(
                        f"| {result.dataset} | {result.task} | "
                        f"{result.metric.upper()} | {result.raw_score:.2f} | "
                        f"{result.rps:.2f} | {sota_mark} |"
                    )
            
            lines.append("")
        
        markdown = "\n".join(lines)
        
        if output_path:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(markdown)
            logger.info(f"Saved Markdown report to {output_path}")
        
        return markdown
    
    def _get_report_date(self) -> str:
        """Get report date from file or current date."""
        try:
            with open(self.report_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("report_date", "Unknown")
        except:
            from datetime import datetime
            return datetime.now().strftime("%Y-%m-%d")
