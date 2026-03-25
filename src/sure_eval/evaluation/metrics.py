"""Evaluation metrics for SURE-EVAL."""

from __future__ import annotations

import re
import unicodedata
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


class TextNormalizer:
    """Text normalizer for ASR evaluation."""
    
    def normalize(self, text: str, language: str = "auto") -> str:
        """Normalize text."""
        if language == "auto":
            language = "zh" if self._contains_chinese(text) else "en"
        
        if language == "zh":
            return self._normalize_zh(text)
        else:
            return self._normalize_en(text)
    
    def _normalize_zh(self, text: str) -> str:
        """Normalize Chinese text."""
        text = unicodedata.normalize("NFKC", text)
        text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9\s]', '', text)
        text = ' '.join(text.split())
        return text.strip()
    
    def _normalize_en(self, text: str) -> str:
        """Normalize English text."""
        text = unicodedata.normalize("NFKC", text)
        text = text.lower()
        text = re.sub(r"[^a-z0-9'\s]", ' ', text)
        text = re.sub(r"'\s", ' ', text)
        text = re.sub(r"\s'", ' ', text)
        text = ' '.join(text.split())
        return text.strip()
    
    def _contains_chinese(self, text: str) -> bool:
        """Check if text contains Chinese."""
        return any('\u4e00' <= char <= '\u9fff' for char in text)


class CERMetric:
    """Character Error Rate metric."""
    
    def __init__(self) -> None:
        self.normalizer = TextNormalizer()
    
    def calculate(
        self,
        prediction: str,
        reference: str,
        **kwargs,
    ) -> MetricResult:
        """Calculate CER."""
        language = kwargs.get("language", "zh")
        
        pred_norm = self.normalizer.normalize(prediction, language)
        ref_norm = self.normalizer.normalize(reference, language)
        
        # Use editdistance for character-level distance
        import editdistance
        
        distance = editdistance.distance(pred_norm, ref_norm)
        cer = distance / len(ref_norm) if ref_norm else 0.0
        
        return MetricResult(
            metric_name="cer",
            score=cer,
            details={
                "distance": distance,
                "ref_len": len(ref_norm),
            },
        )
    
    def calculate_batch(
        self,
        predictions: list[str],
        references: list[str],
        **kwargs,
    ) -> MetricResult:
        """Calculate CER for batch."""
        scores = []
        total_distance = 0
        total_len = 0
        
        for pred, ref in zip(predictions, references):
            result = self.calculate(pred, ref, **kwargs)
            scores.append(result.score)
            total_distance += result.details["distance"]
            total_len += result.details["ref_len"]
        
        avg_cer = sum(scores) / len(scores) if scores else 0.0
        corpus_cer = total_distance / total_len if total_len else 0.0
        
        return MetricResult(
            metric_name="cer",
            score=corpus_cer,
            details={
                "avg_cer": avg_cer,
                "corpus_cer": corpus_cer,
                "num_samples": len(predictions),
            },
        )


class WERMetric:
    """Word Error Rate metric."""
    
    def __init__(self) -> None:
        self.normalizer = TextNormalizer()
    
    def calculate(
        self,
        prediction: str,
        reference: str,
        **kwargs,
    ) -> MetricResult:
        """Calculate WER."""
        language = kwargs.get("language", "en")
        
        pred_norm = self.normalizer.normalize(prediction, language)
        ref_norm = self.normalizer.normalize(reference, language)
        
        pred_words = pred_norm.split()
        ref_words = ref_norm.split()
        
        import editdistance
        distance = editdistance.distance(pred_words, ref_words)
        wer = distance / len(ref_words) if ref_words else 0.0
        
        return MetricResult(
            metric_name="wer",
            score=wer,
            details={
                "distance": distance,
                "ref_len": len(ref_words),
            },
        )
    
    def calculate_batch(
        self,
        predictions: list[str],
        references: list[str],
        **kwargs,
    ) -> MetricResult:
        """Calculate WER for batch."""
        total_distance = 0
        total_words = 0
        
        for pred, ref in zip(predictions, references):
            result = self.calculate(pred, ref, **kwargs)
            total_distance += result.details["distance"]
            total_words += result.details["ref_len"]
        
        wer = total_distance / total_words if total_words else 0.0
        
        return MetricResult(
            metric_name="wer",
            score=wer,
            details={
                "num_samples": len(predictions),
                "total_words": total_words,
            },
        )


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
