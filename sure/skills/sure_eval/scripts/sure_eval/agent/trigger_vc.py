#!/usr/bin/env python3
"""Trigger a model evaluation run via Volcano (vc submit) from an execution_surface.json.

This is the deterministic hand-off script that the main-flow agent calls
instead of ``bash run_evaluation.sh``.  It reads the execution surface,
auto-resolves image / partition / memory, and submits the job.

Usage::

    python src/sure_eval/agent/trigger_vc.py \
        sure/models/asr_qwen3/eval_runs/main_agent_asr_qwen3_002/execution_surface.json

Or directly by model + run_id (when execution_surface.json is at the
standard location)::

    python src/sure_eval/agent/trigger_vc.py --model asr_qwen3 --run-id main_agent_asr_qwen3_002
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
        "--model", "-m", type=str, help="Model directory name (e.g. asr_qwen3)"
    )
    parser.add_argument(
        "--run-id", "-r", type=str, help="Run ID (e.g. main_agent_asr_qwen3_002)"
    )
    parser.add_argument(
        "--image", "-i", type=str, default=None, help="Docker image (auto-detected if omitted)"
    )
    parser.add_argument(
        "--partition", "-p", type=str, default=None, help="GPU partition (auto-detected if omitted)"
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
            Path("sure/models")
            / args.model
            / "eval_runs"
            / args.run_id
            / "execution_surface.json"
        )

    surface = load_execution_surface(surface_path)
    model_name, run_id, entrypoint_path = resolve_model_and_run(surface)

    # CLI overrides take precedence over surface.json
    image = args.image
    partition = args.partition
    memory = args.memory

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
