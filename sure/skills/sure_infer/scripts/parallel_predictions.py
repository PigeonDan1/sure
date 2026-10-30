#!/usr/bin/env python3
"""One approved model server per GPU; isolated attempts and one publisher."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import generate_predictions_via_server as generation
from finalize_result_bundle import localize_prediction_row
from validate_prediction_files import _load_predictions, _load_structured_predictions, _task_contract_violations


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    generation._write_status_file(path, payload)


def discover_gpus(workers: int, env: dict[str, str]) -> list[str]:
    """Keep the allocation's visibility; prove CUDA availability in Model Python."""
    if type(workers) is not int or not 2 <= workers <= 8:
        raise ValueError("Parallel controller requires num_workers to be an integer between 2 and 8")
    visible = env.get("CUDA_VISIBLE_DEVICES")
    devices = [value.strip() for value in visible.split(",")] if visible is not None else []
    if visible is not None and (not all(devices) or len(set(devices)) != len(devices) or "-1" in devices):
        raise ValueError("CUDA_VISIBLE_DEVICES does not name distinct usable GPUs")
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15, check=False, env=env,
        )
        gpu_ids = dict(line.strip().split(", ", 1) for line in result.stdout.splitlines() if ", " in line)
    except (OSError, subprocess.SubprocessError):
        gpu_ids = {}
    if devices and gpu_ids:
        identities = [gpu_ids.get(device, device) for device in devices]
        if len(set(identities)) != len(identities):
            raise ValueError("CUDA_VISIBLE_DEVICES aliases the same GPU more than once")
    probe = subprocess.run(
        [env["MODEL_PYTHON"], "-c",
         "import json,torch; print(json.dumps({'count':torch.cuda.device_count() if torch.cuda.is_available() else 0}))"],
        capture_output=True, text=True, timeout=60, check=False,
        env=generation.model_child_env(env),
    )
    if probe.returncode:
        raise ValueError(f"Model Python CUDA probe failed: {probe.stderr.strip()}")
    try:
        count = int(json.loads(probe.stdout.strip().splitlines()[-1])["count"])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ValueError("Model Python CUDA probe returned no valid device count") from exc
    if count < workers or (visible is not None and len(devices) < workers):
        raise ValueError(f"Requested {workers} Workers, but only {count} CUDA devices are usable")
    if visible is None:
        # nvidia-smi indices can remain host indices inside a restricted
        # container, while CUDA ordinals start at zero. UUIDs are unambiguous.
        devices = list(gpu_ids.values()) if len(gpu_ids) == count else [str(index) for index in range(count)]
    if len(devices) < workers:
        raise ValueError("GPU discovery could not prove enough allocated devices")
    return devices[:workers]


def split_keys(keys: list[str], workers: int) -> list[list[str]]:
    if not keys:
        return []
    size, remainder = divmod(len(keys), min(workers, len(keys)))
    offset = 0
    shards: list[list[str]] = []
    for rank in range(min(workers, len(keys))):
        end = offset + size + (rank < remainder)
        shards.append(keys[offset:end])
        offset = end
    return shards


def _valid_records(
    rows: list[dict[str, Any]], samples: list[dict[str, Any]], dataset: str, base: Path,
) -> dict[str, dict[str, Any]]:
    expected = {str(sample["key"]): sample for sample in samples}
    valid: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for row in rows:
        key = str(row.get("key") or "")
        if key not in expected or key in seen or row.get("dataset") != dataset:
            raise ValueError(f"Unknown, duplicate or wrong-dataset prediction key: {key!r}")
        seen.add(key)
        normalized = row.get("normalized_prediction")
        if not isinstance(normalized, str):
            continue
        if not normalized.strip() and not generation._empty_prediction_allowed(str(expected[key].get("task") or "")):
            continue
        if generation._normalize_task(row.get("task")) != generation._normalize_task(expected[key].get("task", "ASR")):
            raise ValueError(f"Prediction task disagrees with the dataset: {key}")
        for field in ("audio_path", "annotation_path"):
            raw = (row.get("prediction") or {}).get(field)
            if isinstance(raw, str) and raw and not Path(raw).is_absolute() and (base / raw).is_file():
                row = localize_prediction_row(row, raw, str((base / raw).resolve()))
        if _task_contract_violations([expected[key]], {key: row}, base_dir=base):
            continue
        valid[key] = row
    return valid


