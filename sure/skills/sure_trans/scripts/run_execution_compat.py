#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "runtime").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

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
from sure.runtime.execution_bridge import (
    artifact_ref,
    build_receipt,
    build_request,
    capability_evidence,
    digest_json,
    derive_execution_admission_trace,
    snapshot_digest,
    write_contract_bundle,
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


def run_python_probe(python_executable: str) -> tuple[list[str], subprocess.CompletedProcess[str], float]:
    command = [python_executable, "-c", PROBE]
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


_CONTRACT_CONTEXT: dict | None = None


def _start_contract(run_dir: Path, artifacts: Path, resolved: dict, source_image: dict, *, requested: str, source_kind: str, gpu_required: bool) -> None:
    global _CONTRACT_CONTEXT
    input_paths = [path for path in (artifacts / "trans_input_resolved.json", artifacts / "source_image_result.json") if path.is_file()]
    snapshot = snapshot_digest(input_paths)
    inputs = [
        artifact_ref(path, origin="local_staging", source_root=run_dir, reference_snapshot_digest=snapshot, artifact_id=f"input-{index}")
        for index, path in enumerate(input_paths, start=1)
    ]
    runtime_identity = source_image.get("image_id") or source_image.get("lockfile_sha256") or resolved.get("python_executable")
    command = [str(resolved.get("python_executable") or "python3"), "-c", "SURE_TRANS_PROBE"]
    if source_kind == "docker":
        command = ["docker", "run", "--rm", str(source_image.get("image_id") or source_image.get("image") or "<missing-image>")]
    requirements = [
        {"capability_id": "sure.execution.harness-python", "capability_class": "execution_capability", "required": True},
        {"capability_id": "sure.execution.source-runtime", "capability_class": "execution_capability", "required": True},
    ]
    evidence = [
        capability_evidence("sure.execution.harness-python", status="AVAILABLE" if Path(sys.executable).is_file() else "MISSING", details={"executable": sys.executable}),
        capability_evidence(
            "sure.execution.source-runtime",
            status=(
                "AVAILABLE"
                if (source_kind == "docker" and bool(runtime_identity))
                or (source_kind == "python" and Path(str(resolved.get("python_executable") or "")).is_file())
                else "MISSING"
            ),
            details={"source_kind": source_kind},
        ),
    ]
    if source_kind == "docker":
        requirements.append({"capability_id": "sure.execution.docker", "capability_class": "execution_capability", "required": True})
        evidence.append(capability_evidence("sure.execution.docker", status="AVAILABLE" if shutil.which("docker") else "MISSING", details={"executable": "docker"}))
    if gpu_required:
        requirements.append({"capability_id": "sure.execution.gpu", "capability_class": "execution_capability", "required": True})
        evidence.append(capability_evidence("sure.execution.gpu", status="AVAILABLE" if shutil.which("nvidia-smi") else "MISSING", details={"probe": "nvidia-smi"}))
    request = build_request(
        run_id=run_dir.name,
        unit_id="validate_env_compat",
        operation="validation",
        entrypoint={"executable": command[0], "argv": command[1:], "working_directory": str(run_dir)},
        output_root=run_dir,
        subject={
            "bundle_manifest_path": str(Path(str(resolved.get("model_path") or run_dir)).expanduser().resolve()),
            "bundle_digest": resolved.get("model_payload_sha256") or digest_json(resolved),
            "runtime_identity_digest": runtime_identity or digest_json({"source_kind": source_kind}),
            "dataset_identity_digest": snapshot,
        },
        inputs=inputs,
        capability_requirements=requirements,
        runtime_requirements={"requested_device": requested, "source_kind": source_kind, "compatibility_mode": "legacy_views"},
        reference_snapshot_digest=snapshot,
    )
    request_path = artifacts / "execution_request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _CONTRACT_CONTEXT = {
        "run_dir": run_dir,
        "artifacts": artifacts,
        "request": request,
        "evidence": evidence,
        "output": None,
        "log": artifacts / "execution_compat.log",
        "requirements": requirements,
    }


