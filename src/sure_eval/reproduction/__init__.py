"""Generic paper-result reproduction helpers for SURE-EVAL."""

from __future__ import annotations

from .schema import (
    Comparison,
    DatasetTarget,
    LocalEval,
    ModelTarget,
    PaperClaim,
    ReproductionTarget,
)
from .scoring import (
    DatasetScore,
    MetricScoreItem,
    ReproductionScoreReport,
    compute_reproduction_score,
    normalize_metric_name,
    score_metric_item,
)
from .workflow import (
    compare_paper_and_local,
    default_metric_direction,
    get_reference_value,
    metric_direction,
)

__all__ = [
    "Comparison",
    "DatasetTarget",
    "LocalEval",
    "ModelTarget",
    "PaperClaim",
    "ReproductionTarget",
    "DatasetScore",
    "MetricScoreItem",
    "ReproductionScoreReport",
    "compare_paper_and_local",
    "compute_reproduction_score",
    "default_metric_direction",
    "get_reference_value",
    "metric_direction",
    "normalize_metric_name",
    "score_metric_item",
]
