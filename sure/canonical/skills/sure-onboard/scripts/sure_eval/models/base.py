"""
Base model interface for SURE-EVAL.

All models should inherit from BaseModel and implement required methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ModelConfig:
    """Model configuration."""
    name: str
    task: str
    model_id: str
    version: str = "1.0.0"
    description: str = ""
    languages: list[str] | None = None
    device: str = "auto"
    
    # Paths
    model_path: str | None = None
    working_dir: str | None = None


class BaseModel(ABC):
    """Base class for all models."""
    
    def __init__(self, config: ModelConfig | None = None) -> None:
        """Initialize model with config."""
        self.config = config or self._default_config()
        self._loaded = False
    
    @abstractmethod
    def _default_config(self) -> ModelConfig:
        """Return default configuration."""
        pass
    
    @abstractmethod
    def load(self) -> None:
        """Load model weights."""
        pass
    
    @abstractmethod
    def predict(self, input_data: Any) -> Any:
        """Run prediction."""
        pass
    
    def predict_batch(self, inputs: list[Any]) -> list[Any]:
        """Run batch prediction (default: loop over predict)."""
        return [self.predict(inp) for inp in inputs]
    
    def get_mcp_config(self) -> dict[str, Any]:
        """Get MCP server configuration."""
        working_dir = self.config.working_dir or str(Path(__file__).parent)
        
        return {
            "name": self.config.name,
            "command": [".venv/bin/python", "server.py"],
            "working_dir": working_dir,
            "env": {
                "MODEL_PATH": self.config.model_path or self.config.model_id,
                "DEVICE": self.config.device,
            },
            "timeout": 300,
        }
    
    def get_test_results(self, dataset: str) -> dict[str, Any] | None:
        """Get test results for a dataset."""
        results_dir = Path(__file__).parent / "results"
        
        # Look for result files
        for result_file in results_dir.glob(f"{dataset}_*.json"):
            import json
            with open(result_file) as f:
                return json.load(f)
        
        return None
