"""Classification evaluation metrics."""

from __future__ import annotations

from sure_eval.evaluation.base import MetricResult


class AccuracyMetric:
    """Accuracy metric for classification tasks."""

    def calculate(
        self,
        prediction: str,
        reference: str,
        **kwargs,
    ) -> MetricResult:
        """Calculate accuracy for single sample."""
        pred_norm = prediction.strip().lower()
        ref_norm = reference.strip().lower()

        # Normalize synonyms
        synonyms = {
            "happy": "hap", "happiness": "hap",
            "neutral": "neu",
            "angry": "ang", "anger": "ang",
            "sad": "sad", "sadness": "sad",
            "male": "man", "m": "man",
            "female": "woman", "f": "woman",
        }

        pred_norm = synonyms.get(pred_norm, pred_norm)
        ref_norm = synonyms.get(ref_norm, ref_norm)

        correct = 1.0 if pred_norm == ref_norm else 0.0

        return MetricResult(
            metric_name="accuracy",
            score=correct,
            details={"correct": correct},
        )

    def calculate_batch(
        self,
        predictions: list[str],
        references: list[str],
        **kwargs,
    ) -> MetricResult:
        """Calculate accuracy for batch."""
        correct = 0
        for pred, ref in zip(predictions, references):
            result = self.calculate(pred, ref, **kwargs)
            correct += result.score

        accuracy = correct / len(predictions) if predictions else 0.0

        return MetricResult(
            metric_name="accuracy",
            score=accuracy,
            details={
                "correct": int(correct),
                "total": len(predictions),
            },
        )

__all__ = [
    "AccuracyMetric",
]
