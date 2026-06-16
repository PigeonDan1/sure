"""ASR evaluation metrics backed by the SURE evaluator."""

from __future__ import annotations

import tempfile
from pathlib import Path

from sure_eval.evaluation.base import MetricResult


def _write_key_text(path: str, texts: list[str]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for index, text in enumerate(texts, start=1):
            handle.write(f"utt{index}\t{text}\n")


class _ASRMetricBase:
    metric_name = "wer"
    tochar = False
    default_language = "en"

    def calculate(
        self,
        prediction: str,
        reference: str,
        **kwargs,
    ) -> MetricResult:
        """Calculate ASR error rate for a single sample."""
        return self.calculate_batch([prediction], [reference], **kwargs)

    def calculate_batch(
        self,
        predictions: list[str],
        references: list[str],
        **kwargs,
    ) -> MetricResult:
        """Calculate ASR error rate with SUREEvaluator semantics."""
        language = kwargs.get("language", self.default_language)
        verbose = kwargs.get("verbose", 0)

        ref_handle = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        hyp_handle = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        ref_path = ref_handle.name
        hyp_path = hyp_handle.name
        ref_handle.close()
        hyp_handle.close()

        try:
            _write_key_text(ref_path, references)
            _write_key_text(hyp_path, predictions)
            from sure_eval.evaluation.sure_evaluator import SUREEvaluator

            result = SUREEvaluator(language=language).evaluate(
                "ASR",
                ref_path,
                hyp_path,
                tochar=self.tochar,
                verbose=verbose,
            )
        finally:
            Path(ref_path).unlink(missing_ok=True)
            Path(hyp_path).unlink(missing_ok=True)

        return MetricResult(
            metric_name=self.metric_name,
            score=result["score"],
            details={
                "num_samples": len(predictions),
                "sure_result": result,
            },
        )


class CERMetric(_ASRMetricBase):
    """Character Error Rate metric using SUREEvaluator ASR scoring."""

    metric_name = "cer"
    tochar = True
    default_language = "zh"


class WERMetric(_ASRMetricBase):
    """Word Error Rate metric using SUREEvaluator ASR scoring."""

    metric_name = "wer"
    tochar = False
    default_language = "en"

__all__ = [
    "CERMetric",
    "WERMetric",
]
