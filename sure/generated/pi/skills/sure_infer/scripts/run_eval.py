#!/usr/bin/env python3
"""Run /sure_eval: reuse existing predictions and recompute metrics."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
HARNESS_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))
sys.path.insert(0, str(HARNESS_ROOT))

from import_prediction_source import import_predictions
from generate_report_snapshot import build_snapshot
from resolve_evaluation_engine import resolve_engine_root
from resolve_prediction_source import (
    APPROVED_MODELS_ROOT,
    APPROVED_RESULTS_ROOT,
    build_payload as resolve_prediction_source,
)
from sure.site.loader import load_site_policy


LOCAL_RESULTS_ROOT = HARNESS_ROOT / "sure" / "results"
EVALUATION_ENGINE_ROOT = HARNESS_ROOT / "sure" / "external" / "sure-evaluation"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in str(value)).strip("_") or "unknown"


def _split_values(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        for item in str(value).replace(",", " ").split():
            item = item.strip()
            if item and item not in out:
                out.append(item)
    return out


def _user_path(value: str | None, *, base: Path = HARNESS_ROOT) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_nonempty_lines(path: Path) -> int:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


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


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _staging_result_dir(local_results_root: Path, source_result_relative_path: str) -> Path:
    root = local_results_root.expanduser().resolve()
    relative = Path(source_result_relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"invalid approved result relative path: {source_result_relative_path!r}")
    candidate = (root / relative).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"local staging result escapes {root}: {candidate}") from exc
    return candidate


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _copy_tree(source: Path, destination: Path, *, path_replacements: tuple[str, str] | None = None) -> None:
    source = source.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    for current, directory_names, file_names in os.walk(source, followlinks=False):
        current_path = Path(current)
        relative = current_path.relative_to(source)
        target_dir = destination / relative
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in [*directory_names, *file_names]:
            if (current_path / name).is_symlink():
                raise ValueError(f"artifact trees must not contain symlinks: {current_path / name}")
        for name in file_names:
            source_file = current_path / name
            target_file = target_dir / name
            if path_replacements and source_file.suffix.lower() in {".json", ".jsonl", ".yaml", ".yml", ".md"}:
                raw = source_file.read_bytes()
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    shutil.copy2(source_file, target_file)
                else:
                    target_file.write_text(text.replace(*path_replacements), encoding="utf-8")
                    shutil.copystat(source_file, target_file)
            else:
                shutil.copy2(source_file, target_file)


def _fsync_tree(root: Path) -> None:
    directories = [root]
    for path in root.rglob("*"):
        if path.is_file():
            file_fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(file_fd)
            finally:
                os.close(file_fd)
        elif path.is_dir():
            directories.append(path)
    for directory in reversed(directories):
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _verify_approved_base(source_result_dir: Path, staging_result_dir: Path) -> None:
    source_report = source_result_dir / "report.jsonl"
    staging_report = staging_result_dir / "report.jsonl"
    if not staging_report.is_file() or not staging_report.read_bytes().startswith(source_report.read_bytes()):
        raise ValueError(
            f"local staging report is not based on the current approved NFS report: {staging_report}"
        )
    for source_file in source_result_dir.rglob("*"):
        if source_file.is_symlink():
            raise ValueError(f"approved result must not contain symlinks: {source_file}")
        if not source_file.is_file():
            continue
        relative = source_file.relative_to(source_result_dir)
        if relative in {Path("report.jsonl"), Path("report_snapshot.md")}:
            continue
        staging_file = staging_result_dir / relative
        if not staging_file.is_file() or _sha256(staging_file) != _sha256(source_file):
            raise ValueError(f"local staging result differs from approved NFS artifact: {relative}")


def _batch_id(rows: list[dict[str, Any]]) -> str:
    identity = {"record_ids": sorted(str(row["reval"]["record_id"]) for row in rows)}
    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
    return f"sure_eval_{digest[:24]}"


def _localize_batch_paths(value: Any, *, scratch_root: Path, batch_relative: Path) -> Any:
    if isinstance(value, dict):
        return {
            key: _localize_batch_paths(item, scratch_root=scratch_root, batch_relative=batch_relative)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_localize_batch_paths(item, scratch_root=scratch_root, batch_relative=batch_relative) for item in value]
    if isinstance(value, str):
        scratch_text = str(scratch_root)
        if value == scratch_text:
            return batch_relative.as_posix()
        prefix = scratch_text + os.sep
        if value.startswith(prefix):
            relative = Path(value[len(prefix) :])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"scratch artifact reference escapes its run: {value}")
            return (batch_relative / relative).as_posix()
    return value


def _artifact_manifest(
    *,
    batch_dir: Path,
    batch_id: str,
    source_report_sha256: str,
    record_ids: list[str],
    artifact_paths: dict[str, str],
) -> dict[str, Any]:
    files = []
    for path in sorted(batch_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"persisted artifact bundle must not contain symlinks: {path}")
        if path.is_file() and path != batch_dir / "artifact_manifest.json":
            files.append(
                {
                    "path": path.relative_to(batch_dir).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    return {
        "schema": "sure.reval.artifact_bundle.v1",
        "batch_id": batch_id,
        "source_report_sha256": source_report_sha256,
        "record_ids": sorted(record_ids),
        "artifact_paths": artifact_paths,
        "files": files,
    }


def _validate_artifact_manifest(batch_dir: Path, expected_batch_id: str) -> dict[str, Any]:
    manifest_path = batch_dir / "artifact_manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("schema") != "sure.reval.artifact_bundle.v1":
        raise ValueError(f"invalid persisted artifact manifest schema: {manifest_path}")
    if manifest.get("batch_id") != expected_batch_id:
        raise ValueError(f"persisted artifact manifest batch mismatch: {manifest_path}")
    listed: set[str] = set()
    for item in manifest.get("files") or []:
        if not isinstance(item, dict):
            raise ValueError(f"invalid artifact manifest row: {manifest_path}")
        relative = Path(str(item.get("path") or ""))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError(f"unsafe artifact manifest path: {relative}")
        artifact = batch_dir / relative
        if not artifact.is_file() or artifact.is_symlink():
            raise ValueError(f"persisted artifact is missing: {artifact}")
        if item.get("size") != artifact.stat().st_size or item.get("sha256") != _sha256(artifact):
            raise ValueError(f"persisted artifact hash or size changed: {artifact}")
        relative_text = relative.as_posix()
        if relative_text in listed:
            raise ValueError(f"persisted artifact manifest contains a duplicate path: {relative_text}")
        listed.add(relative_text)
    actual = {
        path.relative_to(batch_dir).as_posix()
        for path in batch_dir.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if listed != actual:
        raise ValueError(f"persisted artifact manifest coverage differs from batch contents: {batch_dir}")
    return manifest


def _reval_report_rows(
    *,
    scratch_report: Path,
    source: dict[str, Any],
    engine: dict[str, Any],
) -> list[dict[str, Any]]:
    source_predictions = {
        str(item["dataset"]): item
        for item in source.get("predictions") or []
        if isinstance(item, dict) and item.get("dataset")
    }
    rows: list[dict[str, Any]] = []
    for raw in scratch_report.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise ValueError("scratch report row must be an object")
        dataset = row.get("dataset") if isinstance(row.get("dataset"), dict) else {}
        dataset_id = str(dataset.get("name") or "")
        if dataset_id not in source_predictions:
            raise ValueError(f"evaluation emitted unexpected dataset {dataset_id!r}")
        reference_path = Path(str(dataset.get("jsonl_path") or "")).expanduser().resolve()
        if not reference_path.is_file():
            raise FileNotFoundError(
                f"evaluation report lacks a readable reference JSONL for {dataset_id}: {reference_path}"
            )
        reference_sha256 = _sha256(reference_path)
        reference_samples = _count_nonempty_lines(reference_path)
        dataset["jsonl_path"] = str(reference_path)
        dataset["reference_sha256"] = reference_sha256
        dataset["reference_samples"] = reference_samples
        row["dataset"] = dataset
        run = row.get("run") if isinstance(row.get("run"), dict) else {}
        run["protocol_id"] = source["protocol_id"]
        run["evaluation_only"] = True
        run["inference_executed"] = False
        row["run"] = run
        model = row.get("model") if isinstance(row.get("model"), dict) else {}
        model["model_name"] = source["model_name"]
        model["model_dir"] = source["model_dir"]
        model["fingerprint"] = source["model_fingerprint"]
        row["model"] = model
        prediction = source_predictions[dataset_id]
        pipeline = row.get("pipeline") if isinstance(row.get("pipeline"), dict) else {}
        identity = {
            "model_fingerprint": source["model_fingerprint"],
            "protocol_id": source["protocol_id"],
            "dataset": dataset_id,
            "prediction_sha256": prediction["txt_sha256"],
            "reference_sha256": reference_sha256,
            "reference_samples": reference_samples,
            "metric": (row.get("metric") or {}).get("name") if isinstance(row.get("metric"), dict) else None,
            "pipeline_id": pipeline.get("pipeline_id"),
            "nodes": pipeline.get("nodes") or [],
            "engine_commit": engine.get("commit"),
            "engine_tree_sha256": engine.get("tree_sha256"),
        }
        record_id = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
        run["run_id"] = f"sure_eval_{record_id[:16]}"
        source_prediction = source_predictions[dataset_id]
        report_prediction = row.get("prediction") if isinstance(row.get("prediction"), dict) else {}
        report_prediction["file"] = source_prediction["txt"]
        validation = (
            report_prediction.get("validation")
            if isinstance(report_prediction.get("validation"), dict)
            else {}
        )
        validation["prediction_jsonl_path"] = source_prediction.get("jsonl")
        validation["source_txt_sha256"] = source_prediction["txt_sha256"]
        validation["source_jsonl_sha256"] = source_prediction.get("jsonl_sha256")
        report_prediction["validation"] = validation
        row["prediction"] = report_prediction
        row["pipeline"] = pipeline
        row["reval"] = {
            "schema": "sure.reval.report_append.v2",
            "record_id": record_id,
            "identity": identity,
            "source_report_sha256": source["source_report_sha256"],
            "source_results_dir": source["source_results_dir"],
            "dataset_set_digest": source["dataset_set_digest"],
            "inference_executed": False,
            "old_evaluation_reused": False,
        }
        rows.append(row)
    if not rows:
        raise ValueError(f"evaluation produced no report rows: {scratch_report}")
    return rows


def append_staging_bundle(
    *,
    source_result_dir: Path,
    staging_result_dir: Path,
    scratch_root: Path,
    scratch_artifacts: dict[str, str],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    source_result_dir = source_result_dir.resolve()
    scratch_root = scratch_root.resolve()
    # A local /sure_infer bundle is scored where it lies: no approved base to
    # mirror or verify, and the batch binds the prediction-derived hash the rows
    # already carry instead of a report.jsonl that does not exist yet.
    in_place = source_result_dir == staging_result_dir.resolve()
    source_report = source_result_dir / "report.jsonl"
    if in_place:
        row_hashes = {str(row["reval"]["source_report_sha256"]) for row in rows}
        if len(row_hashes) != 1:
            raise ValueError(f"in-place append rows disagree on source_report_sha256: {sorted(row_hashes)}")
        source_report_sha256 = row_hashes.pop()
    else:
        source_report_sha256 = hashlib.sha256(source_report.read_bytes()).hexdigest()
    staging_result_dir.parent.mkdir(parents=True, exist_ok=True)
    directory_fd = os.open(staging_result_dir.parent, os.O_RDONLY)
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        base_materialized = False
        if not in_place and not staging_result_dir.exists():
            temporary_base = Path(
                tempfile.mkdtemp(prefix=f".{staging_result_dir.name}.base.", dir=staging_result_dir.parent)
            )
            try:
                temporary_base.rmdir()
                _copy_tree(source_result_dir, temporary_base)
                _fsync_tree(temporary_base)
                os.replace(temporary_base, staging_result_dir)
                base_materialized = True
                os.fsync(directory_fd)
            finally:
                if temporary_base.exists():
                    shutil.rmtree(temporary_base)
        if not staging_result_dir.is_dir():
            raise ValueError(f"local staging result must be a directory: {staging_result_dir}")
        if not in_place:
            _verify_approved_base(source_result_dir, staging_result_dir)

        staging_report = staging_result_dir / "report.jsonl"
        if in_place and not staging_report.exists():
            staging_report.touch()
        current = staging_report.read_bytes()
        requested_record_ids = [str(row["reval"]["record_id"]) for row in rows]
        if len(requested_record_ids) != len(set(requested_record_ids)):
            raise ValueError("reval request produced duplicate deterministic record IDs")
        batch_id = _batch_id(rows)
        batch_relative = Path("evaluation_runs") / batch_id
        batch_dir = staging_result_dir / batch_relative
        localized_rows = [
            _localize_batch_paths(row, scratch_root=scratch_root, batch_relative=batch_relative)
            for row in rows
        ]
        for row in localized_rows:
            row["reval"]["artifact_bundle"] = batch_relative.as_posix()

        persisted_artifacts: dict[str, str] = {}
        for key, raw_path in scratch_artifacts.items():
            artifact = Path(raw_path).resolve()
            if not artifact.exists():
                raise FileNotFoundError(f"required scratch artifact is missing: {key}={artifact}")
            try:
                relative = artifact.relative_to(scratch_root)
            except ValueError as exc:
                raise ValueError(f"scratch artifact escapes the reval run: {key}={artifact}") from exc
            persisted_artifacts[key] = (batch_relative / relative).as_posix()

        batch_materialized = False
        if not batch_dir.exists():
            batch_dir.parent.mkdir(parents=True, exist_ok=True)
            temporary_batch = Path(
                tempfile.mkdtemp(prefix=f".{batch_id}.", dir=batch_dir.parent)
            )
            try:
                temporary_batch.rmdir()
                _copy_tree(
                    scratch_root,
                    temporary_batch,
                    path_replacements=(str(scratch_root), batch_relative.as_posix()),
                )
                manifest = _artifact_manifest(
                    batch_dir=temporary_batch,
                    batch_id=batch_id,
                    source_report_sha256=source_report_sha256,
                    record_ids=requested_record_ids,
                    artifact_paths=persisted_artifacts,
                )
                _write_json(temporary_batch / "artifact_manifest.json", manifest)
                _fsync_tree(temporary_batch)
                os.replace(temporary_batch, batch_dir)
                batch_materialized = True
                batch_parent_fd = os.open(batch_dir.parent, os.O_RDONLY)
                try:
                    os.fsync(batch_parent_fd)
                finally:
                    os.close(batch_parent_fd)
            finally:
                if temporary_batch.exists():
                    shutil.rmtree(temporary_batch)
        manifest = _validate_artifact_manifest(batch_dir, batch_id)
        if batch_materialized and manifest.get("source_report_sha256") != source_report_sha256:
            raise ValueError(f"persisted artifact bundle is based on a different approved report: {batch_dir}")
        if manifest.get("record_ids") != sorted(requested_record_ids):
            raise ValueError(f"persisted artifact bundle record IDs differ from this request: {batch_dir}")
        if manifest.get("artifact_paths") != persisted_artifacts:
            raise ValueError(f"persisted artifact bundle mapping differs from this request: {batch_dir}")

        existing: dict[str, dict[str, Any]] = {}
        for line in current.decode("utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            reval = value.get("reval") if isinstance(value, dict) and isinstance(value.get("reval"), dict) else {}
            record_id = reval.get("record_id")
            if record_id:
                existing[str(record_id)] = value

        appended_rows: list[dict[str, Any]] = []
        for row in localized_rows:
            record_id = str(row["reval"]["record_id"])
            prior = existing.get(record_id)
            if prior is not None:
                prior_reval = prior.get("reval") if isinstance(prior.get("reval"), dict) else {}
                row["reval"]["source_report_sha256"] = prior_reval.get("source_report_sha256")
                if _canonical_json(prior) != _canonical_json(row):
                    raise ValueError(f"reval record identity collision with different content: {record_id}")
                continue
            appended_rows.append(row)
            existing[record_id] = row
        if appended_rows:
            payload = current
            if payload and not payload.endswith(b"\n"):
                payload += b"\n"
            payload += ("\n".join(_canonical_json(row) for row in appended_rows) + "\n").encode("utf-8")

            snapshot_parent = Path(tempfile.mkdtemp(prefix=".snapshot.", dir=staging_result_dir.parent))
            try:
                snapshot_run_dir = snapshot_parent / staging_result_dir.name
                snapshot_run_dir.mkdir()
                (snapshot_run_dir / "report.jsonl").write_bytes(payload)
                shutil.copy2(staging_result_dir / "protocol.yaml", snapshot_run_dir / "protocol.yaml")
                snapshot = build_snapshot(snapshot_run_dir).replace(str(snapshot_run_dir), str(staging_result_dir))
                _atomic_write(staging_result_dir / "report_snapshot.md", snapshot.encode("utf-8"))
                _atomic_write(staging_report, payload)
                result_fd = os.open(staging_result_dir, os.O_RDONLY)
                try:
                    os.fsync(result_fd)
                finally:
                    os.close(result_fd)
            finally:
                shutil.rmtree(snapshot_parent)
        artifact_manifest_path = batch_dir / "artifact_manifest.json"
        appended_record_ids = [str(row["reval"]["record_id"]) for row in appended_rows]
        staging_snapshot = staging_result_dir / "report_snapshot.md"
        return {
            "staging_result_dir": str(staging_result_dir),
            "staging_report": str(staging_report),
            "staging_snapshot": str(staging_snapshot),
            "approved_base_result_dir": str(source_result_dir),
            "approved_base_report": None if in_place else str(source_report),
            "approved_base_sha256": None if in_place else source_report_sha256,
            "batch_id": batch_id,
            "batch_dir": str(batch_dir),
            "artifact_manifest": str(artifact_manifest_path),
            "artifact_manifest_sha256": _sha256(artifact_manifest_path),
            "persisted_artifacts": persisted_artifacts,
            "persisted_artifact_count": len(manifest.get("files") or []),
            "base_materialized": base_materialized,
            "batch_materialized": batch_materialized,
            "appended_record_ids": appended_record_ids,
            "requested_record_ids": requested_record_ids,
            "idempotent": not base_materialized and not batch_materialized and not appended_rows,
            "staging_report_sha256": _sha256(staging_report),
            "staging_snapshot_sha256": _sha256(staging_snapshot),
        }
    finally:
        fcntl.flock(directory_fd, fcntl.LOCK_UN)
        os.close(directory_fd)


def _run(command: list[str], *, cwd: Path = SCRIPT_DIR, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            "command failed "
            f"(exit={completed.returncode}): {' '.join(command)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed


def _approved_reference_datasets_root(
    source: dict[str, Any],
    *,
    approved_models_root: Path = APPROVED_MODELS_ROOT,
    approved_results_root: Path | None = APPROVED_RESULTS_ROOT,
) -> Path:
    model_dir = Path(str(source.get("model_dir") or "")).expanduser().resolve()
    results_dir = Path(str(source.get("source_results_dir") or "")).expanduser().resolve()
    if source.get("source_kind") == "local_infer_run":
        # The bundle under sure/results or a user output_dir is not below the NFS
        # trust roots; its own reference projection is the only allowed root.
        candidate = results_dir / "references"
        if (candidate / "sure_benchmark" / "jsonl").is_dir():
            return candidate.resolve()
        raise FileNotFoundError(
            "INPUT_EVIDENCE_MISSING: local inference bundle does not contain "
            "references/sure_benchmark/jsonl; evaluation cannot read an external dataset root"
        )
    if approved_results_root is None:
        raise ValueError("an approved NFS source requires storage.approved_results_roots in the active site policy")
    approved_roots = (approved_models_root.expanduser().resolve(), approved_results_root.expanduser().resolve())
    for source_dir in (results_dir, model_dir):
        if not any(source_dir.is_relative_to(root) for root in approved_roots):
            raise ValueError(f"approved reval source escapes NFS trust roots: {source_dir}")
        candidate = source_dir / "references"
        if (candidate / "sure_benchmark" / "jsonl").is_dir():
            return candidate.resolve()
    raise FileNotFoundError(
        "INPUT_EVIDENCE_MISSING: approved NFS result/model does not contain "
        "references/sure_benchmark/jsonl; re-evaluation cannot read an external dataset root"
    )


def _harness_config(
    run_dir: Path,
    explicit: str | None,
    *,
    source: dict[str, Any],
    approved_models_root: Path,
    approved_results_root: Path | None,
) -> Path:
    if explicit:
        path = _user_path(explicit)
        assert path is not None
        if not path.exists():
            raise FileNotFoundError(path)
        return path.resolve()
    base_config = HARNESS_ROOT / "sure" / "external" / "sure-evaluation" / "config" / "default.yaml"
    datasets_root = _approved_reference_datasets_root(
        source,
        approved_models_root=approved_models_root,
        approved_results_root=approved_results_root,
    )
    if not base_config.is_file():
        raise FileNotFoundError(f"sure-evaluation default config not found: {base_config}")
    if not (datasets_root / "sure_benchmark" / "jsonl").is_dir():
        raise FileNotFoundError(f"dataset root must contain sure_benchmark/jsonl: {datasets_root}")
    config = yaml.safe_load(base_config.read_text(encoding="utf-8")) or {}
    data = dict(config.get("data") or {})
    data.update(
        {
            "root": str(HARNESS_ROOT / "data"),
            "cache": str(HARNESS_ROOT / "data" / "cache"),
            "models": str(HARNESS_ROOT / "data" / "models"),
            "datasets": str(datasets_root),
            "results": str(run_dir / "_unused_results"),
        }
    )
    config["data"] = data
    output = run_dir / "_harness_config.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return output.resolve()


def _engine_info(engine_root: str | None) -> dict[str, Any]:
    resolved = resolve_engine_root(engine_root)
    if resolved is None:
        raise FileNotFoundError("the harness-pinned sure-evaluation engine is unavailable")
    source, root = resolved
    git_prefix = ["git", "-c", f"safe.directory={root}"]
    completed = subprocess.run(
        [*git_prefix, "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "cannot resolve pinned evaluation engine commit: "
            + completed.stderr.strip()
        )
    status = subprocess.run(
        [
            *git_prefix,
            "status",
            "--short",
            "--",
            "pyproject.toml",
            "src/sure_eval/evaluation",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "source": source,
        "engine_root": str(root),
        "commit": completed.stdout.strip(),
        "dirty": bool(status.stdout.strip()) if status.returncode == 0 else None,
        "tree_sha256": _engine_tree_sha256(root),
    }


def _route_plan_from_payload(payload: dict[str, Any], run_dir: Path, engine: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("results") if isinstance(payload.get("results"), list) else []
    return {
        "schema": "sure.reval.route_plan.v1",
        "generated_at": _utc_now(),
        "engine": engine,
        "can_run_now": True,
        "selected_routes": [
            {
                "dataset": row.get("dataset"),
                "task": row.get("task"),
                "language": row.get("language"),
                "metric": row.get("metric"),
                "pipeline_id": row.get("pipeline_id"),
                "route_id": row.get("route_id"),
                "nodes": row.get("nodes") or (row.get("pipeline") or {}).get("nodes"),
            }
            for row in rows
            if isinstance(row, dict)
        ],
        "plan_path": str(run_dir / "evaluation_route_plan.json"),
    }


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("results") if isinstance(payload.get("results"), list) else []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        score = result.get("score")
        key = (str(row.get("dataset") or ""), str(row.get("metric") or ""))
        grouped.setdefault(key, []).append(
            {
                "pipeline_id": row.get("pipeline_id"),
                "nodes": row.get("nodes") or (row.get("pipeline") or {}).get("nodes"),
                "score": score,
            }
        )
    comparisons = []
    for (dataset, metric), items in grouped.items():
        scores = [item.get("score") for item in items if isinstance(item.get("score"), (int, float))]
        comparison: dict[str, Any] = {"dataset": dataset, "metric": metric, "pipelines": items}
        if scores:
            comparison["min_score"] = min(scores)
            comparison["max_score"] = max(scores)
            comparison["score_spread"] = max(scores) - min(scores)
        comparisons.append(comparison)
    return {"num_results": len(rows), "comparisons": comparisons}


def _pipeline_ids_for_metrics(metrics: list[str], *, engine_root: Path, imported: list[dict[str, Any]]) -> list[str]:
    """Resolve --metric to the exact default pipeline ids evaluate_predictions.py would run for it."""
    from evaluate_predictions import _describe_external_pipeline, _effective_audio_task, _metric_task_hint, _summarize_bridge_error
    from evaluation_runtime import EvaluationRuntimeError

    timeout = int(os.environ.get("SURE_EVALUATION_TIMEOUT", "600"))
    task_hint = _metric_task_hint(list(metrics))
    pipeline_ids: list[str] = []
    for item in imported:
        dataset = str(item.get("dataset") or "")
        task = _effective_audio_task(str(item.get("task") or ""), task_hint)
        language = str(item.get("language") or "auto").lower()
        resolved: list[str] = []
        failures: list[str] = []
        for metric in metrics:
            try:
                pipeline = _describe_external_pipeline(
                    engine_root=engine_root, task=task, language=language, metric=metric, timeout=timeout
                )
            except (OSError, EvaluationRuntimeError):
                raise
            except Exception as exc:  # the engine says this metric has no route here; same rule as evaluate_predictions
                failures.append(f"{metric}: {_summarize_bridge_error(exc)}")
                continue
            pipeline_id = str(pipeline.get("pipeline_id") or "")
            if not pipeline_id:
                raise ValueError(f"metric {metric!r} resolved to a pipeline without pipeline_id for {dataset} ({task}/{language})")
            resolved.append(pipeline_id)
            if pipeline_id not in pipeline_ids:
                pipeline_ids.append(pipeline_id)
        if not resolved:
            raise ValueError(f"no requested metric resolves to a pipeline for {dataset} ({task}/{language}): {failures}")
    return pipeline_ids


def _ensure_ffmpeg(run_dir: Path, env: dict[str, str]) -> None:
    """Expose imageio-ffmpeg's binary as `ffmpeg` on the evaluation PATH when the host has none."""
    if shutil.which("ffmpeg", path=env.get("PATH")):
        return
    try:
        import imageio_ffmpeg

        source = Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        return
    # Beside scratch/, not inside it: scratch is persisted whole into the batch and must not hold symlinks.
    bin_dir = run_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / "ffmpeg"
    if not target.exists():
        try:
            target.symlink_to(source)
        except OSError:
            shutil.copy2(source, target)
            target.chmod(0o755)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"


