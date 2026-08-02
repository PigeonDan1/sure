#!/usr/bin/env python3
"""Submit a materialized SURE-EVAL execution surface through vc.

This is the vc counterpart of ``run_local_execution.py`` for the
SUBMIT_EXECUTION unit. It does not wait for the job to finish; the following
EXECUTE_WAIT unit owns polling and final ``execution_result.json``.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from sure_eval.agent import vc_precheck
from sure_eval.agent.vc_submitter import (
    _infer_default_volume_mount,
    _infer_repo_root,
    _translate_to_container_path,
    estimate_memory_gb,
    infer_venv_path,
    select_best_image,
    select_best_partition,
)


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
    requested = str(execution.get("requested") or "auto")
    planned = str(execution.get("planned") or ("vc" if execution.get("path_planned") == "vc_submit" else "local"))
    path_planned = str(execution.get("path_planned") or ("vc_submit" if planned == "vc" else "local_bash"))
    return {
        "requested": requested,
        "planned": planned,
        "path_planned": path_planned,
        "reason": execution.get("reason") or "",
    }


def _device(eval_input: dict[str, Any], surface: dict[str, Any]) -> tuple[str, str]:
    runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
    device = runtime.get("device") if isinstance(runtime.get("device"), dict) else {}
    request = str(device.get("request") or "")
    resolved = str(device.get("resolved") or "")
    inference_runtime = surface.get("inference_runtime") if isinstance(surface.get("inference_runtime"), dict) else {}
    if not request:
        request = str(inference_runtime.get("device_request") or inference_runtime.get("device") or "auto")
    if not resolved:
        resolved = str(inference_runtime.get("device_resolved") or "cuda:0")
    return request, resolved


def _entrypoint(surface: dict[str, Any]) -> str:
    value = surface.get("entrypoint_path") or surface.get("entrypoint")
    if not isinstance(value, str) or not value:
        raise ValueError("execution_surface.json must declare entrypoint_path or entrypoint")
    return value


def _vc_request(eval_input: dict[str, Any]) -> dict[str, Any]:
    runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
    value = runtime.get("vc") if isinstance(runtime.get("vc"), dict) else {}
    user_input = eval_input.get("user_input") if isinstance(eval_input.get("user_input"), dict) else {}
    if not value and isinstance(user_input.get("vc"), dict):
        value = user_input["vc"]
    return value if isinstance(value, dict) else {}


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _memory_gb(value: Any) -> int | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    number = int(match.group(0))
    return number if number > 0 else None


def _job_name(model_name: str, eval_input: dict[str, Any], surface: dict[str, Any], vc_request: dict[str, Any]) -> str:
    explicit = vc_request.get("job_name")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
    run_id = str(runtime.get("run_id") or surface.get("run_id") or "sure-eval")
    stem = model_name.split("__", 1)[-1] or model_name
    name = re.sub(r"[^a-z0-9.-]+", "-", f"{stem}-{run_id}".lower()).strip("-")
    return name[:63] or "sure-eval"


def _model_name(surface: dict[str, Any], eval_input: dict[str, Any]) -> str:
    resolved = surface.get("resolved_inputs") if isinstance(surface.get("resolved_inputs"), dict) else {}
    for value in (
        resolved.get("model_name"),
        (eval_input.get("model") or {}).get("name") if isinstance(eval_input.get("model"), dict) else None,
        (eval_input.get("user_input") or {}).get("model") if isinstance(eval_input.get("user_input"), dict) else None,
    ):
        if isinstance(value, str) and value:
            return value
    raise ValueError("Could not resolve model name from execution_surface.json or eval_input_resolved.json")


def _model_dir(surface: dict[str, Any], eval_input: dict[str, Any]) -> Path | None:
    resolved = surface.get("resolved_inputs") if isinstance(surface.get("resolved_inputs"), dict) else {}
    for value in (
        resolved.get("model_dir"),
        (eval_input.get("model") or {}).get("model_dir") if isinstance(eval_input.get("model"), dict) else None,
        (eval_input.get("main_flow_input") or {}).get("target", {}).get("model_dir")
        if isinstance(eval_input.get("main_flow_input"), dict)
        and isinstance((eval_input.get("main_flow_input") or {}).get("target"), dict)
        else None,
    ):
        if isinstance(value, str) and value:
            path = Path(value).expanduser()
            return path.resolve() if path.exists() else path
    return None


def _model_eval_run_id(surface: dict[str, Any], eval_input: dict[str, Any]) -> str:
    runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
    if isinstance(runtime.get("run_id"), str) and runtime["run_id"]:
        return runtime["run_id"]
    resolved = surface.get("resolved_inputs") if isinstance(surface.get("resolved_inputs"), dict) else {}
    run_dir = resolved.get("run_dir")
    if isinstance(run_dir, str) and run_dir:
        return Path(run_dir).name
    if isinstance(surface.get("run_id"), str) and surface["run_id"]:
        return surface["run_id"]
    return "sure-eval"


def _surface_env_for_container(surface: dict[str, Any], volume_mount: str) -> dict[str, str]:
    raw_env = surface.get("env")
    if not isinstance(raw_env, dict):
        return {}
    values: dict[str, str] = {}
    for key, value in raw_env.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or value is None:
            continue
        text = str(value)
        if text.startswith("/"):
            text = _translate_to_container_path(Path(text), volume_mount)
        values[key] = text
    return values


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _artifact_text(model_dir: Path, relative_path: str) -> str:
    path = model_dir / relative_path
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _artifact_json(model_dir: Path, relative_path: str) -> dict[str, Any]:
    path = model_dir / relative_path
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _scan_model_text(model_dir: Path, relative_paths: tuple[str, ...], pattern: str) -> str:
    regex = re.compile(pattern)
    for relative_path in relative_paths:
        path = model_dir / relative_path
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        match = regex.search(text)
        if match:
            return match.group(1) if match.groups() else match.group(0)
    return ""


def _image_from_model_scripts(model_dir: Path) -> str:
    return _scan_model_text(
        model_dir,
        (
            "docker_validate.sh",
            "docker_build.sh",
            "Dockerfile",
            "artifacts/build.log",
            "artifacts/docker_build.log",
            "artifacts/docker_image_inspect.json",
        ),
        r"(docker\.v2\.aispeech\.com/[^\s\"'}]+)",
    )


def _image_from_model_metadata(model_dir: Path | None, model_cfg: dict[str, Any]) -> str:
    runtime = model_cfg.get("runtime") if isinstance(model_cfg.get("runtime"), dict) else {}
    docker_image = runtime.get("docker_image")
    if isinstance(docker_image, str) and docker_image.strip():
        return docker_image.strip()
    if model_dir is None:
        return ""
    tagged = _artifact_text(model_dir, "artifacts/docker_image_tag.txt")
    if tagged:
        return tagged.splitlines()[0].strip()
    for relative_path in (
        "artifacts/docker_validation.json",
        "artifacts/verdict.json",
        "artifacts/build_plan.json",
    ):
        payload = _artifact_json(model_dir, relative_path)
        for key in ("image", "target_image"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for section in ("docker", "runtime"):
            nested = payload.get(section)
            if isinstance(nested, dict):
                for key in ("image", "docker_image", "target_image"):
                    value = nested.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
    scripted = _image_from_model_scripts(model_dir)
    if scripted:
        return scripted
    return ""


def _is_company_vc_image(image: str) -> bool:
    return image.startswith("docker.v2.aispeech.com/") and "/" in image.removeprefix("docker.v2.aispeech.com/")


def _resolve_vc_image(
    *,
    model_name: str,
    cli_image: str | None,
    requested_image: Any,
    model_dir: Path | None,
    model_cfg: dict[str, Any],
) -> tuple[str, str]:
    if isinstance(cli_image, str) and cli_image.strip():
        return cli_image.strip(), "cli_override"

    requested = str(requested_image or "").strip()
    metadata = _image_from_model_metadata(model_dir, model_cfg)
    if requested and _is_company_vc_image(requested):
        return requested, "resolved_input"
    if requested and metadata and _is_company_vc_image(metadata):
        return metadata, f"model_metadata_over_invalid_requested_image:{requested}"
    if metadata:
        return metadata, "model_metadata"
    if requested:
        return requested, "invalid_requested_image_no_metadata_fallback"
    return select_best_image(model_name), "auto_select_best_image"


def _resolve_model_python_bin(
    *,
    model_name: str,
    model_dir: Path | None,
    model_cfg: dict[str, Any],
    volume_mount: str,
) -> str:
    runtime = model_cfg.get("runtime") if isinstance(model_cfg.get("runtime"), dict) else {}
    container_python = runtime.get("container_python_path")
    if isinstance(container_python, str) and container_python.strip():
        return container_python.strip()

    if model_dir is not None:
        scripted_python = _scan_model_text(
            model_dir,
            (
                "docker_validate.sh",
                "Dockerfile",
                "artifacts/docker_image_inspect.json",
                "artifacts/docker_validation.json",
                "artifacts/verdict.json",
            ),
            r"(/opt/[^\s\"';&|]+/bin/python(?:3(?:\.\d+)?)?)",
        )
        if scripted_python:
            return scripted_python
        if _scan_model_text(
            model_dir,
            ("docker_validate.sh", "Dockerfile"),
            r"\b(python(?:3(?:\.\d+)?)?)\s+validate\.py\b",
        ):
            return "python"

    server_cfg = model_cfg.get("server") if isinstance(model_cfg.get("server"), dict) else {}
    command = server_cfg.get("command")
    if isinstance(command, list) and command:
        first = command[0]
        if isinstance(first, str) and first.strip():
            command_path = Path(first)
            if command_path.is_absolute():
                return _translate_to_container_path(command_path, volume_mount)
            if model_dir is not None:
                return _translate_to_container_path(model_dir / command_path, volume_mount)

    return f"{infer_venv_path(model_name)}/bin/python"


def _model_pythonpath_entries(model_dir: Path | None, volume_mount: str) -> list[str]:
    if model_dir is None:
        return []
    entries: list[Path] = []
    if model_dir.is_dir():
        entries.append(model_dir)
        parts = model_dir.parts
        if len(parts) >= 3 and parts[-3:] and parts[-3:-1] == ("sure_eval", "models"):
            src_root = Path(*parts[:-3])
            if src_root.is_dir():
                entries.append(src_root)
        source_root = model_dir / ".runtime" / "source"
        if source_root.is_dir():
            for source_dir in sorted(source_root.iterdir()):
                if not source_dir.is_dir():
                    continue
                src_dir = source_dir / "src"
                if src_dir.is_dir():
                    entries.append(src_dir)
                entries.append(source_dir)

    deduped: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        translated = _translate_to_container_path(entry.resolve(), volume_mount)
        if translated not in seen:
            deduped.append(translated)
            seen.add(translated)
    return deduped


def _python_shared_library_dirs(python_bin: str | None, volume_mount: str) -> list[str]:
    if not python_bin:
        return []
    python_path = Path(python_bin).expanduser()
    if not python_path.exists():
        return []
    env_root = python_path.parent.parent
    entries: list[str] = []
    seen: set[str] = set()
    for candidate in (env_root / "lib", env_root / "lib64"):
        if not candidate.is_dir() or not any(candidate.glob("libpython*.so*")):
            continue
        translated = _translate_to_container_path(candidate.resolve(), volume_mount)
        if translated not in seen:
            entries.append(translated)
            seen.add(translated)
    return entries


def _python_home_prefix(python_bin: str | None, volume_mount: str) -> str:
    if not python_bin:
        return ""
    python_path = Path(python_bin).expanduser()
    if not python_path.exists():
        return ""
    env_root = python_path.parent.parent
    if (env_root / "pyvenv.cfg").exists() or (env_root / "conda-meta").exists():
        return ""
    for lib_root in (env_root / "lib", env_root / "lib64"):
        if any((candidate / "encodings").is_dir() for candidate in lib_root.glob("python3.*")):
            return _translate_to_container_path(env_root.resolve(), volume_mount)
    return ""


def _command_text(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def _resolved_submission(
    *,
    image: str,
    partition: str,
    memory_gb: int,
    gpus: int,
    cpus: int,
    job_name: str,
    volume_mount: str,
    entrypoint_host: Path,
    entrypoint_container: str,
    run_evaluation_path: str,
    log_path: Path,
    command: str,
) -> dict[str, Any]:
    return {
        "image": image,
        "partition": partition,
        "memory": f"{memory_gb}G",
        "memory_gb": memory_gb,
        "gpus": gpus,
        "cpus": cpus,
        "job_name": job_name,
        "volume_mount": volume_mount,
        "entrypoint_host": str(entrypoint_host),
        "entrypoint_container": entrypoint_container,
        "run_evaluation_host": run_evaluation_path,
        "log_path": str(log_path),
        "command": command,
    }


def _write_surface_resolved_submission(
    surface_path: Path,
    surface: dict[str, Any],
    submission: dict[str, Any],
) -> None:
    vc_runtime = surface.get("vc_runtime")
    if not isinstance(vc_runtime, dict):
        vc_runtime = {}
    vc_runtime["resolved_submission"] = submission
    surface["vc_runtime"] = vc_runtime
    _write_json(surface_path, surface)


def _write_entrypoint(
    *,
    path: Path,
    volume_mount: str,
    container_image: str,
    container_repo_root: str,
    vc_partition: str,
    vc_memory: str,
    vc_gpus: int,
    vc_cpus: int,
    model_python_bin: str,
    model_pythonpath: list[str],
    run_evaluation_path: str,
    log_path: Path,
    execution_requested: str,
    device_request: str,
    device_actual: str,
    harness_python_bin: str | None,
    harness_library_paths: list[str],
    harness_python_home: str,
    entrypoint_env: dict[str, str],
) -> None:
    run_evaluation_container = _translate_to_container_path(Path(run_evaluation_path), volume_mount)
    log_path_container = _translate_to_container_path(log_path, volume_mount)
    harness_python_container = (
        _translate_to_container_path(Path(harness_python_bin), volume_mount) if harness_python_bin else ""
    )
    q = shlex.quote
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"export PYTHON_BIN={q(model_python_bin)}",
        f"export MODEL_PYTHON={q(model_python_bin)}",
        "export SURE_EVAL_EXECUTION_PATH=vc_submit",
        f"export SURE_EVAL_EXECUTION_REQUESTED={q(execution_requested or 'vc')}",
        "export SURE_EVAL_EXECUTION_JOB_ID=${VC_JOB_ID:-}",
        "export SURE_EVAL_EXECUTION_SURFACE_TYPE=main_flow_script",
        f"export SURE_EVAL_CONTAINER_IMAGE={q(container_image)}",
        f"export SURE_EVAL_CONTAINER_REPO_ROOT={q(container_repo_root)}",
        f"export SURE_EVAL_VC_PARTITION={q(vc_partition)}",
        f"export SURE_EVAL_VC_MEMORY={q(vc_memory)}",
        f"export SURE_EVAL_VC_GPU={q(str(vc_gpus))}",
        f"export SURE_EVAL_VC_CPU={q(str(vc_cpus))}",
        "export SURE_EVAL_VC_NODES=1",
        'export NO_RESUME="${NO_RESUME:-1}"',
        f"export SURE_EVAL_DEVICE_REQUEST={q(device_request or 'auto')}",
        f"export SURE_EVAL_DEVICE_ACTUAL={q(device_actual or 'cuda:0')}",
        f"export DEVICE={q(device_actual or 'cuda:0')}",
    ]
    if model_pythonpath:
        lines.append(f"export PYTHONPATH={q(':'.join(model_pythonpath))}:${{PYTHONPATH:-}}")
    if harness_python_container:
        lines.append(f"export HARNESS_PYTHON_BIN={q(harness_python_container)}")
    if harness_library_paths:
        lines.append(f"export LD_LIBRARY_PATH={q(':'.join(harness_library_paths))}:${{LD_LIBRARY_PATH:-}}")
    if harness_python_home:
        lines.append(f"export PYTHONHOME={q(harness_python_home)}")
    for key in sorted(entrypoint_env):
        lines.append(f"export {key}={q(entrypoint_env[key])}")
    lines.extend(
        [
            f"mkdir -p {q(str(Path(log_path_container).parent))}",
            f"bash {q(run_evaluation_container)} > {q(log_path_container)} 2>&1",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit a SURE-EVAL execution surface through vc")
    parser.add_argument("--run-dir", required=True, help="SURE skill run directory, e.g. .sure/runs/<run_id>")
    parser.add_argument("--surface", help="Path to execution_surface.json; defaults to <run-dir>/artifacts/execution_surface.json")
    parser.add_argument("--submit-output", help="Path to submit_result.json")
    parser.add_argument("--cwd", help="Working directory for vc submit")
    parser.add_argument("--image", help="Override vc image")
    parser.add_argument("--partition", help="Override vc partition")
    parser.add_argument("--memory", type=int, help="Override vc memory in GB")
    parser.add_argument("--gpus", type=int, help="Override GPUs per task")
    parser.add_argument("--cpus", type=int, help="Override CPUs per task")
    parser.add_argument("--job-name", help="Override vc job name")
    parser.add_argument(
        "--harness-python-bin",
        default=os.environ.get("SURE_EVAL_HARNESS_PYTHON_BIN", ""),
        help="Optional Python interpreter for harness scripts inside the vc mount.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the vc command without submitting")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    artifacts_dir = run_dir / "artifacts"
    surface_path = _surface_path(run_dir, args.surface)
    surface = _read_json(surface_path)
    if not surface:
        raise FileNotFoundError(f"execution_surface.json not found or invalid: {surface_path}")
    eval_input = _read_json(artifacts_dir / "eval_input_resolved.json")
    execution = _execution_from_surface(surface, eval_input)
    if execution["requested"] == "local" or execution["path_planned"] != "vc_submit":
        raise RuntimeError("Refusing vc submission because the resolved execution plan is not vc_submit.")
    if not _vc_available():
        raise RuntimeError("Refusing vc submission because `which vc && vc info` did not pass.")

    model_name = _model_name(surface, eval_input)
    model_dir = _model_dir(surface, eval_input)
    model_cfg = _load_yaml(model_dir / "config.yaml") if model_dir is not None else {}
    model_eval_run_id = _model_eval_run_id(surface, eval_input)
    vc_request = _vc_request(eval_input)
    device_request, device_actual = _device(eval_input, surface)
    log_path = run_dir / "vc_logs" / "job.log"
    entrypoint_path = artifacts_dir / "vc_entrypoint.sh"
    submit_output = Path(args.submit_output).expanduser().resolve() if args.submit_output else artifacts_dir / "submit_result.json"
    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path.cwd()
    repo_root = _infer_repo_root()
    volume_mount = _infer_default_volume_mount(repo_root)
    entrypoint_container = _translate_to_container_path(entrypoint_path, volume_mount)
    model_python_bin = _resolve_model_python_bin(
        model_name=model_name,
        model_dir=model_dir,
        model_cfg=model_cfg,
        volume_mount=volume_mount,
    )
    model_pythonpath = _model_pythonpath_entries(model_dir, volume_mount)
    harness_library_paths = _python_shared_library_dirs(args.harness_python_bin or None, volume_mount)
    harness_python_home = _python_home_prefix(args.harness_python_bin or None, volume_mount)
    entrypoint_env = _surface_env_for_container(surface, volume_mount)
    entrypoint_env.setdefault(
        "REPO_ROOT",
        _translate_to_container_path(repo_root / "sure" / "skills" / "sure_eval", volume_mount),
    )
    image, image_source = _resolve_vc_image(
        model_name=model_name,
        cli_image=args.image,
        requested_image=vc_request.get("image"),
        model_dir=model_dir,
        model_cfg=model_cfg,
    )
    partition = args.partition or str(vc_request.get("partition") or "") or select_best_partition()
    memory_gb = args.memory or _memory_gb(vc_request.get("mem")) or estimate_memory_gb(model_name)
    gpus = _positive_int(args.gpus if args.gpus is not None else vc_request.get("gpu"), 1)
    cpus = _positive_int(args.cpus if args.cpus is not None else vc_request.get("cpu"), 4)
    job_name = args.job_name or _job_name(model_name, eval_input, surface, vc_request)
    _write_entrypoint(
        path=entrypoint_path,
        volume_mount=volume_mount,
        container_image=image,
        container_repo_root=_translate_to_container_path(repo_root, volume_mount),
        vc_partition=partition,
        vc_memory=f"{memory_gb}G",
        vc_gpus=gpus,
        vc_cpus=cpus,
        model_python_bin=model_python_bin,
        model_pythonpath=model_pythonpath,
        run_evaluation_path=_entrypoint(surface),
        log_path=log_path,
        execution_requested=execution["requested"],
        device_request=device_request,
        device_actual=device_actual,
        harness_python_bin=args.harness_python_bin or None,
        harness_library_paths=harness_library_paths,
        harness_python_home=harness_python_home,
        entrypoint_env=entrypoint_env,
    )
    cmd = [
        "vc",
        "submit",
        "-i",
        image,
        "-p",
        partition,
        "-g",
        str(gpus),
        "-m",
        f"{memory_gb}G",
        "-c",
        str(cpus),
        "-n",
        "1",
        "-j",
        job_name,
        "-v",
        volume_mount,
        "--cmd",
        f"bash {entrypoint_container}",
    ]
    command = _command_text(cmd)
    submission = _resolved_submission(
        image=image,
        partition=partition,
        memory_gb=memory_gb,
        gpus=gpus,
        cpus=cpus,
        job_name=job_name,
        volume_mount=volume_mount,
        entrypoint_host=entrypoint_path,
        entrypoint_container=entrypoint_container,
        run_evaluation_path=str(_entrypoint(surface)),
        log_path=log_path,
        command=command,
    )
    if args.dry_run:
        print(command)
        return 0

    precheck_paths = [str(repo_root), str(entrypoint_path)]
    if model_dir is not None:
        precheck_paths.append(str(model_dir))
    precheck_results = vc_precheck.run_precheck(
        image=image,
        image_source=image_source,
        partition=partition,
        memory=f"{memory_gb}G",
        gpus=gpus,
        cpus=cpus,
        volume_mount=volume_mount,
        paths=precheck_paths,
        expected_venv=infer_venv_path(model_name),
    )
    _write_json(artifacts_dir / "vc_precheck.json", vc_precheck.as_payload(precheck_results))
    if not vc_precheck.precheck_passed(precheck_results):
        print(vc_precheck.format_report(precheck_results), file=sys.stderr)
        return 1

    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(result.stderr or result.stdout or "vc submit failed", file=sys.stderr)
        return result.returncode or 1
    job_id = result.stdout.strip().splitlines()[-1].strip()
    if not job_id:
        print("vc submit succeeded but returned an empty job id", file=sys.stderr)
        return 1

    payload = {
        "execution_path": "vc_submit",
        "vc_available": True,
        "vc_job_id": job_id,
        "vc_info": "Submitted through scripts/run_vc_execution.py",
        "execution_requested": execution["requested"],
        "execution": {
            "requested": execution["requested"],
            "actual": "vc",
            "path_actual": "vc_submit",
        },
        "fallback_approved": False,
        "local_fallback_reason": "",
        "submitted_at": _utc_now(),
        "host": job_id,
        "command": "bash " + str(_entrypoint(surface)),
        "cwd": str(cwd),
        "device_request": device_request,
        "device_actual": device_actual,
        "cuda_visible_devices": "",
        "stdout_log": str(log_path),
        "stderr_log": str(log_path),
        "vc_submit_command": command,
        "vc_partition": partition,
        "vc_mem": f"{memory_gb}G",
        "vc_gpu": gpus,
        "vc_cpu": cpus,
        "vc_image": image,
        "vc_submission": submission,
    }
    _write_surface_resolved_submission(surface_path, surface, submission)
    _write_json(submit_output, payload)
    print(json.dumps({"submit_result": str(submit_output), **payload}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
