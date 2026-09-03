#!/usr/bin/env python3
"""Launch model inference for a /sure_infer run through the approved runtime.

The host side of the inference entrypoint:

1. Read artifacts/eval_input_resolved.json and artifacts/dataset_decision.json
   and write artifacts/execution_surface.json — the surface is authored here,
   never by the agent.
2. Run the compliance checks on that surface (the same functions the
   execution_readiness gate runs) and refuse to launch when they fail.
3. Launch scripts/infer_entrypoint.py with the approved Harness Python inside
   the approved container (local_docker) or on the trusted host (local_python).
4. Write artifacts/execution_result.json describing how the launch ended.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import check_execution_surface_compliance as compliance
from container_execution import build_local_container_command, effective_container_exit_code
from deployment_binding import DEPLOYMENT_BINDING_V1, DEPLOYMENT_BINDING_V2
from python_execution import build_local_python_command, verify_model_integrity

ENTRYPOINT = Path(__file__).resolve().with_name("infer_entrypoint.py")
STAGE_MARKER_RE = re.compile(r"^INFER_STAGE_FAILED (\S+)\s*$", re.MULTILINE)
V1_BINDING_ERROR = "deployment binding schema v1 is no longer supported; re-run /sure_approve"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _input_digest(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes() if path.exists() else b"")
        digest.update(b"\0")
    return digest.hexdigest()


def _approved_binding(eval_input: dict[str, Any]) -> dict[str, Any]:
    model = eval_input.get("model") if isinstance(eval_input.get("model"), dict) else {}
    binding = model.get("deployment_binding")
    if not isinstance(binding, dict):
        runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
        binding = runtime.get("deployment_binding")
    if not isinstance(binding, dict):
        raise RuntimeError("eval_input_resolved.json carries no approved deployment binding")
    if binding.get("schema") == DEPLOYMENT_BINDING_V1:
        raise RuntimeError(V1_BINDING_ERROR)
    if binding.get("schema") != DEPLOYMENT_BINDING_V2:
        raise RuntimeError(f"unsupported deployment binding schema: {binding.get('schema')!r}")
    return binding


def _execution_plan(eval_input: dict[str, Any]) -> dict[str, Any]:
    runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
    execution = runtime.get("execution") if isinstance(runtime.get("execution"), dict) else {}
    local_path = "local_python" if runtime.get("model_runtime") == "python" else "local_docker"
    path_planned = str(execution.get("path_planned") or runtime.get("execution_path") or local_path)
    requested = str(execution.get("requested") or "auto")
    if path_planned not in {"local_docker", "local_python"}:
        raise RuntimeError(f"resolved execution path must be local_python or local_docker, got {path_planned!r}")
    return {
        "requested": requested,
        "planned": "local",
        "path_requested": str(execution.get("path_requested") or "auto"),
        "path_planned": path_planned,
        "fallback_allowed": bool(execution.get("fallback_allowed", requested == "auto")),
        "reason": str(execution.get("reason") or ""),
    }


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


def _selected_datasets(eval_input: dict[str, Any], dataset_decision: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (entries handed to the entrypoint, canonical dataset names)."""
    rows = [row for row in eval_input.get("datasets", []) if isinstance(row, dict)]
    selected = dataset_decision.get("selected_datasets")
    if not isinstance(selected, list) or not selected:
        raise RuntimeError("dataset_decision.json must select at least one dataset")
    entries: list[str] = []
    names: list[str] = []
    for item in selected:
        wanted = str(item)
        row = next(
            (
                candidate
                for candidate in rows
                if wanted in {str(candidate.get(key) or "") for key in ("name", "requested_name", "source_root")}
            ),
            None,
        )
        if row is None:
            raise RuntimeError(f"selected dataset {wanted!r} is not part of the resolved eval input")
        entries.append(str(row.get("requested_name") or row.get("source_root") or row.get("name")))
        names.append(str(row.get("name") or wanted))
    dropped = _find_dropped_version(entries, _user_datasets(eval_input))
    if dropped:
        surface_value, user_value = dropped
        raise RuntimeError(
            f"dataset entry dropped the @version suffix: got {surface_value!r} but the user input was {user_value!r}"
        )
    return entries, names


