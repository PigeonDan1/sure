"""
Model Registry for SURE-EVAL.

Manages model discovery and loading.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ModelInfo:
    """Model information."""
    name: str
    task: str
    path: Path
    config: dict[str, Any]
    
    @property
    def description(self) -> str:
        return self.config.get("description", "")
    
    @property
    def model_id(self) -> str:
        return self.config.get("model", {}).get("id", "")
    
    @property
    def is_implemented(self) -> bool:
        """Check if model is fully implemented."""
        server_file = self.path / "server.py"
        model_file = self.path / "model.py"
        return server_file.exists() and model_file.exists()
    
    def get_mcp_config(self) -> dict[str, Any]:
        """Get MCP configuration."""
        server_config = self.config.get("server", {})
        
        return {
            "name": self.name,
            "command": server_config.get("command", [".venv/bin/python", "server.py"]),
            "working_dir": str(self.path),
            "env": server_config.get("env", {}),
            "timeout": server_config.get("timeout", 300),
        }
    
    def get_test_results(self) -> dict[str, Any]:
        """Get all test results."""
        results_dir = self.path / "results"
        results = {}
        
        if results_dir.exists():
            for result_file in results_dir.glob("*.json"):
                dataset_name = result_file.stem.split("_")[0]
                try:
                    with open(result_file) as f:
                        results[dataset_name] = json.load(f)
                except:
                    pass
        
        return results


class ModelRegistry:
    """Registry for managing models."""
    
    def __init__(self, models_dir: str | Path | None = None) -> None:
        """
        Initialize registry.
        
        Args:
            models_dir: Directory containing models (default: src/sure_eval/models)
        """
        if models_dir is None:
            models_dir = Path(__file__).parent
        
        self.models_dir = Path(models_dir)
        self._models: dict[str, ModelInfo] = {}
        self._discover_models()
    
    def _discover_models(self) -> None:
        """Discover models in models directory."""
        for item in self.models_dir.iterdir():
            if not item.is_dir():
                continue
            
            # Skip special directories
            if item.name.startswith("__") or item.name.startswith("."):
                continue
            
            # Check for config.yaml
            config_file = item / "config.yaml"
            if config_file.exists():
                try:
                    import yaml
                    with open(config_file) as f:
                        config = yaml.safe_load(f)
                    
                    name = config.get("name", item.name)
                    task = config.get("task", "unknown")
                    
                    self._models[name] = ModelInfo(
                        name=name,
                        task=task,
                        path=item,
                        config=config,
                    )
                except Exception as e:
                    print(f"Error loading model config {item}: {e}")
    
    def list_models(self) -> list[str]:
        """List all model names."""
        return list(self._models.keys())
    
    def list_by_task(self, task: str) -> list[str]:
        """List models by task."""
        return [
            name for name, info in self._models.items()
            if info.task == task
        ]
    
    def get_model(self, name: str) -> ModelInfo | None:
        """Get model info by name."""
        return self._models.get(name)
    
    def get_mcp_configs(self) -> dict[str, Any]:
        """Get MCP configurations for all models."""
        return {
            name: info.get_mcp_config()
            for name, info in self._models.items()
            if info.is_implemented
        }
    
    def generate_mcp_tools_yaml(self) -> str:
        """Generate mcp_tools.yaml content."""
        configs = self.get_mcp_configs()
        
        lines = ["# MCP Tool Registry for SURE-EVAL", "# Auto-generated from models", "", "tools:"]
        
        for name, config in configs.items():
            lines.append(f"  {name}:")
            lines.append(f'    name: "{name}"')
            lines.append(f'    command: {config["command"]}')
            lines.append(f'    working_dir: "{config["working_dir"]}"')
            
            if config.get("env"):
                lines.append("    env:")
                for key, value in config["env"].items():
                    lines.append(f'      {key}: "{value}"')
            
            lines.append(f'    timeout: {config.get("timeout", 300)}')
            lines.append("")
        
        return "\n".join(lines)
    
    def print_summary(self) -> None:
        """Print summary of all models."""
        print("=" * 60)
        print("SURE-EVAL Model Registry")
        print("=" * 60)
        
        for name, info in sorted(self._models.items()):
            status = "✓" if info.is_implemented else "○"
            print(f"{status} {name:20s} [{info.task:10s}] {info.description[:30]}...")
        
        print("=" * 60)
        print(f"Total: {len(self._models)} models")
