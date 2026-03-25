#!/usr/bin/env python3
"""
Demo: Quick test to verify the framework is working.

This is the simplest possible demo - it runs a smoke test to verify
that the framework can load and basic functionality works.

Usage:
    python demo_quick_test.py

This will:
    1. List available models
    2. List available datasets
    3. Show configuration
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from sure_eval.core.config import Config
from sure_eval.agent.evaluator import AutonomousEvaluator
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


def main():
    console.print("\n[bold blue]SURE-EVAL Quick Test Demo[/bold blue]")
    console.print(f"[dim]{'=' * 50}[/dim]\n")
    
    # Load configuration
    console.print("[yellow]1. Loading configuration...[/yellow]")
    try:
        config = Config.from_env()
        console.print(f"   [green]✓[/green] Data directory: {config.data.root}")
        console.print(f"   [green]✓[/green] Results directory: {config.data.results}")
    except Exception as e:
        console.print(f"   [red]✗ Failed: {e}[/red]")
        return 1
    
    # Initialize evaluator
    console.print("\n[yellow]2. Initializing evaluator...[/yellow]")
    try:
        evaluator = AutonomousEvaluator(config)
        console.print(f"   [green]✓[/green] Evaluator initialized")
    except Exception as e:
        console.print(f"   [red]✗ Failed: {e}[/red]")
        return 1
    
    # List available models
    console.print("\n[yellow]3. Discovering models...[/yellow]")
    try:
        tools = evaluator.tool_registry.list_tools()
        console.print(f"   [green]✓[/green] Found {len(tools)} tool(s)")
        
        model_table = Table(title="Available Tools")
        model_table.add_column("Tool", style="cyan")
        model_table.add_column("Status", style="yellow")
        
        for tool_name in tools:
            # Check if tool has pyproject.toml (uv setup)
            tool_dir = Path(__file__).parent.parent / "src" / "sure_eval" / "models" / tool_name
            has_uv = (tool_dir / "pyproject.toml").exists()
            status = "✓ uv ready" if has_uv else "⚠ needs setup"
            model_table.add_row(tool_name, status)
        
        console.print(model_table)
    except Exception as e:
        console.print(f"   [red]✗ Failed: {e}[/red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
    
    # List available datasets
    console.print("\n[yellow]4. Discovering datasets...[/yellow]")
    try:
        datasets = evaluator.dataset_manager.list_available()
        console.print(f"   [green]✓[/green] Found {len(datasets)} dataset(s)")
        
        dataset_table = Table(title="Available Datasets")
        dataset_table.add_column("Dataset", style="cyan")
        dataset_table.add_column("Task", style="green")
        dataset_table.add_column("Available", style="yellow")
        
        for dataset_name in datasets:
            info = evaluator.dataset_manager.get_info(dataset_name)
            task = info.get("task", "unknown") if info else "unknown"
            is_avail = evaluator.dataset_manager.is_available(dataset_name)
            dataset_table.add_row(dataset_name, task.upper(), "✓" if is_avail else "✗")
        
        console.print(dataset_table)
    except Exception as e:
        console.print(f"   [red]✗ Failed: {e}[/red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
    
    # Summary
    console.print(f"\n[bold green]✓ Quick test completed successfully![/bold green]\n")
    
    console.print(Panel.fit(
        "[bold]Next Steps:[/bold]\n\n"
        "1. Set up a model environment:\n"
        "   python setup_model.py asr_qwen3\n\n"
        "2. Run a quick evaluation:\n"
        "   python demo_evaluate_model.py -m asr_qwen3 -d aishell1 -n 10\n\n"
        "3. See all demos:\n"
        "   ls demo/",
        title="Getting Started",
        border_style="blue"
    ))
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
