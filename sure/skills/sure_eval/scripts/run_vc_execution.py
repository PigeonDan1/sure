#!/usr/bin/env python3
"""Submit a materialized SURE-EVAL execution surface through vc.

This is the vc counterpart of ``run_local_execution.py`` for the
SUBMIT_EXECUTION unit. It does not wait for the job to finish; the following
EXECUTE_WAIT unit owns polling and final ``execution_result.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from container_execution import deployment_binding, resolve_container_harness_runtime
from evaluation_runtime import evaluation_runtime_from_eval_input
from harness_runtime import harness_runtime_from_eval_input
from sure_eval.agent import vc_precheck
from sure_eval.agent.vc_submitter import (
    _infer_default_volume_mount,
    _infer_repo_root,
    _merge_volume_mounts,
    _translate_to_container_path,
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


def _dataset_source_roots(eval_input: dict) -> list[str]:
    datasets = eval_input.get("datasets") if isinstance(eval_input.get("datasets"), list) else []
    roots: list[str] = []
    for item in datasets:
        if isinstance(item, dict) and item.get("source_root"):
            root = str(item["source_root"])
            if root not in roots:
                roots.append(root)
    return roots


def _execution_from_surface(surface: dict[str, Any], eval_input: dict[str, Any]) -> dict[str, Any]:
    execution = surface.get("execution") if isinstance(surface.get("execution"), dict) else {}
    if not execution:
        runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
        execution = runtime.get("execution") if isinstance(runtime.get("execution"), dict) else {}
    requested = str(execution.get("requested") or "auto")
    planned = str(execution.get("planned") or ("vc" if execution.get("path_planned") == "vc_submit" else "local"))
    path_planned = str(execution.get("path_planned") or ("vc_submit" if planned == "vc" else "local_docker"))
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


def _approved_memory_gb(model_dir: Path) -> int:
    declared: list[float] = []
    weight_sizes: list[float] = []
    for name in ("config.yaml", "model.spec.yaml"):
        path = model_dir / name
        if not path.is_file():
            continue
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(payload, dict):
            continue
        resources = payload.get("resources") if isinstance(payload.get("resources"), dict) else {}
        weights = payload.get("weights") if isinstance(payload.get("weights"), dict) else {}
        for values, raw in ((declared, resources.get("memory_gb")), (weight_sizes, weights.get("size_gb"))):
            try:
                number = float(raw)
            except (TypeError, ValueError):
                continue
            if number > 0:
                values.append(number)
    weight_heuristic = max((size * 2.5 + 4 for size in weight_sizes), default=0)
    requested = max([16.0, weight_heuristic, *declared])
    return max(8, min(64, int(requested + 0.999)))


_VC_JOB_NAME_MAX_LENGTH = 60


def _normalize_job_name(value: str) -> str:
    name = re.sub(r"[^a-z0-9.-]+", "-", value.lower()).strip("-") or "sure-eval"
    if len(name) <= _VC_JOB_NAME_MAX_LENGTH:
        return name
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    prefix_length = _VC_JOB_NAME_MAX_LENGTH - len(digest) - 1
    prefix = name[:prefix_length].rstrip("-") or "sure-eval"
    return f"{prefix}-{digest}"


def _job_name(model_name: str, eval_input: dict[str, Any], surface: dict[str, Any], vc_request: dict[str, Any]) -> str:
    explicit = vc_request.get("job_name")
    if isinstance(explicit, str) and explicit.strip():
        return _normalize_job_name(explicit)
    runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
    run_id = str(runtime.get("run_id") or surface.get("run_id") or "sure-eval")
    stem = model_name.split("__", 1)[-1] or model_name
    return _normalize_job_name(f"{stem}-{run_id}")


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


def _command_text(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def _resolved_submission(
    *,
    image: str,
    image_digest: str,
    image_identity_ref: str,
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
    harness_runtime: dict[str, Any],
    model_runtime: dict[str, Any],
) -> dict[str, Any]:
    return {
        "image": image,
        "image_digest": image_digest,
        "image_identity_ref": image_identity_ref,
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
        "harness_runtime": harness_runtime,
        "model_runtime": model_runtime,
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
    parser.add_argument("--image", help="Deprecated: must equal the approved digest-pinned image")
    parser.add_argument("--partition", help="Override vc partition")
    parser.add_argument("--memory", type=int, help="Override vc memory in GB")
    parser.add_argument("--gpus", type=int, help="Override GPUs per task")
    parser.add_argument("--cpus", type=int, help="Override CPUs per task")
    parser.add_argument("--job-name", help="Override vc job name")
    parser.add_argument(
        "--harness-python-bin",
        default="",
        help="Disabled compatibility option; Harness Runtime comes from eval_input_resolved.json.",
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
    binding = deployment_binding(eval_input)
    host_harness_runtime = harness_runtime_from_eval_input(eval_input)
    container = binding.get("container") if isinstance(binding.get("container"), dict) else {}
    if model_dir is None or model_dir.resolve() != Path(str(binding.get("model_dir"))).resolve():
        raise RuntimeError("execution surface model_dir differs from the approved deployment binding")
    image = str(binding["target_image"])
    image_digest = str(binding["target_image_digest"])
    image_identity_ref = str(binding["target_image_ref"])
    requested_image = str(_vc_request(eval_input).get("image") or "")
    for source, override in (("--image", args.image or ""), ("vc_image", requested_image)):
        if override and override not in {image, image_identity_ref}:
            raise RuntimeError(f"{source} cannot override approved image {image_identity_ref}")
    if args.harness_python_bin:
        raise RuntimeError("host harness Python override is disabled; use the approved container runtime")
    vc_request = _vc_request(eval_input)
    device_request, device_actual = _device(eval_input, surface)
    log_path = run_dir / "vc_logs" / "job.log"
    entrypoint_path = artifacts_dir / "vc_entrypoint.sh"
    submit_output = Path(args.submit_output).expanduser().resolve() if args.submit_output else artifacts_dir / "submit_result.json"
    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path.cwd()
    repo_root = _infer_repo_root()
    volume_mount = _infer_default_volume_mount(repo_root)
    dataset_source_roots = _dataset_source_roots(eval_input)
    harness_runtime, harness_mounted_from_repo = resolve_container_harness_runtime(
        binding,
        host_harness_runtime,
        repo_root,
    )
    harness_root = Path(str(harness_runtime["runtime_root"]))
    evaluation_runtime = evaluation_runtime_from_eval_input(eval_input, prepare=False)
    volume_mount = _merge_volume_mounts(volume_mount, [str(model_dir), *dataset_source_roots])
    model_mount = container.get("model_mount") if isinstance(container.get("model_mount"), dict) else {}
    model_target = str(model_mount.get("target") or "")
    result_mount = container.get("result_mount") if isinstance(container.get("result_mount"), dict) else {}
    result_source = Path(str((eval_input.get("runtime") or {}).get("run_dir") or "")).resolve()
    result_target = str(result_mount.get("target") or "/sure-output")
    if not Path(model_target).is_absolute() or not Path(result_target).is_absolute():
        raise RuntimeError("approved deployment container mount targets must be absolute")
    result_source.mkdir(parents=True, exist_ok=True)
    volume_mount = f"{volume_mount},{model_dir}:{model_target}:ro,{result_source}:{result_target}"
    entrypoint_container = _translate_to_container_path(entrypoint_path, volume_mount)
    model_python_bin = str(container.get("python_executable") or "python")
    harness_python_bin = str(harness_runtime["python_executable"])
    model_pythonpath: list[str] = []
    harness_library_paths: list[str] = []
    harness_python_home = ""
    entrypoint_env = _surface_env_for_container(surface, volume_mount)
    source_provenance = surface.get("source_provenance") if isinstance(surface.get("source_provenance"), dict) else {}
    for key in (
        "MODEL_PYTHON",
        "PYTHON_BIN",
        "HARNESS_PYTHON_BIN",
        "MODEL_DIR",
        "SURE_EVAL_APPROVED_MODEL_DIR",
        "RUN_DIR",
        "SURE_EVAL_APPROVED_RESULT_DIR",
        "SURE_EVAL_CONTAINER_IMAGE",
    ):
        entrypoint_env.pop(key, None)
    entrypoint_env.setdefault(
        "REPO_ROOT",
        _translate_to_container_path(repo_root / "sure" / "skills" / "sure_eval", volume_mount),
    )
    entrypoint_env.update(
        {
            "MODEL_DIR": model_target,
            "SURE_EVAL_APPROVED_MODEL_DIR": model_target,
            "RUN_DIR": result_target,
            "SURE_EVAL_APPROVED_RESULT_DIR": result_target,
            "SURE_HARNESS_RUNTIME_ID": str(harness_runtime["runtime_id"]),
            "SURE_HARNESS_LOCK_SHA256": str(harness_runtime["lock_sha256"]),
            "SURE_HARNESS_MANIFEST_PATH": _translate_to_container_path(
                Path(str(harness_runtime["manifest_path"])), volume_mount
            ),
            "SURE_HARNESS_RUNTIME_ROOT": _translate_to_container_path(harness_root, volume_mount),
            "SURE_EVAL_CONTAINER_IMAGE": image_identity_ref,
            "SURE_EVAL_CONTAINER_IMAGE_DIGEST": image_digest,
            "SURE_EVAL_CONTAINER_IMAGE_REF": image_identity_ref,
            "SURE_EVAL_CONTAINER_WORKING_DIR": str(container.get("working_dir") or model_target),
            "SURE_EVAL_EXECUTION_ENTRYPOINT": entrypoint_container,
            "SURE_EVAL_EXECUTION_GENERATION_METHOD": str(surface.get("generation_method") or "harness_template"),
            "SURE_EVAL_EXECUTION_TEMPLATE_FILE": str(source_provenance.get("template_file") or ""),
            "SURE_EVAL_EXECUTION_TEMPLATE_SHA256": str(source_provenance.get("template_sha256") or ""),
            "SURE_EVAL_PUBLISHED_RUN_DIR": str(result_source),
            "SURE_EVAL_WRITABLE_CACHE_ROOT": f"{result_target}/.runtime/cache",
            "SURE_EVAL_CACHE_DIR": f"{result_target}/.runtime/cache/sure-eval",
            "HF_HOME": f"{result_target}/.runtime/cache/huggingface",
            "HF_HUB_CACHE": f"{result_target}/.runtime/cache/huggingface/hub",
            "TRANSFORMERS_CACHE": f"{result_target}/.runtime/cache/huggingface/transformers",
            "MODELSCOPE_CACHE": f"{result_target}/.runtime/cache/modelscope",
            "TORCH_HOME": f"{result_target}/.runtime/cache/torch",
            "XDG_CACHE_HOME": f"{result_target}/.runtime/cache/xdg",
        }
    )
    if evaluation_runtime is not None:
        entrypoint_env.update(
            {
                "SURE_EVALUATION_PYTHON": _translate_to_container_path(
                    Path(str(evaluation_runtime["python_executable"])), volume_mount
                ),
                "SURE_EVALUATION_RUNTIME_ID": str(evaluation_runtime["runtime_id"]),
                "SURE_EVALUATION_LOCK_SHA256": str(evaluation_runtime["lock_sha256"]),
                "SURE_EVALUATION_RUNTIME_MANIFEST": _translate_to_container_path(
                    Path(str(evaluation_runtime["manifest_path"])), volume_mount
                ),
                "SURE_EVALUATION_HOME": _translate_to_container_path(
                    Path(str(evaluation_runtime["engine_root"])), volume_mount
                ),
            }
        )
    image_source = "approved_deployment_binding"
    partition = args.partition or str(vc_request.get("partition") or "") or select_best_partition()
    memory_gb = args.memory or _memory_gb(vc_request.get("mem")) or _approved_memory_gb(model_dir)
    gpus = _positive_int(args.gpus if args.gpus is not None else vc_request.get("gpu"), 1)
    cpus = _positive_int(args.cpus if args.cpus is not None else vc_request.get("cpu"), 4)
    job_name = _normalize_job_name(args.job_name) if args.job_name else _job_name(model_name, eval_input, surface, vc_request)
    _write_entrypoint(
        path=entrypoint_path,
        volume_mount=volume_mount,
        container_image=image_identity_ref,
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
        harness_python_bin=harness_python_bin,
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
        image_digest=image_digest,
        image_identity_ref=image_identity_ref,
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
        harness_runtime=harness_runtime,
        model_runtime={
            "runtime_type": "model_python",
            "python_executable": model_python_bin,
            "image_ref": image,
        },
    )
    if args.dry_run:
        print(command)
        return 0

    precheck_paths = [str(repo_root), str(entrypoint_path)]
    if evaluation_runtime is not None:
        precheck_paths.append(str(evaluation_runtime["runtime_root"]))
    if model_dir is not None:
        precheck_paths.append(str(model_dir))
    precheck_paths.extend(dataset_source_roots)
    identity_check = vc_precheck.check_image(image_identity_ref, image_source)
    identity_check.name = "image_identity"
    precheck_results = vc_precheck.run_precheck(
        image=image,
        image_source=image_source,
        partition=partition,
        memory=f"{memory_gb}G",
        gpus=gpus,
        cpus=cpus,
        volume_mount=volume_mount,
        paths=precheck_paths,
        expected_venv=None,
    )
    precheck_results[0].name = "image_submission"
    precheck_results.insert(0, identity_check)
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
        "image_digest": image_digest,
        "image_identity_ref": image_identity_ref,
        "deployment_binding": {
            "target_image_ref": image_identity_ref,
            "bundle_identity_sha256": (binding.get("evidence") or {}).get("bundle_identity_sha256"),
            "host_python_fallback": False,
            "model_mount_read_only": True,
        },
        "harness_runtime": harness_runtime,
        "harness_runtime_mounted_from_repo": harness_mounted_from_repo,
        "model_runtime": {
            "runtime_type": "model_python",
            "python_executable": model_python_bin,
            "image_ref": image,
        },
        "vc_submission": submission,
    }
    _write_surface_resolved_submission(surface_path, surface, submission)
    _write_json(submit_output, payload)
    print(json.dumps({"submit_result": str(submit_output), **payload}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