def _journal_records(path: Path) -> list[dict[str, Any]]:
    """A killed process may leave its last append incomplete; earlier rows survive."""
    if not path.exists():
        return []
    lines = path.read_bytes().splitlines(keepends=True)
    rows = []
    for index, line in enumerate(lines):
        if not line.endswith(b"\n") and index == len(lines) - 1:
            break
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"Invalid Worker journal row: {path}")
        rows.append(row)
    return rows


def _identity(ctx: Any, dataset: str, jsonl_path: Path) -> dict[str, Any]:
    protocol = generation._resolve_protocol_parameters(ctx.protocol_id, ctx.model_dir, dict(ctx.child_env))
    return {
        "dataset": dataset,
        "dataset_sha256": generation._sha256(jsonl_path),
        "model_config_sha256": generation._sha256(ctx.model_dir / "config.yaml"),
        "runtime_inventory_sha256": generation._sha256(ctx.model_dir / "artifacts" / "runtime_inventory.json"),
        "approval_sha256": generation._sha256(ctx.model_dir / "approval_manifest.json")
        if (ctx.model_dir / "approval_manifest.json").is_file() else None,
        "protocol_id": ctx.protocol_id,
        "protocol_definition_sha256": generation._sha256(generation.ProtocolResolver().protocols_path),
        "standard_params": protocol["standard_params"],
        "model_params": protocol["model_params"],
        "tool_name": ctx.tool_name,
        "language": ctx.languages.get(dataset, ""),
        "metrics": ctx.metrics,
        "max_samples": ctx.max_samples,
    }


def _default_status(ctx: Any) -> dict[str, Any]:
    inventory = generation._load_runtime_inventory(ctx.model_dir)
    model_config = generation._load_yaml(ctx.model_dir / "config.yaml")
    return {
        "schema": "sure.eval.prediction_generation_status.v2",
        "generated_at": generation._utc_now(),
        "updated_at": generation._utc_now(),
        "run_id": ctx.run_id,
        "run_dir": str(ctx.run_dir),
        "model_name": generation.canonical_model_name(ctx.model_dir, model_config),
        "model_dir": str(ctx.model_dir),
        "tool_name": ctx.tool_name,
        "protocol_id": ctx.protocol_id,
        "inference_call_mode": "direct_server_use",
        "server_protocol": generation.resolve_server_protocol(model_config.get("server", {})),
        "execution_path": ctx.child_env.get("SURE_EVAL_EXECUTION_PATH", "unknown"),
        "execution_requested": ctx.child_env.get("SURE_EVAL_EXECUTION_REQUESTED", ""),
        "execution_job_id": ctx.child_env.get("SURE_EVAL_EXECUTION_JOB_ID", ""),
        "device_request": ctx.child_env.get("SURE_EVAL_DEVICE_REQUEST", ctx.device),
        "device_actual": "cuda:0_per_worker",
        "cuda_visible_devices": ",".join(ctx.gpu_devices),
        "runtime": {
            "server_command": generation._resolve_server_command(ctx.model_dir, inventory),
            "server_working_dir": str(generation._resolve_working_dir(ctx.model_dir, inventory)),
            "model_python": ctx.child_env["MODEL_PYTHON"],
            "harness_python": sys.executable,
            "harness_runtime": generation._harness_runtime_summary(ctx.child_env),
            "runtime_inventory": generation._runtime_inventory_summary(ctx.model_dir),
        },
        "environment": generation._safe_env_snapshot(ctx.child_env),
        "generation": {
            "protocol_id": ctx.protocol_id,
            "protocol_resolution": generation._resolve_protocol_parameters(ctx.protocol_id, ctx.model_dir, dict(ctx.child_env)),
            "tool_name": ctx.tool_name,
            "tool_args": {},
        },
        "datasets": [],
    }


