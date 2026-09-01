"""Volcano (vc) cluster job submitter for SURE-EVAL model evaluation.

Replaces local ``bash run_evaluation.sh`` with an intelligent ``vc submit``
that auto-selects image, GPU partition, memory, and fixes the .venv symlink
inside the container.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from sure_eval.core.logging import get_logger

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.site.loader import load_site_policy

logger = get_logger(__name__)

IMAGE_REGISTRY_PREFIX_ENV = "SURE_VC_IMAGE_REGISTRY_PREFIX"

# GPU partition priority is supplied by the site policy.
def _gpu_priority() -> dict[str, int]:
    resolved = load_site_policy(required=True)
    configured = (resolved or {}).get("policy", {}).get("execution", {}).get("vc_partition_priority", {})
    return {str(name): int(value) for name, value in configured.items()}

# Default mount is inferred from the current checkout.  Keep this module free
# of user-specific repository paths: harness deployments may live beside a
# legacy sandbox checkout and rely on symlinks to shared model assets.

# Base memory heuristic per model weight size (GB).
_WEIGHT_MEM_MULTIPLIER = 2.5
_MEM_MIN_GB = 8
_MEM_MAX_GB = 64


def _run_cmd(
    args: list[str], check: bool = True, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a shell command and return the result."""
    logger.debug("Running command", command=" ".join(args))
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def _image_repo(model_name: str) -> str:
    """Return the Docker repository name for *model_name*.

    Docker repositories are lowercase.  Deployments that rely on automatic
    image selection must provide the project image prefix through the
    SURE_VC_IMAGE_REGISTRY_PREFIX environment variable.
    """
    prefix = _image_registry_prefix()
    if not prefix:
        raise RuntimeError(f"{IMAGE_REGISTRY_PREFIX_ENV} is required for automatic image selection")
    return f"{prefix}{model_name}".lower()


def _image_registry_prefix() -> str:
    return os.environ.get(IMAGE_REGISTRY_PREFIX_ENV, "").strip().lower()


def _infer_repo_root() -> Path:
    """Infer the harness/SURE repository root from cwd or this file path."""
    candidates = [Path.cwd().resolve(), Path(__file__).resolve()]
    for candidate in candidates:
        start = candidate if candidate.is_dir() else candidate.parent
        for parent in (start, *start.parents):
            if (parent / "sure" / "skills" / "sure_eval").exists() and (
                parent / "sure" / "models"
            ).exists():
                return parent
    return Path.cwd().resolve()


def _infer_default_volume_mount(repo_root: Path) -> str:
    """Use an identity mount for the current checkout by default."""
    return f"{repo_root}:{repo_root}"


def _merge_volume_mounts(primary: str, extra_roots: list[str] | tuple[str, ...]) -> str:
    """Append identity mounts for roots not covered by the primary volume."""
    mounts = [primary]
    covered = [PurePosixPath(volume.split(":", 1)[0]) for volume in primary.split(",") if volume.strip()]
    for root in extra_roots:
        value = str(root or "").strip()
        if not value:
            continue
        candidate = PurePosixPath(value)
        if any(candidate.is_relative_to(host) for host in covered):
            continue
        mounts.append(f"{value}:{value}")
        covered.append(candidate)
    return ",".join(mounts)


def _translate_to_container_path(path: Path, volume_mount: str) -> str:
    """Translate a host path into the container path implied by a vc volume."""
    volume = volume_mount.split(",", 1)[0]
    try:
        host_raw, container_raw = volume.split(":", 1)
    except ValueError:
        return str(path)
    host = Path(host_raw).resolve()
    container = Path(container_raw)
    try:
        rel = path.resolve().relative_to(host)
    except ValueError:
        return str(path)
    return str(container / rel)


def _container_path_for_entrypoint(entrypoint_path: str, repo_root_in_container: str, volume_mount: str) -> str:
    """Return the container-side path for a materialized execution entrypoint."""
    path = Path(entrypoint_path)
    if path.is_absolute():
        return _translate_to_container_path(path, volume_mount)
    return str(Path(repo_root_in_container) / entrypoint_path)


def _container_path_for_optional(path_value: str | None, volume_mount: str) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    if path.is_absolute():
        return _translate_to_container_path(path, volume_mount)
    return path_value


def list_local_images(model_name: str) -> list[str]:
    """Return locally available Docker images matching the model prefix."""
    prefix = _image_repo(model_name)
    result = _run_cmd(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        check=False,
    )
    images: list[str] = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line.lower().startswith(prefix):
            images.append(line)
    return images