def _tool_name(binding: dict[str, Any], model_dir: Path) -> str:
    runtime_key = "python" if binding.get("runtime_kind") == "python" else "container"
    runtime = binding.get(runtime_key) if isinstance(binding.get(runtime_key), dict) else {}
    tools = runtime.get("tool_names")
    if isinstance(tools, list) and tools and isinstance(tools[0], str) and tools[0]:
        return tools[0]
    config_yaml = model_dir / "config.yaml"
    if config_yaml.is_file():
        import yaml

        config = yaml.safe_load(config_yaml.read_text(encoding="utf-8")) or {}
        for tool in config.get("tools") or []:
            if isinstance(tool, dict) and tool.get("name"):
                return str(tool["name"])
    raise RuntimeError("the approved deployment binding declares no tool name and the model config has no tools")


def _metrics(eval_input: dict[str, Any]) -> list[str]:
    user_input = eval_input.get("user_input") if isinstance(eval_input.get("user_input"), dict) else {}
    raw = user_input.get("metrics")
    if isinstance(raw, str):
        return raw.split()
    if isinstance(raw, list):
        return [str(item) for item in raw if item]
    return []


def _device_request(eval_input: dict[str, Any]) -> str:
    runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
    device = runtime.get("device") if isinstance(runtime.get("device"), dict) else {}
    for value in (device.get("request"), device.get("resolved")):
        if isinstance(value, str) and value:
            return value
    user_input = eval_input.get("user_input") if isinstance(eval_input.get("user_input"), dict) else {}
    if isinstance(user_input.get("device"), str) and user_input["device"]:
        return user_input["device"]
    return "auto"


def _local_device_env(device_request: str) -> tuple[dict[str, str], str, str | None]:
    request = (device_request or "auto").strip()
    lowered = request.lower()
    env: dict[str, str] = {"SURE_EVAL_DEVICE_REQUEST": request}
    if lowered == "cpu":
        env["DEVICE"] = "cpu"
        env["SURE_EVAL_DEVICE_ACTUAL"] = "cpu"
        env["CUDA_VISIBLE_DEVICES"] = ""
        return env, "cpu", ""
    match = re.fullmatch(r"cuda:(\d+)", lowered)
    if match:
        env["CUDA_VISIBLE_DEVICES"] = match.group(1)
        env["DEVICE"] = request
        env["SURE_EVAL_DEVICE_ACTUAL"] = "cuda:0"
        return env, "cuda:0", match.group(1)
    if lowered == "cuda":
        env["DEVICE"] = "cuda"
        env["SURE_EVAL_DEVICE_ACTUAL"] = "cuda"
        return env, "cuda", os.environ.get("CUDA_VISIBLE_DEVICES")
    if lowered == "auto":
        return env, "auto", os.environ.get("CUDA_VISIBLE_DEVICES")
    env["DEVICE"] = request
    env["SURE_EVAL_DEVICE_ACTUAL"] = request
    return env, request, os.environ.get("CUDA_VISIBLE_DEVICES")


def _build_surface(
    *,
    eval_input: dict[str, Any],
    binding: dict[str, Any],
    execution: dict[str, Any],
    entries: list[str],
    names: list[str],
    tool_name: str,
    metrics: list[str],
    device_request: str,
    device_actual: str,
    cuda_visible: str | None,
    input_resolved_path: Path,
    read_files: list[Path],
) -> dict[str, Any]:
    runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
    user_input = eval_input.get("user_input") if isinstance(eval_input.get("user_input"), dict) else {}
    model = eval_input.get("model") if isinstance(eval_input.get("model"), dict) else {}
    product_dir = str(runtime.get("run_dir") or "")
    max_samples = int(runtime.get("max_samples") or user_input.get("max_samples") or 0)
    protocol_id = str(runtime.get("protocol_id") or user_input.get("protocol") or "standard_system")
    run_id = str(runtime.get("run_id") or "")
    model_name = str(user_input.get("model") or model.get("model_name") or "")
    env: dict[str, str] = {
        "DATASETS": " ".join(entries),
        "MODEL_NAME": model_name,
        "RUN_ID": run_id,
        "TOOL_NAME": tool_name,
        "MAX_SAMPLES": str(max_samples),
        "PROTOCOL_ID": protocol_id,
        "METRICS": " ".join(metrics),
        "SURE_EVAL_INPUT_RESOLVED": str(input_resolved_path),
    }
    projection = runtime.get("dataset_projection") if isinstance(runtime.get("dataset_projection"), dict) else {}
    if projection.get("host_root"):
        env["SURE_EVAL_DATASETS_ROOT"] = str(projection["host_root"])
    return {
        "run_id": run_id,
        "timestamp": _utc_now(),
        "execution_surface_type": "python_entrypoint",
        "materialized": True,
        "entrypoint_path": str(ENTRYPOINT),
        "generation_method": "run_infer",
        "execution": execution,
        "deployment_binding": compliance.expected_binding_summary(binding),
        "inference_runtime": {
            "device_request": device_request,
            "device_actual": device_actual,
            "cuda_visible_devices": "" if cuda_visible is None else cuda_visible,
            "execution_path": execution["path_planned"],
        },
        "source_provenance": {
            "template_file": str(ENTRYPOINT),
            "template_sha256": _sha256_file(ENTRYPOINT),
            "files_read_during_generation": [str(path) for path in read_files],
            "isolation_compliance": {
                "eval_runs_referenced": False,
                "prior_run_scripts_copied": False,
                "template_parameters_deviated": [],
                "deviation_approved_by_user": False,
            },
            "materialized_at": _utc_now(),
        },
        "resolved_inputs": {
            "model_name": model_name,
            "model_dir": str(model.get("model_dir") or binding.get("model_dir") or ""),
            "dataset": names[0] if names else "",
            "datasets": names,
            "dataset_entries": entries,
            "run_dir": product_dir,
            "protocol_id": protocol_id,
            "max_samples": max_samples,
            "evaluation_metrics": metrics,
            "execution_path": execution["path_planned"],
            "device": device_request,
            "tool_name": tool_name,
        },
        "expected_outputs": {
            "prepare_summary": f"{product_dir}/prepare_summary.json",
            "prediction_manifest": f"{product_dir}/predictions/manifest.json",
            "prediction_generation_status": f"{product_dir}/prediction_generation_status.json",
            "validation_payload": f"{product_dir}/validation_payload.json",
            "protocol": f"{product_dir}/protocol.yaml",
            "references_dir": f"{product_dir}/references/sure_benchmark/jsonl",
        },
        "env": env,
        "reason": "surface written by scripts/run_infer.py from the resolved eval input and the dataset decision",
        "notes": [],
    }


