"""Volcano (vc) cluster job submitter for SURE-EVAL model evaluation.

Replaces local ``bash run_evaluation.sh`` with an intelligent ``vc submit``
that auto-selects image, pins the GPU partition, estimates memory, and fixes the .venv symlink
inside the container.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from sure_eval.core.logging import get_logger

logger = get_logger(__name__)

# Fixed prefix for all model images in this project.
IMAGE_REGISTRY_PREFIX = "docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_"

# Main-flow VC submissions default to one partition for reproducibility and to
# avoid model-specific failures caused by silently moving across GPU classes.
DEFAULT_GPU_PARTITION = "pdgpu-4090"

# Default mount: host repo root -> container repo root.
_DEFAULT_VOLUME_MOUNT = "/mnt/cloudstorfs/sjtu_home/junhao.du/sure-eval-sandbox:/workspace/sure-eval"
_HPC_STORAGE_RE = re.compile(r"^/(hpc_stor\d+)(?:/|$)")

# Base memory heuristic per model weight size (GB).
_WEIGHT_MEM_MULTIPLIER = 2.5
_MEM_MIN_GB = 8
_MEM_MAX_GB = 64


def _run_cmd(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a shell command and return the result."""
    logger.debug("Running command", command=" ".join(args))
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=check,
    )


def list_local_images(model_name: str) -> list[str]:
    """Return locally available Docker images matching the model prefix."""
    prefix = f"{IMAGE_REGISTRY_PREFIX}{model_name}"
    result = _run_cmd(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        check=False,
    )
    images: list[str] = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith(prefix):
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


def select_best_image(model_name: str) -> str:
    """Select the newest Docker image tag for *model_name*.

    If multiple local tags exist, pick the one with the highest version tuple.
    Falls back to ``{prefix}{model_name}:latest`` when no local image is found.
    """
    prefix = f"{IMAGE_REGISTRY_PREFIX}{model_name}"
    images = list_local_images(model_name)

    if not images:
        fallback = f"{prefix}:latest"
        logger.warning("No local image found; using fallback", fallback=fallback)
        return fallback

    # Parse each image into (repo, tag, version_tuple)
    parsed: list[tuple[str, str, tuple[int, ...]]] = []
    for img in images:
        if ":" not in img:
            continue
        repo, tag = img.rsplit(":", 1)
        parsed.append((repo, tag, _parse_tag_version(tag)))

    if not parsed:
        fallback = f"{prefix}:latest"
        logger.warning("Could not parse local image tags; using fallback", fallback=fallback)
        return fallback

    # Sort by version tuple descending, then by tag string descending as tie-breaker
    parsed.sort(key=lambda x: (x[2], x[1]), reverse=True)
    best_repo, best_tag, _ = parsed[0]
    best_image = f"{best_repo}:{best_tag}"
    logger.info("Selected best image", model=model_name, image=best_image)
    return best_image


def get_user_partitions() -> set[str]:
    """Parse ``vc info -u`` and return the set of partitions the user may use."""
    result = _run_cmd(["vc", "info", "-u"], check=False)
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
            if stripped:
                partitions.add(stripped)
    return partitions


def get_partition_status() -> dict[str, dict[str, Any]]:
    """Parse ``vc info`` and return a dict of partition -> {gpu_total, gpu_allocated, free}."""
    result = _run_cmd(["vc", "info"], check=False)
    status: dict[str, dict[str, Any]] = {}
    # Example line:
    # pdgpu-4090         | 2/8                  | 16/124               | 64Gi/495.0Gi
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


def select_best_partition(required_partition: str | None = None) -> str:
    """Return the required partition for main-flow VC submissions.

    No fallback is attempted: the explicit required partition is used when
    provided, otherwise the default pdgpu-4090 partition is required.
    """
    partition = required_partition or DEFAULT_GPU_PARTITION
    allowed = get_user_partitions()
    if partition not in allowed:
        visible = ", ".join(sorted(allowed)) or "<none>"
        raise RuntimeError(
            f"Required GPU partition '{partition}' is not available for this user. "
            f"Visible partitions: {visible}."
        )

    logger.info("Selected required partition", partition=partition)
    return partition


def estimate_memory_gb(model_name: str) -> int:
    """Estimate required container memory (``-m``) for *model_name*.

    Reads ``model.spec.yaml`` or ``config.yaml`` under the model directory.
    Heuristic: max(declared_memory, weight_size * 2.5 + 4), clamped to [8, 64].
    """
    model_dir = Path("src/sure_eval/models") / model_name

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


