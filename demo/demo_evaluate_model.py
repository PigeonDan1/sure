#!/usr/bin/env python3
"""
Demo: Evaluate a specific model on a specific dataset.

This script demonstrates how to evaluate a model (e.g., asr_qwen3) 
on a dataset (e.g., aishell1) using the SURE-EVAL framework.

Usage:
    python demo_evaluate_model.py --model asr_qwen3 --dataset aishell1 --samples 10
    python demo_evaluate_model.py --model asr_qwen3 --dataset aishell1 --full

Requirements:
    - The model must be set up (pyproject.toml dependencies installed)
    - The dataset must be available in data/processed/
"""

import sys
import argparse
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from sure_eval.core.config import Config
from sure_eval.agent.evaluator import AutonomousEvaluator
from rich.console import Console
from rich.table import Table

console = Console()


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate a model on a dataset",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quick test (10 samples)
  python demo_evaluate_model.py --model asr_qwen3 --dataset aishell1 --samples 10
  
  # Full evaluation (all samples)
  python demo_evaluate_model.py --model asr_qwen3 --dataset aishell1 --full
  
  # Compare with ground truth for first few samples
  python demo_evaluate_model.py --model asr_qwen3 --dataset aishell1 --samples 5 --verbose
        """
    )
    parser.add_argument(
        "--model", "-m",
        required=True,
        help="Model name (e.g., asr_qwen3)"
    )
    parser.add_argument(
        "--dataset", "-d",
        required=True,
        help="Dataset name (e.g., aishell1, librispeech_clean)"
    )
    parser.add_argument(
        "--samples", "-n",
        type=int,
        default=10,
        help="Number of samples to evaluate (default: 10)"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Run full evaluation on all samples"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed predictions vs ground truth"
    )
    parser.add_argument(
        "--output", "-o",
        help="Output JSON file for results"
    )
    
    args = parser.parse_args()
    
    # Override samples if --full is specified
    num_samples = None if args.full else args.samples
    
    console.print(f"\n[bold blue]SURE-EVAL Model Evaluation Demo[/bold blue]")
    console.print(f"[dim]{'=' * 50}[/dim]\n")
    
    # Show configuration
    config_table = Table(title="Configuration")
    config_table.add_column("Parameter", style="cyan")
    config_table.add_column("Value", style="green")
    config_table.add_row("Model", args.model)
    config_table.add_row("Dataset", args.dataset)
    config_table.add_row("Samples", "All" if num_samples is None else str(num_samples))
    config_table.add_row("Verbose", "Yes" if args.verbose else "No")
    console.print(config_table)
    console.print()
    
    # Initialize evaluator
    console.print("[yellow]Initializing evaluator...[/yellow]")
    config = Config.from_env()
    evaluator = AutonomousEvaluator(config)
    
    # Check if model exists
    available_models = evaluator.tool_registry.list_models()
    if args.model not in available_models:
        console.print(f"[red]Error: Model '{args.model}' not found![/red]")
        console.print(f"\nAvailable models:")
        for m in available_models:
            console.print(f"  - {m}")
        return 1
    
    # Check if dataset exists
    available_datasets = evaluator.dataset_manager.list_datasets()
    if args.dataset not in available_datasets:
        console.print(f"[red]Error: Dataset '{args.dataset}' not found![/red]")
        console.print(f"\nAvailable datasets:")
        for d in available_datasets:
            console.print(f"  - {d}")
        return 1
    
    # Run evaluation
    console.print(f"\n[green]Starting evaluation...[/green]\n")
    
    try:
        results = evaluator.quick_test(
            tool_name=args.model,
            dataset_name=args.dataset,
            num_samples=num_samples or 10,
            verbose=args.verbose
        )
        
        # Display results
        console.print(f"\n[bold green]✓ Evaluation Complete![/bold green]\n")
        
        results_table = Table(title="Results")
        results_table.add_column("Metric", style="cyan")
        results_table.add_column("Value", style="green")
        
        # Task-specific metrics
        task = results.get("task", "unknown")
        metrics = results.get("metrics", {})
        
        results_table.add_row("Task", task.upper())
        results_table.add_row("Model", args.model)
        results_table.add_row("Dataset", args.dataset)
        results_table.add_row("Samples", str(results.get("num_samples", "N/A")))
        
        if task == "asr":
            results_table.add_row("WER (%)", f"{metrics.get('wer', 'N/A')}")
            if metrics.get('cer') is not None:
                results_table.add_row("CER (%)", f"{metrics.get('cer')}")
        elif task == "s2tt":
            results_table.add_row("BLEU", f"{metrics.get('bleu', 'N/A')}")
            results_table.add_row("chrF", f"{metrics.get('chrf', 'N/A')}")
        elif task == "sd":
            results_table.add_row("DER (%)", f"{metrics.get('der', 'N/A')}")
            results_table.add_row("JER (%)", f"{metrics.get('jer', 'N/A')}")
        
        results_table.add_row("RPS", f"{results.get('rps', 'N/A'):.2f}")
        results_table.add_row("Duration (s)", f"{results.get('duration_sec', 'N/A'):.2f}")
        
        console.print(results_table)
        
        # Show sample predictions if verbose
        if args.verbose and "samples" in results:
            console.print(f"\n[bold]Sample Predictions:[/bold]\n")
            for i, sample in enumerate(results["samples"][:5], 1):
                console.print(f"[cyan]Sample {i}:[/cyan]")
                console.print(f"  Audio: {sample.get('audio_path', 'N/A')}")
                console.print(f"  Ground Truth: {sample.get('ground_truth', 'N/A')}")
                console.print(f"  Prediction: {sample.get('prediction', 'N/A')}")
                console.print()
        
        # Save results if output specified
        if args.output:
            import json
            with open(args.output, "w") as f:
                json.dump(results, f, indent=2, default=str)
            console.print(f"[green]Results saved to: {args.output}[/green]")
        
        return 0
        
    except Exception as e:
        console.print(f"[red]Error during evaluation: {e}[/red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
