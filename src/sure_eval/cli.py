"""CLI for SURE-EVAL."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click
import typer
from rich.console import Console
from rich.table import Table

from sure_eval.agent.evaluator import AutonomousEvaluator
from sure_eval.agent.vc_submitter import (
    build_vc_submit_command,
    get_job_info,
    get_job_logs,
    submit_vc_run,
)
from sure_eval.core.config import Config
from sure_eval.core.logging import configure_logging, get_logger
from sure_eval.datasets import DatasetManager
from sure_eval.evaluation.rps import RPSManager
from sure_eval.inference import (
    dry_run_prediction_job,
    run_prediction_job,
    validate_prediction_artifact,
)
from sure_eval.inference.errors import InferenceSurfaceError
from sure_eval.inference.runner import get_runtime_readiness
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


@app.command()
def download_dataset(
    dataset: str = typer.Argument(..., help="Dataset name"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    """Download a dataset."""
    evaluator = get_evaluator(config)
    
    try:
        path = evaluator.dataset_manager.download_and_convert(dataset)
        console.print(f"[green]Downloaded:[/green] {path}")
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def list_datasets(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    """List available datasets."""
    evaluator = get_evaluator(config)
    datasets = evaluator.dataset_manager.list_available()
    
    console.print("\n[bold]Available Datasets[/bold]")
    for ds in datasets:
        console.print(f"  - {ds}")


@app.command()
def list_metrics() -> None:
    """List supported metrics."""
    metrics = {
        "ASR": ["cer", "wer"],
        "S2TT": ["bleu", "bleu_char", "chrf"],
        "SER": ["accuracy"],
        "GR": ["accuracy"],
        "SLU": ["accuracy"],
        "SD": ["der"],
        "SA-ASR": ["cpwer"],
    }
    
    table = Table(title="Supported Metrics by Task")
    table.add_column("Task", style="cyan")
    table.add_column("Metrics", style="green")
    
    for task, task_metrics in metrics.items():
        table.add_row(task, ", ".join(task_metrics))
    
    console.print(table)


@app.command()
def show_results(
    dataset: Optional[str] = typer.Option(None, "--dataset", "-d", help="Filter by dataset"),
    tool: Optional[str] = typer.Option(None, "--tool", "-t", help="Filter by tool"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    """Show evaluation results."""
    evaluator = get_evaluator(config)
    
    # Get RPS records
    records = evaluator.rps_manager.records
    
    if dataset:
        records = [r for r in records if r.dataset == dataset]
    if tool:
        records = [r for r in records if r.tool_name == tool]
    
    if not records:
        console.print("[yellow]No results found.[/yellow]")
        return
    
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
            record.timestamp,
        )
    
    console.print(table)


# --- Model inspection commands ---

@models_app.command("list")
def list_models() -> None:
    """List registered models."""
    registry = get_model_registry()
    models = registry.list_models()
    
    if not models:
        console.print("[yellow]No models registered.[/yellow]")
        return
    
    table = Table(title="Registered Models")
    table.add_column("Name", style="cyan")
    table.add_column("Task", style="magenta")
    table.add_column("Description", style="white")
    
    for model in models:
        table.add_row(
            model.name,
            model.task or "N/A",
            model.description or "N/A",
        )
    
    console.print(table)


@models_app.command("inspect")
def inspect_model(
    model_name: str = typer.Argument(..., help="Model name"),
) -> None:
    """Inspect a registered model."""
    model = get_model_or_exit(model_name)
    
    table = Table(title=f"Model: {model.name}")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="white")
    
    table.add_row("Name", model.name)
    table.add_row("Task", model.task or "N/A")
    table.add_row("Description", model.description or "N/A")
    table.add_row("Version", model.version or "N/A")
    table.add_row("Model ID", model.model_id or "N/A")
    table.add_row("Languages", ", ".join(model.languages) if model.languages else "N/A")
    table.add_row("Config Path", str(model.config_path) if model.config_path else "N/A")
    
    console.print(table)


@app.command()
def doctor(
    model_name: Optional[str] = typer.Option(None, "--model", "-m", help="Model name to check"),
) -> None:
    """Run diagnostics on the evaluation environment."""
    console.print("[bold]SURE-EVAL Diagnostics[/bold]\n")
    
    # Check Python version
    import platform
    console.print(f"Python: {platform.python_version()}")
    
    # Check CUDA availability
    try:
        import torch
        cuda_available = torch.cuda.is_available()
        cuda_version = torch.version.cuda if cuda_available else "N/A"
        console.print(f"PyTorch: {torch.__version__}")
        console.print(f"CUDA available: {cuda_available}")
        console.print(f"CUDA version: {cuda_version}")
        if cuda_available:
            console.print(f"GPU count: {torch.cuda.device_count()}")
            for i in range(torch.cuda.device_count()):
                console.print(f"  GPU {i}: {torch.cuda.get_device_name(i)}")
    except ImportError:
        console.print("[yellow]PyTorch not installed[/yellow]")
    
    # Check model if specified
    if model_name:
        console.print(f"\n[bold]Model Check: {model_name}[/bold]")
        try:
            model = get_model_or_exit(model_name)
            console.print(f"Config: {model.config_path}")
            if model.config_path and model.config_path.exists():
                console.print("[green]Config file exists[/green]")
            else:
                console.print("[red]Config file not found[/red]")
        except typer.Exit:
            pass


@app.command()
def serve(
    model_name: str = typer.Argument(..., help="Model name"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate without starting"),
) -> None:
    """Start a model's MCP server."""
    model = get_model_or_exit(model_name)
    
    console.print(f"[bold]Starting server for {model.name}...[/bold]")
    
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


