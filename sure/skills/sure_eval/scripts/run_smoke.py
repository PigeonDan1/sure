#!/usr/bin/env python3
"""Gate script for the SMOKE_TEST_UNIT.

Runs the materialized evaluation entrypoint in smoke-only mode, writes
smoke_test_result.json, and validates that at least one bounded prediction row
was produced. Called by the Sure hook with:
    python3 scripts/run_smoke.py --run-dir <runDir> --produces <abs>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from container_execution import build_local_container_command, effective_container_exit_code

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _excerpt(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _write_result(path: Path, *, passed: bool, sample_count: int, exit_code: int, stdout: str, failures: list[str]) -> None:
    payload = {
        "smoke_passed": passed,
        "sample_count": sample_count,
        "exit_code": exit_code,
        "stdout_excerpt": _excerpt(stdout),
        "stderr_excerpt": "",
        "failures": failures,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _count_valid_predictions(pred_path: Path) -> tuple[int, int]:
    if not pred_path.exists():
        return 0, 0
    total = 0
    valid = 0
    for line in pred_path.read_text(encoding="utf-8", errors="replace").splitlines():
        total += 1
        parts = line.split("\t", 1)
        if len(parts) > 1 and parts[1].strip():
            valid += 1
    return total, valid


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _surface_env(surface: dict) -> dict[str, str]:
    env = surface.get("env")
    if not isinstance(env, dict):
        return {}
    values: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not _ENV_NAME_RE.fullmatch(key) or value is None:
            continue
        values[key] = str(value)
    return values


def _device_request(surface: dict[str, Any], eval_input: dict[str, Any]) -> str:
    runtime = surface.get("inference_runtime") if isinstance(surface.get("inference_runtime"), dict) else {}
    for value in (runtime.get("device_request"), runtime.get("device")):
        if isinstance(value, str) and value:
            return value
    resolved_runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
    device = resolved_runtime.get("device") if isinstance(resolved_runtime.get("device"), dict) else {}
    for value in (device.get("request"), device.get("resolved")):
        if isinstance(value, str) and value:
            return value
    resolved_inputs = surface.get("resolved_inputs") if isinstance(surface.get("resolved_inputs"), dict) else {}
    if isinstance(resolved_inputs.get("device"), str) and resolved_inputs["device"]:
        return resolved_inputs["device"]
    return "auto"


def _local_device_env(device_request: str) -> dict[str, str]:
    request = (device_request or "auto").strip()
    lowered = request.lower()
    env: dict[str, str] = {
        "SURE_EVAL_DEVICE_REQUEST": request,
        "DEVICE_RESOLVED": request,
        "SURE_EVAL_DEVICE": request,
    }
    if lowered == "cpu":
        env["DEVICE"] = "cpu"
        env["SURE_EVAL_DEVICE_ACTUAL"] = "cpu"
        env["CUDA_VISIBLE_DEVICES"] = ""
        return env
    match = re.fullmatch(r"cuda:(\d+)", lowered)
    if match:
        env["CUDA_VISIBLE_DEVICES"] = match.group(1)
        env["DEVICE"] = request
        env["SURE_EVAL_DEVICE_ACTUAL"] = "cuda:0"
        return env
    if lowered == "cuda":
        env["DEVICE"] = "cuda"
        env["SURE_EVAL_DEVICE_ACTUAL"] = "cuda"
        return env
    env["DEVICE"] = request
    env["SURE_EVAL_DEVICE_ACTUAL"] = request
    return env


def _user_datasets(eval_input: dict[str, Any]) -> list[str]:
    user_input = eval_input.get("user_input")
    if not isinstance(user_input, dict):
        return []
    raw = user_input.get("datasets")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(entry) for entry in raw if entry]
    return []


def _find_dropped_version(candidates: list[str], user_datasets: list[str]) -> tuple[str, str] | None:
    """Return (surface_value, user_value) when a user-supplied @version suffix was dropped."""
    for user_value in user_datasets:
        if "@" not in user_value:
            continue
        base = user_value.split("@", 1)[0].rstrip("/")
        for candidate in candidates:
            if candidate and "@" not in candidate and candidate.rstrip("/") == base:
                return candidate, user_value
    return None


def _canonical_dataset(eval_input: dict[str, Any], surface_dataset: str) -> str:
    resolved = eval_input.get("datasets")
    rows = [row for row in resolved if isinstance(row, dict)] if isinstance(resolved, list) else []
    if len(rows) == 1 and rows[0].get("name"):
        return str(rows[0]["name"])
    for row in rows:
        candidates = {str(row.get(key) or "") for key in ("name", "source_root", "jsonl_path")}
        if surface_dataset in candidates and row.get("name"):
            return str(row["name"])
    return surface_dataset


def _execution_requested(surface: dict[str, Any]) -> str:
    execution = surface.get("execution") if isinstance(surface.get("execution"), dict) else {}
    requested = execution.get("requested")
    if isinstance(requested, str) and requested:
        return requested
    return "local"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True, help="absolute path to smoke_test_result.json")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    path = Path(args.produces)

    surface_path = run_dir / "artifacts" / "execution_surface.json"
    if not surface_path.exists():
        _write_result(
            path,
            passed=False,
            sample_count=0,
            exit_code=1,
            stdout="",
            failures=[f"execution_surface.json not found at {surface_path}"],
        )
        print(f"execution_surface.json not found at {surface_path}", file=sys.stderr)
        return 1

    try:
        surface = json.loads(surface_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _write_result(path, passed=False, sample_count=0, exit_code=1, stdout="", failures=[str(exc)])
        print(f"execution_surface.json is not valid JSON: {exc}", file=sys.stderr)
        return 1
    eval_input = _read_json(run_dir / "artifacts" / "eval_input_resolved.json")

    entrypoint = surface.get("entrypoint") or surface.get("entrypoint_path") or ""
    if not entrypoint:
        _write_result(path, passed=False, sample_count=0, exit_code=1, stdout="", failures=["execution_surface entrypoint is missing"])
        print("execution_surface entrypoint is missing", file=sys.stderr)
        return 1

    entrypoint_path = Path(entrypoint)
    if not entrypoint_path.is_absolute():
        entrypoint_path = run_dir / entrypoint_path
    if not entrypoint_path.exists():
        _write_result(
            path,
            passed=False,
            sample_count=0,
            exit_code=1,
            stdout="",
            failures=[f"declared entrypoint does not exist: {entrypoint_path}"],
        )
        print(f"declared entrypoint does not exist: {entrypoint_path}", file=sys.stderr)
        return 1

    resolved = surface.get("resolved_inputs") or {}
    datasets = resolved.get("datasets") or []
    if isinstance(datasets, str):
        datasets = [datasets]
    dataset = str(resolved.get("dataset") or (datasets[0] if datasets else ""))
    eval_run_dir = Path(str(resolved.get("run_dir") or ""))
    if not dataset or not eval_run_dir:
        _write_result(path, passed=False, sample_count=0, exit_code=1, stdout="", failures=["resolved dataset/run_dir missing"])
        print("resolved dataset/run_dir missing", file=sys.stderr)
        return 1

    surface_env_values = _surface_env(surface)
    version_candidates = [dataset] + [str(entry) for entry in datasets]
    for env_key in ("DATASET", "DATASETS"):
        env_value = surface_env_values.get(env_key)
        if env_value:
            version_candidates.extend(env_value.split())
    dropped = _find_dropped_version(version_candidates, _user_datasets(eval_input))
    if dropped:
        surface_value, user_value = dropped
        message = (
            f"execution surface dropped the dataset @version suffix: got '{surface_value}' "
            f"but the user input was '{user_value}'; carry the full source path including "
            "'@<version>' into execution_surface resolved_inputs and env DATASET/DATASETS"
        )
        _write_result(path, passed=False, sample_count=0, exit_code=1, stdout="", failures=[message])
        print(message, file=sys.stderr)
        return 1

    max_samples = resolved.get("max_samples")
    smoke_samples = 1
    if isinstance(max_samples, int) and max_samples > 0:
        smoke_samples = min(max_samples, 2)

    logs_dir = run_dir / "local_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "smoke_test.log"
    device_request = _device_request(surface, eval_input)
    command, _ = build_local_container_command(
        surface=surface,
        eval_input=eval_input,
        control_run_dir=run_dir.resolve(),
        entrypoint=entrypoint_path.resolve(),
        repo_root=Path(__file__).resolve().parents[4],
        device_request=device_request,
        extra_env={
            **_local_device_env(device_request),
            "SMOKE_ONLY": "1",
            "SMOKE_TEST_SAMPLES": str(smoke_samples),
            "SURE_EVAL_EXECUTION_PATH": "local_docker_smoke",
            "SURE_EVAL_EXECUTION_REQUESTED": _execution_requested(surface),
        },
    )

    exit_code = 1
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command,
                cwd=str(Path(__file__).resolve().parents[4]),
                env=os.environ.copy(),
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=args.timeout,
            )
        exit_code = int(completed.returncode)
    except subprocess.TimeoutExpired:
        log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        _write_result(
            path,
            passed=False,
            sample_count=0,
            exit_code=124,
            stdout=log_text,
            failures=[f"smoke execution timed out after {args.timeout}s"],
        )
        print(f"smoke execution timed out after {args.timeout}s", file=sys.stderr)
        return 1

    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    exit_code = effective_container_exit_code(exit_code, log_text)
    canonical_dataset = _canonical_dataset(eval_input, dataset)
    pred_path = eval_run_dir / "predictions" / f"{canonical_dataset}.txt"
    total, valid = _count_valid_predictions(pred_path)
    failures: list[str] = []
    if exit_code != 0:
        failures.append(f"entrypoint exited with code {exit_code}")
    if valid < 1:
        failures.append(f"no valid predictions found in {pred_path}")

    passed = exit_code == 0 and valid >= 1
    _write_result(path, passed=passed, sample_count=valid, exit_code=exit_code, stdout=log_text, failures=failures)
    if not passed:
        detail = "\n  - " + "\n  - ".join(failures) if failures else ""
        print(f"smoke_test gate failed.{detail}", file=sys.stderr)
        return 1

    print("smoke_test OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
