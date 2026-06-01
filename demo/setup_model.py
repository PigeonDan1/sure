#!/usr/bin/env python3
"""
Setup script for model environments using uv.

This script sets up the Python environment for a specific model using uv,
which is a fast Python package installer and resolver.

Usage:
    python setup_model.py <model_name>
    python setup_model.py asr_qwen3
    python setup_model.py asr_whisper

What it does:
    1. Checks if uv is installed
    2. Creates a virtual environment in the model directory
    3. Installs dependencies from pyproject.toml
    4. Verifies the installation

Requirements:
    - uv must be installed: curl -LsSf https://astral.sh/uv/install.sh | sh
"""

import sys
import subprocess
import shutil
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


def check_uv() -> bool:
    """Check if uv is installed."""
    return shutil.which("uv") is not None


def get_model_dir(model_name: str) -> Path:
    """Get the model directory path."""
    return Path(__file__).parent.parent / "src" / "sure_eval" / "models" / model_name


def setup_model(model_name: str) -> bool:
    """Set up the environment for a model."""
    
    model_dir = get_model_dir(model_name)
    
    # Check if model exists
    if not model_dir.exists():
        console.print(f"[red]Error: Model '{model_name}' not found at {model_dir}[/red]")
        return False
    
    # Check if pyproject.toml exists
    pyproject_path = model_dir / "pyproject.toml"
    if not pyproject_path.exists():
        console.print(f"[red]Error: pyproject.toml not found in {model_dir}[/red]")
        console.print(f"[yellow]Please create pyproject.toml for {model_name} first.[/yellow]")
        return False
    
    console.print(f"\n[bold blue]Setting up environment for {model_name}[/bold blue]")
    console.print(f"[dim]Model directory: {model_dir}[/dim]\n")
    
    # Change to model directory
    original_dir = Path.cwd()
    
    try:
        import os
        os.chdir(model_dir)
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            
            # Step 1: Create virtual environment
            task = progress.add_task("Creating virtual environment...", total=None)
            
            venv_path = model_dir / ".venv"
            if venv_path.exists():
                progress.update(task, description="Removing existing virtual environment...")
                shutil.rmtree(venv_path)
            
            progress.update(task, description="Creating virtual environment...")
            result = subprocess.run(
                ["uv", "venv"],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                console.print(f"[red]Failed to create venv: {result.stderr}[/red]")
                return False
            
            # Step 2: Install dependencies
            progress.update(task, description="Installing dependencies...")
            result = subprocess.run(
                ["uv", "pip", "install", "."],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                console.print(f"[red]Failed to install dependencies: {result.stderr}[/red]")
                return False
            
            # Step 3: Install additional common dependencies if needed
            progress.update(task, description="Installing MCP dependencies...")
            result = subprocess.run(
                ["uv", "pip", "install", "mcp"],
                capture_output=True,
                text=True
            )
            # Don't fail if this fails - it might already be in pyproject.toml
            
            progress.update(task, description="Setup complete!")
        
        # Verify installation
        console.print(f"\n[yellow]Verifying installation...[/yellow]")
        
        python_path = venv_path / "bin" / "python"
        if not python_path.exists():
            python_path = venv_path / "Scripts" / "python.exe"  # Windows
        
        result = subprocess.run(
            [str(python_path), "-c", "import sys; print(sys.version)"],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            console.print(f"[green]✓ Python version: {result.stdout.strip()}[/green]")
        else:
            console.print(f"[red]✗ Failed to verify Python installation[/red]")
            return False
        
        # Check if key packages are installed
        result = subprocess.run(
            [str(python_path), "-c", "import mcp; print('MCP version:', mcp.__version__)"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            console.print(f"[green]✓ {result.stdout.strip()}[/green]")
        else:
            console.print(f"[yellow]⚠ MCP not found in environment (may be installed separately)[/yellow]")
        
        console.print(f"\n[bold green]✓ {model_name} environment setup complete![/bold green]")
        console.print(f"[dim]Virtual environment: {venv_path}[/dim]")
        
        return True
        
    finally:
        os.chdir(original_dir)


def list_models():
    """List all available models."""
    models_dir = Path(__file__).parent.parent / "src" / "sure_eval" / "models"
    
    if not models_dir.exists():
        console.print("[red]Models directory not found[/red]")
        return
    
    console.print("\n[bold]Available Models:[/bold]\n")
    
    from rich.table import Table
    table = Table()
    table.add_column("Model", style="cyan")
    table.add_column("pyproject.toml", style="green")
    table.add_column(".venv", style="yellow")
    table.add_column("Status", style="white")
    
    for item in sorted(models_dir.iterdir()):
        if item.is_dir() and not item.name.startswith("_"):
            has_pyproject = (item / "pyproject.toml").exists()
            has_venv = (item / ".venv").exists()
            
            if has_pyproject and has_venv:
                status = "[green]Ready[/green]"
            elif has_pyproject:
                status = "[yellow]Needs setup[/yellow]"
            else:
                status = "[red]No pyproject.toml[/red]"
            
            table.add_row(
                item.name,
                "✓" if has_pyproject else "✗",
                "✓" if has_venv else "✗",
                status
            )
    
    console.print(table)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Setup model environments using uv",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Setup a specific model
  python setup_model.py asr_qwen3
  
  # List all models
  python setup_model.py --list
  
  # Install uv first if not already installed
  curl -LsSf https://astral.sh/uv/install.sh | sh
        """
    )
    parser.add_argument(
        "model",
        nargs="?",
        help="Model name to setup (e.g., asr_qwen3)"
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List all available models"
    )
    
    args = parser.parse_args()
    
    # Check if uv is installed
    if not check_uv():
        console.print("[red]Error: uv is not installed![/red]")
        console.print("\nTo install uv:")
        console.print("  curl -LsSf https://astral.sh/uv/install.sh | sh")
        console.print("\nOr visit: https://github.com/astral-sh/uv")
        return 1
    
    console.print("[green]✓ uv is installed[/green]")
    
    if args.list:
        list_models()
        return 0
    
    if not args.model:
        parser.print_help()
        console.print("\n[yellow]Available models:[/yellow]")
        list_models()
        return 1
    
    success = setup_model(args.model)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
