#!/usr/bin/env python3
"""
Demo: Compare multiple models on the same dataset.

This script demonstrates how to evaluate and compare multiple models
on the same dataset to see which performs best.

Usage:
    python demo_compare_models.py --models asr_qwen3,asr_whisper --dataset aishell1
    python demo_compare_models.py --models asr_qwen3 --dataset aishell1 --output comparison.json

Note:
    For a fair comparison, all models should be evaluated on the same
    subset of samples. The script ensures this by using consistent
    random sampling with a fixed seed.
"""

import sys
import json
import argparse
from pathlib import Path
from typing import List

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from sure_eval.core.config import Config
from sure_eval.agent.evaluator import AutonomousEvaluator
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


def compare_models(models: List[str], dataset: str, num_samples: int = 10, verbose: bool = False):
    """Compare multiple models on the same dataset."""
    
    config = Config.from_env()
    evaluator = AutonomousEvaluator(config)
    
    results = {}
    
    console.print(f"\n[bold blue]Comparing {len(models)} model(s) on {dataset}[/bold blue]\n")
    
    for i, model in enumerate(models, 1):
        console.print(f"[yellow][{i}/{len(models)}] Evaluating {model}...[/yellow]")
        
        try:
            result = evaluator.quick_test(
                tool_name=model,
                dataset_name=dataset,
                num_samples=num_samples,
                verbose=verbose
            )
            results[model] = result
            console.print(f"   [green]✓ {model}: WER={result.get('metrics', {}).get('wer', 'N/A')}%, "
                         f"RPS={result.get('rps', 0):.2f}[/green]")
        except Exception as e:
            console.print(f"   [red]✗ {model} failed: {e}[/red]")
            results[model] = {"error": str(e)}
    
    return results


def display_comparison(results: dict, dataset: str):
    """Display comparison results in a table."""
    
    console.print(f"\n[bold]Comparison Results for {dataset}[/bold]\n")
    
    # Determine task type from first successful result
    task = "unknown"
    for r in results.values():
        if "error" not in r:
            task = r.get("task", "unknown")
            break
    
    # Create comparison table
    if task == "asr":
        table = Table(title=f"ASR Model Comparison - {dataset}")
        table.add_column("Model", style="cyan")
        table.add_column("WER (%)", style="green", justify="right")
        table.add_column("CER (%)", style="blue", justify="right")
        table.add_column("RPS", style="yellow", justify="right")
        table.add_column("Duration (s)", style="magenta", justify="right")
        table.add_column("Status", style="white")
        
        for model, result in results.items():
            if "error" in result:
                table.add_row(model, "-", "-", "-", "-", f"[red]Failed[/red]")
            else:
                metrics = result.get("metrics", {})
                wer = f"{metrics.get('wer', 'N/A')}"
                cer = f"{metrics.get('cer', 'N/A')}" if metrics.get('cer') else "-"
                rps = f"{result.get('rps', 0):.2f}"
                duration = f"{result.get('duration_sec', 0):.2f}"
                table.add_row(model, wer, cer, rps, duration, "[green]OK[/green]")
    
    elif task == "s2tt":
        table = Table(title=f"S2TT Model Comparison - {dataset}")
        table.add_column("Model", style="cyan")
        table.add_column("BLEU", style="green", justify="right")
        table.add_column("chrF", style="blue", justify="right")
        table.add_column("RPS", style="yellow", justify="right")
        table.add_column("Duration (s)", style="magenta", justify="right")
        table.add_column("Status", style="white")
        
        for model, result in results.items():
            if "error" in result:
                table.add_row(model, "-", "-", "-", "-", f"[red]Failed[/red]")
            else:
                metrics = result.get("metrics", {})
                bleu = f"{metrics.get('bleu', 'N/A')}"
                chrf = f"{metrics.get('chrf', 'N/A')}"
                rps = f"{result.get('rps', 0):.2f}"
                duration = f"{result.get('duration_sec', 0):.2f}"
                table.add_row(model, bleu, chrf, rps, duration, "[green]OK[/green]")
    
    else:
        table = Table(title=f"Model Comparison - {dataset}")
        table.add_column("Model", style="cyan")
        table.add_column("RPS", style="yellow", justify="right")
        table.add_column("Status", style="white")
        
        for model, result in results.items():
            if "error" in result:
                table.add_row(model, "-", f"[red]Failed[/red]")
            else:
                rps = f"{result.get('rps', 0):.2f}"
                table.add_row(model, rps, "[green]OK[/green]")
    
    console.print(table)
    
    # Find best model
    successful = {k: v for k, v in results.items() if "error" not in v}
    if successful:
        if task == "asr":
            # Lower WER is better
            best = min(successful.items(), key=lambda x: x[1].get("metrics", {}).get("wer", float('inf')))
            console.print(f"\n[bold green]🏆 Best Model (lowest WER): {best[0]}[/bold green]")
            console.print(f"   WER: {best[1].get('metrics', {}).get('wer')}%")
            console.print(f"   RPS: {best[1].get('rps', 0):.2f}")
        elif task == "s2tt":
            # Higher BLEU is better
            best = max(successful.items(), key=lambda x: x[1].get("metrics", {}).get("bleu", 0))
            console.print(f"\n[bold green]🏆 Best Model (highest BLEU): {best[0]}[/bold green]")
            console.print(f"   BLEU: {best[1].get('metrics', {}).get('bleu')}")
            console.print(f"   RPS: {best[1].get('rps', 0):.2f}")


def main():
    parser = argparse.ArgumentParser(
        description="Compare multiple models on a dataset",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Compare two models
  python demo_compare_models.py -m asr_qwen3,asr_whisper -d aishell1
  
  # Compare with more samples
  python demo_compare_models.py -m asr_qwen3,asr_whisper -d aishell1 -n 50
  
  # Save results
  python demo_compare_models.py -m asr_qwen3 -d aishell1 -o results.json
        """
    )
    parser.add_argument(
        "--models", "-m",
        required=True,
        help="Comma-separated list of model names (e.g., asr_qwen3,asr_whisper)"
    )
    parser.add_argument(
        "--dataset", "-d",
        required=True,
        help="Dataset name (e.g., aishell1)"
    )
    parser.add_argument(
        "--samples", "-n",
        type=int,
        default=10,
        help="Number of samples per model (default: 10)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output"
    )
    parser.add_argument(
        "--output", "-o",
        help="Output JSON file for results"
    )
    
    args = parser.parse_args()
    
    models = [m.strip() for m in args.models.split(",")]
    
    # Run comparison
    results = compare_models(
        models=models,
        dataset=args.dataset,
        num_samples=args.samples,
        verbose=args.verbose
    )
    
    # Display results
    display_comparison(results, args.dataset)
    
    # Save if requested
    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2, default=str)
        console.print(f"\n[green]Results saved to: {args.output}[/green]")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
