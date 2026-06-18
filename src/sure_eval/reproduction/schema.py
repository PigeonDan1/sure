"""Schemas for generic paper-to-local reproduction targets."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

MetricDirection = Literal["higher_is_better", "lower_is_better"]
ComparisonStatus = Literal[
    "matched",
    "slightly_different",
    "significantly_different",
    "not_comparable",
    "failed",
]


@dataclass
class PaperClaim:
    paper_id: str
    paper_title: str
    source_pdf: str | None
    evidence_page: int | None
    evidence_table: str | None
    evidence_text: str
    model_name: str
    dataset: str
    split: str | None
    task: str
    metric: str
    metric_direction: MetricDirection
    paper_value: float
    paper_value_unit: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ModelTarget:
    model_name: str
    model_dir: str | None = None
    repo_url: str | None = None
    checkpoint: str | None = None
    onboarding_state: str = "unknown"
    readiness_state: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DatasetTarget:
    dataset_name: str
    dataset_dir: str | None = None
    jsonl_path: str | None = None
    source_format: str = "unknown"
    task: str = "unknown"
    split: str | None = None
    label_schema: dict[str, Any] = field(default_factory=dict)
    num_samples: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LocalEval:
    protocol_id: str
    prediction_file: str | None
    eval_result_file: str | None
    metric: str
    score: float | None
    score_unit: str
    num_samples: int
    evaluator_version: str
    model_name: str | None = None
    dataset: str | None = None
    split: str | None = None
    task: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Comparison:
    paper_value: float | None
    local_eval_value: float | None
    absolute_delta: float | None
    relative_delta: float | None
    metric_direction: MetricDirection | None
    status: ComparisonStatus
    reason: str
    model_name: str | None = None
    dataset: str | None = None
    split: str | None = None
    task: str | None = None
    metric: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReproductionTarget:
    paper_claim: PaperClaim
    model_target: ModelTarget
    dataset_target: DatasetTarget
    local_eval: LocalEval | None = None
    comparison: Comparison | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "paper_claim": self.paper_claim.to_dict(),
            "model_target": self.model_target.to_dict(),
            "dataset_target": self.dataset_target.to_dict(),
            "local_eval": self.local_eval.to_dict() if self.local_eval else None,
            "comparison": self.comparison.to_dict() if self.comparison else None,
        }
        return data
