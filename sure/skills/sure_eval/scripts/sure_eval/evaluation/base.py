"""Shared evaluation metric types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class MetricResult:
    """Result of metric calculation."""

    metric_name: str
    score: float
    details: dict[str, Any] = field(default_factory=dict)


class Metric(Protocol):
    """Protocol for evaluation metrics."""

    def calculate(
        self,
        prediction: str,
        reference: str,
        **kwargs,
    ) -> MetricResult:
        """Calculate metric for a single sample."""
        ...

    def calculate_batch(
        self,
        predictions: list[str],
        references: list[str],
        **kwargs,
    ) -> MetricResult:
        """Calculate metric for a batch."""
        ...
