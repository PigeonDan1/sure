#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from vc_exec import (
    DEFAULT_CPUS,
    DEFAULT_GPUS,
    DEFAULT_MEMORY_GB,
    default_partition,
    diagnose_oom,
    docker_run_to_vc,
    ensure_registry_image,
    recorded_push_digest,
    registry_image,
    run_vc_job,
)

GIB = 1024 ** 3
RAM_SAFETY_FACTOR = 2


PASS_KEYS = {
    "original_inference": "inference_passed",
    "import": "import_passed",
    "load": "load_passed",
    "infer": "infer_passed",
    "contract": "contract_passed",
    "mcp": "mcp_passed",
    "equivalence": "equivalent",
}


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def command_for(value: object) -> tuple[list[str] | str, bool]:
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return value, False
    if isinstance(value, str) and value.strip():
        return value, True
    raise ValueError("validation artifact must contain a non-empty run_command")


def vc_resources(resolved: dict) -> tuple[str, int, int, int]:
    partition = str(resolved.get("vc_partition") or default_partition())
    gpus = int(resolved.get("vc_gpus") or DEFAULT_GPUS)
    memory_gb = int(resolved.get("vc_memory_gb") or DEFAULT_MEMORY_GB)
    return partition, gpus, memory_gb, DEFAULT_CPUS


def model_payload_bytes(run_dir: Path, resolved: dict, kind: str) -> int:
    """Return the model payload size the job will load into RAM.

    ``import`` only imports the wrapper module and never loads weights.
    Prefer the staged manifest total; fall back to a metadata-only walk of
    the supplied model path.
    """
    if kind == "import":
        return 0
    manifest = run_dir / "artifacts" / "model_payload_manifest.json"
    if manifest.is_file():
        try:
            total = read_object(manifest).get("total_bytes")
            if isinstance(total, int):
                return total
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    model_path = str(resolved.get("model_path") or "")
    path = Path(model_path).expanduser()
    if not model_path or not path.is_dir():
        return 0
    return sum(entry.stat().st_size for entry in path.rglob("*") if entry.is_file())


