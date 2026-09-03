"""Configuration management for SURE-EVAL."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class DataConfig(BaseModel):
    """Data directory configuration."""
    root: str = "./data"
    cache: str = "./data/cache"
    models: str = "./data/models"
    datasets: str = "./data/datasets"
    results: str = "./results"


class ModelDownloadConfig(BaseModel):
    """Model download configuration."""
    max_retries: int = 3
    timeout: int = 300
    resume: bool = True


class ModelsConfig(BaseModel):
    """Model configuration."""
    providers: list[str] = ["huggingface", "modelscope"]
    cache_dir: str = "./data/models"
    download: ModelDownloadConfig = Field(default_factory=ModelDownloadConfig)


class DatasetDefinition(BaseModel):
    """Dataset definition."""
    name: str
    task: str
    language: str
    source: str
    dataset_id: str
    config: str | None = None
    subset: str | None = None  # Subset within the dataset (e.g., aishell1 in SURE_Test_Suites)
    splits: list[str] = Field(default_factory=list)
    num_samples: int | None = None  # Total number of samples


class DatasetsConfig(BaseModel):
    """Dataset configuration."""
    sources: list[str] = ["modelscope", "huggingface"]
    definitions: dict[str, DatasetDefinition] = Field(default_factory=dict)


class EvaluationConfig(BaseModel):
    """Evaluation configuration."""
    default_metrics: dict[str, str] = Field(default_factory=dict)
    metric_settings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    batch: dict[str, Any] = Field(default_factory=dict)


class ToolsConfig(BaseModel):
    """Tools configuration."""
    mcp: dict[str, Any] = Field(default_factory=dict)
    default_tools: dict[str, str] = Field(default_factory=dict)
    local: dict[str, dict[str, Any]] = Field(default_factory=dict)


class RPSBaseline(BaseModel):
    """RPS baseline definition."""
    metric: str
    score: float
    higher_is_better: bool


class RPSConfig(BaseModel):
    """RPS configuration."""
    baselines: dict[str, RPSBaseline] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    """Agent configuration."""
    auto_eval: dict[str, Any] = Field(default_factory=dict)
    decision: dict[str, Any] = Field(default_factory=dict)


class LoggingConfig(BaseModel):
    """Logging configuration."""
    level: str = "INFO"
    format: Literal["structured", "simple"] = "structured"
    file: str | None = "./logs/sure-eval.log"
    rotation: str = "1 day"
    retention: str = "30 days"


class APIConfig(BaseModel):
    """API configuration."""
    tool_api: dict[str, Any] = Field(default_factory=dict)
    model_api: dict[str, Any] = Field(default_factory=dict)


class Config(BaseModel):
    """Main configuration class."""
    data: DataConfig = Field(default_factory=DataConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    datasets: DatasetsConfig = Field(default_factory=DatasetsConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    rps: RPSConfig = Field(default_factory=RPSConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    
    @classmethod
    def from_yaml(cls, path: str | Path) -> Config:
        """Load configuration from YAML file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        
        return cls(**data)
    
    @classmethod
    def from_env(cls) -> Config:
        """Load configuration from environment variables."""
        config_path = os.environ.get("SURE_EVAL_CONFIG", "./config/default.yaml")
        return cls.from_yaml(config_path)
    
    def to_yaml(self, path: str | Path) -> None:
        """Save configuration to YAML file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False)
    
    def get_dataset(self, name: str) -> DatasetDefinition | None:
        """Get dataset definition by name."""
        return self.datasets.definitions.get(name)
    
    def get_baseline(self, dataset: str) -> RPSBaseline | None:
        """Get RPS baseline for a dataset."""
        return self.rps.baselines.get(dataset)
    
    def get_default_metric(self, task: str) -> str:
        """Get default metric for a task."""
        return self.evaluation.default_metrics.get(task, "accuracy")
    
    def ensure_directories(self) -> None:
        """Ensure all data directories exist."""
        for attr in ["root", "cache", "models", "datasets", "results"]:
            path = Path(getattr(self.data, attr))
            path.mkdir(parents=True, exist_ok=True)
