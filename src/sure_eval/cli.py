"""CLI for SURE-EVAL."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from sure_eval.agent.evaluator import AutonomousEvaluator
from sure_eval.core.config import Config
from sure_eval.core.logging import configure_logging, get_logger
from sure_eval.datasets import DatasetManager
from sure_eval.evaluation.rps import RPSManager

app = typer.Typer(name="sure-eval", help="SURE-EVAL: Tool and Model Evaluation Framework")
console = Console()
logger = get_logger(__name__)


def get_evaluator(config_path: Optional[str] = None) -> AutonomousEvaluator:
    """Get evaluator instance."""
    if config_path:
        config = Config.from_yaml(config_path)
    else:
        config = Config.from_env()
    
    configure_logging(
        level=config.logging.level,
        format_type=config.logging.format,
        log_file=config.logging.file,
    )
    
    return AutonomousEvaluator(config)


@app.command()
def evaluate(
    tool: str = typer.Argument(..., help="Tool name to evaluate"),
    dataset: str = typer.Argument(..., help="Dataset name"),
    max_samples: Optional[int] = typer.Option(None, "--max-samples", "-n", help="Maximum samples to evaluate"),
    metric: Optional[str] = typer.Option(None, "--metric", "-m", help="Metric type (cer, wer, bleu, etc.)"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    """Evaluate a tool on a dataset."""
    evaluator = get_evaluator(config)
    
    try:
        result = evaluator.evaluate_tool(tool, dataset, max_samples, metric)
        
        # Display results
        console.print("\n[bold green]Evaluation Results[/bold green]")
        console.print(f"Tool: {result.tool_name}")
        console.print(f"Dataset: {result.dataset}")
        console.print(f"Metric: {result.metric}")
        console.print(f"Score: {result.score:.4f}")
        console.print(f"RPS: {result.rps:.4f}" if result.rps else "RPS: N/A")
        console.print(f"Samples: {result.num_samples}")
        console.print(f"Duration: {result.duration:.2f}s")
        
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def batch_evaluate(
    tool: str = typer.Argument(..., help="Tool name to evaluate"),
    datasets: list[str] = typer.Argument(..., help="Dataset names"),
    max_samples: Optional[int] = typer.Option(None, "--max-samples", "-n", help="Maximum samples per dataset"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    """Evaluate a tool on multiple datasets."""
    evaluator = get_evaluator(config)
    
    results = evaluator.batch_evaluate(tool, datasets, max_samples)
    
    # Display results table
    table = Table(title=f"Batch Evaluation Results for {tool}")
    table.add_column("Dataset", style="cyan")
    table.add_column("Metric", style="magenta")
    table.add_column("Score", style="green")
    table.add_column("RPS", style="yellow")
    table.add_column("Duration", style="blue")
    
    for result in results:
        rps_str = f"{result.rps:.4f}" if result.rps else "N/A"
        table.add_row(
            result.dataset,
            result.metric,
            f"{result.score:.4f}",
            rps_str,
            f"{result.duration:.2f}s",
        )
    
    console.print(table)


@app.command()
def compare(
    tools: list[str] = typer.Argument(..., help="Tool names to compare"),
    dataset: str = typer.Argument(..., help="Dataset name"),
    max_samples: Optional[int] = typer.Option(None, "--max-samples", "-n", help="Maximum samples"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    """Compare multiple tools on a dataset."""
    evaluator = get_evaluator(config)
    
    comparison = evaluator.compare_tools(tools, dataset, max_samples)
    
    # Display comparison table
    table = Table(title=f"Tool Comparison on {dataset}")
    table.add_column("Rank", style="dim")
    table.add_column("Tool", style="cyan")
    table.add_column("Score", style="green")
    table.add_column("RPS", style="yellow")
    table.add_column("Duration", style="blue")
    
    for rank, (tool_name, data) in enumerate(comparison["ranking"], 1):
        table.add_row(
            str(rank),
            tool_name,
            f"{data['score']:.4f}",
            f"{data['rps']:.4f}",
            f"{data['duration']:.2f}s",
        )
    
    console.print(table)


@app.command()
def recommend(
    dataset: str = typer.Argument(..., help="Dataset name"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    """Recommend the best tool for a dataset."""
    evaluator = get_evaluator(config)
    
    recommendation = evaluator.recommend_tool(dataset)
    
    console.print(f"\n[bold]Recommendation for {dataset}[/bold]")
    
    if recommendation["best_tool"]:
        console.print(f"\n[green]Best Tool:[/green] {recommendation['best_tool']}")
        console.print(f"[green]RPS:[/green] {recommendation['best_rps']:.4f}")
    else:
        console.print("\n[yellow]No evaluation records found.[/yellow]")
    
    if recommendation.get("suggested_tool"):
        console.print(f"\n[blue]Suggested Tool:[/blue] {recommendation['suggested_tool']} (default for task)")
    
    if recommendation["ranking"]:
        console.print("\n[bold]Tool Ranking:[/bold]")
        for rank, (tool, rps) in enumerate(recommendation["ranking"], 1):
            console.print(f"  {rank}. {tool} (RPS: {rps:.4f})")


@app.command()
def download_dataset(
    name: str = typer.Argument(..., help="Dataset name"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    """Download a dataset."""
    cfg = Config.from_yaml(config) if config else Config.from_env()
    configure_logging(level=cfg.logging.level)
    
    manager = DatasetManager(cfg)
    
    try:
        path = manager.download_and_convert(name)
        console.print(f"[green]Dataset downloaded:[/green] {path}")
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def list_datasets(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    """List available datasets."""
    cfg = Config.from_yaml(config) if config else Config.from_env()
    configure_logging(level=cfg.logging.level)
    
    manager = DatasetManager(cfg)
    
    table = Table(title="Available Datasets")
    table.add_column("Name", style="cyan")
    table.add_column("Task", style="magenta")
    table.add_column("Language", style="green")
    table.add_column("Source", style="blue")
    table.add_column("Available", style="yellow")
    
    for name in manager.list_available():
        info = manager.get_info(name)
        if info:
            available = "✓" if info["is_available"] else "✗"
            table.add_row(
                name,
                info["task"],
                info["language"],
                info["source"],
                available,
            )
    
    console.print(table)


@app.command()
def list_metrics() -> None:
    """List available metrics."""
    from sure_eval.evaluation.metrics import MetricRegistry
    
    metrics = MetricRegistry.list_metrics()
    
    console.print("\n[bold]Available Metrics:[/bold]")
    for metric in metrics:
        console.print(f"  - {metric}")


@app.command()
def show_results(
    tool: Optional[str] = typer.Option(None, "--tool", "-t", help="Filter by tool"),
    dataset: Optional[str] = typer.Option(None, "--dataset", "-d", help="Filter by dataset"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    """Show evaluation results."""
    cfg = Config.from_yaml(config) if config else Config.from_env()
    configure_logging(level=cfg.logging.level)
    
    rps_manager = RPSManager(cfg)
    records = rps_manager.database.get_records(tool_name=tool, dataset=dataset)
    
    table = Table(title="Evaluation Results")
    table.add_column("Tool", style="cyan")
    table.add_column("Dataset", style="magenta")
    table.add_column("Metric", style="blue")
    table.add_column("Score", style="green")
    table.add_column("RPS", style="yellow")
    table.add_column("Timestamp", style="dim")
    
    for record in records:
        rps_str = f"{record.rps:.4f}" if record.rps else "N/A"
        table.add_row(
            record.tool_name,
            record.dataset,
            record.metric,
            f"{record.score:.4f}",
            rps_str,
            record.timestamp[:19],
        )
    
    console.print(table)


def main() -> None:
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