def infer_venv_path(model_name: str) -> str:
    """Infer the virtual-env path inside the Docker image for *model_name*."""
    # The Dockerfile convention used in this project is /opt/{model_name}_venv
    return f"/opt/{model_name}_venv"


def _mount_for_host_path(path: str) -> str | None:
    """Return a storage-root mount entry for a host path, if one is needed."""
    match = _HPC_STORAGE_RE.match(path)
    if not match:
        return None
    root = f"/{match.group(1)}"
    return f"{root}:{root}"


def _compose_volume_mount(
    base_volume_mount: str,
    additional_host_paths: list[str] | None = None,
) -> str:
    """Merge the base vc volume string with storage mounts required by paths."""
    entries = [entry.strip() for entry in base_volume_mount.split(",") if entry.strip()]
    seen = set(entries)

    for path in additional_host_paths or []:
        mount = _mount_for_host_path(path)
        if mount and mount not in seen:
            entries.append(mount)
            seen.add(mount)

    return ",".join(entries)


def build_vc_submit_command(
    model_name: str,
    run_id: str,
    image: str | None = None,
    partition: str | None = None,
    memory_gb: int | None = None,
    gpus: int = 1,
    cpus: int = 4,
    volume_mount: str | None = None,
    additional_host_paths: list[str] | None = None,
    repo_root_in_container: str = "/workspace/sure-eval",
    entrypoint_path: str | None = None,
    container_python_path: str | None = None,
) -> list[str]:
    """Build the ``vc submit`` command as a list of arguments.

    If ``entrypoint_path`` is provided (relative to the repo root, as declared in
    ``execution_surface.json``), it is used as the container-side shell entrypoint.
    Otherwise the default ``eval_runs/<run_id>/run_evaluation.sh`` layout is used.
    """
    image = image or select_best_image(model_name)
    partition = select_best_partition(partition)
    memory_gb = memory_gb or estimate_memory_gb(model_name)
    volume = _compose_volume_mount(
        volume_mount or _DEFAULT_VOLUME_MOUNT,
        additional_host_paths=additional_host_paths,
    )
    venv_path = infer_venv_path(model_name)
    python_bin = container_python_path or f"{venv_path}/bin/python"

    # Path to run_evaluation.sh inside the container
    if entrypoint_path:
        run_sh = f"{repo_root_in_container}/{entrypoint_path}"
    else:
        run_sh = (
            f"{repo_root_in_container}/src/sure_eval/models/{model_name}"
            f"/eval_runs/{run_id}/run_evaluation.sh"
        )
    # Path to .venv inside the container (where we create the symlink)
    venv_link = (
        f"{repo_root_in_container}/src/sure_eval/models/{model_name}/.venv"
    )

    # The inner bash command that fixes the symlink and then runs the script.
    # We cannot use "ln -sfn" blindly because the host .venv may be a real
    # directory (not a symlink).  "ln -sfn" against a directory creates the link
    # *inside* the directory rather than replacing it, so .venv/bin/python still
    # won't exist.  Instead: if .venv is a non-symlink directory, rename it to
    # .venv.hostbak (never delete), then create the symlink.
    if container_python_path:
        inner_cmd = (
            f'export REPO_ROOT={repo_root_in_container}; '
            f'export PYTHON_BIN={python_bin}; '
            f'bash {run_sh}'
        )
    else:
        inner_cmd = (
            f'[ -d {venv_link} -a ! -L {venv_link} ] && mv {venv_link} {venv_link}.hostbak; '
            f'ln -sfn {venv_path} {venv_link}; '
            f'export REPO_ROOT={repo_root_in_container}; '
            f'export PYTHON_BIN={python_bin}; '
            f'bash {run_sh}'
        )

    cmd = [
        "vc",
        "submit",
        "-i", image,
        "-p", partition,
        "-g", str(gpus),
        "-m", f"{memory_gb}G",
        "-c", str(cpus),
        "-n", "1",
        "-j", run_id,
        "-v", volume,
        "--cmd", f'bash -c "{inner_cmd}"',
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
    additional_host_paths: list[str] | None = None,
    container_python_path: str | None = None,
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
        additional_host_paths=additional_host_paths,
        container_python_path=container_python_path,
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