def _parse_tag_version(tag: str) -> tuple[int, ...]:
    """Extract version tuple from a tag like 'v1.2.3' -> (1, 2, 3)."""
    # Remove leading 'v' and split by '.'
    body = tag.lstrip("v")
    parts = re.split(r"[._]", body)
    nums: list[int] = []
    for p in parts:
        m = re.search(r"\d+", p)
        if m:
            nums.append(int(m.group()))
    if not nums:
        return (0,)
    return tuple(nums)


def _local_sure_images() -> list[str]:
    prefix = _image_registry_prefix()
    if not prefix:
        return []
    result = _run_cmd(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        check=False,
    )
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().lower().startswith(prefix)
    ]


def select_best_image(model_name: str) -> str:
    """Select the newest local Docker image tag for *model_name*.

    Raises RuntimeError when no local image matches: the registry cannot be
    listed from the submit host, so guessing a tag would only fail later at
    ``vc submit`` with the whole pipeline already spent.
    """
    prefix = _image_repo(model_name)
    images = list_local_images(model_name)

    parsed: list[tuple[str, str, tuple[int, ...]]] = []
    for img in images:
        if ":" not in img:
            continue
        repo, tag = img.rsplit(":", 1)
        parsed.append((repo, tag, _parse_tag_version(tag)))

    if not parsed:
        available = ", ".join(_local_sure_images()) or "none"
        raise RuntimeError(
            f"no local Docker image matches {prefix}; refusing to guess a registry tag. "
            f"Declare the image explicitly (runtime.vc.image or --image) or load one locally. "
            f"Local SURE images: {available}"
        )

    # Sort by version tuple descending, then by tag string descending as tie-breaker
    parsed.sort(key=lambda x: (x[2], x[1]), reverse=True)
    best_repo, best_tag, _ = parsed[0]
    best_image = f"{best_repo}:{best_tag}"
    logger.info("Selected best image", model=model_name, image=best_image)
    return best_image


def get_user_partitions(timeout: float | None = None) -> set[str]:
    """Parse ``vc info -u`` and return the set of partitions the user may use."""
    result = _run_cmd(["vc", "info", "-u"], check=False, timeout=timeout)
    partitions: set[str] = set()
    in_partition_block = False
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped == "[Partition]":
            in_partition_block = True
            continue
        if in_partition_block:
            if stripped.startswith("["):
                break
            # `vc info -u` prints a dashed separator between blocks; it is
            # not a partition name.
            if stripped and stripped.strip("-"):
                partitions.add(stripped)
    return partitions


def get_partition_status() -> dict[str, dict[str, Any]]:
    """Parse ``vc info`` and return a dict of partition -> {gpu_total, gpu_allocated, free}."""
    result = _run_cmd(["vc", "info"], check=False)
    status: dict[str, dict[str, Any]] = {}
    # Example line:
    # gpu-standard       | 2/8                  | 16/124               | 64Gi/495.0Gi
    pattern = re.compile(
        r"^(\S+)\s+\|\s+(\d+)/(\d+)\s+\|\s+(\d+)/(\d+)\s+\|\s+([\d.]+)Gi/([\d.]+)Gi"
    )
    for line in result.stdout.splitlines():
        m = pattern.match(line.strip())
        if m:
            name = m.group(1)
            allocated = int(m.group(2))
            total = int(m.group(3))
            status[name] = {
                "gpu_allocated": allocated,
                "gpu_total": total,
                "gpu_free": total - allocated,
                "cpu_allocated": int(m.group(4)),
                "cpu_total": int(m.group(5)),
                "mem_allocated_gb": float(m.group(6)),
                "mem_total_gb": float(m.group(7)),
            }
    return status


def select_best_partition() -> str:
    """Pick the best partition the user has access to that also has free GPUs.

    Priority order comes from the active site policy; unknown partitions have priority zero.
    """
    priority = _gpu_priority()
    allowed = get_user_partitions()
    status = get_partition_status()

    candidates: list[tuple[int, str]] = []
    for name, info in status.items():
        if name not in allowed:
            continue
        if info["gpu_free"] <= 0:
            continue
        candidates.append((priority.get(name, 0), name))

    if not candidates:
        # No partition with free GPUs; fall back to the highest-priority allowed partition
        # and let vc queue the job.
        for name in sorted(allowed, key=lambda n: priority.get(n, 0), reverse=True):
            candidates.append((priority.get(name, 0), name))

    if not candidates:
        raise RuntimeError("No GPU partitions available for this user.")

    candidates.sort(reverse=True)
    best = candidates[0][1]
    logger.info("Selected best partition", partition=best)
    return best