def _run_compliance(surface_path: Path) -> None:
    checks = {
        "entrypoint_provenance": compliance.check_entrypoint_provenance(surface_path),
        "inference_runtime": compliance.check_inference_runtime(surface_path),
    }
    failures = [f"{name}: {result.get('evidence', 'failed')}" for name, result in checks.items() if not result.get("passed")]
    if failures:
        raise RuntimeError("EXECUTION_SURFACE_ISOLATION red line: " + "; ".join(failures))


def _nonempty_prediction_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t", 1)
        if len(parts) > 1 and parts[1].strip():
            count += 1
    return count


def _dataset_rows(product_dir: Path, names: list[str]) -> list[dict[str, Any]]:
    status_rows = {
        str(row.get("dataset")): row
        for row in _read_json(product_dir / "prediction_generation_status.json").get("datasets", [])
        if isinstance(row, dict) and row.get("dataset")
    }
    validation_rows = {
        str(row.get("dataset")): row
        for row in _read_json(product_dir / "validation_payload.json").get("results", [])
        if isinstance(row, dict) and row.get("dataset")
    }
    rows: list[dict[str, Any]] = []
    for name in names:
        status = status_rows.get(name, {})
        validation = validation_rows.get(name, {})
        expected = status.get("num_expected_samples")
        if not isinstance(expected, int):
            expected = validation.get("expected_samples") if isinstance(validation.get("expected_samples"), int) else 0
        generated = _nonempty_prediction_rows(product_dir / "predictions" / f"{name}.txt")
        provided = validation.get("provided_predictions") if isinstance(validation.get("provided_predictions"), int) else generated
        invalid = len(validation.get("empty_prediction_keys") or []) + len(validation.get("contract_violation_keys") or [])
        rows.append(
            {
                "dataset": name,
                "expected": int(expected),
                "generated": generated,
                "valid": max(int(provided) - invalid, 0) if validation else generated,
            }
        )
    return rows