@app.command()
def predict(
    model_name: str = typer.Argument(..., help="Model name"),
    input: Path = typer.Option(..., "--input", help="Input JSONL path"),
    output: Path = typer.Option(..., "--output", help="Output prediction JSONL path"),
    task: str = typer.Option("asr", "--task", help="Task name"),
    device: str = typer.Option("auto", "--device", help="Inference device"),
    batch_size: int = typer.Option(1, "--batch-size", help="Batch size"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate inputs without running inference"),
) -> None:
    """Run unified prediction generation for a registered model."""
    model = get_model_or_exit(model_name)

    try:
        if dry_run:
            summary = dry_run_prediction_job(
                model_info=model,
                input_path=input,
                output_path=output,
                task=task,
                device=device,
                batch_size=batch_size,
            )
            table = Table(title=f"Predict Dry Run: {model.name}")
            table.add_column("Field", style="cyan")
            table.add_column("Value", style="white")
            table.add_row("model", summary["model"])
            table.add_row("task", summary["task"])
            table.add_row("input_path", summary["input_path"])
            table.add_row("output_path", summary["output_path"])
            table.add_row("num_instances", str(summary["num_instances"]))
            table.add_row("config_path", summary["config_path"])
            table.add_row("model_path", summary["model_path"])
            table.add_row("runtime_command", summary["runtime_command"])
            table.add_row("runtime_executable", summary["runtime_executable"])
            table.add_row("working_dir", summary["working_dir"])
            table.add_row("status", summary["status"])
            table.add_row("failure_class", str(summary["failure_class"] or "-"))
            table.add_row("action_hint", summary["action_hint"])
            table.add_row("device", summary["device"])
            table.add_row("batch_size", str(summary["batch_size"]))
            console.print(table)
            return

        result = run_prediction_job(
            model_info=model,
            input_path=input,
            output_path=output,
            task=task,
            device=device,
            batch_size=batch_size,
        )
        console.print(f"[green]Predictions written to {result['output_path']}[/green]")

    except InferenceSurfaceError as exc:
        console.print(f"[bold red]Inference Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Hint:[/yellow] {exc.hint}")
        raise typer.Exit(1) from exc
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(1) from exc


# --- Volcano cluster submission ---

@app.command(name="submit-run")
def submit_run(
    model_name: str = typer.Argument(..., help="Model directory name (e.g. asr_qwen3)"),
    run_id: str = typer.Argument(..., help="Run ID (e.g. main_agent_asr_qwen3_002)"),
    image: Optional[str] = typer.Option(None, "--image", "-i", help="Docker image (auto-detected if omitted)"),
    partition: Optional[str] = typer.Option(None, "--partition", "-p", help="GPU partition (auto-detected if omitted)"),
    memory: Optional[int] = typer.Option(None, "--memory", "-m", help="Container memory in GB (auto-estimated if omitted)"),
    gpus: int = typer.Option(1, "--gpus", "-g", help="GPUs per task"),
    cpus: int = typer.Option(4, "--cpus", "-c", help="CPUs per task"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the command without submitting"),
) -> None:
    """Submit a model evaluation run to the Volcano cluster (vc submit).

    Auto-selects image, GPU partition, and memory unless overridden.
    Fixes the .venv symlink inside the container automatically.
    """
    try:
        cmd = build_vc_submit_command(
            model_name=model_name,
            run_id=run_id,
            image=image,
            partition=partition,
            memory_gb=memory,
            gpus=gpus,
            cpus=cpus,
        )
    except RuntimeError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    if dry_run:
        console.print("[bold]vc submit command (dry run):[/bold]")
        console.print(" ".join(cmd))
        return

    try:
        job_id = submit_vc_run(
            model_name=model_name,
            run_id=run_id,
            image=image,
            partition=partition,
            memory_gb=memory,
            gpus=gpus,
            cpus=cpus,
        )
        console.print(f"[bold green]Job submitted successfully![/bold green]")
        console.print(f"Job ID: [cyan]{job_id}[/cyan]")
        console.print(f"\nView logs:")
        console.print(f"  vc logs -t {job_id}-master-0")
        console.print(f"  vc logs -t {job_id}-master-0 -f")
    except RuntimeError as exc:
        console.print(f"[bold red]Submission failed:[/bold red] {exc}")
        raise typer.Exit(1) from exc


@app.command(name="job-logs")
def job_logs(
    job_id: str = typer.Argument(..., help="Job ID from vc submit"),
    lines: Optional[int] = typer.Option(None, "--lines", "-l", help="Tail N lines"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow logs"),
) -> None:
    """Fetch logs for a Volcano job."""
    task_name = f"{job_id}-master-0"
    cmd = ["vc", "logs", "-t", task_name]
    if lines is not None:
        cmd += ["-l", str(lines)]
    if follow:
        cmd += ["-f"]

    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        console.print("[bold red]Error:[/bold red] vc command not found.")
        raise typer.Exit(1)


@app.command(name="job-info")
def job_info(
    job_id: str = typer.Argument(..., help="Job ID from vc submit"),
) -> None:
    """Show info for a Volcano job."""
    try:
        info = get_job_info(job_id)
    except RuntimeError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    table = Table(title=f"Job Info: {job_id}")
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="white")

    for key, val in info.items():
        if key == "raw":
            continue
        table.add_row(key, str(val))

    console.print(table)


@app.command()
def validate_predictions(
    model_name: str = typer.Argument(..., help="Model name"),
    dataset: str = typer.Argument(..., help="Dataset name"),
    pred_dir: Path = typer.Option(..., "--pred-dir", help="Prediction directory"),
) -> None:
    """Validate prediction files for a model."""
    model = get_model_or_exit(model_name)
    
    try:
        result = validate_prediction_artifact(
            model_info=model,
            dataset_name=dataset,
            pred_dir=pred_dir,
        )
        console.print(f"[green]Validation passed:[/green] {result}")
    except Exception as e:
        console.print(f"[bold red]Validation failed:[/bold red] {e}")
        raise typer.Exit(1)


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
