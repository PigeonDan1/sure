#!/usr/bin/env python3
"""Resolve an exact, approved NFS prediction set for /sure_reval."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from resolve_model_dir import APPROVED_MODELS_ROOT, resolve_approved_model_identity

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.site.loader import load_site_policy


_configured_policy = load_site_policy()
APPROVED_RESULTS_ROOT = (
    Path(_configured_policy["policy"]["storage"]["approved_results_roots"][0])
    if _configured_policy
    else None
)
ALLOWED_PROTOCOLS = frozenset({"standard_system", "strict_core"})
DATASET_ID_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)__(?P<version>v[0-9][A-Za-z0-9.-]*)$")
LEGACY_VERSION_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*?)(?:__|_)(?P<version>v[0-9][A-Za-z0-9.-]*)(?:__[A-Za-z0-9_-]+)?$"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_nonempty_lines(path: Path) -> int:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


def _split_values(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        for item in str(value).replace(",", " ").split():
            if item and item not in out:
                out.append(item)
    return out


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="strict").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"report row must be an object at {path}:{line_number}")
        rows.append(value)
    if not rows:
        raise ValueError(f"approved report has no rows: {path}")
    return rows


def _canonical_dataset_id(value: str) -> str:
    value = value.strip()
    match = LEGACY_VERSION_RE.fullmatch(value)
    if match:
        return f"{match.group('name')}__{match.group('version')}"
    raise ValueError(
        f"dataset identity {value!r} has no exact version; expected <dataset_name>__<version_id>"
    )


def _requested_dataset_id(value: str) -> str:
    value = value.strip()
    if DATASET_ID_RE.fullmatch(value):
        return value
    raise ValueError(
        f"requested dataset identity {value!r} is not canonical; expected <dataset_name>__<version_id>"
    )


def _dataset_from_row(row: dict[str, Any]) -> str:
    dataset = row.get("dataset")
    if isinstance(dataset, dict):
        value = dataset.get("id") or dataset.get("name")
    else:
        value = dataset
    if not isinstance(value, str) or not value.strip():
        raise ValueError("approved report row has no dataset identity")
    return _canonical_dataset_id(value)


def _protocol_from_row(row: dict[str, Any]) -> str:
    run = row.get("run") if isinstance(row.get("run"), dict) else {}
    value = run.get("protocol_id") or row.get("protocol_id")
    return str(value or "")


def _model_from_row(row: dict[str, Any]) -> str:
    model = row.get("model") if isinstance(row.get("model"), dict) else {}
    return str(model.get("model_name") or row.get("tool_uid") or "")


def _prediction_stem(row: dict[str, Any]) -> str:
    prediction = row.get("prediction") if isinstance(row.get("prediction"), dict) else {}
    value = prediction.get("file")
    return Path(str(value)).stem if value else ""


def _model_fingerprint(model_dir: Path, verdict_path: str) -> str:
    digest = hashlib.sha256()
    for path in (model_dir / "config.yaml", Path(verdict_path)):
        digest.update(path.name.encode("utf-8"))
        digest.update(_sha256(path).encode("ascii"))
    return digest.hexdigest()


def build_payload(
    args: argparse.Namespace,
    *,
    approved_models_root: Path | None = APPROVED_MODELS_ROOT,
    approved_results_root: Path | None = APPROVED_RESULTS_ROOT,
) -> dict[str, Any]:
    model = str(args.model)
    protocol_id = str(getattr(args, "protocol_id", None) or "standard_system")
    if protocol_id not in ALLOWED_PROTOCOLS:
        raise ValueError(f"unsupported protocol {protocol_id!r}; expected {sorted(ALLOWED_PROTOCOLS)}")
    requested_datasets = _split_values(args.datasets)
    if not requested_datasets:
        raise ValueError("--datasets requires the complete approved dataset__version set")
    requested = sorted(_requested_dataset_id(item) for item in requested_datasets)
    if len(requested) != len(set(requested)):
        raise ValueError("requested datasets contain duplicate canonical identities")

    model_resolution = resolve_approved_model_identity(model, approved_root=approved_models_root)
    if not model_resolution["ok"]:
        detail = model_resolution.get("identity_error") or "approved model identity is incomplete"
        raise ValueError(f"model {model!r} is not approved with a successful verdict in NFS: {detail}")
    model_dir = Path(str(model_resolution["model_dir"]))

    if approved_results_root is None:
        resolved_policy = load_site_policy(required=True)
        approved_results_root = Path(resolved_policy["policy"]["storage"]["approved_results_roots"][0])
    results_root = approved_results_root.expanduser().resolve()
    model_results_dir = (results_root / model).resolve(strict=False)
    if not _is_relative_to(model_results_dir, results_root):
        raise ValueError(f"approved model result path escapes NFS root: {model_results_dir}")
    if not model_results_dir.is_dir():
        raise FileNotFoundError(f"approved model has no result directory: {model_results_dir}")
    candidates: list[tuple[Path, list[dict[str, Any]], dict[str, set[str]]]] = []
    discovered: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    for candidate in sorted(path for path in model_results_dir.iterdir() if path.is_dir()):
        if candidate.is_symlink():
            raise ValueError(f"approved result candidates must be real NFS directories, not symlink aliases: {candidate}")
        resolved_candidate = candidate.resolve()
        if not _is_relative_to(resolved_candidate, results_root):
            raise ValueError(f"approved result candidate escapes NFS root: {candidate} -> {resolved_candidate}")
        candidate_protocol = candidate / "protocol.yaml"
        candidate_report = candidate / "report.jsonl"
        candidate_predictions = candidate / "predictions"
        missing = [
            name
            for name, present in (
                ("protocol.yaml", candidate_protocol.is_file()),
                ("report.jsonl", candidate_report.is_file()),
                ("predictions/", candidate_predictions.is_dir()),
            )
            if not present
        ]
        if missing:
            # Skipping these silently is what made a crashed run unexplainable:
            # [5/5] is what writes protocol.yaml and report.jsonl, so a run that
            # died there leaves predictions and nothing else, and the error read
            # as if the directory had not been looked at.
            incomplete.append({"path": str(candidate), "missing": missing})
            continue
        for label, artifact in (
            ("protocol", candidate_protocol),
            ("report", candidate_report),
            ("predictions", candidate_predictions),
        ):
            if not _is_relative_to(artifact.resolve(), resolved_candidate):
                raise ValueError(f"approved result {label} escapes its result directory: {artifact}")
        candidate_protocol_payload = yaml.safe_load(candidate_protocol.read_text(encoding="utf-8")) or {}
        candidate_protocol_id = candidate_protocol_payload.get("protocol_id") if isinstance(candidate_protocol_payload, dict) else None
        if candidate_protocol_id != protocol_id:
            discovered.append({"path": str(candidate), "protocol_id": candidate_protocol_id})
            continue
        candidate_rows = _read_jsonl(candidate_report)
        row_protocols = {value for value in (_protocol_from_row(row) for row in candidate_rows) if value}
        row_models = {value for value in (_model_from_row(row) for row in candidate_rows) if value}
        if row_protocols != {protocol_id}:
            raise ValueError(
                f"report protocol identities do not exactly match {protocol_id!r} in {candidate}: {sorted(row_protocols)}"
            )
        if row_models != {model}:
            raise ValueError(f"report model identities do not exactly match {model!r} in {candidate}: {sorted(row_models)}")
        candidate_stems: dict[str, set[str]] = {}
        for row in candidate_rows:
            dataset_id = _dataset_from_row(row)
            stem = _prediction_stem(row)
            candidate_stems.setdefault(dataset_id, set())
            if stem:
                candidate_stems[dataset_id].add(stem)
        candidate_datasets = sorted(candidate_stems)
        discovered.append(
            {"path": str(candidate), "protocol_id": candidate_protocol_id, "datasets": candidate_datasets}
        )
        if candidate_datasets == requested:
            candidates.append((resolved_candidate, candidate_rows, candidate_stems))
    if not candidates:
        detail = (
            "no approved NFS result exactly matches model, protocol, and dataset set; "
            f"model={model!r}, protocol={protocol_id!r}, datasets={requested}, discovered={discovered}"
        )
        if incomplete:
            detail += (
                f", incomplete={incomplete}. /sure_reval recomputes pipelines from a complete approved "
                "result; a directory missing protocol.yaml or report.jsonl never finished evaluating and "
                "cannot be revalidated. Re-run the evaluation for it instead: generation resumes from the "
                "predictions already there."
            )
        raise FileNotFoundError(detail)
    if len(candidates) != 1:
        raise ValueError(
            "approved NFS result identity is ambiguous for model, protocol, and dataset set: "
            + ", ".join(str(item[0]) for item in candidates)
        )
    result_dir, rows, stems_by_dataset = candidates[0]
    protocol_path = result_dir / "protocol.yaml"
    report_path = result_dir / "report.jsonl"
    predictions_dir = result_dir / "predictions"
    approved = sorted(stems_by_dataset)

    predictions: list[dict[str, Any]] = []
    for dataset_id in approved:
        stems = stems_by_dataset[dataset_id]
        existing = sorted(stem for stem in stems if (predictions_dir / f"{stem}.txt").is_file())
        if len(existing) != 1:
            raise ValueError(
                f"dataset {dataset_id!r} must resolve to exactly one approved prediction stem; found={existing}"
            )
        stem = existing[0]
        txt = (predictions_dir / f"{stem}.txt").resolve()
        jsonl = (predictions_dir / f"{stem}.jsonl").resolve()
        if not _is_relative_to(txt, predictions_dir.resolve()):
            raise ValueError(f"prediction path escapes approved result: {txt}")
        if jsonl.is_file() and not _is_relative_to(jsonl, predictions_dir.resolve()):
            raise ValueError(f"structured prediction path escapes approved result: {jsonl}")
        predictions.append(
            {
                "dataset": dataset_id,
                "prediction_stem": stem,
                "txt": str(txt),
                "txt_sha256": _sha256(txt),
                "txt_samples": _count_nonempty_lines(txt),
                "jsonl": str(jsonl) if jsonl.is_file() else None,
                "jsonl_sha256": _sha256(jsonl) if jsonl.is_file() else None,
                "jsonl_samples": _count_nonempty_lines(jsonl) if jsonl.is_file() else 0,
            }
        )

    return {
        "schema": "sure.reval.approved_prediction_source.v2",
        "generated_at": _utc_now(),
        "source_kind": "approved_nfs_results",
        "model_name": model,
        "model_dir": str(model_dir),
        "model_fingerprint": _model_fingerprint(model_dir, str(model_resolution["verdict_path"])),
        "verdict_path": model_resolution["verdict_path"],
        "protocol_id": protocol_id,
        "datasets": approved,
        "dataset_set_digest": hashlib.sha256("\n".join(approved).encode("utf-8")).hexdigest(),
        "source_results_dir": str(result_dir),
        "source_result_relative_path": str(result_dir.relative_to(results_root)),
        "source_predictions_dir": str(predictions_dir.resolve()),
        "source_protocol": str(protocol_path.resolve()),
        "source_report": str(report_path.resolve()),
        "source_report_sha256": _sha256(report_path),
        "predictions": predictions,
        "old_evaluation_reused": False,
        "inference_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve an exact approved NFS prediction source")
    parser.add_argument("--model", required=True)
    parser.add_argument("--datasets", nargs="+", required=True)
    parser.add_argument("--protocol-id", choices=sorted(ALLOWED_PROTOCOLS), default="standard_system")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        payload = build_payload(args)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
