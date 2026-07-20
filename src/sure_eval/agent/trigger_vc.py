#!/usr/bin/env python3
"""Trigger a model evaluation run via Volcano (vc submit) from an execution_surface.json.

This is the deterministic hand-off script that the main-flow agent calls
instead of ``bash run_evaluation.sh``.  It reads the execution surface,
auto-resolves image / memory, uses an explicit required partition when the
execution surface declares one, otherwise requires the default ``pdgpu-4090``
partition, and submits the job.

Usage::

    python src/sure_eval/agent/trigger_vc.py \
        src/sure_eval/models/Qwen__Qwen3-ASR-1.7B/eval_runs/<run_id>/execution_surface.json

Or directly by model + run_id (when execution_surface.json is at the
standard location)::

    python src/sure_eval/agent/trigger_vc.py --model Qwen__Qwen3-ASR-1.7B --run-id <run_id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sure_eval.agent.vc_submitter import (
    build_vc_submit_command,
    get_job_info,
    get_job_logs,
    submit_vc_run,
)
from sure_eval.core.logging import configure_logging, get_logger

configure_logging(level="INFO")
logger = get_logger(__name__)


def load_execution_surface(path: Path) -> dict:
    """Load and validate execution_surface.json."""
    if not path.exists():
        raise FileNotFoundError(f"execution_surface.json not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("materialized", False):
        logger.warning("execution_surface.json says materialized=false")
    return data


def resolve_model_and_run(surface: dict) -> tuple[str, str, str | None]:
    """Extract (model_name, run_id, entrypoint_path) from execution_surface.json."""
    resolved = surface.get("resolved_inputs", {})
    model_name = resolved.get("model_name", "")
    run_id = surface.get("run_id", "")
    entrypoint_path = surface.get("entrypoint_path")
    if not model_name:
        raise ValueError("execution_surface.json missing resolved_inputs.model_name")
    if not run_id:
        raise ValueError("execution_surface.json missing run_id")
    return model_name, run_id, entrypoint_path


def resolve_required_partition(surface: dict) -> str | None:
    """Return required_partition from execution_surface.json if declared."""
    candidates = [
        surface.get("vc_runtime_contract"),
        surface.get("runtime_context", {}).get("vc_runtime_contract", {}),
        surface.get("resolved_inputs", {}).get("vc_runtime_contract", {}),
    ]
    for contract in candidates:
        if not isinstance(contract, dict):
            continue
        fallback = contract.get("allow_partition_fallback")
        if fallback is True:
            raise ValueError("vc_runtime_contract.allow_partition_fallback must be false")
        partition = contract.get("required_partition")
        if isinstance(partition, str) and partition.strip():
            return partition.strip()
    return None


def resolve_declared_image(surface: dict) -> str | None:
    """Return a Docker image explicitly declared by execution_surface.json."""
    candidates = [
        surface.get("docker_image"),
        surface.get("image"),
        surface.get("runtime_context", {}).get("docker_image", {}),
        surface.get("resolved_inputs", {}).get("docker_image", {}),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _iter_vc_runtime_contracts(surface: dict) -> list[dict]:
    """Return vc_runtime_contract dictionaries from all supported surface locations."""
    candidates = [
        surface.get("vc_runtime_contract"),
        surface.get("runtime_context", {}).get("vc_runtime_contract", {}),
        surface.get("resolved_inputs", {}).get("vc_runtime_contract", {}),
    ]
    return [contract for contract in candidates if isinstance(contract, dict)]


def resolve_container_python_path(surface: dict) -> str | None:
    """Return an explicit container Python path from execution_surface.json if declared."""
    for contract in _iter_vc_runtime_contracts(surface):
        runtime = contract.get("runtime_paths")
        if isinstance(runtime, dict):
            value = runtime.get("container_python_path")
            if isinstance(value, str) and value.strip():
                return value.strip()

        value = contract.get("container_python_path")
        if isinstance(value, str) and value.strip():
            return value.strip()

        value = contract.get("container_venv_path")
        if isinstance(value, str) and value.strip() and value.strip() != "discover_and_verify":
            return f"{value.strip().rstrip('/')}/bin/python"
    return None


def _append_absolute_path(paths: list[str], value: object) -> None:
    if isinstance(value, str) and value.startswith("/") and value not in paths:
        paths.append(value)


def _append_absolute_paths_from_mapping(paths: list[str], value: object) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _append_absolute_paths_from_mapping(paths, item)
    elif isinstance(value, list):
        for item in value:
            _append_absolute_paths_from_mapping(paths, item)
    else:
        _append_absolute_path(paths, value)


def resolve_additional_mount_paths(surface: dict) -> list[str]:
    """Return host paths whose storage roots should be mounted into the vc job."""
    paths: list[str] = []
    for container_name in ("resolved_inputs", "runtime_context"):
        container = surface.get(container_name)
        if not isinstance(container, dict):
            continue
        for key in ("model_dir", "run_dir", "output_dir", "results_dir"):
            _append_absolute_path(paths, container.get(key))

    _append_absolute_paths_from_mapping(paths, surface.get("expected_outputs", {}))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Submit a SURE-EVAL run to the Volcano cluster via vc submit."
    )
    parser.add_argument(
        "surface_json",
        nargs="?",
        type=Path,
        help="Path to execution_surface.json (optional if --model and --run-id are given)",
    )
    parser.add_argument(
        "--model", "-m", type=str, help="Model directory name (e.g. Qwen__Qwen3-ASR-1.7B)"
    )
    parser.add_argument(
        "--run-id", "-r", type=str, help="Run ID under the model's eval_runs directory"
    )
    parser.add_argument(
        "--image", "-i", type=str, default=None, help="Docker image (auto-detected if omitted)"
    )
    parser.add_argument(
        "--partition",
        "-p",
        type=str,
        default=None,
        help="Required GPU partition (defaults to execution surface value, then pdgpu-4090)",
    )
    parser.add_argument(
        "--memory", type=int, default=None, help="Container memory in GB (auto-estimated if omitted)"
    )
    parser.add_argument(
        "--gpus", "-g", type=int, default=1, help="GPUs per task"
    )
    parser.add_argument(
        "--cpus", "-c", type=int, default=4, help="CPUs per task"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the vc submit command without executing"
    )
    parser.add_argument(
        "--wait", action="store_true", help="After submission, poll until the job finishes"
    )

    args = parser.parse_args()

    # Resolve surface_json path
    surface_path: Path | None = args.surface_json
    if surface_path is None:
        if not args.model or not args.run_id:
            parser.error("Either provide <surface_json> or both --model and --run-id")
        surface_path = (
            Path("src/sure_eval/models")
            / args.model
            / "eval_runs"
            / args.run_id
            / "execution_surface.json"
        )

    surface = load_execution_surface(surface_path)
    model_name, run_id, entrypoint_path = resolve_model_and_run(surface)

    # CLI overrides take precedence over surface.json. If neither declares a
    # partition, vc_submitter requires the default pdgpu-4090 partition.
    image = args.image or resolve_declared_image(surface)
    partition = args.partition or resolve_required_partition(surface)
    memory = args.memory
    container_python_path = resolve_container_python_path(surface)
    additional_host_paths = resolve_additional_mount_paths(surface)

    if args.dry_run:
        cmd = build_vc_submit_command(
            model_name=model_name,
            run_id=run_id,
            image=image,
            partition=partition,
            memory_gb=memory,
            gpus=args.gpus,
            cpus=args.cpus,
            entrypoint_path=entrypoint_path,
            additional_host_paths=additional_host_paths,
            container_python_path=container_python_path,
        )
        print(" ".join(cmd))
        return 0

    try:
        job_id = submit_vc_run(
            model_name=model_name,
            run_id=run_id,
            image=image,
            partition=partition,
            memory_gb=memory,
            gpus=args.gpus,
            cpus=args.cpus,
            entrypoint_path=entrypoint_path,
            additional_host_paths=additional_host_paths,
            container_python_path=container_python_path,
        )
    except RuntimeError as exc:
        logger.error("Submission failed", error=str(exc))
        return 1

    print(f"Job submitted: {job_id}")
    print(f"  View logs: vc logs -t {job_id}-master-0")
    print(f"  Follow:    vc logs -t {job_id}-master-0 -f")
    print(f"  Info:      vc info --job {job_id}")

    if args.wait:
        import time

        print("\nWaiting for job to complete...")
        while True:
            info = get_job_info(job_id)
            status = info.get("Status", "").lower()
            if status in {"completed", "succeeded", "failed", "error"}:
                print(f"Job finished with status: {status}")
                break
            time.sleep(10)

    return 0


if __name__ == "__main__":
    sys.exit(main())