def prepare_container_outputs(spec: object, run_dir: Path) -> None:
    """Clear the directory the container writes its stage output into.

    The target is the mount SURE_VALIDATE_ARTIFACTS_DIR names, which is what
    SKILL.md prescribes and what container_stage_error already reads; guessing
    at /output and /artifacts instead cleared nothing on the documented path and
    would have emptied the run's own artifacts directory on an undocumented one.

    The mounts come from the run_command the agent under test wrote into its
    own <stage>_result.json, so a host path here is untrusted input. Refuse
    anything that resolves outside the run directory, and refuse the run's own
    artifacts directory, which holds gate products rather than container output.
    """
    root = run_dir.resolve()
    target = str((getattr(spec, "env", None) or {}).get("SURE_VALIDATE_ARTIFACTS_DIR", "") or "")
    if not target:
        return
    mounts = getattr(spec, "mounts", ())
    for mount in mounts:
        parts = str(mount).split(":")
        if len(parts) < 2 or parts[1] != target:
            continue
        output_dir = Path(parts[0]).expanduser()
        if not output_dir.is_absolute():
            raise ValueError(f"validation output mount host path must be absolute: {mount!r}")
        resolved = output_dir.resolve()
        if root not in resolved.parents:
            raise ValueError(
                f"validation output mount must stay inside the run directory: {mount!r}"
            )
        if resolved == root / "artifacts":
            raise ValueError(
                f"validation output mount must not target the run artifacts directory: {mount!r}"
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        for child in output_dir.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()


MCP_STEPS = ("initialize", "tools_list", "tools_call")
GPU_OOM_MAX_ATTEMPTS = 8
# Leave the gate room to collect diagnostics and write the stage result after
# the last attempt it is allowed to start.
GATE_BUDGET_RESERVE_SECONDS = 120.0


def gate_budget_seconds() -> float:
    """Wall clock the hook allows this script before it kills it.

    checkpoints.ts spawns the gate with a fixed timeout and exports it here, so
    the two stay in step. Eight OOM retries at the default per-stage timeout run
    four times past that budget; a retry started with no room to finish is
    killed mid-flight, which loses the stage result, orphans the vc job and
    spends a state-machine retry on work that was still running.
    """
    try:
        return max(0.0, float(os.environ.get("SURE_TRANS_GATE_BUDGET_SECONDS", "") or 0.0))
    except ValueError:
        return 0.0


def is_gpu_oom(result: object, stage_error: str = "") -> bool:
    """Decide whether a failed job died of a GPU out-of-memory condition.

    The exit code has to agree. A recovered OutOfMemoryError leaves its
    traceback in the log of a job that goes on to exit 0, and resubmitting that
    job wastes a GPU allocation and then judges the stage on a later attempt.
    stage_error carries what the container wrote to <stage>_result.json, which
    is the only place templates/validate.py records a caught exception, so a
    real OOM that never reached the job log still counts.
    """
    if getattr(result, "exit_code", None) in (0, None):
        return False
    evidence = "\n".join(
        [stage_error] + [
            str(getattr(result, field, "") or "")
            for field in ("stdout", "stderr", "vc_diagnostics")
        ]
    )
    return "cuda out of memory" in evidence.lower()


def validate_mcp_evidence(evidence_path: Path, tool_name: str) -> str | None:
    """Require deterministic mcp_smoke.py protocol evidence for the mcp gate.

    Returns None when the evidence proves initialize/tools/list/tools/call;
    otherwise a repair message. Placeholder run_commands never produce the
    evidence file, so they are rejected here.
    """
    if not evidence_path.is_file():
        return (
            "MCP smoke must be driven by scripts/mcp_smoke.py: run_command must execute "
            "`python /opt/sure_trans/mcp_smoke.py --audio <fixture> --tool <tool_name> "
            f"--produces {evidence_path}` so the gate can verify protocol evidence; "
            "placeholder commands are rejected."
        )
    protocol = read_object(evidence_path)
    if protocol.get("status") != "passed":
        return f"mcp_smoke evidence did not pass: {protocol.get('error') or protocol.get('status')}"
    for step in MCP_STEPS:
        entry = protocol.get(step)
        if not isinstance(entry, dict) or entry.get("ok") is not True:
            return f"mcp_smoke evidence must prove {step} passed"
    call = protocol.get("tools_call") or {}
    if call.get("output_nonempty") is not True and call.get("text_nonempty") is not True:
        return "mcp_smoke evidence must return a non-empty primary output from tools/call"
    smoke_tool = str(protocol.get("tool") or "")
    if tool_name and smoke_tool and smoke_tool != tool_name:
        return f"mcp_smoke tool {smoke_tool!r} does not match declared tool {tool_name!r}"
    return None


EQUIVALENCE_POLICIES = ("exact", "normalized_whitespace")


def output_text(path: Path, primary_field: str) -> str:
    """Pull the comparable text out of a recorded inference output."""
    raw = path.read_text(encoding="utf-8")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        field = value.get(primary_field)
        if isinstance(field, str):
            return field
        raise ValueError(
            f"{path} carries no string {primary_field!r} field to compare; write the adapter "
            f"io_contract primary field into both recorded outputs"
        )
    raise ValueError(f"{path} is neither a string nor an object holding {primary_field!r}")


def adapter_primary_field(run_dir: Path) -> str:
    manifest = run_dir / "artifacts" / "adapter_manifest.json"
    if not manifest.is_file():
        return "text"
    contract = read_object(manifest).get("io_contract")
    if isinstance(contract, dict) and isinstance(contract.get("primary_field"), str):
        return contract["primary_field"]
    return "text"


def compare_equivalence_outputs(run_dir: Path, data: dict) -> tuple[dict | None, str | None]:
    """Decide equivalence by reading both recorded outputs.

    The run_command only proves that something ran: a `/bin/true` command once
    carried this gate to passed while neither output file was ever opened. The
    comparison happens here so the verdict rests on the recorded evidence
    rather than on an exit code the agent chooses.
    """
    policy = str(data.get("comparison_policy") or "normalized_whitespace")
    if policy not in EQUIVALENCE_POLICIES:
        return None, (
            f"comparison_policy must be one of {list(EQUIVALENCE_POLICIES)}; got {policy!r}"
        )
    primary_field = adapter_primary_field(run_dir)
    texts: dict[str, str] = {}
    for key in ("baseline_output", "adapter_output"):
        raw = str(data.get(key) or "")
        path = Path(raw)
        if not raw or not path.is_file():
            return None, (
                f"{key} must be the path of the recorded output file, not the transcript itself; "
                f"got {raw!r}. Point it at the JSON or text file the run wrote."
            )
        try:
            texts[key] = output_text(path, primary_field)
        except ValueError as error:
            return None, str(error)

    def normalized(text: str) -> str:
        return text if policy == "exact" else " ".join(text.split())

    baseline, adapter = texts["baseline_output"], texts["adapter_output"]
    match = normalized(baseline) == normalized(adapter)
    evidence = {
        "policy": policy,
        "primary_field": primary_field,
        "baseline_text": baseline,
        "adapter_text": adapter,
        "match": match,
    }
    if not match:
        return evidence, (
            f"baseline and adapter outputs differ under {policy}: {baseline!r} vs {adapter!r}"
        )
    return evidence, None


def ensure_validation_image(
    run_dir: Path, resolved: dict, kind: str, artifacts: Path
) -> tuple[str, str, str | None]:
    version = str(resolved.get("image_version") or "0.1.0")
    model_name = str(resolved.get("model_name") or "")
    task_type = str(resolved.get("task_type") or "asr")
    delivery = resolved.get("container_delivery")
    if kind == "original_inference":
        registry_ref = (
            str(delivery.get("source_image"))
            if isinstance(delivery, dict) and delivery.get("source_image")
            else registry_image(model_name, version, "source", task_type=task_type)
        )
        return registry_ref, "source_push.log", None
    registry_ref = (
        str(delivery.get("target_image"))
        if isinstance(delivery, dict) and delivery.get("target_image")
        else registry_image(model_name, version, task_type=task_type)
    )
    push_digest: str | None = None
    if kind == "import":
        adapter = read_object(artifacts / "adapter_image_result.json")
        local_image = str(adapter.get("image_id") or adapter.get("target_image") or "")
        if not local_image:
            raise ValueError("adapter image identity is missing")
        push_log = run_dir / "artifacts" / "vc_logs" / "adapter_push.log"
        push_digest = ensure_registry_image(
            local_image,
            registry_ref,
            push_log,
            known_digest=recorded_push_digest(adapter, registry_ref),
        ) or None
        adapter["registry_ref"] = registry_ref
        adapter["registry_push"] = {
            "log_path": str(push_log),
            "digest": push_digest,
            "pushed_at": datetime.now(timezone.utc).isoformat(),
        }
        (artifacts / "adapter_image_result.json").write_text(
            json.dumps(adapter, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return registry_ref, "adapter_push.log", push_digest


def container_stage_error(run_command: object, kind: str) -> str:
    """Reason the container recorded for a failed validation stage.

    templates/validate.py catches every stage exception, writes it to
    <artifacts>/<stage>_result.json in its mounted output directory, and prints
    nothing. The job log therefore carries no error at all, so diagnose_oom
    never saw the CUDA OOM it exists to catch and the gate sent the agent to a
    log with nothing wrong in it. Read the file the container actually wrote.
    """
    try:
        # Only the mounts and env matter here, and resolving the image
        # entrypoint would shell out to docker for a command nobody runs.
        spec = docker_run_to_vc(run_command, resolve_entrypoint=lambda _image: ((), ()))
        target = spec.env.get("SURE_VALIDATE_ARTIFACTS_DIR", "")
        for mount in spec.mounts:
            parts = mount.split(":")
            if len(parts) < 2 or parts[1] != target:
                continue
            result = Path(parts[0]) / f"{kind}_result.json"
            if result.is_file():
                return str(read_object(result).get("error") or "")
    except (OSError, TypeError, ValueError):
        return ""
    return ""


def run_vc_validation(
    run_dir: Path,
    resolved: dict,
    data: dict,
    kind: str,
    artifacts: Path,
    timeout: float,
) -> tuple[int, dict, str]:
    command, _ = command_for(data.get("run_command"))
    spec = docker_run_to_vc(command)
    prepare_container_outputs(spec, run_dir)
    registry_ref, _, _ = ensure_validation_image(run_dir, resolved, kind, artifacts)
    env: dict[str, str] = dict(spec.env)
    for key, value in (data.get("env") or {}).items():
        if isinstance(key, str) and isinstance(value, (str, int, float, bool)):
            env[key] = str(value)
    env["SURE_DEVICE"] = "cuda"
    env["DEVICE"] = "cuda"
    partition, gpus, memory_gb, cpus = vc_resources(resolved)
    payload_bytes = model_payload_bytes(run_dir, resolved, kind)
    required_gb = math.ceil(payload_bytes * RAM_SAFETY_FACTOR / GIB)
    if required_gb > memory_gb:
        raise ValueError(
            f"model payload is {payload_bytes / GIB:.1f} GiB; with {RAM_SAFETY_FACTOR}x loading "
            f"headroom the job needs about {required_gb} GiB RAM but vc_memory_gb={memory_gb} "
            f"is configured. Set vc_memory_gb to at least {required_gb} and adjust vc_gpus if "
            f"required by scheduler policy, then rerun the gate."
        )
    log_dir = run_dir / "artifacts" / "vc_logs" / kind
    attempts: list[dict[str, object]] = []
    result = None
    budget = gate_budget_seconds()
    budget_started = time.monotonic()
    budget_exhausted = False
    for attempt in range(1, GPU_OOM_MAX_ATTEMPTS + 1):
        if attempt > 1 and budget:
            spent = time.monotonic() - budget_started
            if budget - GATE_BUDGET_RESERVE_SECONDS - spent < timeout:
                budget_exhausted = True
                break
        prepare_container_outputs(spec, run_dir)
        attempt_log_dir = log_dir if attempt == 1 else log_dir / f"oom-attempt-{attempt}"
        result = run_vc_job(
            image=registry_ref,
            command=shlex.join(spec.command),
            log_dir=attempt_log_dir,
            mounts=spec.mounts,
            workdir=spec.workdir,
            env=env,
            partition=partition,
            gpus=gpus,
            memory_gb=memory_gb,
            cpus=cpus,
            job_name=f"sure-trans-{resolved.get('model_name')}-{kind}-attempt-{attempt}",
            timeout_seconds=timeout,
        )
        gpu_oom = is_gpu_oom(result, container_stage_error(data.get("run_command"), kind))
        attempts.append({
            "attempt": attempt,
            "job_id": result.job_id,
            "log_path": str(result.log_dir),
            "exit_code": result.exit_code,
            "gpu_oom": gpu_oom,
        })
        if not gpu_oom:
            break
    assert result is not None
    exit_code = -1 if result.exit_code is None else result.exit_code
    extra = {
        "execution_surface": "vc",
        "vc_job_id": result.job_id,
        "vc_partition": partition,
        "vc_memory_gb": memory_gb,
        "vc_gpus": gpus,
        "vc_submit_command": result.submit_command,
        "vc_log_path": str(result.log_dir),
        "vc_timed_out": result.timed_out,
        "registry_ref": registry_ref,
        "vc_diagnostics": result.vc_diagnostics[:4000],
        "vc_attempts": attempts,
        "gpu_oom_attempts": len(attempts) if attempts[-1]["gpu_oom"] else len(attempts) - 1,
        "gpu_oom_retry_exhausted": bool(attempts[-1]["gpu_oom"]),
        "gpu_oom_retry_budget_exhausted": budget_exhausted,
    }
    rendered = (
        f"$ {' '.join(result.submit_command)}\n$ {shlex.join(spec.command)}\n"
        f"{result.stdout}\n{result.stderr}\n"
    )
    return exit_code, extra, rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    parser.add_argument("--kind", choices=tuple(PASS_KEYS), required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    output = Path(args.produces)
    data = read_object(output)
    command, shell = command_for(data.get("run_command"))
    cwd = Path(str(data.get("cwd") or run_dir)).resolve()
    if not cwd.is_dir():
        raise ValueError(f"validation cwd does not exist: {cwd}")
    compat = read_object(run_dir / "artifacts" / "execution_compat.json")
    env = os.environ.copy()
    selected_device = str(compat.get("selected_device") or "")
    if selected_device:
        env["SURE_DEVICE"] = selected_device
        env["DEVICE"] = selected_device
    for key, value in (data.get("env") or {}).items():
        if isinstance(key, str) and isinstance(value, (str, int, float, bool)):
            env[key] = str(value)
    timeout = float(data.get("timeout_seconds") or 1800)
    log_path = Path(str(data.get("log_path") or run_dir / "artifacts" / f"{args.kind}_execution.log"))
    if not log_path.is_absolute():
        log_path = run_dir / "artifacts" / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    extra: dict = {}
    if selected_device == "cuda":
        resolved = read_object(run_dir / "artifacts" / "trans_input_resolved.json")
        exit_code, extra, rendered = run_vc_validation(
            run_dir, resolved, data, args.kind, run_dir / "artifacts", timeout
        )
        log_path.write_text(rendered, encoding="utf-8")
        duration_ms = round((time.monotonic() - started) * 1000, 3)
    else:
        process = subprocess.run(command, shell=shell, cwd=cwd, env=env, check=False, capture_output=True, text=True, timeout=timeout)
        duration_ms = round((time.monotonic() - started) * 1000, 3)
        exit_code = process.returncode
        rendered = command if isinstance(command, str) else " ".join(command)
        log_path.write_text(f"$ {rendered}\n{process.stdout}\n{process.stderr}", encoding="utf-8")
    passed = exit_code == 0
    stage_error = "" if passed else container_stage_error(data.get("run_command"), args.kind)
    evidence = f"{rendered}\n{extra.get('vc_diagnostics', '')}\n{stage_error}"
    hint = "" if passed else (diagnose_oom(exit_code, evidence) or "")
    if not passed and not hint and "permission denied" in evidence.lower():
        hint = (
            "container hit Permission denied writing to a mounted path; the host mount "
            "source is likely owned by another uid. Recreate the empty output dir as your "
            "user (rm the dir and let the gate create it) or point the mount at a "
            "user-owned directory, then rerun the gate."
        )
    if not passed and exit_code == 124 and not hint:
        hint = (
            "container command hit its hard timeout (exit 124); reduce the workload or "
            "raise the command timeout, then rerun the gate."
        )
    if args.kind == "mcp" and selected_device == "cuda":
        evidence_path = run_dir / "artifacts" / "vc_logs" / "mcp" / "mcp_smoke.json"
        mcp_error = validate_mcp_evidence(evidence_path, str(data.get("tool_name") or ""))
        if mcp_error:
            passed = False
            hint = f"{hint} {mcp_error}".strip() if hint else mcp_error
        elif evidence_path.is_file():
            data["protocol"] = read_object(evidence_path)
    if args.kind == "equivalence":
        comparison, equivalence_error = compare_equivalence_outputs(run_dir, data)
        if comparison is not None:
            data["comparison_evidence"] = comparison
        if equivalence_error:
            passed = False
            hint = f"{hint} {equivalence_error}".strip() if hint else equivalence_error
    # A gate that rejects the evidence must not report it as a job failure: the
    # command exited 0 and pointing the agent at the job log sends it to the
    # wrong place.
    failure_text = (
        f"vc job failed or timed out; inspect {log_path}"
        if exit_code != 0
        else f"the command succeeded but the gate rejected its evidence; inspect {log_path}"
    )
    error_text = " ".join(part for part in (failure_text, stage_error, hint) if part)
    data.update({
        "status": "passed" if passed else "failed",
        PASS_KEYS[args.kind]: passed,
        "executed": True,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "log_path": str(log_path),
        "selected_device": selected_device or ("cuda" if "vc_job_id" in extra else "cpu"),
        "error": None if passed else error_text,
        **extra,
    })
    if args.kind == "original_inference":
        data["model_loaded"] = passed
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not passed:
        raise ValueError(f"{args.kind} validation failed: {error_text}")
    print(output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