def run_eval(
    args: argparse.Namespace,
    *,
    approved_models_root: Path | None = APPROVED_MODELS_ROOT,
    approved_results_root: Path | None = APPROVED_RESULTS_ROOT,
    local_results_root: Path = LOCAL_RESULTS_ROOT,
    harness_config: Path | None = None,
    evaluation_engine_root: Path | None = None,
) -> dict[str, Any]:
    pipeline_ids = _split_values(args.pipeline_id)
    metrics = _split_values(getattr(args, "metric", None))
    if bool(pipeline_ids) == bool(metrics):
        raise ValueError("/sure_eval requires exactly one of --pipeline-id or --metric")
    if approved_models_root is None or approved_results_root is None:
        resolved_policy = load_site_policy(required=True)
        storage = resolved_policy["policy"]["storage"]
        approved_models_root = approved_models_root or Path(storage["approved_models_roots"][0])
        # A local-source site may have no approved results root; the resolver
        # raises only when the request actually falls through to the NFS path.
        if approved_results_root is None and storage["approved_results_roots"]:
            approved_results_root = Path(storage["approved_results_roots"][0])
    source_payload = resolve_prediction_source(
        argparse.Namespace(
            model=args.model,
            datasets=args.datasets,
            protocol_id=args.protocol_id,
            source_run=getattr(args, "source_run", None),
            output=None,
        ),
        approved_models_root=approved_models_root,
        approved_results_root=approved_results_root,
    )
    local_source = source_payload.get("source_kind") == "local_infer_run"
    model_name = str(args.model)
    run_id = args.run_id or f"sure_eval_{_safe(model_name)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    invocation_run_dir = Path(args.invocation_run_dir).expanduser().resolve()
    expected_invocation_root = (HARNESS_ROOT / ".sure" / "runs").resolve()
    try:
        invocation_run_dir.relative_to(expected_invocation_root)
    except ValueError as exc:
        raise ValueError(
            f"--invocation-run-dir must stay below {expected_invocation_root}: {invocation_run_dir}"
        ) from exc
    run_dir = invocation_run_dir / "scratch"
    run_dir.mkdir(parents=True, exist_ok=True)
    engine_root = (evaluation_engine_root or EVALUATION_ENGINE_ROOT).expanduser().resolve()

    config_path = _harness_config(
        run_dir,
        str(harness_config) if harness_config else None,
        source=source_payload,
        approved_models_root=approved_models_root,
        approved_results_root=approved_results_root,
    )
    source_path = run_dir / "prediction_source_resolved.json"
    _write_json(source_path, source_payload)

    reuse_manifest = import_predictions(
        argparse.Namespace(
            source_resolved=str(source_path),
            run_dir=str(run_dir),
            copy_mode="copy",
            max_samples=0,
            config=str(config_path),
            output=str(run_dir / "prediction_reuse_manifest.json"),
        )
    )

    datasets = [str(item) for item in source_payload.get("datasets") or []]
    validate_cmd = [
        sys.executable,
        str(SCRIPT_DIR / "validate_prediction_files.py"),
        "--dataset",
        *datasets,
        "--pred-dir",
        str(run_dir / "predictions"),
        "--require-nonempty",
        "--config",
        str(config_path),
        "--output",
        str(run_dir / "validation_payload.json"),
    ]
    _run(validate_cmd)

    if metrics:
        pipeline_ids = _pipeline_ids_for_metrics(
            metrics, engine_root=engine_root, imported=reuse_manifest.get("imported") or []
        )
    eval_cmd = [
        sys.executable,
        str(SCRIPT_DIR / "evaluate_predictions.py"),
        "--dataset",
        *datasets,
        "--pred-dir",
        str(run_dir / "predictions"),
        "--tool-name",
        model_name,
        "--protocol-id",
        str(source_payload["protocol_id"]),
        "--run-dir",
        str(run_dir),
        "--validation-payload",
        str(run_dir / "validation_payload.json"),
        "--config",
        str(config_path),
        "--evaluation-backend",
        "external",
        "--output",
        str(run_dir / "evaluation_payload.json"),
        "--external-runs-dir",
        str(run_dir / "evaluation_runs"),
        "--evaluation-device",
        args.device,
        "--no-copy-source-report",
    ]
    model_dir = source_payload.get("model_dir")
    if model_dir:
        eval_cmd.extend(["--model-dir", str(model_dir)])
    eval_cmd.extend(["--evaluation-engine-root", str(engine_root)])
    for pipeline_id in pipeline_ids:
        eval_cmd.extend(["--pipeline-id", pipeline_id])
    eval_env = os.environ.copy()
    eval_env.update(
        {
            "SURE_EVAL_EXECUTION_PATH": "reused_predictions",
            "SURE_EVAL_EXECUTION_REQUESTED": "local",
            "SURE_EVAL_EXECUTION_SURFACE_TYPE": "sure_eval",
            "SURE_EVAL_EXECUTION_GENERATION_METHOD": "reuse_existing_predictions",
            "SURE_EVAL_PREDICTION_GENERATED_BY": "scripts/import_prediction_source.py",
        }
    )
    _ensure_ffmpeg(invocation_run_dir, eval_env)
    _run(eval_cmd, env=eval_env)

    _run(
        [
            sys.executable,
            str(SCRIPT_DIR / "generate_report_snapshot.py"),
            "--run-dir",
            str(run_dir),
            "--output",
            str(run_dir / "report_snapshot.md"),
        ]
    )

    payload = _read_json(run_dir / "evaluation_payload.json")
    engine = _engine_info(str(engine_root))
    route_plan = _route_plan_from_payload(payload, run_dir, engine)
    _write_json(run_dir / "evaluation_route_plan.json", route_plan)
    summary = _summary(payload)

    manifest = {
        "schema": "sure.reval.model_eval_manifest.v1",
        "run_id": run_id,
        "model_name": model_name,
        "model_dir": str(model_dir or ""),
        "created_at": _utc_now(),
        "status": "success",
        "selected_datasets": datasets,
        "evaluation_only": True,
        "old_evaluation_reused": False,
        "source_prediction": {
            "source": source_payload.get("source_results_dir"),
            "source_kind": source_payload.get("source_kind"),
            "source_results_dir": source_payload.get("source_results_dir"),
            "source_report_sha256": source_payload.get("source_report_sha256"),
        },
        "artifacts": {
            "prediction_source_resolved": str(source_path),
            "prediction_reuse_manifest": str(run_dir / "prediction_reuse_manifest.json"),
            "source_inference_provenance": str(run_dir / "source_inference_provenance.json"),
            "evaluation_route_plan": str(run_dir / "evaluation_route_plan.json"),
            "validation_payload": str(run_dir / "validation_payload.json"),
            "evaluation_payload": str(run_dir / "evaluation_payload.json"),
            "protocol": str(run_dir / "protocol.yaml"),
            "report_jsonl": str(run_dir / "report.jsonl"),
            "report_snapshot": str(run_dir / "report_snapshot.md"),
            "predictions_dir": str(run_dir / "predictions"),
            "metrics_dir": str(run_dir / "metrics"),
            "sample_reports_dir": str(run_dir / "sample_reports"),
        },
        "summary": summary,
        "prediction_reuse": reuse_manifest,
    }
    _write_json(run_dir / "model_eval_manifest.json", manifest)

    dataset_total = sum(int(item.get("dataset_total_samples") or 0) for item in reuse_manifest.get("imported", []))
    evaluated_samples = sum(int(item.get("imported_samples") or 0) for item in reuse_manifest.get("imported", []))
    run_report = {
        "run_id": run_id,
        "timestamp": _utc_now(),
        "task_type": "rejudge_existing_predictions",
        "goal": "Reuse existing predictions and recompute evaluation routes without model inference.",
        "selected_datasets": datasets,
        "executed_steps": [
            "SOURCE_RESOLUTION",
            "PREDICTION_IMPORT",
            "PREDICTION_VALIDATION",
            "ROUTE_BACKED_EVALUATION",
            "REPORT_SNAPSHOT",
        ],
        "status": "success",
        "report_persisted": True,
        "execution_path_actual": "local_bash",
        "execution_path_requested": "local",
        "evaluation_only": True,
        "run_dir": str(run_dir),
        "evaluation_run_dir": str(run_dir),
        "artifact_root": str(run_dir),
        "model_eval_manifest": str(run_dir / "model_eval_manifest.json"),
        "device_request": args.device,
        "device_actual": args.device,
        "max_samples": 0,
        "dataset_total_samples": dataset_total,
        "evaluated_samples": evaluated_samples,
        "artifacts": manifest["artifacts"],
        "next_action": "Review score differences across selected pipelines.",
        "notes": [
            "No model server was started and no inference was run.",
            "Predictions were copied or filtered into this run before validation.",
            "Old evaluation reports and metric artifacts were not reused.",
        ],
    }
    _write_json(run_dir / "main_agent_run_report.json", run_report)
    _run(
        [
            sys.executable,
            str(SCRIPT_DIR / "check_run_report.py"),
            "--run-dir",
            str(run_dir),
            "--produces",
            str(run_dir / "main_agent_run_report.json"),
        ]
    )

    appended_rows = _reval_report_rows(
        scratch_report=run_dir / "report.jsonl",
        source=source_payload,
        engine=engine,
    )
    if local_source:
        staging_result_dir = Path(str(source_payload["source_results_dir"])).resolve()
    else:
        staging_result_dir = _staging_result_dir(
            local_results_root,
            str(source_payload["source_result_relative_path"]),
        )
    scratch_artifacts = manifest["artifacts"] | {
        "model_eval_manifest": str(run_dir / "model_eval_manifest.json"),
        "main_agent_run_report": str(run_dir / "main_agent_run_report.json"),
    }
    append_result = append_staging_bundle(
        source_result_dir=Path(source_payload["source_results_dir"]),
        staging_result_dir=staging_result_dir,
        scratch_root=run_dir,
        scratch_artifacts=scratch_artifacts,
        rows=appended_rows,
    )

    eval_report = {
        "schema": "sure.eval.run_report.v1",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "model_name": model_name,
        "datasets": datasets,
        "metrics": metrics,
        "pipeline_ids": pipeline_ids,
        "evaluation_only": True,
        "old_evaluation_reused": False,
        "artifacts": scratch_artifacts,
        "summary": summary,
        "source_identity": {
            "model_fingerprint": source_payload["model_fingerprint"],
            "protocol_id": source_payload["protocol_id"],
            "dataset_set_digest": source_payload["dataset_set_digest"],
            "source_report_sha256": source_payload["source_report_sha256"],
        },
        "staging_append": append_result,
    }
    _write_json(run_dir / "eval_run_report.json", eval_report)
    _write_json(invocation_run_dir / "artifacts" / "eval_run_report.json", eval_report)
    print(json.dumps(eval_report, indent=2, ensure_ascii=False))
    return eval_report


