#!/usr/bin/env python3
"""Validate the terminal /sure_reval report and its identity/evaluation evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


HARNESS_ROOT = Path(__file__).resolve().parents[4]
for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.site.loader import load_site_policy

_configured_policy = load_site_policy()
APPROVED_MODELS_ROOT = (
    Path(_configured_policy["policy"]["storage"]["approved_models_roots"][0]).resolve()
    if _configured_policy
    else Path("<site-policy-required>")
)
APPROVED_RESULTS_ROOT = (
    Path(_configured_policy["policy"]["storage"]["approved_results_roots"][0]).resolve()
    if _configured_policy
    else Path("<site-policy-required>")
)
LOCAL_RESULTS_ROOT = (HARNESS_ROOT / "sure" / "results").resolve()
EVALUATION_ENGINE_ROOT = (HARNESS_ROOT / "sure" / "external" / "sure-evaluation").resolve()
REQUIRED_ARTIFACTS = [
    "prediction_source_resolved",
    "prediction_reuse_manifest",
    "source_inference_provenance",
    "evaluation_route_plan",
    "validation_payload",
    "evaluation_payload",
    "protocol",
    "report_jsonl",
    "report_snapshot",
    "predictions_dir",
    "metrics_dir",
    "sample_reports_dir",
    "model_eval_manifest",
    "main_agent_run_report",
]


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row must be an object: {path}:{number}")
        rows.append(value)
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_nonempty_lines(path: Path) -> int:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


def _model_fingerprint(model_dir: Path, verdict_path: Path) -> str:
    digest = hashlib.sha256()
    for artifact in (model_dir / "config.yaml", verdict_path):
        digest.update(artifact.name.encode("utf-8"))
        digest.update(_sha256(artifact).encode("ascii"))
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _resolve_result_path(value: Any, result_dir: Path) -> Path:
    relative = Path(str(value or ""))
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"persisted artifact path must be result-relative: {value!r}")
    resolved = (result_dir / relative).resolve()
    if not _inside(resolved, result_dir):
        raise ValueError(f"persisted artifact path escapes result directory: {value!r}")
    return resolved


def _regular_files(root: Path, *, exclude_manifest: bool = False) -> set[str]:
    files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"artifact tree must not contain symlinks: {path}")
        if path.is_file() and not (exclude_manifest and path == root / "artifact_manifest.json"):
            files.add(path.relative_to(root).as_posix())
    return files


def _engine_source_paths(root: Path) -> list[Path]:
    command_prefix = ["git", "-c", f"safe.directory={root}", "ls-files", "-z"]
    pathspec = ["--", "pyproject.toml", "src/sure_eval/evaluation"]
    relative_paths: set[str] = set()
    for options in ([], ["--others", "--exclude-standard"]):
        completed = subprocess.run(
            [*command_prefix, *options, *pathspec],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"cannot enumerate pinned evaluation engine sources: {message}")
        relative_paths.update(
            item.decode("utf-8", errors="surrogateescape")
            for item in completed.stdout.split(b"\0")
            if item
        )
    return [root / relative for relative in sorted(relative_paths)]


def _engine_tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _engine_source_paths(root):
        relative = path.relative_to(root)
        digest.update(str(relative).encode("utf-8"))
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        elif path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b"missing")
        digest.update(b"\0")
    return digest.hexdigest()


def _engine_commit(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "cannot resolve pinned evaluation engine commit: "
            + completed.stderr.strip()
        )
    return completed.stdout.strip()


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        resolved_policy = load_site_policy(required=True)
        approved_models_root = Path(resolved_policy["policy"]["storage"]["approved_models_roots"][0]).resolve()
        approved_results_root = Path(resolved_policy["policy"]["storage"]["approved_results_roots"][0]).resolve()
    except ValueError as error:
        return [str(error)]
    try:
        payload = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]
    if payload.get("schema") != "sure.reval.run_report.v1":
        errors.append("schema must be sure.reval.run_report.v1")
    if payload.get("evaluation_only") is not True:
        errors.append("evaluation_only must be true")
    if payload.get("old_evaluation_reused") is not False:
        errors.append("old_evaluation_reused must be false")

    run_dir = Path(str(payload.get("run_dir") or "")).resolve()
    invocation_root = (HARNESS_ROOT / ".sure" / "runs").resolve()
    if not run_dir.is_dir() or run_dir.name != "scratch" or not _inside(run_dir, invocation_root):
        errors.append(f"run_dir must be an invocation-local .sure/runs/.../scratch directory: {run_dir}")
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
    missing_keys = [key for key in REQUIRED_ARTIFACTS if not artifacts.get(key)]
    if missing_keys:
        errors.append("missing artifact keys: " + ", ".join(missing_keys))
        return errors
    artifact_paths = {key: Path(str(artifacts[key])).resolve() for key in REQUIRED_ARTIFACTS}
    for key, artifact in artifact_paths.items():
        if not artifact.exists():
            errors.append(f"missing artifact path: {key}={artifact}")
        elif not _inside(artifact, run_dir):
            errors.append(f"scratch artifact escapes run_dir: {key}={artifact}")
    if errors:
        return errors

    source = _read_json(artifact_paths["prediction_source_resolved"])
    if source.get("schema") != "sure.reval.approved_prediction_source.v2":
        errors.append("prediction source must use approved_prediction_source.v2")
    if source.get("source_kind") != "approved_nfs_results" or source.get("inference_allowed") is not False:
        errors.append("prediction source must be approved NFS and inference_allowed=false")
    if not _inside(Path(str(source.get("model_dir") or "")), approved_models_root):
        errors.append("source model_dir is outside approved NFS models")
    if not _inside(Path(str(source.get("source_results_dir") or "")), approved_results_root):
        errors.append("source results_dir is outside approved NFS results")
    source_model_dir = Path(str(source.get("model_dir") or "")).resolve()
    source_results_dir = Path(str(source.get("source_results_dir") or "")).resolve()
    source_report = Path(str(source.get("source_report") or "")).resolve()
    source_protocol = Path(str(source.get("source_protocol") or "")).resolve()
    verdict_path = Path(str(source.get("verdict_path") or "")).resolve()
    source_model_name = str(source.get("model_name") or "")
    expected_model_dir = (approved_models_root / source_model_name).resolve()
    if payload.get("model_name") != source_model_name or source_model_dir != expected_model_dir:
        errors.append("terminal model_name and NFS model directory must exactly match the resolved source")
    if source_report != source_results_dir / "report.jsonl" or not source_report.is_file():
        errors.append("source_report must be the approved result report.jsonl")
    elif source.get("source_report_sha256") != _sha256(source_report):
        errors.append("source_report_sha256 differs from the current approved report")
    if source_protocol != source_results_dir / "protocol.yaml" or not source_protocol.is_file():
        errors.append("source_protocol must be the approved result protocol.yaml")
    else:
        source_protocol_payload = yaml.safe_load(source_protocol.read_text(encoding="utf-8")) or {}
        if source_protocol_payload.get("protocol_id") != source.get("protocol_id"):
            errors.append("approved protocol.yaml does not match the resolved protocol identity")
    if verdict_path not in {source_model_dir / "verdict.json", source_model_dir / "artifacts" / "verdict.json"}:
        errors.append("verdict_path must be model-local approved verdict evidence")
    elif not verdict_path.is_file():
        errors.append("approved model verdict is missing")
    elif (source_model_dir / "config.yaml").is_file():
        if source.get("model_fingerprint") != _model_fingerprint(source_model_dir, verdict_path):
            errors.append("model_fingerprint differs from current approved model evidence")
    else:
        errors.append("approved model config.yaml is missing")
    requested_datasets = sorted(str(item) for item in payload.get("datasets") or [])
    if requested_datasets != sorted(str(item) for item in source.get("datasets") or []):
        errors.append("terminal report dataset set differs from approved prediction set")
    expected_dataset_digest = hashlib.sha256("\n".join(requested_datasets).encode("utf-8")).hexdigest()
    if source.get("dataset_set_digest") != expected_dataset_digest:
        errors.append("resolved dataset_set_digest differs from the exact terminal dataset set")
    prediction_by_dataset: dict[str, dict[str, Any]] = {}
    approved_predictions_dir = (source_results_dir / "predictions").resolve()
    for item in source.get("predictions") or []:
        if not isinstance(item, dict):
            errors.append("resolved prediction entries must be objects")
            continue
        dataset = str(item.get("dataset") or "")
        txt = Path(str(item.get("txt") or "")).resolve()
        jsonl = Path(str(item.get("jsonl") or "")).resolve() if item.get("jsonl") else None
        if not dataset or dataset in prediction_by_dataset:
            errors.append(f"resolved prediction dataset is empty or duplicated: {dataset!r}")
            continue
        prediction_by_dataset[dataset] = item
        if not txt.is_file() or not _inside(txt, approved_predictions_dir):
            errors.append(f"approved prediction txt is missing or outside its result: {dataset}")
        else:
            if item.get("txt_sha256") != _sha256(txt):
                errors.append(f"approved prediction txt hash changed: {dataset}")
            if item.get("txt_samples") != _count_nonempty_lines(txt):
                errors.append(f"approved prediction txt sample count changed: {dataset}")
        if jsonl is not None:
            if not jsonl.is_file() or not _inside(jsonl, approved_predictions_dir):
                errors.append(f"approved prediction jsonl is missing or outside its result: {dataset}")
            elif item.get("jsonl_sha256") != _sha256(jsonl):
                errors.append(f"approved prediction jsonl hash changed: {dataset}")
            elif item.get("jsonl_samples") != _count_nonempty_lines(jsonl):
                errors.append(f"approved prediction jsonl sample count changed: {dataset}")
    if sorted(prediction_by_dataset) != requested_datasets:
        errors.append("resolved prediction entries do not exactly cover the terminal dataset set")

    identity = payload.get("source_identity") if isinstance(payload.get("source_identity"), dict) else {}
    for key in ("model_fingerprint", "protocol_id", "dataset_set_digest", "source_report_sha256"):
        if identity.get(key) != source.get(key):
            errors.append(f"source_identity.{key} differs from resolved source")

    validation_payload = _read_json(artifact_paths["validation_payload"])
    if validation_payload.get("is_valid") is not True:
        errors.append("validation_payload.is_valid must be true")
    protocol = yaml.safe_load(artifact_paths["protocol"].read_text(encoding="utf-8")) or {}
    reuse = protocol.get("prediction_reuse") if isinstance(protocol.get("prediction_reuse"), dict) else {}
    if protocol.get("protocol_id") != source.get("protocol_id"):
        errors.append("protocol.yaml protocol_id differs from approved source")
    if reuse.get("enabled") is not True or reuse.get("generation_policy") != "reused_predictions_no_inference":
        errors.append("protocol.yaml must prove reused_predictions_no_inference")
    if reuse.get("old_evaluation_reused") is not False:
        errors.append("protocol.yaml must set old_evaluation_reused=false")

    main_report = _read_json(artifact_paths["main_agent_run_report"])
    if main_report.get("evaluation_only") is not True:
        errors.append("main_agent_run_report.evaluation_only must be true")
    notes = " ".join(str(item) for item in main_report.get("notes") or []).lower()
    if "no inference" not in notes or "no model server" not in notes:
        errors.append("main agent report must state that no inference and no model server ran")

    evaluation = _read_json(artifact_paths["evaluation_payload"])
    evaluation_rows = [row for row in evaluation.get("results") or [] if isinstance(row, dict)]
    requested_pipelines = sorted(str(item) for item in payload.get("pipeline_ids") or [])
    actual_pipelines = sorted({str(row.get("pipeline_id") or "") for row in evaluation_rows})
    if not requested_pipelines or requested_pipelines != actual_pipelines:
        errors.append(
            f"evaluation payload pipeline set must exactly match the request: requested={requested_pipelines}, actual={actual_pipelines}"
        )
    route_plan = _read_json(artifact_paths["evaluation_route_plan"])
    route_engine = route_plan.get("engine") if isinstance(route_plan.get("engine"), dict) else {}
    current_engine_commit = _engine_commit(EVALUATION_ENGINE_ROOT)
    current_engine_tree = _engine_tree_sha256(EVALUATION_ENGINE_ROOT)
    if Path(str(route_engine.get("engine_root") or "")).resolve() != EVALUATION_ENGINE_ROOT:
        errors.append("evaluation route plan did not use the harness-pinned sure-evaluation engine")
    if route_engine.get("tree_sha256") != current_engine_tree:
        errors.append("evaluation engine tree changed or differs from the route plan fingerprint")
    if route_engine.get("commit") != current_engine_commit:
        errors.append("evaluation engine commit differs from the harness-pinned evaluator")

    append = payload.get("staging_append") if isinstance(payload.get("staging_append"), dict) else {}
    staging_result_dir = Path(str(append.get("staging_result_dir") or "")).resolve()
    staging_report = Path(str(append.get("staging_report") or "")).resolve()
    staging_snapshot = Path(str(append.get("staging_snapshot") or "")).resolve()
    approved_base_result_dir = Path(str(append.get("approved_base_result_dir") or "")).resolve()
    approved_base = Path(str(append.get("approved_base_report") or "")).resolve()
    source_relative = str(source.get("source_result_relative_path") or "")
    expected_staging_result_dir = (LOCAL_RESULTS_ROOT / source_relative).resolve()
    expected_staging_report = expected_staging_result_dir / "report.jsonl"
    expected_staging_snapshot = expected_staging_result_dir / "report_snapshot.md"
    if (
        not staging_result_dir.is_dir()
        or staging_result_dir != expected_staging_result_dir
        or not _inside(staging_result_dir, LOCAL_RESULTS_ROOT)
    ):
        errors.append("staging result directory must exactly mirror the approved NFS result below sure/results")
    if not staging_report.is_file() or staging_report != expected_staging_report:
        errors.append("staging report must be the aggregate report.jsonl in the local result mirror")
    if not staging_snapshot.is_file() or staging_snapshot != expected_staging_snapshot:
        errors.append("staging snapshot must be the aggregate report_snapshot.md in the local result mirror")
    if (staging_result_dir / "reval").exists():
        errors.append("staging result must not create a separate top-level reval directory")
    if approved_base_result_dir != source_results_dir:
        errors.append("approved base result directory must equal the resolved NFS source result")
    if not approved_base.is_file() or approved_base != source_report or not _inside(approved_base, approved_results_root):
        errors.append("approved base report must equal the resolved NFS source report")
    elif append.get("approved_base_sha256") != _sha256(approved_base):
        errors.append("approved base report hash changed")
    if staging_report.is_file():
        staging_bytes = staging_report.read_bytes()
        if append.get("staging_report_sha256") != hashlib.sha256(staging_bytes).hexdigest():
            errors.append("staging report hash differs from append receipt")
        if approved_base.is_file() and not staging_bytes.startswith(approved_base.read_bytes()):
            errors.append("staging report does not preserve the approved report as an exact prefix")
    if staging_snapshot.is_file() and append.get("staging_snapshot_sha256") != _sha256(staging_snapshot):
        errors.append("staging snapshot hash differs from append receipt")

    if source_results_dir.is_dir() and staging_result_dir.is_dir():
        for source_artifact in source_results_dir.rglob("*"):
            if source_artifact.is_symlink():
                errors.append(f"approved result contains a forbidden symlink: {source_artifact}")
                continue
            if not source_artifact.is_file():
                continue
            relative = source_artifact.relative_to(source_results_dir)
            if relative in {Path("report.jsonl"), Path("report_snapshot.md")}:
                continue
            staging_artifact = staging_result_dir / relative
            if not staging_artifact.is_file() or _sha256(staging_artifact) != _sha256(source_artifact):
                errors.append(f"local staging baseline differs from approved NFS artifact: {relative}")

    requested_record_ids = {str(item) for item in append.get("requested_record_ids") or []}
    expected_batch_digest = hashlib.sha256(
        _canonical_json({"record_ids": sorted(requested_record_ids)}).encode("utf-8")
    ).hexdigest()
    expected_batch_id = f"sure_reval_{expected_batch_digest[:24]}"
    batch_id = str(append.get("batch_id") or "")
    batch_dir = Path(str(append.get("batch_dir") or "")).resolve()
    expected_batch_dir = staging_result_dir / "evaluation_runs" / expected_batch_id
    artifact_manifest_path = Path(str(append.get("artifact_manifest") or "")).resolve()
    if batch_id != expected_batch_id:
        errors.append("artifact batch ID is not the deterministic source/report identity hash")
    if not batch_dir.is_dir() or batch_dir != expected_batch_dir:
        errors.append("artifact batch must use evaluation_runs/<deterministic-batch-id>")
    if artifact_manifest_path != batch_dir / "artifact_manifest.json" or not artifact_manifest_path.is_file():
        errors.append("artifact manifest must be batch-local artifact_manifest.json")

    bundle_manifest: dict[str, Any] = {}
    if artifact_manifest_path.is_file() and batch_dir.is_dir():
        bundle_manifest = _read_json(artifact_manifest_path)
        if append.get("artifact_manifest_sha256") != _sha256(artifact_manifest_path):
            errors.append("artifact manifest hash differs from append receipt")
        if bundle_manifest.get("schema") != "sure.reval.artifact_bundle.v1":
            errors.append("artifact manifest schema must be sure.reval.artifact_bundle.v1")
        if bundle_manifest.get("batch_id") != batch_id:
            errors.append("artifact manifest batch ID differs from append receipt")
        manifest_source_sha256 = str(bundle_manifest.get("source_report_sha256") or "")
        if len(manifest_source_sha256) != 64:
            errors.append("artifact manifest must bind a 64-character approved base report hash")
        if set(str(item) for item in bundle_manifest.get("record_ids") or []) != requested_record_ids:
            errors.append("artifact manifest record IDs differ from append receipt")
        listed_files: set[str] = set()
        for item in bundle_manifest.get("files") or []:
            if not isinstance(item, dict):
                errors.append("artifact manifest file entries must be objects")
                continue
            try:
                artifact = _resolve_result_path(
                    (Path("evaluation_runs") / batch_id / str(item.get("path") or "")).as_posix(),
                    staging_result_dir,
                )
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if not _inside(artifact, batch_dir) or not artifact.is_file() or artifact.is_symlink():
                errors.append(f"manifested artifact is missing or outside its batch: {artifact}")
                continue
            relative = artifact.relative_to(batch_dir).as_posix()
            if relative in listed_files:
                errors.append(f"artifact manifest contains a duplicate path: {relative}")
            listed_files.add(relative)
            if item.get("size") != artifact.stat().st_size or item.get("sha256") != _sha256(artifact):
                errors.append(f"manifested artifact hash or size changed: {relative}")
        actual_files = _regular_files(batch_dir, exclude_manifest=True)
        if listed_files != actual_files:
            errors.append("artifact manifest does not cover every persisted batch file exactly once")
        if append.get("persisted_artifact_count") != len(actual_files):
            errors.append("persisted artifact count differs from the batch contents")

    persisted_artifacts = (
        append.get("persisted_artifacts") if isinstance(append.get("persisted_artifacts"), dict) else {}
    )
    manifest_artifact_paths = (
        bundle_manifest.get("artifact_paths") if isinstance(bundle_manifest.get("artifact_paths"), dict) else {}
    )
    if persisted_artifacts != manifest_artifact_paths:
        errors.append("persisted artifact mapping differs from the signed artifact manifest")
    for key in REQUIRED_ARTIFACTS:
        value = persisted_artifacts.get(key)
        if not value:
            errors.append(f"persisted artifact mapping is missing: {key}")
            continue
        try:
            persisted = _resolve_result_path(value, staging_result_dir)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not _inside(persisted, batch_dir) or not persisted.exists():
            errors.append(f"persisted artifact is missing or outside its batch: {key}={persisted}")
    if batch_dir.is_dir() and run_dir.is_dir():
        scratch_files = _regular_files(run_dir)
        scratch_files.discard("reval_run_report.json")
        persisted_files = _regular_files(batch_dir, exclude_manifest=True)
        if not scratch_files.issubset(persisted_files):
            missing_persisted = sorted(scratch_files - persisted_files)
            errors.append("persisted batch is missing scratch evaluation files: " + ", ".join(missing_persisted))

    approved_source_by_record_id: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(source_report) if source_report.is_file() else []:
        reval = row.get("reval") if isinstance(row.get("reval"), dict) else {}
        record_id = str(reval.get("record_id") or "")
        if record_id:
            approved_source_by_record_id[record_id] = row
    staged_rows = _read_jsonl(staging_report) if staging_report.is_file() else []
    staged_by_record_id: dict[str, dict[str, Any]] = {}
    for row in staged_rows:
        reval = row.get("reval") if isinstance(row.get("reval"), dict) else {}
        record_id = str(reval.get("record_id") or "")
        if not record_id:
            continue
        if record_id in staged_by_record_id:
            errors.append(f"staging report contains duplicate reval record_id: {record_id}")
        staged_by_record_id[record_id] = row
    staged_record_ids = set(staged_by_record_id)
    if not requested_record_ids or not requested_record_ids.issubset(staged_record_ids):
        errors.append("staging report does not contain every requested reval record_id")
    appended_record_ids = {str(item) for item in append.get("appended_record_ids") or []}
    if not appended_record_ids.issubset(requested_record_ids):
        errors.append("append receipt contains record IDs outside this request")
    materialized = bool(append.get("base_materialized")) or bool(append.get("batch_materialized"))
    if bool(append.get("idempotent")) == bool(materialized or appended_record_ids):
        errors.append("append receipt idempotent flag contradicts materialized artifacts or appended rows")
    requested_source_hashes = {
        str((staged_by_record_id[record_id].get("reval") or {}).get("source_report_sha256") or "")
        for record_id in requested_record_ids & staged_record_ids
    }
    if bundle_manifest and requested_source_hashes != {str(bundle_manifest.get("source_report_sha256") or "")}:
        errors.append("artifact manifest approved base hash differs from its requested report rows")
    for record_id in requested_record_ids & staged_record_ids:
        row = staged_by_record_id[record_id]
        reval = row.get("reval") if isinstance(row.get("reval"), dict) else {}
        row_identity = reval.get("identity") if isinstance(reval.get("identity"), dict) else {}
        dataset = str(row_identity.get("dataset") or "")
        pipeline = row.get("pipeline") if isinstance(row.get("pipeline"), dict) else {}
        run = row.get("run") if isinstance(row.get("run"), dict) else {}
        model = row.get("model") if isinstance(row.get("model"), dict) else {}
        metric = row.get("metric") if isinstance(row.get("metric"), dict) else {}
        row_prediction = row.get("prediction") if isinstance(row.get("prediction"), dict) else {}
        row_dataset = row.get("dataset") if isinstance(row.get("dataset"), dict) else {}
        prediction = prediction_by_dataset.get(dataset) or {}
        expected_record_id = hashlib.sha256(_canonical_json(row_identity).encode("utf-8")).hexdigest()
        if record_id != expected_record_id:
            errors.append(f"reval row record_id is not the deterministic identity hash: {record_id}")
        if row_identity.get("model_fingerprint") != source.get("model_fingerprint"):
            errors.append(f"reval row model fingerprint differs from source: {record_id}")
        if model.get("model_name") != source_model_name or model.get("fingerprint") != source.get("model_fingerprint"):
            errors.append(f"reval row model fields differ from source: {record_id}")
        if row_identity.get("protocol_id") != source.get("protocol_id") or run.get("protocol_id") != source.get("protocol_id"):
            errors.append(f"reval row protocol differs from source: {record_id}")
        if dataset not in requested_datasets:
            errors.append(f"reval row dataset is outside the approved set: {record_id}")
        reference_path = Path(str(row_dataset.get("jsonl_path") or "")).expanduser().resolve()
        if not reference_path.is_file():
            errors.append(f"reval row reference JSONL is missing: {record_id}")
        else:
            reference_sha256 = _sha256(reference_path)
            reference_samples = _count_nonempty_lines(reference_path)
            if row_dataset.get("reference_sha256") != reference_sha256:
                errors.append(f"reval row dataset reference hash changed: {record_id}")
            if row_dataset.get("reference_samples") != reference_samples:
                errors.append(f"reval row dataset reference sample count changed: {record_id}")
            if row_identity.get("reference_sha256") != reference_sha256:
                errors.append(f"reval row identity does not bind the dataset reference: {record_id}")
            if row_identity.get("reference_samples") != reference_samples:
                errors.append(f"reval row identity does not bind the reference sample count: {record_id}")
        if row_identity.get("prediction_sha256") != prediction.get("txt_sha256"):
            errors.append(f"reval row prediction hash differs from source: {record_id}")
        if Path(str(row_prediction.get("file") or "")).resolve() != Path(str(prediction.get("txt") or "")).resolve():
            errors.append(f"reval row prediction path differs from source: {record_id}")
        if row_identity.get("pipeline_id") not in requested_pipelines or pipeline.get("pipeline_id") != row_identity.get("pipeline_id"):
            errors.append(f"reval row pipeline differs from the exact request: {record_id}")
        if row_identity.get("nodes") != (pipeline.get("nodes") or []):
            errors.append(f"reval row pipeline nodes differ from its identity: {record_id}")
        if row_identity.get("metric") != metric.get("name"):
            errors.append(f"reval row metric differs from its identity: {record_id}")
        if row_identity.get("engine_commit") != route_engine.get("commit"):
            errors.append(f"reval row engine commit differs from the route plan: {record_id}")
        if row_identity.get("engine_tree_sha256") != current_engine_tree:
            errors.append(f"reval row engine tree hash differs from the pinned evaluator: {record_id}")
        if reval.get("schema") != "sure.reval.report_append.v2":
            errors.append(f"reval row must use artifact-aware append schema v2: {record_id}")
        expected_bundle_relative = (Path("evaluation_runs") / batch_id).as_posix()
        if reval.get("artifact_bundle") != expected_bundle_relative:
            errors.append(f"reval row artifact bundle differs from the append receipt: {record_id}")
        row_artifacts = row.get("artifacts") if isinstance(row.get("artifacts"), dict) else {}
        artifact_references = {
            "pipeline.report_path": pipeline.get("report_path"),
            "pipeline.description_path": pipeline.get("description_path"),
            "artifacts.metric_artifact_dir": row_artifacts.get("metric_artifact_dir"),
            "artifacts.report": row_artifacts.get("report"),
            "artifacts.pipeline_description": row_artifacts.get("pipeline_description"),
            "artifacts.sample_report": row_artifacts.get("sample_report"),
        }
        for label, value in artifact_references.items():
            if not value:
                errors.append(f"reval row is missing persisted {label}: {record_id}")
                continue
            try:
                persisted = _resolve_result_path(value, staging_result_dir)
            except ValueError as exc:
                errors.append(f"{record_id}: {exc}")
                continue
            if not _inside(persisted, batch_dir) or not persisted.exists():
                errors.append(f"reval row {label} is missing or outside its artifact batch: {record_id}")
        if reval.get("inference_executed") is not False or run.get("inference_executed") is not False:
            errors.append(f"reval row must prove inference_executed=false: {record_id}")
        approved_source_row = approved_source_by_record_id.get(record_id)
        if approved_source_row is not None:
            if _canonical_json(approved_source_row) != _canonical_json(row):
                errors.append(f"reval row differs from its approved NFS counterpart: {record_id}")
        elif reval.get("source_report_sha256") != source.get("source_report_sha256"):
            errors.append(f"new reval row approved base hash differs from the current source: {record_id}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    path = Path(args.report)
    if not path.is_file():
        print(f"reval report not found: {path}", file=sys.stderr)
        return 1
    try:
        errors = validate(path)
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        errors = [str(exc)]
    if errors:
        print("reval report invalid:\n  - " + "\n  - ".join(errors), file=sys.stderr)
        return 1
    print(f"reval_run_report OK: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