def _stop_group(process: subprocess.Popen[str]) -> None:
    # Each Worker starts a new session; its model server belongs to this group.
    # Windows has no process groups to signal, so the tree is ended by PID.
    if os.name == "nt":
        if process.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True, check=False,
                )
            except OSError:
                pass  # No taskkill on PATH; the kill below is the guarantee.
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is None:
            process.kill()
            process.wait()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    # A Worker may have exited before its server; finish that group as well.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _error_kind(text: str, exit_code: int) -> str:
    lowered = text.lower()
    if "cuda out of memory" in lowered or "cuda error: out of memory" in lowered:
        return "gpu_oom"
    if "oomkilled" in lowered:
        return "host_oom"
    if exit_code in {-9, 137}:
        return "killed_unknown"
    if any(marker in lowered for marker in (
        "valueerror:", "filenotfounderror:", "modulenotfounderror:", "permissionerror:",
    )):
        return "input_or_config"
    return "runtime_error" if exit_code else "none"


def run_dataset(ctx: Any, dataset: str) -> dict[str, Any]:
    """Process one dataset to completion (or bounded failure) before the next."""
    if type(ctx.num_workers) is not int or not 1 <= ctx.num_workers <= 8:
        raise ValueError("Dataset controller requires num_workers to be an integer between 1 and 8")
    if len(ctx.gpu_devices) < ctx.num_workers:
        raise ValueError("Dataset Workers exceed the available GPU allocation")
    assert ctx.datasets_root is not None
    jsonl_path = ctx.datasets_root / "sure_benchmark" / "jsonl" / f"{dataset}.jsonl"
    all_samples = generation._load_jsonl(jsonl_path)
    samples = all_samples[:ctx.max_samples] if ctx.max_samples > 0 else all_samples
    audit = {
        "worker_selection": getattr(ctx, "worker_selection", "explicit"),
        "planned_workers": ctx.num_workers,
        "total_samples": len(all_samples), "selected_samples": len(samples),
    }
    planned = getattr(ctx, "worker_plans", {}).get(dataset)
    if planned and planned != audit:
        raise ValueError("Dataset sample scope or Workers changed after planning")
    model_config = generation._load_yaml(ctx.model_dir / "config.yaml")
    samples = [{**sample, "task": generation._effective_generation_task(
        str(sample.get("task", "ASR")), model_config, generation._split_metrics(ctx.metrics),
    )} for sample in samples]
    keys = [str(sample.get("key") or "") for sample in samples]
    if len(set(keys)) != len(keys) or any(not key or any(char in key for char in "\r\n\t") for key in keys):
        raise ValueError("Dataset keys must be unique, nonempty and single-line")
    root = ctx.run_dir / "parallel_inference" / dataset
    root.mkdir(parents=True, exist_ok=True)
    identity = _identity(ctx, dataset, jsonl_path)
    previous = read_json(root / "plan.json")
    if previous and previous.get("identity") != identity:
        raise ValueError("Parallel recovery identity differs; use a new output directory")
    session = uuid.uuid4().hex
    reuse_sessions = [*previous.get("reuse_sessions", []), previous["session"]] if previous and not ctx.no_resume else []
    records: dict[str, dict[str, Any]] = {}
    attempt_statuses: list[dict[str, Any]] = []
    if not ctx.no_resume:
        formal = ctx.run_dir / "predictions" / f"{dataset}.jsonl"
        publication = read_json(root / "merge_report.json")
        published_root = str(ctx.child_env.get("SURE_EVAL_PUBLISHED_RUN_DIR") or ctx.run_dir)
        def local_row(row: dict[str, Any]) -> dict[str, Any]:
            return localize_prediction_row(row, published_root, str(ctx.run_dir))

        if formal.exists() and (not previous or (publication.get("status") == "completed" and publication.get("session") in reuse_sessions)):
            if not previous:
                raise ValueError("Existing predictions have no verified parallel identity; use a new output directory")
            structured, duplicate, invalid = _load_structured_predictions(formal)
            if duplicate or invalid:
                raise ValueError("Existing formal predictions are malformed or duplicated")
            text, duplicate_text = _load_predictions(formal.with_suffix(".txt"))
            if duplicate_text or set(text) - set(keys):
                raise ValueError("Existing text predictions contain duplicate or unknown keys")
            for key, row in _valid_records([local_row(row) for row in structured.values()], samples, dataset, formal.parent).items():
                if text.get(key) != structured[key]["normalized_prediction"]:
                    raise ValueError("Existing text and structured predictions disagree")
                records[key] = row
        for attempt_path in sorted(root.glob("workers/worker_*/attempt_*")):
            attempt = read_json(attempt_path / "attempt.json")
            if not attempt:
                continue
            if attempt.get("session") not in reuse_sessions:
                continue
            if attempt.get("identity") != identity:
                raise ValueError("Worker attempt identity differs from this run")
            keys_path = attempt_path / "keys.txt"
            if generation._sha256(keys_path) != attempt["keys_sha256"]:
                raise ValueError("Worker key file changed after its attempt was recorded")
            subset = set(keys_path.read_text(encoding="utf-8").splitlines())
            if not subset.issubset(keys):
                raise ValueError("Historical Worker contains keys outside the selected scope")
            rows = _valid_records([local_row(row) for row in _journal_records(attempt_path / "completed.jsonl")],
                                  [sample for sample in samples if str(sample["key"]) in subset], dataset, attempt_path)
            for key, row in rows.items():
                if key in records and records[key] != row:
                    raise ValueError(f"Conflicting historical predictions for key {key}")
                records[key] = row
            attempt_statuses.append(read_json(attempt_path / "prediction_generation_status.json"))
    pending = [key for key in keys if key not in records]
    shards = split_keys(pending, ctx.num_workers)
    initial_completed = len(records)
    plan = {
        **audit,
        "schema": "sure.infer.parallel_plan.v1", "session": session,
        "reuse_sessions": reuse_sessions,
        "identity": identity, "requested_workers": ctx.num_workers,
        "effective_workers": len(shards), "gpu_devices": ctx.gpu_devices,
        "expected": len(keys), "reused": initial_completed,
        "shards": [{"rank": rank, "gpu": ctx.gpu_devices[rank], "keys": shard}
                   for rank, shard in enumerate(shards)],
        "execution": {"path": ctx.child_env.get("SURE_EVAL_EXECUTION_PATH", "unknown"),
                      "job_id": ctx.child_env.get("SURE_EVAL_EXECUTION_JOB_ID", ""),
                      "harness_python": sys.executable, "model_python": ctx.child_env["MODEL_PYTHON"]},
        "created_at": generation._utc_now(),
    }
    atomic_json(root / "plan.json", plan)
    atomic_json(root / "sessions" / f"{session}.json", plan)
    default = _default_status(ctx)
    default["worker_selection"] = audit["worker_selection"]
    status_path = ctx.run_dir / "prediction_generation_status.json"
    dataset_status = {
        **audit,
        "dataset": dataset, "status": "running", "num_expected_samples": len(keys),
        "num_generated_samples": len(records), "prediction_file": str(ctx.run_dir / "predictions" / f"{dataset}.txt"),
        "structured_prediction_file": str(ctx.run_dir / "predictions" / f"{dataset}.jsonl"),
        "requested_workers": ctx.num_workers, "effective_workers": len(shards),
        "log_path": str(root), "error": None,
    }
    payload, row_status = generation._upsert_dataset_status(status_path, default, dataset_status)
    payload.update({key: value for key, value in default.items() if key != "datasets"})
    atomic_json(status_path, payload)
    attempts: list[dict[str, Any]] = []
    started = time.monotonic()
    blocked: set[str] = set()
    active: list[tuple[subprocess.Popen[str], Any, Path, dict[str, Any]]] = []
    def interrupted(signum: int, _frame: Any) -> None:
        raise InterruptedError(f"Parallel inference interrupted by signal {signum}")
    handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for round_index in range(3):
            for rank, shard in enumerate(shards):
                pending_shard = [key for key in shard if key not in records and key not in blocked]
                if not pending_shard:
                    continue
                worker_root = root / "workers" / f"worker_{rank}"
                worker_root.mkdir(parents=True, exist_ok=True)
                indices = [int(path.name.split("_")[-1]) for path in worker_root.glob("attempt_*")]
                attempt_dir = worker_root / f"attempt_{max(indices, default=-1) + 1:04d}"
                attempt_dir.mkdir()
                keys_path = attempt_dir / "keys.txt"
                keys_path.write_text("".join(f"{key}\n" for key in pending_shard), encoding="utf-8")
                worker_request = ctx.child_env.get("SURE_EVAL_DEVICE_REQUEST") or ctx.device
                worker_device = generation._worker_model_device(worker_request)
                env = {**ctx.child_env, "CUDA_VISIBLE_DEVICES": ctx.gpu_devices[rank],
                       "DEVICE": worker_device, "SURE_EVAL_DEVICE_ACTUAL": worker_device,
                       "SURE_EVAL_DEVICE_REQUEST": worker_request or worker_device,
                       "SURE_INFER_WORKER_RANK": str(rank)}
                command = [sys.executable, str(ctx.repo_root / "scripts" / "generate_predictions_via_server.py"),
                           "--model-dir", str(ctx.model_dir), "--dataset", dataset, "--run-dir", str(attempt_dir),
                           "--tool-name", ctx.tool_name, "--protocol", ctx.protocol_id,
                           "--device", worker_device, "--keys-file", str(keys_path), "--worker-mode"]
                if ctx.max_samples:
                    command += ["--max-samples", str(ctx.max_samples)]
                if ctx.languages.get(dataset):
                    command += ["--language", ctx.languages[dataset]]
                attempt = {
                    "identity": identity, "session": session, "rank": rank, "round": round_index,
                    "gpu": ctx.gpu_devices[rank], "keys_sha256": generation._sha256(keys_path),
                    "expected": len(pending_shard), "command": command, "status": "running",
                    "started_at": generation._utc_now(), "started_monotonic": time.monotonic(),
                }
                atomic_json(attempt_dir / "attempt.json", attempt)
                log = (attempt_dir / "worker.log").open("w", encoding="utf-8")
                try:
                    process = subprocess.Popen(command, env=env, cwd=str(ctx.harness_root),
                                               stdout=log, stderr=log, text=True, start_new_session=True)
                except BaseException:
                    log.close()
                    raise
                active.append((process, log, attempt_dir, attempt))
            # All attempts have started before any wait. A failed process cannot
            # terminate its peers; cancellation always cleans their sessions.
            for process, log, attempt_dir, attempt in active:
                exit_code = process.wait()
                _stop_group(process)
                log.close()
                worker_status = read_json(attempt_dir / "prediction_generation_status.json")
                attempt_statuses.append(worker_status)
                subset = set((attempt_dir / "keys.txt").read_text(encoding="utf-8").splitlines())
                rows = _valid_records(_journal_records(attempt_dir / "completed.jsonl"),
                                      [sample for sample in samples if str(sample["key"]) in subset], dataset, attempt_dir)
                if set(records) & set(rows):
                    raise ValueError("Worker produced a duplicate completed key")
                records.update(rows)
                text = (attempt_dir / "worker.log").read_text(encoding="utf-8", errors="replace")
                for model_log in (attempt_dir / "predictions" / "logs").glob("*.log"):
                    text += model_log.read_text(encoding="utf-8", errors="replace")
                kind = _error_kind(text, exit_code)
                for dataset_row in worker_status.get("datasets", []):
                    for error in dataset_row.get("sample_errors", []):
                        if error.get("error_type") in {"ValueError", "FileNotFoundError", "PermissionError", "ModuleNotFoundError"}:
                            blocked.add(str(error.get("key")))
                attempt.update({"exit_code": exit_code, "error_kind": kind,
                                "generated": len(rows), "missing_keys": sorted(subset - set(rows)),
                                "status": "completed" if subset == set(rows) else "failed",
                                "ended_at": generation._utc_now(),
                                "duration_seconds": time.monotonic() - attempt.pop("started_monotonic")})
                atomic_json(attempt_dir / "attempt.json", attempt)
                attempts.append({"path": str(attempt_dir), **attempt})
                if kind == "input_or_config":
                    blocked.update(subset - set(rows))
                row_status["num_generated_samples"] = len(records)
                atomic_json(status_path, payload)
            active = []
            if len(records) == len(keys):
                break
    except BaseException as exc:
        row_status.update({"status": "failed", "error": str(exc), "num_generated_samples": len(records)})
        atomic_json(status_path, payload)
        atomic_json(root / "merge_report.json", {
            **audit,
            "status": "failed", "identity": identity, "session": session,
            "expected": len(keys), "generated": len(records), "error": str(exc),
            "missing_keys": [key for key in keys if key not in records], "attempts": attempts,
        })
        raise
    finally:
        for process, log, attempt_path, attempt in active:
            _stop_group(process)
            log.close()
            if attempt.get("status") == "running":
                attempt.update({"status": "failed", "exit_code": process.returncode,
                                "error_kind": "shared_failure", "error": "Controller interrupted before collection",
                                "ended_at": generation._utc_now(),
                                "duration_seconds": time.monotonic() - attempt.pop("started_monotonic")})
                atomic_json(attempt_path / "attempt.json", attempt)
                attempts.append({"path": str(attempt_path), **attempt})
        if active:
            interrupted_report = read_json(root / "merge_report.json")
            if interrupted_report.get("session") == session and interrupted_report.get("status") == "failed":
                interrupted_report["attempts"] = attempts
                atomic_json(root / "merge_report.json", interrupted_report)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    missing = [key for key in keys if key not in records]
    report = {
        **audit,
        "status": "failed" if missing else "completed", "identity": identity, "session": session,
        "requested_workers": ctx.num_workers, "effective_workers": len(shards),
        "expected": len(keys), "generated": len(records), "reused": initial_completed,
        "missing_keys": missing, "attempts": attempts, "duration_seconds": time.monotonic() - started,
    }
    try:
        if not missing:
            _publish(ctx, dataset, samples, records, default, row_status, report)
    except BaseException as exc:
        report.update({"status": "failed", "error": str(exc)})
        row_status.update({"status": "failed", "error": str(exc), "num_generated_samples": len(records)})
        atomic_json(root / "merge_report.json", report)
        atomic_json(status_path, payload)
        raise
    for worker_status in reversed(attempt_statuses):
        if worker_status.get("generation"):
            payload["generation"] = worker_status["generation"]
            payload["server_protocol"] = worker_status.get("server_protocol")
            break
    row_status.update({"status": report["status"], "num_generated_samples": len(records),
                       "error": f"Missing {len(missing)} prediction keys" if missing else None,
                       "parallel_report": str(root / "merge_report.json")})
    payload["updated_at"] = generation._utc_now()
    atomic_json(root / "merge_report.json", report)
    atomic_json(root / "sessions" / f"{session}.result.json", report)
    atomic_json(status_path, payload)
    return report


