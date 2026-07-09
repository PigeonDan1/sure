"""Reproduction-level scoring for paper-to-local evaluation runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from typing import Any, Literal

MetricDirection = Literal["higher_is_better", "lower_is_better"]

HIGHER_IS_BETTER_METRICS = {
    "WA",
    "UA",
    "UAR",
    "WF1",
    "MacroF1",
    "Accuracy",
    "Acc",
    "F1",
    "BLEU",
    "chrF",
    "chrF2",
    "SIM",
    "SIM-o",
    "SS",
    "MOS",
    "UTMOS",
    "DNSMOS",
    "WVMOS",
    "WV-MOS",
    "sim/eres2net",
}

LOWER_IS_BETTER_METRICS = {
    "WER",
    "CER",
    "DER",
    "JER",
    "cpWER",
    "cpCER",
    "MER",
    "SER_error",
    "IER",
    "insertion_error_rate",
    "deletion_error_rate",
    "#Ins.&Del.",
    "Ins Del",
    "insertions_deletions",
    "5-Dup",
    "RTF",
}

READINESS_POINTS = {
    "import": 20,
    "load": 20,
    "infer": 25,
    "contract": 25,
    "smoke_test": 10,
}

READINESS_KEY_VARIANTS = {
    "import": {"import", "import_test", "validate_import"},
    "load": {"load", "load_test", "validate_load"},
    "infer": {"infer", "inference", "infer_test", "validate_infer"},
    "contract": {"contract", "validate_contract", "io_contract"},
    "smoke_test": {
        "smoke",
        "smoke_test",
        "smoke_passed",
        "smoke_test_passed",
        "server_smoke_test_passed",
        "server_declaration_smoke",
    },
}

READINESS_FILE_NAMES = (
    "runtime_readiness.json",
    "tool_readiness_routing.json",
    "model_artifacts_verdict.json",
    "model artifacts verdict.json",
    "verdict.json",
    "assessment_report.json",
    "execution_readiness_report.json",
    "smoke_test_report.json",
)

MODEL_LOCAL_READINESS_FILES = (
    "artifacts/verdict.json",
    "artifacts/artifact_manifest.json",
    "artifacts/validation.log",
    "config.yaml",
    "model.spec.yaml",
)

PASS_VALUES = {"pass", "passed", "success", "ready", "ok", "server_ready"}
FAIL_VALUES = {"fail", "failed", "error", "blocked", "tool_broken_needs_repair"}
UNKNOWN_VALUES = {"unknown", "not_run", "none", "null", ""}

COMPARABILITY_RISK_FACTORS = {
    "dataset_mismatch": 0.60,
    "split_mismatch": 0.70,
    "metric_definition_mismatch": 0.50,
    "normalization_mismatch": 0.75,
    "sample_scope_smoke_only": 0.30,
    "partial_dataset": 0.50,
    "protocol_mismatch": 0.70,
}

COMMON_SUBDIRS = (
    "",
    "paper_to_userspec",
    "paper_parse",
    "comparison",
    "evaluation",
    "model_readiness",
    "preflight",
    "final",
    "continuation",
)

READINESS_RGLOB_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".runtime",
    ".venv",
    "__pycache__",
    "dataset",
    "inference",
    "metrics",
}


@dataclass
class MetricScoreItem:
    dataset: str
    metric: str
    normalized_metric: str
    direction: str | None
    paper_value: float | None
    local_value: float | None
    match_score: float | None
    weight: float
    status: str
    reason: str
    local_better_than_paper: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DatasetScore:
    dataset: str
    metric_agreement_score: float | None
    metric_items: list[MetricScoreItem]
    aggregation_method: str
    warnings: list[str] = field(default_factory=list)
    status: str = "not_evaluable"
    weight: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReproductionScoreReport:
    run_root: str
    paper_side_score: float | None
    runtime_readiness_score: float
    comparability_factor: float
    metric_agreement_score: float | None
    local_reproduction_score: float | None
    final_score: float | None
    dataset_scores: list[DatasetScore]
    readiness_breakdown: dict[str, dict[str, Any]]
    weights: dict[str, float]
    status: str
    warnings: list[str]
    evidence_files: list[str]
    created_at: str
    comparability_notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _MetricEntry:
    dataset: str
    metric: str
    paper_value: float | None
    local_value: float | None
    source: str
    included_in_score: bool | None = None
    exclusion_reason: str | None = None


@dataclass
class _ReadinessEvidence:
    canonical: str
    status: str
    path: Path
    json_path: str
    value: Any
    evidence_scope: str
    reason: str


def normalize_metric_name(metric: str | None) -> str:
    """Normalize metric spelling across case, separators, punctuation, and symbols."""

    text = str(metric or "").casefold().strip()
    return re.sub(r"[^a-z0-9]+", "", text)


def _normalize_weight_key(value: str) -> str:
    return normalize_metric_name(value)


def _normalize_dataset_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


HIGHER_NORMALIZED = {normalize_metric_name(metric) for metric in HIGHER_IS_BETTER_METRICS}
LOWER_NORMALIZED = {normalize_metric_name(metric) for metric in LOWER_IS_BETTER_METRICS}


def metric_direction(metric: str | None) -> MetricDirection | None:
    normalized = normalize_metric_name(metric)
    if normalized in HIGHER_NORMALIZED:
        return "higher_is_better"
    if normalized in LOWER_NORMALIZED:
        return "lower_is_better"
    return None


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def parse_numeric(value: Any) -> float | None:
    if _is_finite_number(value):
        return float(value)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.casefold() in UNKNOWN_VALUES:
            return None
        cleaned = text.replace(",", "").rstrip("%").strip()
        try:
            number = float(cleaned)
        except ValueError:
            match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", cleaned)
            if not match:
                return None
            number = float(match.group(0))
        return number if math.isfinite(number) else None
    return None


def _round_score(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(100.0, float(value)))


def score_metric_item(dataset: str, metric: str, paper_value: Any, local_value: Any) -> MetricScoreItem:
    paper = parse_numeric(paper_value)
    local = parse_numeric(local_value)
    normalized = normalize_metric_name(metric)
    direction = metric_direction(metric)

    if direction is None:
        return MetricScoreItem(
            dataset=dataset,
            metric=metric,
            normalized_metric=normalized,
            direction=None,
            paper_value=paper,
            local_value=local,
            match_score=None,
            weight=0.0,
            status="not_evaluable",
            reason="unknown_metric_direction",
        )

    if paper is None or local is None:
        return MetricScoreItem(
            dataset=dataset,
            metric=metric,
            normalized_metric=normalized,
            direction=direction,
            paper_value=paper,
            local_value=local,
            match_score=None,
            weight=0.0,
            status="not_evaluable",
            reason="missing_or_non_finite_value",
        )

    if paper == local:
        return MetricScoreItem(
            dataset=dataset,
            metric=metric,
            normalized_metric=normalized,
            direction=direction,
            paper_value=paper,
            local_value=local,
            match_score=100.0,
            weight=0.0,
            status="evaluated",
            reason="ok",
            local_better_than_paper=False,
        )

    if paper <= 0 or local <= 0:
        denominator = paper if direction == "higher_is_better" else local
        reason = "zero_division_risk" if denominator == 0 else "non_positive_value"
        return MetricScoreItem(
            dataset=dataset,
            metric=metric,
            normalized_metric=normalized,
            direction=direction,
            paper_value=paper,
            local_value=local,
            match_score=None,
            weight=0.0,
            status="not_evaluable",
            reason=reason,
        )

    if direction == "higher_is_better":
        score = 100.0 * min(1.0, local / paper)
        better = local > paper
    else:
        score = 100.0 * min(1.0, paper / local)
        better = local < paper

    return MetricScoreItem(
        dataset=dataset,
        metric=metric,
        normalized_metric=normalized,
        direction=direction,
        paper_value=paper,
        local_value=local,
        match_score=_round_score(score),
        weight=0.0,
        status="evaluated",
        reason="ok",
        local_better_than_paper=better,
    )


def _lookup(obj: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in obj:
            return obj[key]
    normalized = {normalize_metric_name(key): value for key, value in obj.items()}
    for key in keys:
        lookup_key = normalize_metric_name(key)
        if lookup_key in normalized:
            return normalized[lookup_key]
    return None


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_structured_readiness_file(path: Path) -> Any:
    if path.suffix.casefold() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - dependency is declared by the project
            raise ValueError(f"PyYAML is required to parse {path}") from exc
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    if path.suffix.casefold() in {".log", ".txt"}:
        rows: list[Any] = []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                else:
                    if isinstance(parsed, dict):
                        parsed.setdefault("line", line_number)
                    rows.append(parsed)
        return {"log_entries": rows[-200:]}
    return _read_json(path)


def _candidate_paths(run_root: Path, filename: str) -> list[Path]:
    paths = []
    for subdir in COMMON_SUBDIRS:
        paths.append(run_root / subdir / filename if subdir else run_root / filename)
    seen: set[Path] = set()
    unique = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _find_first_file(run_root: Path, filename: str) -> Path | None:
    for path in _candidate_paths(run_root, filename):
        if path.exists():
            return path
    matches = sorted(run_root.rglob(filename))
    return matches[0] if matches else None


def _shallow_readiness_candidate_dirs(run_root: Path) -> list[Path]:
    dirs: list[Path] = [run_root]
    for subdir in COMMON_SUBDIRS:
        if subdir:
            dirs.append(run_root / subdir)
    try:
        children = sorted(run_root.iterdir(), key=lambda path: str(path))
    except OSError:
        children = []
    for path in children:
        if path.name in READINESS_RGLOB_SKIP_DIRS:
            continue
        if path.is_dir():
            dirs.append(path)
    seen: set[Path] = set()
    unique: list[Path] = []
    for directory in dirs:
        if directory not in seen:
            seen.add(directory)
            unique.append(directory)
    return unique


def _find_files(run_root: Path, filenames: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for filename in filenames:
        for path in _candidate_paths(run_root, filename):
            if path.exists() and path not in seen:
                found.append(path)
                seen.add(path)
    for directory in _shallow_readiness_candidate_dirs(run_root):
        for filename in filenames:
            path = directory / filename
            if path.exists() and path not in seen:
                found.append(path)
                seen.add(path)
    return found


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _read_yaml_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = _read_structured_readiness_file(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _model_dir_aliases(model_dir: Path, *, include_yaml: bool = False) -> set[str]:
    aliases = {model_dir.name}
    if "__" in model_dir.name:
        aliases.add(model_dir.name.split("__", 1)[1])
    if not include_yaml:
        return aliases
    for yaml_name in ("model.spec.yaml", "config.yaml"):
        metadata = _read_yaml_metadata(model_dir / yaml_name)
        for key in ("model", "model_name", "model_id", "repo_id"):
            value = _lookup(metadata, (key,))
            if isinstance(value, str) and value.strip():
                aliases.add(value)
        weights = metadata.get("weights")
        if isinstance(weights, dict):
            value = _lookup(weights, ("repo_id", "model_id"))
            if isinstance(value, str) and value.strip():
                aliases.add(value)
    return aliases


def _extract_model_strings(data: Any, keys: set[str]) -> list[str]:
    values: list[str] = []
    if data is None:
        return values
    normalized_keys = {normalize_metric_name(key) for key in keys}
    for key, value, _path in _walk_json(data):
        if normalize_metric_name(key) not in normalized_keys:
            continue
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return values


def _resolve_declared_model_dir(value: str, repo_root: Path) -> Path | None:
    candidate = Path(value).expanduser()
    candidates = [candidate] if candidate.is_absolute() else [
        repo_root / candidate,
        repo_root / "src" / "sure_eval" / "models" / candidate,
    ]
    for path in candidates:
        if path.exists() and path.is_dir():
            return path.resolve()
    return None


def _infer_model_dir(
    run_root: Path,
    comparison_doc: Any,
    readiness_docs: list[tuple[Path, Any] | tuple[Path, Any, str]],
) -> Path | None:
    repo_root = _repo_root()
    docs = [comparison_doc] if comparison_doc is not None else []
    docs.extend(doc for _path, doc, *_scope in readiness_docs)

    for doc in docs:
        for value in _extract_model_strings(
            doc,
            {"model_dir", "model_directory", "model_local_dir", "sure_model_dir"},
        ):
            resolved = _resolve_declared_model_dir(value, repo_root)
            if resolved:
                return resolved

    models_root = repo_root / "src" / "sure_eval" / "models"
    if not models_root.exists():
        return None

    model_strings: list[tuple[str, int]] = [(run_root.name, 1)]
    for doc in docs:
        for value in _extract_model_strings(
            doc,
            {"model", "model_name", "model_id", "runtime_model_name"},
        ):
            model_strings.append((value, 3))

    model_dirs = sorted(path for path in models_root.iterdir() if path.is_dir())

    def collect_matches(*, include_yaml: bool) -> list[tuple[int, Path]]:
        matches: list[tuple[int, Path]] = []
        for model_dir in model_dirs:
            aliases = {normalize_metric_name(alias) for alias in _model_dir_aliases(model_dir, include_yaml=include_yaml)}
            aliases = {alias for alias in aliases if alias}
            for value, priority in model_strings:
                normalized_value = normalize_metric_name(value)
                if not normalized_value:
                    continue
                for alias in aliases:
                    if normalized_value == alias:
                        matches.append((priority * 1000 + len(alias), model_dir.resolve()))
                    elif alias in normalized_value:
                        matches.append((priority * 1000 + len(alias), model_dir.resolve()))
                    elif normalized_value in alias and len(normalized_value) >= 3:
                        matches.append((priority * 1000 + len(normalized_value), model_dir.resolve()))
        return matches

    matches = collect_matches(include_yaml=False)
    if not matches:
        matches = collect_matches(include_yaml=True)

    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
    return matches[0][1]


def _find_model_local_readiness_docs(
    model_dir: Path | None,
    warnings: list[str],
) -> list[tuple[Path, Any, str]]:
    if model_dir is None:
        return []
    docs: list[tuple[Path, Any, str]] = []
    for relative_name in MODEL_LOCAL_READINESS_FILES:
        path = model_dir / relative_name
        if not path.exists():
            continue
        try:
            docs.append((path, _read_structured_readiness_file(path), "model_local_onboarding"))
        except (json.JSONDecodeError, ValueError) as exc:
            warnings.append(f"Could not parse model-local readiness file {path}: {exc}")
    return docs


def _extract_metric_entry(obj: dict[str, Any], default_dataset: str, source: str) -> _MetricEntry | None:
    metric = _lookup(obj, ("metric", "metric_name", "paper_metric", "name"))
    if metric is None:
        return None
    dataset = _lookup(obj, ("dataset", "dataset_name")) or default_dataset or "default"
    included_in_score = _parse_included_in_score(_lookup(obj, ("included_in_score", "include_in_score")))
    status = _lookup(obj, ("status", "metric_status"))
    if included_in_score is None and _metric_status_excludes_from_score(status):
        included_in_score = False
    return _MetricEntry(
        dataset=str(dataset),
        metric=str(metric),
        paper_value=parse_numeric(
            _lookup(obj, ("paper_value", "paper", "paper_score", "reference_value"))
        ),
        local_value=parse_numeric(
            _lookup(obj, ("local_value", "local", "local_score", "local_eval_value", "reproduced_value"))
        ),
        source=source,
        included_in_score=included_in_score,
        exclusion_reason=_lookup(obj, ("exclusion_reason", "exclude_reason", "reason", "status")),
    )


def _first_numeric(obj: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _lookup(obj, (key,))
        number = parse_numeric(value)
        if number is not None:
            return number
    return None


def _extract_paper_local_metric_entries(data: dict[str, Any], source: str) -> list[_MetricEntry]:
    paper = data.get("paper")
    if not isinstance(paper, dict):
        return []

    local_sources = [
        data.get("old_run_values_if_found"),
        data.get("local"),
        data.get("rerun_values"),
    ]
    local_sources = [source_obj for source_obj in local_sources if isinstance(source_obj, dict)]
    if not local_sources:
        return []

    dataset = (
        _lookup(data, ("dataset", "dataset_name", "dataset_id"))
        or _lookup(paper, ("dataset", "dataset_name", "dataset_id"))
        or next(
            (
                _lookup(source_obj, ("dataset", "dataset_name", "dataset_id"))
                for source_obj in local_sources
                if _lookup(source_obj, ("dataset", "dataset_name", "dataset_id")) is not None
            ),
            None,
        )
        or "default"
    )
    metric_specs = (
        ("WER", ("wer_percent", "wer"), ("wer_percent", "wer")),
        ("IER", ("ier_percent", "ier", "insertion_error_rate"), ("ier_percent", "ier", "insertion_error_rate")),
        ("5-Dup", ("five_dup", "five_dup_total", "5-Dup"), ("five_dup_total", "five_dup", "5-Dup")),
    )

    entries: list[_MetricEntry] = []
    for metric, paper_keys, local_keys in metric_specs:
        paper_value = _first_numeric(paper, paper_keys)
        local_value = next(
            (
                local_number
                for source_obj in local_sources
                for local_number in (_first_numeric(source_obj, local_keys),)
                if local_number is not None
            ),
            None,
        )
        if paper_value is None and local_value is None:
            continue
        entries.append(
            _MetricEntry(
                dataset=str(dataset),
                metric=metric,
                paper_value=paper_value,
                local_value=local_value,
                source=source,
            )
        )
    return entries


def _parse_included_in_score(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "y", "1", "include", "included"}:
            return True
        if normalized in {"false", "no", "n", "0", "exclude", "excluded"}:
            return False
    return None


def _metric_status_excludes_from_score(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().casefold()
    return normalized in {
        "not_evaluated",
        "not_evaluated_for_full",
        "excluded",
        "excluded_from_score",
        "skipped",
        "failed",
        "error",
    }


def extract_metric_entries(data: Any, source: str = "paper_value_comparison.json") -> list[_MetricEntry]:
    entries: list[_MetricEntry] = []

    if isinstance(data, list):
        for item in data:
            entries.extend(extract_metric_entries(item, source))
        return entries

    if not isinstance(data, dict):
        return entries

    default_dataset = str(_lookup(data, ("dataset", "dataset_name")) or "default")

    datasets = data.get("datasets")
    if isinstance(datasets, list):
        for dataset_obj in datasets:
            if not isinstance(dataset_obj, dict):
                continue
            dataset_name = str(_lookup(dataset_obj, ("dataset", "dataset_name")) or default_dataset)
            metrics = dataset_obj.get("metrics")
            if isinstance(metrics, list):
                for metric_obj in metrics:
                    if isinstance(metric_obj, dict):
                        entry = _extract_metric_entry(metric_obj, dataset_name, source)
                        if entry:
                            entries.append(entry)
            else:
                entry = _extract_metric_entry(dataset_obj, dataset_name, source)
                if entry:
                    entries.append(entry)
        return entries

    metrics = data.get("metrics")
    if isinstance(metrics, list):
        for metric_obj in metrics:
            if isinstance(metric_obj, dict):
                entry = _extract_metric_entry(metric_obj, default_dataset, source)
                if entry:
                    entries.append(entry)
        return entries

    comparisons = data.get("comparisons")
    if isinstance(comparisons, list):
        for comparison in comparisons:
            if isinstance(comparison, dict):
                entry = _extract_metric_entry(comparison, default_dataset, source)
                if entry:
                    entries.append(entry)
        if entries:
            return entries

    paper_local_entries = _extract_paper_local_metric_entries(data, source)
    if paper_local_entries:
        return paper_local_entries

    entry = _extract_metric_entry(data, default_dataset, source)
    if entry:
        entries.append(entry)
    return entries


def _normalize_metric_weights(metric_weights: dict[str, float] | None) -> dict[str, float]:
    if not metric_weights:
        return {}
    normalized: dict[str, float] = {}
    for key, value in metric_weights.items():
        number = parse_numeric(value)
        if number is not None and number > 0:
            normalized[_normalize_weight_key(key)] = number
    return normalized


def _normalize_dataset_weights(dataset_weights: dict[str, float] | None) -> dict[str, float]:
    if not dataset_weights:
        return {}
    normalized: dict[str, float] = {}
    for key, value in dataset_weights.items():
        number = parse_numeric(value)
        if number is not None and number > 0:
            normalized[_normalize_dataset_key(key)] = number
    return normalized


def _normalize_excluded_metrics(excluded_metrics: list[str] | set[str] | tuple[str, ...] | None) -> set[str]:
    if not excluded_metrics:
        return set()
    return {normalize_metric_name(metric) for metric in excluded_metrics}


def score_dataset(
    dataset: str,
    entries: list[_MetricEntry],
    metric_weights: dict[str, float] | None = None,
    excluded_metrics: list[str] | set[str] | tuple[str, ...] | None = None,
) -> DatasetScore:
    warnings: list[str] = []
    normalized_excluded = _normalize_excluded_metrics(excluded_metrics)
    items: list[MetricScoreItem] = []
    for entry in entries:
        item = score_metric_item(entry.dataset, entry.metric, entry.paper_value, entry.local_value)
        if item.normalized_metric in normalized_excluded:
            item.status = "excluded_from_score"
            item.reason = "excluded_by_user"
            item.weight = 0.0
        elif entry.included_in_score is False:
            item.status = "excluded_from_score"
            item.reason = str(entry.exclusion_reason or "auxiliary_reported_only")
            item.weight = 0.0
        items.append(item)
    valid_items = [item for item in items if item.status == "evaluated" and item.match_score is not None]

    for item in items:
        if item.status == "excluded_from_score":
            warnings.append(f"{dataset}:{item.metric} excluded from MetricAgreementScore: {item.reason}")
        elif item.status != "evaluated":
            warnings.append(
                f"{dataset}:{item.metric} excluded from aggregation: {item.reason}"
            )

    if not valid_items:
        return DatasetScore(
            dataset=dataset,
            metric_agreement_score=None,
            metric_items=items,
            aggregation_method="no_evaluable_metrics",
            warnings=warnings or [f"{dataset}: no evaluable metrics"],
            status="not_evaluable",
        )

    normalized_weights = _normalize_metric_weights(metric_weights)
    if normalized_weights:
        raw_weights = [normalized_weights.get(item.normalized_metric, 0.0) for item in valid_items]
        if sum(raw_weights) <= 0:
            warnings.append(f"{dataset}: metric_weights matched no evaluable metrics; using equal weights")
            raw_weights = [1.0 for _ in valid_items]
            method = "equal_metric_average_fallback"
        else:
            method = "weighted_metric_average"
    else:
        raw_weights = [1.0 for _ in valid_items]
        method = "equal_metric_average"

    total_weight = sum(raw_weights)
    score = 0.0
    for item, raw_weight in zip(valid_items, raw_weights):
        item.weight = raw_weight / total_weight
        score += float(item.match_score) * item.weight

    return DatasetScore(
        dataset=dataset,
        metric_agreement_score=score,
        metric_items=items,
        aggregation_method=method,
        warnings=warnings,
        status="evaluated",
    )


def aggregate_dataset_scores(
    dataset_scores: list[DatasetScore],
    dataset_weights: dict[str, float] | None = None,
) -> tuple[float | None, list[str]]:
    warnings: list[str] = []
    valid = [
        score
        for score in dataset_scores
        if score.status == "evaluated" and score.metric_agreement_score is not None
    ]
    for score in dataset_scores:
        if score.status != "evaluated":
            warnings.append(f"{score.dataset} excluded from global aggregation: no evaluable metrics")
    if not valid:
        return None, warnings or ["No dataset has evaluable metrics"]

    normalized_weights = _normalize_dataset_weights(dataset_weights)
    if normalized_weights:
        raw_weights = [normalized_weights.get(_normalize_dataset_key(score.dataset), 0.0) for score in valid]
        if sum(raw_weights) <= 0:
            warnings.append("dataset_weights matched no evaluable datasets; using equal weights")
            raw_weights = [1.0 for _ in valid]
    else:
        raw_weights = [1.0 for _ in valid]

    total_weight = sum(raw_weights)
    aggregate = 0.0
    for score, raw_weight in zip(valid, raw_weights):
        score.weight = raw_weight / total_weight
        aggregate += float(score.metric_agreement_score) * score.weight
    return aggregate, warnings


def _walk_json(obj: Any, path: str = "") -> list[tuple[str, Any, str]]:
    rows: list[tuple[str, Any, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child_path = f"{path}.{key}" if path else str(key)
            rows.append((str(key), value, child_path))
            rows.extend(_walk_json(value, child_path))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            rows.extend(_walk_json(value, f"{path}[{index}]"))
    return rows


def _walk_objects(obj: Any, path: str = "") -> list[tuple[Any, str]]:
    rows: list[tuple[Any, str]] = [(obj, path)]
    if isinstance(obj, dict):
        for key, value in obj.items():
            child_path = f"{path}.{key}" if path else str(key)
            rows.extend(_walk_objects(value, child_path))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            rows.extend(_walk_objects(value, f"{path}[{index}]"))
    return rows


def _readiness_status(value: Any) -> str:
    if isinstance(value, bool):
        return "pass" if value else "fail"
    if value is None:
        return "unknown"
    if isinstance(value, str):
        normalized = value.strip().casefold()
        compact = normalize_metric_name(normalized)
        normalized_pass = {normalize_metric_name(item) for item in PASS_VALUES}
        normalized_fail = {normalize_metric_name(item) for item in FAIL_VALUES}
        normalized_unknown = {normalize_metric_name(item) for item in UNKNOWN_VALUES}
        if normalized in PASS_VALUES or compact in normalized_pass:
            return "pass"
        if normalized in FAIL_VALUES or compact in normalized_fail:
            return "fail"
        if normalized in UNKNOWN_VALUES or compact in normalized_unknown:
            return "unknown"
        if (
            compact.startswith(("fail", "failed", "error", "blocked"))
            or "failed" in compact
            or "blocked" in compact
            or "unavailable" in compact
            or compact.startswith("notready")
            or compact.endswith("notready")
        ):
            return "fail"
        if compact.startswith(("passed", "success", "successful")) or compact in {"ok", "ready"}:
            return "pass"
    if isinstance(value, dict):
        for key in ("passed", "ok", "success", "ready", "status", "state", "result"):
            if key in value:
                return _readiness_status(value[key])
    return "unknown"


def _readiness_normalized_variants() -> dict[str, set[str]]:
    return {
        canonical: {normalize_metric_name(variant) for variant in variants}
        for canonical, variants in READINESS_KEY_VARIANTS.items()
    }


def _canonical_from_standard_key(key: Any) -> str | None:
    normalized = normalize_metric_name(str(key))
    for canonical, variants in _readiness_normalized_variants().items():
        if normalized in variants:
            return canonical
    return None


def _canonicals_from_readiness_signal(signal: Any) -> list[str]:
    normalized = normalize_metric_name(str(signal or ""))
    if not normalized:
        return []
    matches: set[str] = set()
    for canonical, variants in _readiness_normalized_variants().items():
        for variant in variants:
            if normalized == variant or variant in normalized or normalized in variant:
                matches.add(canonical)
                break

    semantic_terms = {
        "import": ("import",),
        "load": ("load",),
        "infer": ("infer", "inference"),
        "contract": ("contract", "iocontract"),
        "smoke_test": ("smoke",),
    }
    for canonical, terms in semantic_terms.items():
        if any(term in normalized for term in terms):
            matches.add(canonical)

    return [canonical for canonical in READINESS_POINTS if canonical in matches]


def _completed_item_status(text: Any) -> str:
    normalized = normalize_metric_name(str(text or ""))
    if not normalized:
        return "unknown"
    if any(token in normalized for token in ("failed", "blocked", "error", "notready")):
        return "fail"
    non_runtime_completion = (
        "scriptcreated",
        "scriptprepared",
        "sourcecreated",
        "sourcematerialized",
        "checkpointmaterialized",
        "weightsmaterialized",
        "runtimebuilt",
    )
    strong_success = any(
        token in normalized
        for token in ("passed", "succeeded", "success", "validated", "complete", "completed", "ok")
    )
    if any(token in normalized for token in non_runtime_completion) and not strong_success:
        return "unknown"
    return "pass"


def _add_readiness_evidence(
    evidences: list[_ReadinessEvidence],
    *,
    canonical: str,
    status: str,
    path: Path,
    json_path: str,
    value: Any,
    evidence_scope: str,
    reason: str,
) -> None:
    if canonical not in READINESS_POINTS or status == "unknown":
        return
    evidence = _ReadinessEvidence(
        canonical=canonical,
        status=status,
        path=path,
        json_path=json_path,
        value=value,
        evidence_scope=evidence_scope,
        reason=reason,
    )
    key = (
        evidence.canonical,
        evidence.status,
        str(evidence.path),
        evidence.json_path,
        repr(evidence.value),
        evidence.evidence_scope,
        evidence.reason,
    )
    existing = {
        (
            item.canonical,
            item.status,
            str(item.path),
            item.json_path,
            repr(item.value),
            item.evidence_scope,
            item.reason,
        )
        for item in evidences
    }
    if key not in existing:
        evidences.append(evidence)


def _normalize_readiness_doc(
    path: Path,
    doc: Any,
    evidence_scope: str,
) -> list[_ReadinessEvidence]:
    evidences: list[_ReadinessEvidence] = []

    for key, value, json_path in _walk_json(doc):
        canonical = _canonical_from_standard_key(key)
        if canonical:
            _add_readiness_evidence(
                evidences,
                canonical=canonical,
                status=_readiness_status(value),
                path=path,
                json_path=json_path,
                value=value,
                evidence_scope=evidence_scope,
                reason="standard_key",
            )

    for obj, obj_path in _walk_objects(doc):
        if not isinstance(obj, dict):
            continue

        signal = _lookup(obj, ("check", "stage", "name", "step"))
        if signal is not None:
            status = _readiness_status(obj)
            for canonical in _canonicals_from_readiness_signal(signal):
                _add_readiness_evidence(
                    evidences,
                    canonical=canonical,
                    status=status,
                    path=path,
                    json_path=obj_path or "$",
                    value=obj,
                    evidence_scope=evidence_scope,
                    reason="object_check_status",
                )

        for key, value in obj.items():
            normalized_key = normalize_metric_name(key)
            child_path = f"{obj_path}.{key}" if obj_path else str(key)
            if normalized_key == "checks" and isinstance(value, dict):
                for check_name, check_value in value.items():
                    check_path = f"{child_path}.{check_name}"
                    for canonical in _canonicals_from_readiness_signal(check_name):
                        _add_readiness_evidence(
                            evidences,
                            canonical=canonical,
                            status=_readiness_status(check_value),
                            path=path,
                            json_path=check_path,
                            value=check_value,
                            evidence_scope=evidence_scope,
                            reason="checks_map",
                        )
            elif normalized_key == "evidence" and isinstance(value, list):
                for index, item in enumerate(value):
                    item_path = f"{child_path}[{index}]"
                    if isinstance(item, dict):
                        evidence_signal = _lookup(item, ("check", "stage", "name", "step"))
                        status = _readiness_status(item)
                        item_value = item
                    else:
                        evidence_signal = item
                        status = _readiness_status(item)
                        item_value = item
                    for canonical in _canonicals_from_readiness_signal(evidence_signal):
                        _add_readiness_evidence(
                            evidences,
                            canonical=canonical,
                            status=status,
                            path=path,
                            json_path=item_path,
                            value=item_value,
                            evidence_scope=evidence_scope,
                            reason="evidence_array",
                        )
            elif normalized_key in {"completed", "stepscompleted"} and isinstance(value, list):
                for index, item in enumerate(value):
                    item_path = f"{child_path}[{index}]"
                    for canonical in _canonicals_from_readiness_signal(item):
                        _add_readiness_evidence(
                            evidences,
                            canonical=canonical,
                            status=_completed_item_status(item),
                            path=path,
                            json_path=item_path,
                            value=item,
                            evidence_scope=evidence_scope,
                            reason="completed_array_semantic_match",
                        )

    return evidences


def normalize_runtime_readiness_evidence(
    readiness_docs: list[tuple[Path, Any] | tuple[Path, Any, str]],
) -> list[_ReadinessEvidence]:
    evidences: list[_ReadinessEvidence] = []
    for item in readiness_docs:
        if len(item) == 2:
            path, doc = item
            evidence_scope = "run_local"
        else:
            path, doc, evidence_scope = item
        evidences.extend(_normalize_readiness_doc(path, doc, evidence_scope))
    return evidences


def _format_readiness_evidence(evidence: _ReadinessEvidence) -> str:
    return (
        f"[{evidence.evidence_scope}] "
        f"{evidence.path}:{evidence.json_path}={evidence.value!r}"
    )


def _readiness_source_rank(evidence: _ReadinessEvidence) -> tuple[int, int]:
    name = evidence.path.name
    if evidence.evidence_scope == "run_local":
        file_rank = {
            "runtime_readiness.json": 80,
            "tool_readiness_routing.json": 70,
            "model_artifacts_verdict.json": 65,
            "model artifacts verdict.json": 65,
            "verdict.json": 60,
            "smoke_test_report.json": 50,
            "execution_readiness_report.json": 40,
            "assessment_report.json": 30,
        }.get(name, 20)
        return (2, file_rank)
    model_relative = "/".join(evidence.path.parts[-2:])
    file_rank = {
        "artifacts/verdict.json": 80,
        "artifacts/artifact_manifest.json": 60,
        "artifacts/validation.log": 50,
        "config.yaml": 20,
        "model.spec.yaml": 20,
    }.get(model_relative, 10)
    return (1, file_rank)


def _select_readiness_evidence(matches: list[_ReadinessEvidence]) -> _ReadinessEvidence | None:
    if not matches:
        return None
    top_rank = max(_readiness_source_rank(evidence) for evidence in matches)
    top_matches = [evidence for evidence in matches if _readiness_source_rank(evidence) == top_rank]
    fail_match = next((evidence for evidence in top_matches if evidence.status == "fail"), None)
    if fail_match:
        return fail_match
    pass_match = next((evidence for evidence in top_matches if evidence.status == "pass"), None)
    return pass_match or top_matches[0]


def extract_runtime_readiness(
    readiness_docs: list[tuple[Path, Any] | tuple[Path, Any, str]],
) -> tuple[float, dict[str, dict[str, Any]], list[str]]:
    warnings: list[str] = []
    breakdown: dict[str, dict[str, Any]] = {}
    evidences = normalize_runtime_readiness_evidence(readiness_docs)

    if not readiness_docs:
        warnings.append("No readiness files found; RuntimeReadinessScore defaults to 0.")

    total = 0.0
    for canonical, points in READINESS_POINTS.items():
        matches = [evidence for evidence in evidences if evidence.canonical == canonical]
        selected = _select_readiness_evidence(matches)

        if selected and selected.status == "fail":
            passed: bool | None = False
            status = "failed"
            evidence = _format_readiness_evidence(selected)
            evidence_scope: str | None = selected.evidence_scope
            reason = selected.reason
            awarded = 0
        elif selected and selected.status == "pass":
            passed = True
            status = "passed"
            evidence = _format_readiness_evidence(selected)
            evidence_scope = selected.evidence_scope
            reason = selected.reason
            awarded = points
            total += points
        else:
            passed = None
            status = "unknown"
            evidence = "not_found"
            evidence_scope = None
            reason = "no_normalized_readiness_evidence"
            awarded = 0
            warnings.append(f"Readiness item {canonical} is unknown; no points awarded.")

        breakdown[canonical] = {
            "passed": passed,
            "status": status,
            "points": awarded,
            "max_points": points,
            "evidence": evidence,
            "evidence_scope": evidence_scope,
            "reason": reason,
        }

    return total, breakdown, warnings


def _extract_paper_side_score(data: Any, warnings: list[str]) -> float | None:
    if not isinstance(data, dict):
        warnings.append("paper_confidence_report.json is not an object; PaperSideScore is null.")
        return None
    for key in ("overall_percent", "score", "confidence_score", "paper_side_score"):
        value = parse_numeric(data.get(key))
        if value is None:
            continue
        if 0.0 <= value <= 1.0:
            value *= 100.0
        if value < 0.0 or value > 100.0:
            warnings.append(f"PaperSideScore from {key} was clamped to [0, 100].")
            value = _round_score(value) or 0.0
        return value
    warnings.append("PaperSideScore field missing from paper_confidence_report.json.")
    return None


def _extract_values_for_key(data: Any, normalized_key: str) -> list[tuple[Any, str]]:
    values = []
    for key, value, path in _walk_json(data):
        if normalize_metric_name(key) == normalized_key:
            values.append((value, path))
    return values


def _normalize_risk_flag(value: str) -> str | None:
    normalized = normalize_metric_name(value)
    for flag in COMPARABILITY_RISK_FACTORS:
        if normalized == normalize_metric_name(flag):
            return flag
    return None


def _risk_flags_from_value(value: Any) -> list[str]:
    flags: list[str] = []
    if isinstance(value, str):
        flag = _normalize_risk_flag(value)
        if flag:
            flags.append(flag)
    elif isinstance(value, list):
        for item in value:
            flags.extend(_risk_flags_from_value(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            flag = _normalize_risk_flag(str(key))
            if flag and _readiness_status(item) != "fail" and item not in (False, None):
                flags.append(flag)
            flags.extend(_risk_flags_from_value(item))
    return flags


def _clamp_comparability_factor(value: Any, warnings: list[str], source: str) -> float | None:
    number = parse_numeric(value)
    if number is None:
        warnings.append(f"Invalid comparability_factor from {source}; ignored.")
        return None
    if number > 1.0:
        warnings.append(f"comparability_factor from {source} was > 1 and clamped to 1.0.")
        return 1.0
    if number <= 0.0:
        warnings.append(f"comparability_factor from {source} was <= 0 and clamped to 0.01.")
        return 0.01
    return number


def resolve_comparability_factor(
    docs: list[tuple[str, Any]],
    override: Any = None,
) -> tuple[float, dict[str, Any], list[str]]:
    warnings: list[str] = []
    factor = _clamp_comparability_factor(override, warnings, "cli") if override is not None else None
    source = "cli" if factor is not None else "default"

    if factor is None:
        for name, doc in docs:
            for value, path in _extract_values_for_key(doc, "comparabilityfactor"):
                candidate = _clamp_comparability_factor(value, warnings, f"{name}:{path}")
                if candidate is not None:
                    factor = candidate
                    source = f"{name}:{path}"
                    break
            if factor is not None:
                break
    if factor is None:
        factor = 1.0

    risk_flags: list[dict[str, Any]] = []
    coverage_values: list[float] = []
    for name, doc in docs:
        for key, value, path in _walk_json(doc):
            normalized_key = normalize_metric_name(key)
            if normalized_key in {"riskflags", "comparabilityrisks", "risks", "risk"}:
                for flag in _risk_flags_from_value(value):
                    risk_flags.append({"flag": flag, "evidence": f"{name}:{path}"})
            flag = _normalize_risk_flag(key)
            if flag and value not in (False, None):
                risk_flags.append({"flag": flag, "evidence": f"{name}:{path}"})
            if normalized_key in {"samplecoverage", "coverageratio", "datasetcoverage", "coverage"}:
                coverage = parse_numeric(value)
                if coverage is not None:
                    coverage_values.append(coverage / 100.0 if coverage > 1.0 else coverage)

    applied_factors = [factor]
    seen_flags: set[str] = set()
    deduped_flags: list[dict[str, Any]] = []
    for risk in risk_flags:
        flag = risk["flag"]
        if flag in seen_flags:
            continue
        seen_flags.add(flag)
        risk_factor = COMPARABILITY_RISK_FACTORS[flag]
        if flag == "partial_dataset" and coverage_values:
            risk_factor = max(0.50, min(0.80, max(coverage_values)))
        risk["factor"] = risk_factor
        deduped_flags.append(risk)
        applied_factors.append(risk_factor)

    final_factor = min(applied_factors)
    notes = {
        "factor": final_factor,
        "source": source,
        "risk_flags": deduped_flags,
        "evidence": [risk["evidence"] for risk in deduped_flags],
    }
    return final_factor, notes, warnings


def _group_entries_by_dataset(entries: list[_MetricEntry]) -> dict[str, list[_MetricEntry]]:
    grouped: dict[str, list[_MetricEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.dataset or "default", []).append(entry)
    return grouped


def compute_reproduction_score(
    run_root: str | Path,
    *,
    paper_weight: float = 0.30,
    local_weight: float = 0.70,
    runtime_weight: float = 0.20,
    metric_weight: float = 0.80,
    comparability_factor: float | None = None,
    metric_weights: dict[str, float] | None = None,
    dataset_weights: dict[str, float] | None = None,
    excluded_metrics: list[str] | set[str] | tuple[str, ...] | None = None,
    strict: bool = False,
) -> ReproductionScoreReport:
    root = Path(run_root)
    warnings: list[str] = []
    evidence_files: list[str] = []

    confidence_path = _find_first_file(root, "paper_confidence_report.json")
    comparison_path = _find_first_file(root, "paper_value_comparison.json")

    if strict and confidence_path is None:
        raise FileNotFoundError(f"Missing paper_confidence_report.json under {root}")
    if strict and comparison_path is None:
        raise FileNotFoundError(f"Missing paper_value_comparison.json under {root}")

    paper_side_score: float | None = None
    confidence_doc: Any = None
    if confidence_path is None:
        warnings.append("paper_confidence_report.json not found; PaperSideScore is null.")
    else:
        confidence_doc = _read_json(confidence_path)
        evidence_files.append(str(confidence_path))
        paper_side_score = _extract_paper_side_score(confidence_doc, warnings)

    entries: list[_MetricEntry] = []
    comparison_doc: Any = None
    if comparison_path is None:
        warnings.append("paper_value_comparison.json not found; MetricAgreementScore is null.")
    else:
        comparison_doc = _read_json(comparison_path)
        evidence_files.append(str(comparison_path))
        entries = extract_metric_entries(comparison_doc, str(comparison_path))
        if not entries:
            warnings.append("paper_value_comparison.json contains no metric entries.")

    readiness_paths = _find_files(root, READINESS_FILE_NAMES)
    readiness_docs: list[tuple[Path, Any, str]] = []
    for path in readiness_paths:
        try:
            readiness_docs.append((path, _read_structured_readiness_file(path), "run_local"))
            evidence_files.append(str(path))
        except (json.JSONDecodeError, ValueError) as exc:
            warnings.append(f"Could not parse readiness file {path}: {exc}")

    model_dir = _infer_model_dir(root, comparison_doc, readiness_docs)
    model_local_readiness_docs = _find_model_local_readiness_docs(model_dir, warnings)
    for path, _doc, _scope in model_local_readiness_docs:
        evidence_files.append(str(path))
    readiness_docs.extend(model_local_readiness_docs)

    runtime_score, readiness_breakdown, readiness_warnings = extract_runtime_readiness(readiness_docs)
    warnings.extend(readiness_warnings)

    comparability_docs: list[tuple[str, Any]] = []
    if comparison_doc is not None:
        comparability_docs.append((str(comparison_path), comparison_doc))
    if confidence_doc is not None:
        comparability_docs.append((str(confidence_path), confidence_doc))
    for path, doc, _scope in readiness_docs:
        comparability_docs.append((str(path), doc))

    resolved_comparability, comparability_notes, comparability_warnings = resolve_comparability_factor(
        comparability_docs,
        override=comparability_factor,
    )
    warnings.extend(comparability_warnings)

    dataset_scores: list[DatasetScore] = []
    for dataset, dataset_entries in _group_entries_by_dataset(entries).items():
        score = score_dataset(
            dataset,
            dataset_entries,
            metric_weights=metric_weights,
            excluded_metrics=excluded_metrics,
        )
        dataset_scores.append(score)
        warnings.extend(score.warnings)

    metric_agreement_score, dataset_warnings = aggregate_dataset_scores(
        dataset_scores,
        dataset_weights=dataset_weights,
    )
    warnings.extend(dataset_warnings)

    local_reproduction_score: float | None = None
    if metric_agreement_score is not None:
        local_reproduction_score = (
            runtime_weight * runtime_score
            + metric_weight * resolved_comparability * metric_agreement_score
        )

    final_score: float | None = None
    if paper_side_score is not None and local_reproduction_score is not None:
        final_score = paper_weight * paper_side_score + local_weight * local_reproduction_score

    if comparison_path is None or not entries:
        status = "blocked"
    elif final_score is None:
        status = "partial"
    else:
        status = "success"

    return ReproductionScoreReport(
        run_root=str(root),
        paper_side_score=paper_side_score,
        runtime_readiness_score=runtime_score,
        comparability_factor=resolved_comparability,
        metric_agreement_score=metric_agreement_score,
        local_reproduction_score=local_reproduction_score,
        final_score=final_score,
        dataset_scores=dataset_scores,
        readiness_breakdown=readiness_breakdown,
        weights={
            "paper_weight": paper_weight,
            "local_weight": local_weight,
            "runtime_weight": runtime_weight,
            "metric_weight": metric_weight,
        },
        status=status,
        warnings=sorted(set(warnings)),
        evidence_files=sorted(set(evidence_files)),
        created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        comparability_notes=comparability_notes,
    )


def build_reproduction_score_markdown(report: ReproductionScoreReport) -> str:
    lines = [
        "# Reproduction Score Summary",
        "",
        f"- Status: `{report.status}`",
        f"- FinalScore: `{report.final_score}`",
        f"- PaperSideScore: `{report.paper_side_score}`",
        f"- LocalReproductionScore: `{report.local_reproduction_score}`",
        f"- RuntimeReadinessScore: `{report.runtime_readiness_score}`",
        f"- ComparabilityFactor: `{report.comparability_factor}`",
        f"- MetricAgreementScore: `{report.metric_agreement_score}`",
        "",
        "## Weights",
        "",
    ]
    for key, value in report.weights.items():
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(["", "## Dataset Scores", ""])
    if not report.dataset_scores:
        lines.append("- No dataset scores were evaluable.")
    for dataset_score in report.dataset_scores:
        lines.append(f"- `{dataset_score.dataset}`: `{dataset_score.metric_agreement_score}`")
        for item in dataset_score.metric_items:
            lines.append(
                "  - `{metric}` paper=`{paper}` local=`{local}` match=`{match}` "
                "direction=`{direction}` status=`{status}` local_better_than_paper=`{better}`".format(
                    metric=item.metric,
                    paper=item.paper_value,
                    local=item.local_value,
                    match=item.match_score,
                    direction=item.direction,
                    status=item.status,
                    better=item.local_better_than_paper,
                )
            )

    lines.extend(["", "## Comparability", ""])
    notes = report.comparability_notes
    lines.append(f"- factor: `{notes.get('factor', report.comparability_factor)}`")
    for risk in notes.get("risk_flags", []):
        lines.append(
            f"- risk: `{risk.get('flag')}` factor=`{risk.get('factor')}` evidence=`{risk.get('evidence')}`"
        )
    if not notes.get("risk_flags"):
        lines.append("- risk_flags: none")

    lines.extend(["", "## Warnings", ""])
    if report.warnings:
        lines.extend([f"- {warning}" for warning in report.warnings])
    else:
        lines.append("- none")

    lines.extend(["", "## Evidence Files", ""])
    if report.evidence_files:
        lines.extend([f"- `{path}`" for path in report.evidence_files])
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def write_reproduction_score_outputs(
    report: ReproductionScoreReport,
    output_json: str | Path,
    output_md: str | Path,
) -> None:
    json_path = Path(output_json)
    md_path = Path(output_md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(build_reproduction_score_markdown(report), encoding="utf-8")
