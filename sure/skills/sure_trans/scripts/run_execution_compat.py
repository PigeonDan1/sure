#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from vc_exec import (
    DEFAULT_CPUS,
    DEFAULT_GPUS,
    DEFAULT_MEMORY_GB,
    agent_bin_cleared_env,
    default_partition,
    diagnose_oom,
    ensure_registry_image,
    recorded_push_digest,
    registry_image,
    run_vc_job,
)


PROBE = """import json
try:
 import torch
 result={'python_ok':True,'torch':torch.__version__,'cuda_available':torch.cuda.is_available(),'bf16_supported':bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported())}
except Exception as error:
 result={'python_ok':True,'torch_error':str(error),'cuda_available':False,'bf16_supported':False}
try:
 import transformers
 result['transformers']=transformers.__version__
except Exception as error:
 result['transformers_error']=str(error)
print(json.dumps(result, sort_keys=True))
"""


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def run_probe(image: str, use_gpu: bool) -> tuple[list[str], subprocess.CompletedProcess[str], float]:
    command = ["docker", "run", "--rm"]
    if use_gpu:
        command.extend(["--gpus", "all"])
    command.extend(["--entrypoint", "python", image, "-c", PROBE])
    started = time.monotonic()
    process = subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=180,
        env=agent_bin_cleared_env(),
    )
    return command, process, round((time.monotonic() - started) * 1000, 3)


def parse_probe(stdout: str) -> dict:
    probe: dict = {}
    lines = [line for line in stdout.splitlines() if line.strip()]
    if lines:
        try:
            probe = json.loads(lines[-1])
        except json.JSONDecodeError:
            probe = {}
    return probe if isinstance(probe, dict) else {}