def _publish(ctx: Any, dataset: str, samples: list[dict[str, Any]], records: dict[str, dict[str, Any]],
             default: dict[str, Any], row_status: dict[str, Any], report: dict[str, Any]) -> None:
    prediction_dir = ctx.run_dir / "predictions"
    prediction_dir.mkdir(exist_ok=True)
    sample_task = str(samples[0].get("task", "ASR")) if samples else "ASR"
    language = str(samples[0].get("language", "")) if samples else ""
    generation._write_prediction_snapshots(
        samples=samples, prediction_path=prediction_dir / f"{dataset}.txt",
        structured_prediction_path=prediction_dir / f"{dataset}.jsonl",
        prediction_map={key: row["normalized_prediction"] for key, row in records.items()},
        structured_map=records, canonical_dataset=dataset, sample_task=sample_task, sample_language=language,
    )
    manifest, conversion = generation._write_prediction_manifests(
        predictions_dir=prediction_dir, run_dir=ctx.run_dir, model_name=default["model_name"],
        tool_name=ctx.tool_name, dataset=dataset, task=sample_task, language=language,
        prediction_path=prediction_dir / f"{dataset}.txt", structured_prediction_path=prediction_dir / f"{dataset}.jsonl",
        protocol_id=ctx.protocol_id, server_protocol=default.get("server_protocol", generation.MCP_SERVER_PROTOCOL),
        source_samples=len(samples), generated_samples=len(records),
    )
    row_status.update({"prediction_manifest": str(manifest), "conversion_manifest": str(conversion)})
    report["txt_sha256"] = generation._sha256(prediction_dir / f"{dataset}.txt")
    report["jsonl_sha256"] = generation._sha256(prediction_dir / f"{dataset}.jsonl")
