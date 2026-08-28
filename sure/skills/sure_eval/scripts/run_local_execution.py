#!/usr/bin/env python3
"""Run a materialized SURE-EVAL execution surface locally.

This helper is the local counterpart of vc submission. It intentionally writes
the same two main-flow artifacts:
- submit_result.json: where and how the run was launched
- execution_result.json: whether the launched command completed
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from container_execution import build_local_container_command, effective_container_exit_code
from python_execution import build_local_python_command


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


def _vc_available() -> bool:
    if not shutil.which("vc"):
        return False
    try:
        completed = subprocess.run(["vc", "info"], capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _surface_path(run_dir: Path, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (run_dir / "artifacts" / "execution_surface.json").resolve()


def _execution_from_surface(surface: dict[str, Any], eval_input: dict[str, Any]) -> dict[str, Any]:
    execution = surface.get("execution") if isinstance(surface.get("execution"), dict) else {}
    if not execution:
        runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
        execution = runtime.get("execution") if isinstance(runtime.get("execution"), dict) else {}
    requested = str(execution.get("requested") or "local")
    planned = str(execution.get("planned") or ("vc" if execution.get("path_planned") == "vc_submit" else "local"))
    runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
    local_path = "local_python" if runtime.get("model_runtime") == "python" else "local_docker"
    path_planned = str(execution.get("path_planned") or ("vc_submit" if planned == "vc" else local_path))
    return {
        "requested": requested,
        "planned": planned,
        "path_planned": path_planned,
        "reason": execution.get("reason") or "",
        "fallback_allowed": bool(execution.get("fallback_allowed", requested == "auto")),
    }


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
    env["DEVICE"] = request
    env["SURE_EVAL_DEVICE_ACTUAL"] = request
    return env, request, os.environ.get("CUDA_VISIBLE_DEVICES")


def _entrypoint(surface: dict[str, Any]) -> Path:
    value = surface.get("entrypoint_path") or surface.get("entrypoint")
    if not isinstance(value, str) or not value:
        raise ValueError("execution_surface.json must declare entrypoint_path or entrypoint")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        raise FileNotFoundError(f"Execution entrypoint does not exist: {path}")
    return path.resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a SURE-EVAL execution surface locally")
    parser.add_argument("--run-dir", required=True, help="SURE skill run directory, e.g. .sure/runs/<run_id>")
    parser.add_argument("--surface", help="Path to execution_surface.json; defaults to <run-dir>/artifacts/execution_surface.json")
    parser.add_argument("--submit-output", help="Path to submit_result.json")
    parser.add_argument("--execution-output", help="Path to execution_result.json")
    parser.add_argument("--cwd", help="Working directory for the command")
    parser.add_argument("--allow-auto-local-override", action="store_true", help="Allow execution=auto to run local even when vc is available")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    artifacts_dir = run_dir / "artifacts"
    surface_path = _surface_path(run_dir, args.surface)
    surface = _read_json(surface_path)
    if not surface:
        raise FileNotFoundError(f"execution_surface.json not found or invalid: {surface_path}")
    eval_input = _read_json(artifacts_dir / "eval_input_resolved.json")
    execution = _execution_from_surface(surface, eval_input)
    vc_available = _vc_available()
    if execution["requested"] == "vc" or execution["path_planned"] == "vc_submit":
        raise RuntimeError("Refusing local execution because the resolved execution plan requires vc_submit.")
    if execution["path_planned"] not in {"local_python", "local_docker"}:
        raise RuntimeError("Resolved local execution path must be local_python or local_docker.")
    if (
        execution["path_planned"] == "local_docker"
        and execution["requested"] == "auto"
        and vc_available
        and not args.allow_auto_local_override
    ):
        raise RuntimeError("Refusing local execution because execution=auto and vc is available; submit through vc instead.")

    entrypoint = _entrypoint(surface)
    host = socket.gethostname()
    device_request = _device_request(surface, eval_input)
    device_env, device_actual, cuda_visible = _local_device_env(device_request)
    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path(__file__).resolve().parents[4]
    stdout_path = artifacts_dir / "local_execution.stdout.log"
    stderr_path = artifacts_dir / "local_execution.stderr.log"
    submit_output = Path(args.submit_output).expanduser().resolve() if args.submit_output else artifacts_dir / "submit_result.json"
    execution_output = Path(args.execution_output).expanduser().resolve() if args.execution_output else artifacts_dir / "execution_result.json"

    local_fallback_reason = ""
    fallback_approved = False
    if execution["requested"] == "auto" and not vc_available:
        local_fallback_reason = "execution=auto selected local execution because vc is unavailable"
    elif execution["requested"] == "auto" and execution["path_planned"] == "local_python":
        local_fallback_reason = "execution=auto selected the approved Python runtime, which is local-only"
        fallback_approved = True
    elif execution["requested"] == "auto" and vc_available and args.allow_auto_local_override:
        local_fallback_reason = "execution=auto local override was explicitly allowed while vc was available"
        fallback_approved = True

    execution_path = execution["path_planned"]
    extra_env = {
        **device_env,
        "SURE_EVAL_EXECUTION_PATH": execution_path,
        "SURE_EVAL_EXECUTION_REQUESTED": execution["requested"],
        "SURE_EVAL_EXECUTION_JOB_ID": f"local:{host}:{run_dir.name}",
        "SURE_EVAL_VC_PARTITION": "",
        "SURE_EVAL_VC_GPU": "0",
        "SURE_EVAL_VC_CPU": "",
        "SURE_EVAL_VC_MEMORY": "",
        "SURE_EVAL_VC_NODES": "0",
    }
    if execution_path == "local_python":
        command, python_env, launch_binding = build_local_python_command(
            surface=surface,
            eval_input=eval_input,
            entrypoint=entrypoint,
            repo_root=cwd,
            extra_env=extra_env,
        )
        process_env = {**os.environ, **python_env}
    else:
        command, launch_binding = build_local_container_command(
            surface=surface,
            eval_input=eval_input,
            control_run_dir=run_dir,
            entrypoint=entrypoint,
            repo_root=cwd,
            device_request=device_request,
            extra_env={**extra_env, "SURE_EVAL_CONTAINER_REPO_ROOT": str(cwd)},
        )
        process_env = os.environ.copy()

    start = time.monotonic()
    started_at = _utc_now()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(command, cwd=cwd, env=process_env, stdout=stdout, stderr=stderr, text=True)
        submit_payload = {
            "execution_path": execution_path,
            "runtime_kind": "python" if execution_path == "local_python" else "container",
            "execution_requested": execution["requested"],
            "execution": {
                "requested": execution["requested"],
                "actual": "local",
                "path_actual": execution_path,
            },
            "vc_available": vc_available,
            "vc_job_id": "",
            "vc_info": "local execution selected by user or auto policy",
            "vc_image": str(launch_binding.get("image_ref") or ""),
            "deployment_binding": launch_binding,
            "fallback_approved": fallback_approved,
            "local_fallback_reason": local_fallback_reason,
            "submitted_at": started_at,
            "host": host,
            "pid": process.pid,
            "command": shlex.join(command),
            "cwd": str(cwd),
            "device_request": device_request,
            "device_actual": device_actual,
            "cuda_visible_devices": "" if cuda_visible is None else cuda_visible,
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
        }
        _write_json(submit_output, submit_payload)
        returncode = process.wait()
    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
    if execution_path == "local_docker":
        returncode = effective_container_exit_code(returncode, stdout_text, stderr_text)
    duration = time.monotonic() - start
    job_status = "succeeded" if returncode == 0 else "failed"
    execution_payload = {
        "job_status": job_status,
        "execution_path": execution_path,
        "runtime_kind": "python" if execution_path == "local_python" else "container",
        "execution_requested": execution["requested"],
        "host": host,
        "pid": submit_payload["pid"],
        "command": submit_payload["command"],
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
    }
    _write_json(execution_output, execution_payload)
    print(json.dumps({"submit_result": str(submit_output), "execution_result": str(execution_output), **execution_payload}, indent=2, ensure_ascii=False))
    return 0 if returncode == 0 else returncode


if __name__ == "__main__":
    raise SystemExit(main())
