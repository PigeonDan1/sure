"""CLI for SURE-EVAL."""

from __future__ import annotations

import os
import subprocess
from typing import Optional

import click
import typer
from rich.console import Console
from rich.table import Table

from sure_eval.agent.evaluator import AutonomousEvaluator
from sure_eval.core.config import Config
from sure_eval.core.logging import configure_logging, get_logger
from sure_eval.datasets import DatasetManager
from sure_eval.evaluation.rps import RPSManager
from sure_eval.models.registry import ModelInfo, ModelRegistry


def _apply_click_metavar_compatibility_patch() -> None:
    """Bridge Click 8.3 metavar signatures for older Typer help rendering."""
    required_ctx = click.Context(click.Command(name="sure-eval"))

    def _patch_option_like(cls: type[click.Parameter]) -> None:
        original = cls.make_metavar
        if getattr(original, "__sure_eval_compat__", False):
            return

        def wrapped(self, ctx: click.Context | None = None) -> str:
            return original(self, ctx or required_ctx)

        wrapped.__sure_eval_compat__ = True  # type: ignore[attr-defined]
        cls.make_metavar = wrapped  # type: ignore[assignment]

    _patch_option_like(click.Argument)
    _patch_option_like(click.Option)


_apply_click_metavar_compatibility_patch()

app = typer.Typer(name="sure-eval", help="SURE-EVAL: Tool and Model Evaluation Framework")
models_app = typer.Typer(help="Inspect registered models")
app.add_typer(models_app, name="models")
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


def get_model_registry() -> ModelRegistry:
    """Get model registry instance."""
    return ModelRegistry()


def get_model_or_exit(model_name: str) -> ModelInfo:
    """Get a registered model or exit with a clear error."""
    model = get_model_registry().get_model(model_name)
    if model is None:
        console.print(f"[bold red]Error:[/bold red] Model '{model_name}' not found in registry.")
        raise typer.Exit(1)
    return model


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


@models_app.command("list")
def list_models() -> None:
    """List discovered models."""
    registry = get_model_registry()

    table = Table(title="Registered Models")
    table.add_column("Name", style="cyan")
    table.add_column("Task", style="magenta")
    table.add_column("Implemented", style="green")

    for model_name in sorted(registry.list_models()):
        model = registry.get_model(model_name)
        if model is None:
            continue
        table.add_row(
            model.name,
            model.task,
            "PASS" if model.is_implemented else "FAIL",
        )

    console.print(table)


@models_app.command("inspect")
def inspect_model(
    model_name: str = typer.Argument(..., help="Model name"),
) -> None:
    """Inspect a registered model."""
    model = get_model_or_exit(model_name)

    table = Table(title=f"Model Inspection: {model.name}")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("name", model.name)
    table.add_row("task", model.task)
    table.add_row("path", str(model.path.resolve()))
    table.add_row("description", model.description or "-")
    table.add_row("model_id", model.model_id or "-")
    table.add_row("server command", " ".join(model.server_command) if model.server_command else "-")
    table.add_row("working_dir", str(model.working_dir))
    table.add_row("env", str(model.env) if model.env else "{}")
    table.add_row("timeout", str(model.timeout))

    console.print(table)


@app.command()
def doctor(
    model_name: str = typer.Argument(..., help="Model name"),
) -> None:
    """Run static checks for a registered model."""
    registry = get_model_registry()
    model = registry.get_model(model_name)

    checks: list[tuple[str, bool, str]] = []

    checks.append((
        "Registry discovery",
        model is not None,
        f"Model '{model_name}' discovered by ModelRegistry." if model else f"Model '{model_name}' not found in ModelRegistry.",
    ))

    if model is None:
        model_path = registry.models_dir / model_name
        config_file = model_path / "config.yaml"
        model_file = model_path / "model.py"
        server_file = model_path / "server.py"
        checks.extend([
            ("config.yaml exists", config_file.exists(), str(config_file)),
            ("model.py exists", model_file.exists(), str(model_file)),
            ("server.py exists", server_file.exists(), str(server_file)),
            ("Server command declared", False, "Model is missing from registry, so server configuration could not be loaded."),
        ])
    else:
        config_file = model.path / "config.yaml"
        model_file = model.path / "model.py"
        server_file = model.path / "server.py"
        checks.extend([
            ("config.yaml exists", config_file.exists(), str(config_file)),
            ("model.py exists", model_file.exists(), str(model_file)),
            ("server.py exists", server_file.exists(), str(server_file)),
            (
                "Server command declared",
                bool(model.server_command),
                " ".join(model.server_command) if model.server_command else "Missing server.command in config.yaml.",
            ),
        ])

    table = Table(title=f"Doctor Report: {model_name}")
    table.add_column("Check", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Details", style="white")

    failed = False
    for label, passed, details in checks:
        status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        table.add_row(label, status, details)
        failed = failed or not passed

    console.print(table)

    if failed:
        raise typer.Exit(1)


@app.command()
def serve(
    model_name: str = typer.Argument(..., help="Model name"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print command without starting the server"),
) -> None:
    """Start a model server using the registered server configuration."""
    model = get_model_or_exit(model_name)

    if not model.server_command:
        console.print(f"[bold red]Error:[/bold red] Model '{model_name}' does not declare server.command.")
        raise typer.Exit(1)

    working_dir = model.working_dir
    env = os.environ.copy()
    env.update(model.env)

    table = Table(title=f"Serve Context: {model.name}")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("command", " ".join(model.server_command))
    table.add_row("working_dir", str(working_dir))
    table.add_row("env", str(model.env) if model.env else "{}")
    table.add_row("timeout", str(model.timeout))
    console.print(table)

    if dry_run:
        console.print("[green]Dry run complete.[/green] Server was not started.")
        return

    try:
        completed = subprocess.run(
            model.server_command,
            cwd=working_dir,
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        console.print(f"[bold red]Error:[/bold red] Failed to start server: {exc}")
        raise typer.Exit(1) from exc
    except OSError as exc:
        console.print(f"[bold red]Error:[/bold red] Failed to start server: {exc}")
        raise typer.Exit(1) from exc

    if completed.returncode != 0:
        console.print(f"[bold red]Error:[/bold red] Server exited with code {completed.returncode}.")
        raise typer.Exit(completed.returncode)


def main() -> None:
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