def _failed_stage(stdout_text: str) -> str:
    matches = STAGE_MARKER_RE.findall(stdout_text)
    return matches[-1] if matches else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch model inference for a /sure_infer run")
    parser.add_argument("--run-dir", required=True, help="SURE skill run directory, e.g. .sure/runs/<run_id>")
    parser.add_argument("--execution-output", help="Path to execution_result.json")
    parser.add_argument("--cwd", help="Repository root used as the working directory")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    artifacts_dir = run_dir / "artifacts"
    eval_input_path = artifacts_dir / "eval_input_resolved.json"
    decision_path = artifacts_dir / "dataset_decision.json"
    eval_input = _read_json(eval_input_path)
    if not eval_input:
        raise FileNotFoundError(f"eval_input_resolved.json not found or invalid: {eval_input_path}")
    dataset_decision = _read_json(decision_path)
    if not dataset_decision:
        raise FileNotFoundError(f"dataset_decision.json not found or invalid: {decision_path}")

    binding = _approved_binding(eval_input)
    execution = _execution_plan(eval_input)
    entries, names = _selected_datasets(eval_input, dataset_decision)
    model = eval_input.get("model") if isinstance(eval_input.get("model"), dict) else {}
    model_dir = Path(str(model.get("model_dir") or binding.get("model_dir") or "")).expanduser()
    tool_name = _tool_name(binding, model_dir)
    metrics = _metrics(eval_input)
    device_request = _device_request(eval_input)
    device_env, device_actual, cuda_visible = _local_device_env(device_request)

    surface = _build_surface(
        eval_input=eval_input,
        binding=binding,
        execution=execution,
        entries=entries,
        names=names,
        tool_name=tool_name,
        metrics=metrics,
        device_request=device_request,
        device_actual=device_actual,
        cuda_visible=cuda_visible,
        input_resolved_path=eval_input_path,
        read_files=[eval_input_path, decision_path],
    )
    surface_path = artifacts_dir / "execution_surface.json"
    _write_json(surface_path, surface)
    _run_compliance(surface_path)

    execution_path = execution["path_planned"]
    host = socket.gethostname()
    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path(__file__).resolve().parents[4]
    stdout_path = artifacts_dir / "local_execution.stdout.log"
    stderr_path = artifacts_dir / "local_execution.stderr.log"
    execution_output = (
        Path(args.execution_output).expanduser().resolve() if args.execution_output else artifacts_dir / "execution_result.json"
    )
    extra_env = {
        **device_env,
        "SURE_EVAL_EXECUTION_PATH": execution_path,
        "SURE_EVAL_EXECUTION_REQUESTED": execution["requested"],
        "SURE_EVAL_EXECUTION_JOB_ID": f"local:{host}:{run_dir.name}",
    }
    if execution_path == "local_python":
        command, process_env, _launch = build_local_python_command(
            surface=surface,
            eval_input=eval_input,
            entrypoint=ENTRYPOINT,
            repo_root=cwd,
            extra_env=extra_env,
        )
    else:
        command, _launch = build_local_container_command(
            surface=surface,
            eval_input=eval_input,
            control_run_dir=run_dir,
            entrypoint=ENTRYPOINT,
            repo_root=cwd,
            device_request=device_request,
            extra_env={**extra_env, "SURE_EVAL_CONTAINER_REPO_ROOT": str(cwd)},
        )
        process_env = os.environ.copy()

    runtime_kind = "python" if execution_path == "local_python" else "container"
    start = time.monotonic()
    started_at = _utc_now()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(command, cwd=cwd, env=process_env, stdout=stdout, stderr=stderr, text=True)
        returncode = process.wait()
    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
    if execution_path == "local_docker":
        returncode = effective_container_exit_code(returncode, stdout_text, stderr_text)
    else:
        try:
            verify_model_integrity(binding)
        except ValueError as exc:
            with stderr_path.open("a", encoding="utf-8") as stderr:
                stderr.write(f"\nMODEL_INTEGRITY_VIOLATION: {exc}\n")
            returncode = returncode or 70
    duration = time.monotonic() - start
    product_dir = Path(str((eval_input.get("runtime") or {}).get("run_dir") or "")).expanduser()
    execution_payload = {
        "job_status": "succeeded" if returncode == 0 else "failed",
        "execution_path": execution_path,
        "runtime_kind": runtime_kind,
        "execution_requested": execution["requested"],
        "host": host,
        "pid": process.pid,
        "command": shlex.join(command),
        "cwd": str(cwd),
        "started_at": started_at,
        "ended_at": _utc_now(),
        "duration_seconds": duration,
        "exit_code": returncode,
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "device_request": device_request,
        "device_actual": device_actual,
        "cuda_visible_devices": "" if cuda_visible is None else cuda_visible,
        "product_dir": str(product_dir),
        "failed_stage": _failed_stage(stdout_text) if returncode != 0 else "",
        "input_digest": _input_digest(eval_input_path, decision_path),
        "datasets": _dataset_rows(product_dir, names),
    }
    _write_json(execution_output, execution_payload)
    print(json.dumps({"execution_result": str(execution_output), **execution_payload}, indent=2, ensure_ascii=False))
    return 0 if returncode == 0 else returncode


if __name__ == "__main__":
    raise SystemExit(main())
