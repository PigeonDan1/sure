"""Task-scoped evaluation metric registry."""

from __future__ import annotations

from sure_eval.evaluation.asr.metrics import CERMetric, WERMetric
from sure_eval.evaluation.base import Metric
from sure_eval.evaluation.classification.metrics import AccuracyMetric
from sure_eval.evaluation.s2tt.metrics import BLEUMetric


class MetricRegistry:
    """Registry for evaluation metrics."""

    _METRICS = {
        "cer": CERMetric,
        "wer": WERMetric,
        "accuracy": AccuracyMetric,
        "bleu": BLEUMetric,
    }

    @classmethod
    def get_metric(cls, name: str, **kwargs) -> Metric:
        """Get a metric instance."""
        metric_class = cls._METRICS.get(name.lower())
        if not metric_class:
            raise ValueError(f"Unknown metric: {name}")
        return metric_class(**kwargs)

    @classmethod
    def list_metrics(cls) -> list[str]:
        """List available metrics."""
        return list(cls._METRICS.keys())