def estimate_memory_gb(model_name: str) -> int:
    """Estimate required container memory (``-m``) for *model_name*.

    Reads ``model.spec.yaml`` or ``config.yaml`` under the model directory.
    Heuristic: max(declared_memory, weight_size * 2.5 + 4), clamped to [8, 64].
    """
    model_dir = Path("sure/models") / model_name

    declared_mem: int | None = None
    weight_size: float | None = None

    # Try model.spec.yaml first (more detailed)
    spec_path = model_dir / "model.spec.yaml"
    if spec_path.exists():
        try:
            import yaml

            data = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            declared_mem = data.get("resources", {}).get("memory_gb")
            weight_size = data.get("weights", {}).get("size_gb")
        except Exception:
            pass

    # Fallback to config.yaml
    if declared_mem is None:
        config_path = model_dir / "config.yaml"
        if config_path.exists():
            try:
                import yaml

                data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                declared_mem = data.get("resources", {}).get("memory_gb")
            except Exception:
                pass

    if weight_size is not None:
        heuristic = int(weight_size * _WEIGHT_MEM_MULTIPLIER + 4)
    else:
        heuristic = _MEM_MIN_GB

    if declared_mem is not None:
        mem = max(declared_mem, heuristic)
    else:
        mem = heuristic

    mem = max(_MEM_MIN_GB, min(_MEM_MAX_GB, mem))
    logger.info("Estimated memory", model=model_name, memory_gb=mem)
    return mem


def _runtime_model_stem(model_name: str) -> str:
    """Return the model stem used by model Docker virtualenv paths."""
    model_dir = Path("sure/models") / model_name
    spec_path = model_dir / "model.spec.yaml"
    runtime_name = ""
    if spec_path.exists():
        try:
            import yaml

            data = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            runtime_name = str(data.get("model_name") or "")
        except Exception:
            runtime_name = ""
    if not runtime_name:
        runtime_name = model_name.split("__", 1)[-1]
    stem = re.sub(r"[^a-z0-9]+", "_", runtime_name.lower()).strip("_")
    return re.sub(r"_+", "_", stem) or model_name.lower()


def infer_venv_path(model_name: str) -> str:
    """Infer the virtual-env path inside the Docker image for *model_name*."""
    # The Dockerfile convention used in this project is
    # /opt/<normalized runtime model name>_venv, e.g.
    # /opt/qwen3_tts_12hz_1_7b_base_venv.
    return f"/opt/{_runtime_model_stem(model_name)}_venv"


