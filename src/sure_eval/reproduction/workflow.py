"""Generic Step A-E reproduction workflow utilities.

The helpers in this module are deliberately model- and dataset-agnostic. Case
files may call these utilities, but the utilities do not depend on case-specific
adapters.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sure_eval.models.registry import ModelRegistry

from .schema import Comparison, DatasetTarget, LocalEval, MetricDirection, ModelTarget, PaperClaim

HIGHER_IS_BETTER = {
    "accuracy",
    "acc",
    "f1",
    "macro_f1",
    "weighted_f1",
    "uar",
    "bleu",
    "bleu_char",
    "chrf",
    "chrf2",
    "intent accuracy",
    "intent_accuracy",
    "slot f1",
    "slot_f1",
}
LOWER_IS_BETTER = {"wer", "cer", "der", "jer", "cpwer", "cpcer", "mer", "eer"}

TASK_REFERENCE_FIELDS: dict[str, tuple[str, ...]] = {
    "ASR": ("text", "transcript", "target"),
    "SER": ("label", "emotion", "target"),
    "S2TT": ("translation", "reference_text", "target"),
    "SD": ("speaker_segments", "rttm", "target"),
    "SLU": ("intent", "slots", "answer", "label", "target"),
    "GR": ("label", "gender", "target"),
    "SA-ASR": ("speaker_segments", "rttm", "segments", "transcript", "target"),
}

TASK_METRIC_ROUTES: dict[str, tuple[str, ...]] = {
    "ASR": ("wer", "cer"),
    "SER": ("accuracy", "uar", "f1"),
    "S2TT": ("bleu", "chrf"),
    "SD": ("der", "jer"),
    "SLU": ("intent_accuracy", "slot_f1", "accuracy", "f1"),
    "GR": ("accuracy", "f1"),
    "SA-ASR": ("cpwer", "cpcer", "der"),
}


def _norm_metric(metric: str) -> str:
    return metric.strip().lower().replace("-", "_")


def default_metric_direction(metric: str) -> MetricDirection:
    normalized = _norm_metric(metric)
    if normalized in HIGHER_IS_BETTER:
        return "higher_is_better"
    if normalized in LOWER_IS_BETTER:
        return "lower_is_better"
    raise ValueError(f"Unknown metric direction for metric: {metric}")


def metric_direction(metric: str, explicit: str | None = None) -> MetricDirection:
    if explicit in {"higher_is_better", "lower_is_better"}:
        return explicit  # type: ignore[return-value]
    return default_metric_direction(metric)


def get_reference_value(sample: dict[str, Any], task: str) -> Any:
    fields = TASK_REFERENCE_FIELDS.get(task.upper(), ("target",))
    for field in fields:
        if field in sample and sample[field] not in (None, ""):
            if task.upper() == "SLU" and field == "slots" and "intent" in sample:
                return {"intent": sample.get("intent"), "slots": sample.get("slots")}
            return sample[field]
    return ""


def task_label_schema(task: str) -> dict[str, Any]:
    task_upper = task.upper()
    return {
        "task": task_upper,
        "reference_fields": list(TASK_REFERENCE_FIELDS.get(task_upper, ("target",))),
        "metrics": list(TASK_METRIC_ROUTES.get(task_upper, ())),
    }


def compare_paper_and_local(
    paper_claim: PaperClaim,
    local_eval: LocalEval | None,
    *,
    matched_threshold: float = 1e-6,
    slight_relative_threshold: float = 0.05,
) -> Comparison:
    if local_eval is None or local_eval.score is None:
        return Comparison(
            paper_value=paper_claim.paper_value,
            local_eval_value=None,
            absolute_delta=None,
            relative_delta=None,
            metric_direction=paper_claim.metric_direction,
            status="failed",
            reason="local evaluation result is missing",
            model_name=paper_claim.model_name,
            dataset=paper_claim.dataset,
            split=paper_claim.split,
            task=paper_claim.task,
            metric=paper_claim.metric,
        )

    if not _same_target(paper_claim, local_eval):
        return Comparison(
            paper_value=paper_claim.paper_value,
            local_eval_value=local_eval.score,
            absolute_delta=None,
            relative_delta=None,
            metric_direction=paper_claim.metric_direction,
            status="not_comparable",
            reason="model_name, dataset, split, task, or metric do not align",
            model_name=paper_claim.model_name,
            dataset=paper_claim.dataset,
            split=paper_claim.split,
            task=paper_claim.task,
            metric=paper_claim.metric,
        )

    paper_value = float(paper_claim.paper_value)
    local_value = float(local_eval.score)
    raw_delta = local_value - paper_value
    absolute_delta = abs(raw_delta)
    relative_delta = None if paper_value == 0 else absolute_delta / abs(paper_value)

    if absolute_delta <= matched_threshold:
        status = "matched"
        reason = "local value numerically matches the paper claim"
    elif relative_delta is not None and relative_delta <= slight_relative_threshold:
        status = "slightly_different"
        better = _local_is_better(raw_delta, paper_claim.metric_direction)
        reason = "local value is slightly different and better" if better else "local value is slightly different and worse"
    else:
        status = "significantly_different"
        better = _local_is_better(raw_delta, paper_claim.metric_direction)
        reason = "local value is significantly different and better" if better else "local value is significantly different and worse"

    return Comparison(
        paper_value=paper_value,
        local_eval_value=local_value,
        absolute_delta=absolute_delta,
        relative_delta=relative_delta,
        metric_direction=paper_claim.metric_direction,
        status=status,  # type: ignore[arg-type]
        reason=reason,
        model_name=paper_claim.model_name,
        dataset=paper_claim.dataset,
        split=paper_claim.split,
        task=paper_claim.task,
        metric=paper_claim.metric,
    )


def _local_is_better(raw_delta: float, direction: MetricDirection) -> bool:
    if direction == "higher_is_better":
        return raw_delta > 0
    return raw_delta < 0


def _same_target(claim: PaperClaim, local_eval: LocalEval) -> bool:
    return (
        _key(claim.model_name) == _key(local_eval.model_name)
        and _key(claim.dataset) == _key(local_eval.dataset)
        and _key(claim.split) == _key(local_eval.split)
        and _key(claim.task) == _key(local_eval.task)
        and _key(claim.metric) == _key(local_eval.metric)
    )


def _key(value: str | None) -> str:
    return (value or "").strip().lower().replace("-", "_").replace(" ", "_")


def build_model_readiness_report(model_name: str, models_dir: str | Path | None = None) -> dict[str, Any]:
    registry = ModelRegistry(models_dir=models_dir)
    info = registry.get_model(model_name)
    if info is None:
        return {
            "model_name": model_name,
            "exists": False,
            "model_target": ModelTarget(
                model_name=model_name,
                onboarding_state="not_onboarded",
                readiness_state="needs_tool_onboarding",
            ).to_dict(),
            "model_input_template": generic_model_input_template(model_name),
        }
    readiness_state = "ready" if info.is_implemented else "incomplete"
    return {
        "model_name": model_name,
        "exists": True,
        "model_target": ModelTarget(
            model_name=info.name,
            model_dir=str(info.path),
            repo_url=info.config.get("model", {}).get("repo_url"),
            checkpoint=info.config.get("model", {}).get("local_path"),
            onboarding_state="onboarded",
            readiness_state=readiness_state,
        ).to_dict(),
        "smoke_test": "not_run",
    }


def generic_model_input_template(model_name: str) -> dict[str, Any]:
    return {
        "model_id": model_name,
        "model_name": model_name,
        "task_type": "unknown",
        "deployment_type": "local",
        "repo": {"url": None, "commit": None},
        "weights": {
            "source": None,
            "local_path": None,
            "required": True,
            "cache_policy": "model_local",
            "local_dir_name": model_name,
        },
        "environment_hint": {
            "preferred_backend": "unknown",
            "python_version": None,
            "requires_gpu": None,
            "system_packages": [],
        },
        "phase1_runtime_target": "import_load_infer_smoke",
        "entrypoints": {"import_test": None, "load_test": None, "infer_test": None},
        "fixture": {"audio": None, "task_specific": {}, "fallback_allowed": True},
        "io_contract": {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": None,
            "required_fields": [],
            "nonempty_fields": [],
            "json_serializable": True,
        },
    }


def build_dataset_readiness_report(
    *,
    dataset_name: str,
    task: str,
    jsonl_path: str | Path | None,
    dataset_dir: str | Path | None = None,
    source_format: str = "unknown",
    split: str | None = None,
) -> dict[str, Any]:
    path = Path(jsonl_path) if jsonl_path else None
    num_samples = _count_jsonl(path) if path and path.exists() else None
    target = DatasetTarget(
        dataset_name=dataset_name,
        dataset_dir=str(dataset_dir) if dataset_dir else None,
        jsonl_path=str(path) if path else None,
        source_format=source_format,
        task=task,
        split=split,
        label_schema=task_label_schema(task),
        num_samples=num_samples,
    )
    return {
        "dataset_name": dataset_name,
        "exists": bool(path and path.exists()),
        "dataset_target": target.to_dict(),
    }


def _count_jsonl(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def write_json(path: str | Path, data: Any) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output
