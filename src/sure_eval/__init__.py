"""SURE-EVAL: Tool and Model Evaluation Framework."""

from sure_eval.core.config import Config
from sure_eval.core.logging import configure_logging, get_logger
from sure_eval.agent import AutonomousEvaluator, EvaluationResult
from sure_eval.datasets import DatasetManager
from sure_eval.evaluation import SUREEvaluator, RPSManager

__version__ = "0.1.0"

__all__ = [
    "Config",
    "configure_logging",
    "get_logger",
    "AutonomousEvaluator",
    "EvaluationResult",
    "DatasetManager",
    "SUREEvaluator",
    "RPSManager",
]