def _finish_contract(*, lifecycle: str, exit_code: int | None, diagnostics: list[dict] | None = None) -> None:
    global _CONTRACT_CONTEXT
    context = _CONTRACT_CONTEXT
    if context is None:
        return
    outputs: list[dict] = []
    for index, path in enumerate((context.get("output"), context.get("log")), start=1):
        if isinstance(path, Path) and path.is_file():
            outputs.append(artifact_ref(path, origin="generated", source_root=context["run_dir"], artifact_id=f"output-{index}"))
    receipt = build_receipt(
        context["request"],
        lifecycle=lifecycle,
        executor_kind=str(context.get("executor_kind") or "python"),
        capability_evidence_values=context["evidence"],
        outputs=outputs,
        exit_code=exit_code if lifecycle != "NOT_STARTED" else None,
        diagnostics=diagnostics or [],
    )
    admission_trace = derive_execution_admission_trace(context["request"], receipt)
    write_contract_bundle(
        context["artifacts"],
        context["request"],
        receipt,
        admission_trace=admission_trace,
        legacy_result=context.get("output"),
    )
    _CONTRACT_CONTEXT = None


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    artifacts = run_dir / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
    source_image = read_object(artifacts / "source_image_result.json")
    source_kind = str(resolved.get("source_kind") or "docker")
    image = str(source_image.get("image_id") or source_image.get("image") or "")
    model_name = str(resolved.get("model_name") or "")
    # Input materialization canonicalizes Transformers aliases to this value.
    model_framework = str(resolved["model_framework"]).strip().lower()
    transformers_required = model_framework == "transformers"
    requested = str(resolved.get("device") or "auto")
    gpu_required = resolved.get("gpu_required") is True or requested == "cuda"
    bf16_required = resolved.get("bf16_required") is True
    version = str(resolved.get("image_version") or "0.1.0")
    task_type = str(resolved.get("task_type") or "asr")
    delivery = resolved.get("container_delivery")

    _start_contract(
        run_dir,
        artifacts,
        resolved,
        source_image,
        requested=requested,
        source_kind=source_kind,
        gpu_required=gpu_required,
    )
    context = _CONTRACT_CONTEXT
    if context is not None:
        context["output"] = Path(args.produces).resolve()
        context["executor_kind"] = "python" if source_kind == "python" else "docker"
    if source_kind == "docker" and not image:
        raise ValueError("source image identity is missing")

    vc_payload: dict = {}
    log_path = artifacts / "execution_compat.log"
    if source_kind == "python":
        command, process, duration_ms = run_python_probe(str(resolved["python_executable"]))
        probe_command = command
        exit_code = process.returncode
        stdout, stderr = process.stdout, process.stderr
        fallback = None
        execution_surface = "local_python"
        log_path.write_text(
            f"$ {' '.join(command[:-1])} <probe>\n{stdout}\n{stderr}", encoding="utf-8"
        )
    elif requested == "cpu":
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
        registry_ref = (
            str(delivery.get("source_image"))
            if isinstance(delivery, dict) and delivery.get("source_image")
            else registry_image(model_name, version, "source", task_type=task_type)
        )
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
        incompatibilities.append("source runtime probe produced no exit code")
    elif exit_code != 0:
        hint = diagnose_oom(exit_code, f"{stdout}\n{stderr}")
        incompatibilities.append(
            f"source runtime probe exited {exit_code}" + (f": {hint}" if hint else "")
        )
    if gpu_required and not cuda_available:
        incompatibilities.append("model requires CUDA but the selected runtime cannot access a GPU")
    if bf16_required and not bf16_supported:
        incompatibilities.append("model requires BF16 but the selected GPU does not report BF16 support")
    if transformers_required and "transformers" not in probe:
        incompatibilities.append("Transformers import failed in the source runtime")
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
    _finish_contract(lifecycle="SUCCEEDED", exit_code=0)
    print(output)
    return 0


def main() -> int:
    try:
        return _main()
    except Exception as error:
        missing = isinstance(error, (FileNotFoundError, OSError)) or "missing" in str(error).lower() or "required" in str(error).lower()
        _finish_contract(
            lifecycle="NOT_STARTED" if missing else "FAILED",
            exit_code=None if missing else 1,
            diagnostics=[{"code": "CAPABILITY_MISSING" if missing else "EXECUTOR_FAILED", "message": str(error)}],
        )
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
