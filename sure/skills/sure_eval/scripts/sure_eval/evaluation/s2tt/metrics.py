"""S2TT evaluation metrics."""

from __future__ import annotations

from sure_eval.evaluation.base import MetricResult


class BLEUMetric:
    """BLEU metric for translation."""

    def __init__(self, language: str = "zh") -> None:
        self.language = language
        self._bleu = None

    def _init_bleu(self):
        """Initialize BLEU calculator."""
        if self._bleu is None:
            from sacrebleu.metrics import BLEU
            tokenize = "zh" if self.language in ["zh", "ch", "chinese"] else "13a"
            self._bleu = BLEU(tokenize=tokenize)

    def calculate(
        self,
        prediction: str,
        reference: str,
        **kwargs,
    ) -> MetricResult:
        """Calculate BLEU (sentence-level)."""
        self._init_bleu()
        score = self._bleu.sentence_score(prediction, [reference])

        return MetricResult(
            metric_name="bleu",
            score=score.score,
            details={"bp": score.bp},
        )

    def calculate_batch(
        self,
        predictions: list[str],
        references: list[str],
        **kwargs,
    ) -> MetricResult:
        """Calculate BLEU (corpus-level)."""
        self._init_bleu()
        score = self._bleu.corpus_score(predictions, [references])

        return MetricResult(
            metric_name="bleu",
            score=score.score,
            details={
                "bp": score.bp,
                "precisions": score.precisions,
            },
        )

__all__ = [
    "BLEUMetric",
]