def vc_resources(resolved: dict) -> tuple[str, int, int, int]:
    partition = str(resolved.get("vc_partition") or default_partition())
    gpus = int(resolved.get("vc_gpus") or DEFAULT_GPUS)
    memory_gb = int(resolved.get("vc_memory_gb") or DEFAULT_MEMORY_GB)
    return partition, gpus, memory_gb, DEFAULT_CPUS


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    artifacts = run_dir / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
    source_image = read_object(artifacts / "source_image_result.json")
    image = str(source_image.get("image_id") or source_image.get("image") or "")
    if not image:
        raise ValueError("source image identity is missing")
    model_name = str(resolved.get("model_name") or "")
    # Input materialization canonicalizes Transformers aliases to this value.
    model_framework = str(resolved["model_framework"]).strip().lower()
    transformers_required = model_framework == "transformers"
    requested = str(resolved.get("device") or "auto")
    gpu_required = resolved.get("gpu_required") is True or requested == "cuda"
    bf16_required = resolved.get("bf16_required") is True
    version = str(resolved.get("image_version") or "0.1.0")

    vc_payload: dict = {}
    log_path = artifacts / "execution_compat.log"
    if requested == "cpu":
        command, process, duration_ms = run_probe(image, False)
        probe_command: list[str] = command
        exit_code: int | None = process.returncode
        stdout, stderr = process.stdout, process.stderr
        fallback = None
        execution_surface = "local_docker"
        log_path.write_text(
            f"$ {' '.join(command[:-1])} <probe>\n{stdout}\n{stderr}", encoding="utf-8"
        )
    else:
        registry_ref = registry_image(model_name, version, "source")
        push_log = run_dir / "artifacts" / "vc_logs" / "source_push.log"
        push_digest = ensure_registry_image(
            image,
            registry_ref,
            push_log,
            known_digest=recorded_push_digest(source_image, registry_ref),
        )
        source_image["registry_ref"] = registry_ref
        source_image["registry_push"] = {
            "log_path": str(push_log),
            "digest": push_digest or None,
            "pushed_at": datetime.now(timezone.utc).isoformat(),
        }
        (artifacts / "source_image_result.json").write_text(
            json.dumps(source_image, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        partition, gpus, memory_gb, cpus = vc_resources(resolved)
        log_dir = run_dir / "artifacts" / "vc_logs" / "compat"
        result = run_vc_job(
            image=registry_ref,
            command=shlex.join(["python", "-c", PROBE]),
            log_dir=log_dir,
            partition=partition,
            gpus=gpus,
            memory_gb=memory_gb,
            cpus=cpus,
            job_name=f"sure-trans-{model_name}-compat",
        )
        probe_command = ["python", "-c", PROBE]
        exit_code = result.exit_code
        stdout, stderr = result.stdout, result.stderr
        fallback = None
        duration_ms = result.duration_ms
        if (result.timed_out or exit_code != 0) and requested == "auto" and not gpu_required:
            first_command = ["vc", "submit", "-i", registry_ref, "-p", partition, "--cmd", "bash <probe>"]
            first_exit = result.exit_code
            first_stderr = result.stderr.strip()
            fb_command, fb_process, fb_duration_ms = run_probe(image, False)
            exit_code = fb_process.returncode
            stdout, stderr = fb_process.stdout, fb_process.stderr
            duration_ms += fb_duration_ms
            fallback = {
                "reason": "VC CUDA probe failed; model does not require CUDA, so auto retried on CPU locally.",
                "vc_command": first_command,
                "vc_job_id": result.job_id,
                "vc_exit_code": first_exit,
                "vc_stderr": first_stderr[:4000],
                "cpu_command": fb_command,
                "cpu_exit_code": fb_process.returncode,
            }
            probe_command = fb_command
        execution_surface = "vc"
        vc_payload = {
            "vc_partition": partition,
            "vc_job_id": result.job_id,
            "vc_memory_gb": memory_gb,
            "vc_gpus": gpus,
            "vc_cpus": cpus,
            "vc_submit_command": result.submit_command,
            "vc_log_path": str(result.log_dir),
            "vc_timed_out": result.timed_out,
            "source_registry_ref": registry_ref,
            "push_log_path": str(push_log),
        }
        log_path.write_text(
            f"$ {' '.join(result.submit_command)}\n$ {probe_command}\n{stdout}\n{stderr}\n",
            encoding="utf-8",
        )
    probe = parse_probe(stdout)
    cuda_available = probe.get("cuda_available") is True
    bf16_supported = probe.get("bf16_supported") is True
    selected = "cuda" if cuda_available and requested != "cpu" else "cpu"
    incompatibilities = []
    if requested != "cpu" and vc_payload.get("vc_timed_out"):
        incompatibilities.append("vc GPU probe timed out")
    elif exit_code is None:
        incompatibilities.append("container runtime probe produced no exit code")
    elif exit_code != 0:
        hint = diagnose_oom(exit_code, f"{stdout}\n{stderr}")
        incompatibilities.append(
            f"container runtime probe exited {exit_code}" + (f": {hint}" if hint else "")
        )
    if gpu_required and not cuda_available:
        incompatibilities.append("model requires CUDA but the selected container cannot access a GPU")
    if bf16_required and not bf16_supported:
        incompatibilities.append("model requires BF16 but the selected GPU does not report BF16 support")
    if transformers_required and "transformers" not in probe:
        incompatibilities.append("Transformers import failed in the source image")
    payload = {
        "schema": "sure.trans.execution_compat.v1",
        "status": "ready" if not incompatibilities else "blocked",
        "compat_ok": not incompatibilities,
        "execution_surface": execution_surface,
        "requested_device": requested,
        "model_framework": model_framework,
        "transformers_required": transformers_required,
        "selected_device": selected,
        "gpu_required": gpu_required,
        "bf16_required": bf16_required,
        "cuda_available": cuda_available,
        "bf16_supported": bf16_supported,
        "probe": probe,
        "probe_command": probe_command,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "log_path": str(log_path),
        "incompatibilities": incompatibilities,
        "fallback": fallback,
        **vc_payload,
    }
    output = Path(args.produces)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if incompatibilities:
        raise ValueError("; ".join(incompatibilities))
    print(output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