def build_vc_submit_command(
    model_name: str,
    run_id: str,
    image: str | None = None,
    partition: str | None = None,
    memory_gb: int | None = None,
    gpus: int = 1,
    cpus: int = 4,
    volume_mount: str | None = None,
    repo_root_in_container: str | None = None,
    entrypoint_path: str | None = None,
    log_path: str | None = None,
    execution_requested: str = "vc",
    device_request: str = "auto",
    device_actual: str = "cuda:0",
    harness_python_bin: str | None = None,
    job_name: str | None = None,
) -> list[str]:
    """Build the ``vc submit`` command as a list of arguments.

    If ``entrypoint_path`` is provided (relative to the repo root, as declared in
    ``execution_surface.json``), it is used as the container-side shell entrypoint.
    Otherwise the default ``eval_runs/<run_id>/run_evaluation.sh`` layout is used.
    """
    image = image or select_best_image(model_name)
    partition = partition or select_best_partition()
    memory_gb = memory_gb or estimate_memory_gb(model_name)
    host_repo_root = _infer_repo_root()
    volume = volume_mount or _infer_default_volume_mount(host_repo_root)
    if repo_root_in_container is None:
        repo_root_in_container = _translate_to_container_path(host_repo_root, volume)
    venv_path = infer_venv_path(model_name)

    if entrypoint_path:
        run_sh = _container_path_for_entrypoint(entrypoint_path, repo_root_in_container, volume)
    else:
        run_sh = (
            f"{repo_root_in_container}/sure/models/{model_name}"
            f"/eval_runs/{run_id}/run_evaluation.sh"
        )
    log_path_in_container = _container_path_for_optional(log_path, volume)
    # Path to .venv inside the container (where we create the symlink)
    venv_link = (
        f"{repo_root_in_container}/sure/models/{model_name}/.venv"
    )

    # The inner bash command that fixes the symlink and then runs the script.
    # We cannot use "ln -sfn" blindly because the host .venv may be a real
    # directory (not a symlink).  "ln -sfn" against a directory creates the link
    # *inside* the directory rather than replacing it, so .venv/bin/python still
    # won't exist.  Instead: if .venv is a non-symlink directory, rename it to
    # .venv.hostbak (never delete), then create the symlink.
    q = shlex.quote
    inner_parts = [
        "set -e",
        (
            f"if [ -d {q(venv_link)} ] && [ ! -L {q(venv_link)} ]; then "
            f"mv {q(venv_link)} {q(venv_link)}.hostbak.$(date +%s); "
            "fi"
        ),
        f"ln -sfn {q(venv_path)} {q(venv_link)}",
        f"export REPO_ROOT={q(repo_root_in_container)}",
        f"export PYTHON_BIN={q(f'{venv_path}/bin/python')}",
        "export SURE_EVAL_EXECUTION_PATH=vc_submit",
        f"export SURE_EVAL_EXECUTION_REQUESTED={q(execution_requested or 'vc')}",
        "export SURE_EVAL_EXECUTION_JOB_ID=${VC_JOB_ID:-}",
        f"export SURE_EVAL_DEVICE_REQUEST={q(device_request or 'auto')}",
        f"export SURE_EVAL_DEVICE_ACTUAL={q(device_actual or 'cuda:0')}",
        f"export DEVICE={q(device_actual or 'cuda:0')}",
    ]
    if harness_python_bin:
        inner_parts.append(f"export HARNESS_PYTHON_BIN={q(harness_python_bin)}")
    if log_path_in_container:
        inner_parts.extend(
            [
                f"mkdir -p {q(str(Path(log_path_in_container).parent))}",
                f"bash {q(run_sh)} > {q(log_path_in_container)} 2>&1",
            ]
        )
    else:
        inner_parts.append(f"bash {q(run_sh)}")
    inner_cmd = "; ".join(inner_parts)
    # vc submit has historically behaved more reliably with the command in a
    # simple `bash -c "..."` form than with a nested single-quoted `bash -lc`.
    # Keep paths shell-quoted inside the command, then escape only double quotes
    # for the outer bash -c argument.
    escaped_inner_cmd = inner_cmd.replace('"', '\\"')

    cmd = [
        "vc",
        "submit",
        "-i", image,
        "-p", partition,
        "-g", str(gpus),
        "-m", f"{memory_gb}G",
        "-c", str(cpus),
        "-n", "1",
        "-j", job_name or run_id,
        "-v", volume,
        "--cmd", f'bash -c "{escaped_inner_cmd}"',
    ]
    return cmd


def submit_vc_run(
    model_name: str,
    run_id: str,
    image: str | None = None,
    partition: str | None = None,
    memory_gb: int | None = None,
    gpus: int = 1,
    cpus: int = 4,
    entrypoint_path: str | None = None,
    log_path: str | None = None,
    execution_requested: str = "vc",
    device_request: str = "auto",
    device_actual: str = "cuda:0",
    harness_python_bin: str | None = None,
    job_name: str | None = None,
) -> str:
    """Submit a model evaluation run to the Volcano cluster via ``vc submit``.

    Returns the job ID printed by ``vc submit``.
    """
    cmd = build_vc_submit_command(
        model_name=model_name,
        run_id=run_id,
        image=image,
        partition=partition,
        memory_gb=memory_gb,
        gpus=gpus,
        cpus=cpus,
        entrypoint_path=entrypoint_path,
        log_path=log_path,
        execution_requested=execution_requested,
        device_request=device_request,
        device_actual=device_actual,
        harness_python_bin=harness_python_bin,
        job_name=job_name,
    )

    logger.info("Submitting vc job", command=" ".join(cmd))
    result = _run_cmd(cmd, check=False)

    if result.returncode != 0:
        logger.error("vc submit failed", stderr=result.stderr, stdout=result.stdout)
        raise RuntimeError(f"vc submit failed: {result.stderr or result.stdout}")

    job_id = result.stdout.strip()
    if not job_id:
        raise RuntimeError("vc submit succeeded but returned empty job ID")

    logger.info("vc job submitted", job_id=job_id)
    return job_id


def get_job_logs(job_id: str, task_suffix: str = "master-0", lines: int | None = None) -> str:
    """Fetch logs for a submitted job."""
    task_name = f"{job_id}-{task_suffix}"
    cmd = ["vc", "logs", "-t", task_name]
    if lines is not None:
        cmd += ["-l", str(lines)]
    result = _run_cmd(cmd, check=False)
    return result.stdout or result.stderr


def get_job_info(job_id: str) -> dict[str, Any]:
    """Fetch structured info for a job."""
    result = _run_cmd(["vc", "info", "--job", job_id], check=False)
    info: dict[str, Any] = {"raw": result.stdout}
    # Parse key: value lines
    for line in result.stdout.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            info[key.strip()] = val.strip()
    return info
