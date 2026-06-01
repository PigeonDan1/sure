#!/usr/bin/env python3
"""
Demo: Model Performance Reports and SOTA Management.

This script demonstrates how to use the reports module to:
1. View SOTA baselines for all datasets
2. View model performance reports
3. Compare models on specific datasets
4. Generate leaderboard

Usage:
    python demo/demo_reports.py --sota-summary
    python demo/demo_reports.py --model-summary Qwen3-Omni
    python demo/demo_reports.py --leaderboard aishell1
    python demo/demo_reports.py --compare Qwen3-Omni,Kimi-Audio,Gemini-3.0pro --dataset aishell1
"""

import sys
import argparse
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from sure_eval.reports import SOTAManager, ReportManager
from rich.console import Console

console = Console()


def show_sota_summary():
    """Show SOTA baselines summary."""
    console.print("\n[bold blue]Loading SOTA Manager...[/bold blue]")
    sota = SOTAManager()
    sota.print_summary()


def show_model_summary(model_name: str):
    """Show summary for a specific model."""
    console.print(f"\n[bold blue]Loading Model Report: {model_name}...[/bold blue]")
    reports = ReportManager()
    reports.print_model_summary(model_name)


def show_leaderboard(dataset: str | None = None):
    """Show leaderboard."""
    reports = ReportManager()
    reports.print_leaderboard(dataset)


def compare_models(model_names: list[str], dataset: str):
    """Compare models on a dataset."""
    console.print(f"\n[bold blue]Comparing models on {dataset}...[/bold blue]\n")
    
    reports = ReportManager()
    comparison = reports.compare_models(model_names, dataset)
    
    from rich.table import Table
    
    table = Table(title=f"Model Comparison: {dataset}")
    table.add_column("Rank", style="white", justify="right")
    table.add_column("Model", style="cyan")
    table.add_column("Score", style="yellow", justify="right")
    table.add_column("RPS", style="magenta", justify="right")
    table.add_column("Metric", style="green")
    table.add_column("Status", style="white")
    
    for rank, (model_name, rps) in enumerate(comparison["ranking"], 1):
        model_data = comparison["models"].get(model_name, {})
        
        if "error" in model_data:
            table.add_row(
                str(rank),
                model_name,
                "-",
                "-",
                "-",
                f"[red]{model_data['error']}[/red]"
            )
        else:
            is_sota = model_data.get("is_sota", False)
            status = "[green]✓ SOTA[/green]" if is_sota else ""
            table.add_row(
                str(rank),
                model_name,
                f"{model_data.get('raw_score', 0):.2f}",
                f"{rps:.2f}",
                model_data.get("metric", "").upper(),
                status
            )
    
    console.print(table)
    console.print()


def test_rps_calculation():
    """Test RPS calculation with SOTA manager."""
    console.print("\n[bold blue]Testing RPS Calculation...[/bold blue]\n")
    
    sota = SOTAManager()
    
    # Test datasets with different metric types
    test_cases = [
        ("aishell1", 0.80),      # CER, lower is better, should be RPS=1.0
        ("aishell1", 1.60),      # CER, should be RPS=0.5
        ("covost2_en2zh", 46.25), # BLEU, higher is better, should be RPS=1.0
        ("covost2_en2zh", 23.125), # BLEU, should be RPS=0.5
        ("iemocap", 69.38),      # Accuracy, RPS=1.0
    ]
    
    from rich.table import Table
    table = Table()
    table.add_column("Dataset", style="cyan")
    table.add_column("Score", style="yellow", justify="right")
    table.add_column("RPS", style="magenta", justify="right")
    table.add_column("Expected", style="green", justify="right")
    
    for dataset, score in test_cases:
        rps = sota.calculate_rps(dataset, score)
        baseline = sota.get_baseline(dataset)
        
        if baseline:
            if baseline.higher_is_better:
                expected = score / baseline.score
            else:
                expected = baseline.score / score
            
            table.add_row(
                dataset,
                f"{score:.2f}",
                f"{rps:.2f}" if rps else "N/A",
                f"{expected:.2f}"
            )
    
    console.print(table)
    console.print()


def generate_markdown():
    """Generate Markdown report."""
    console.print("\n[bold blue]Generating Markdown Report...[/bold blue]\n")
    
    reports = ReportManager()
    output_path = Path(__file__).parent.parent / "reports" / "model_report.md"
    markdown = reports.generate_markdown_report(output_path)
    
    console.print(f"[green]Report saved to: {output_path}[/green]")
    console.print(f"[dim]Report length: {len(markdown)} characters[/dim]\n")


def main():
    parser = argparse.ArgumentParser(
        description="SURE-EVAL Reports Demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show SOTA summary
  python demo/demo_reports.py --sota-summary
  
  # Show model summary
  python demo/demo_reports.py --model-summary Qwen3-Omni
  
  # Show leaderboard
  python demo/demo_reports.py --leaderboard
  
  # Show leaderboard for specific dataset
  python demo/demo_reports.py --leaderboard aishell1
  
  # Compare models
  python demo/demo_reports.py --compare Qwen3-Omni,Kimi-Audio --dataset aishell1
  
  # Test RPS calculation
  python demo/demo_reports.py --test-rps
  
  # Generate Markdown report
  python demo/demo_reports.py --generate-md
        """
    )
    
    parser.add_argument(
        "--sota-summary",
        action="store_true",
        help="Show SOTA baselines summary"
    )
    parser.add_argument(
        "--model-summary",
        metavar="MODEL",
        help="Show summary for a specific model"
    )
    parser.add_argument(
        "--leaderboard",
        nargs="?",
        const="overall",
        metavar="DATASET",
        help="Show leaderboard (optional: specific dataset)"
    )
    parser.add_argument(
        "--compare",
        metavar="MODELS",
        help="Compare models (comma-separated)"
    )
    parser.add_argument(
        "--dataset",
        metavar="DATASET",
        help="Dataset for comparison"
    )
    parser.add_argument(
        "--test-rps",
        action="store_true",
        help="Test RPS calculation"
    )
    parser.add_argument(
        "--generate-md",
        action="store_true",
        help="Generate Markdown report"
    )
    
    args = parser.parse_args()
    
    # If no arguments, show help
    if not any([
        args.sota_summary,
        args.model_summary,
        args.leaderboard,
        args.compare,
        args.test_rps,
        args.generate_md,
    ]):
        parser.print_help()
        return 0
    
    try:
        if args.sota_summary:
            show_sota_summary()
        
        if args.model_summary:
            show_model_summary(args.model_summary)
        
        if args.leaderboard:
            dataset = None if args.leaderboard == "overall" else args.leaderboard
            show_leaderboard(dataset)
        
        if args.compare:
            if not args.dataset:
                console.print("[red]Error: --dataset required when using --compare[/red]")
                return 1
            model_names = [m.strip() for m in args.compare.split(",")]
            compare_models(model_names, args.dataset)
        
        if args.test_rps:
            test_rps_calculation()
        
        if args.generate_md:
            generate_markdown()
        
        return 0
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
