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

    max_samples = resolved.get("max_samples")
    smoke_samples = 1
    if isinstance(max_samples, int) and max_samples > 0:
        smoke_samples = min(max_samples, 2)

    logs_dir = run_dir / "local_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "smoke_test.log"
    env = os.environ.copy()
    env.update(_surface_env(surface))
    if env.get("SURE_EVAL_HARNESS_PYTHON_BIN") and not env.get("HARNESS_PYTHON_BIN"):
        env["HARNESS_PYTHON_BIN"] = env["SURE_EVAL_HARNESS_PYTHON_BIN"]
    env.setdefault("REPO_ROOT", str(Path(__file__).resolve().parents[1]))
    env["SMOKE_ONLY"] = "1"
    env["SMOKE_TEST_SAMPLES"] = str(smoke_samples)
    env.setdefault("SURE_EVAL_EXECUTION_PATH", "local_smoke")
    env.setdefault("SURE_EVAL_EXECUTION_REQUESTED", "vc")

    exit_code = 1
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                ["bash", str(entrypoint_path)],
                cwd=str(entrypoint_path.parent),
                env=env,
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
    pred_path = eval_run_dir / "predictions" / f"{dataset}.txt"
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