def _write_incomplete_report(args: argparse.Namespace, error: Exception) -> Path:
    invocation_run_dir = Path(args.invocation_run_dir).expanduser().resolve()
    try:
        source = resolve_prediction_source(
            argparse.Namespace(
                model=args.model,
                datasets=args.datasets,
                protocol_id=args.protocol_id,
                source_run=getattr(args, "source_run", None),
                output=None,
            )
        )
    except (FileNotFoundError, ValueError):
        # The local resolver itself raises INPUT_EVIDENCE_MISSING for a bundle
        # without references; the incomplete report still has to be written.
        source = {}
    source_path = invocation_run_dir / "artifacts" / "prediction_source_resolved.json"
    if source and not source_path.is_file():
        _write_json(source_path, source)
    report = {
        "schema": "sure.eval.run_report.v1",
        "run_id": args.run_id or invocation_run_dir.name,
        "status": "incomplete",
        "error_code": "INPUT_EVIDENCE_MISSING",
        "error": str(error),
        "model_name": str(args.model),
        "datasets": [str(item) for item in source.get("datasets") or _split_values(args.datasets)],
        "pipeline_ids": _split_values(args.pipeline_id),
        "evaluation_only": True,
        "inference_executed": False,
        "old_evaluation_reused": False,
        "append_attempted": False,
        "source_identity": {
            "model_fingerprint": source.get("model_fingerprint"),
            "protocol_id": source.get("protocol_id"),
            "dataset_set_digest": source.get("dataset_set_digest"),
            "source_report_sha256": source.get("source_report_sha256"),
        },
        "artifacts": {"prediction_source_resolved": str(source_path)},
    }
    report_path = invocation_run_dir / "artifacts" / "eval_run_report.json"
    _write_json(report_path, report)
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Reuse existing predictions and rerun SURE evaluation routes")
    parser.add_argument("--model", required=True)
    parser.add_argument("--datasets", nargs="+", required=True)
    parser.add_argument("--pipeline-id", action="append", help="Exact pipeline id to run; repeatable. Exclusive with --metric.")
    parser.add_argument("--metric", action="append", help="Metric resolved to its engine default pipeline; repeatable. Exclusive with --pipeline-id.")
    parser.add_argument(
        "--source-run",
        help="Local inference run: an absolute bundle directory, or a run id below sure/results/<model>/<protocol>/",
    )
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--protocol-id", choices=("standard_system", "strict_core"), default="standard_system")
    parser.add_argument("--invocation-run-dir", required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    try:
        run_eval(args)
    except FileNotFoundError as exc:
        if "INPUT_EVIDENCE_MISSING" not in str(exc):
            raise
        report_path = _write_incomplete_report(args, exc)
        print(f"INPUT_EVIDENCE_MISSING: wrote {report_path}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
