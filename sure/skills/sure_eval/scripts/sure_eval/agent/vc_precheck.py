"""Pre-submission readiness checks for vc submit.

Image existence is probed through vc itself: the server validates the image
before the queue, so a submission against a queue that cannot exist proves
image existence from the error wording without ever creating a job.
"""

from __future__ import annotations

import re
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath

IMAGE_REGISTRY_PREFIX_ENV = "SURE_VC_IMAGE_REGISTRY_PREFIX"
PROBE_QUEUE = "sure-precheck-nonexistent-queue"
IMAGE_MISSING_MARKER = "镜像不存在"
PARTITION_MISSING_MARKER = "partition not found"
CODE_DERIVED_IMAGE_SOURCES = {"auto_select_best_image", "approved_deployment_binding"}
MAX_GPUS = 32
MEMORY_PATTERN = re.compile(r"^[1-9]\d*G$")


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    candidates: list[str] = field(default_factory=list)


def _run(args, timeout, run=subprocess.run):
    try:
        return run(args, capture_output=True, text=True, check=False, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def list_local_sure_images(run=subprocess.run):
    prefix = os.environ.get(IMAGE_REGISTRY_PREFIX_ENV, "").strip().lower()
    if not prefix:
        return []
    result = _run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], 30, run)
    if result is None or result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip().lower().startswith(prefix)]


def image_is_local(image, run=subprocess.run):
    result = _run(["docker", "image", "inspect", "--format", "{{.Id}}", image], 30, run)
    if result is None or result.returncode != 0:
        return False
    return result.stdout.strip().startswith("sha256:")


def probe_registry_image(image, run=subprocess.run):
    result = _run(
        [
            "vc", "submit", "-p", PROBE_QUEUE, "-i", image,
            "-j", "sure-precheck-probe", "-n", "1", "-c", "1",
            "-m", "4G", "-g", "0", "--cmd", "true",
        ],
        90,
        run,
    )
    if result is None:
        return "unknown"
    text = (result.stdout or "") + (result.stderr or "")
    if PARTITION_MISSING_MARKER in text:
        return "exists"
    if IMAGE_MISSING_MARKER in text:
        return "missing"
    return "unknown"


def check_image(image, image_source, run=subprocess.run):
    if image_is_local(image, run):
        return CheckResult("image", "pass", f"{image} is available locally")
    code_derived = image_source in CODE_DERIVED_IMAGE_SOURCES
    verdict = probe_registry_image(image, run)
    if verdict == "exists":
        return CheckResult("image", "pass", f"{image} exists in the registry (not local, venv layout unverified)")
    candidates = list_local_sure_images(run)
    if verdict == "missing":
        if code_derived:
            return CheckResult("image", "fail", f"{image} was derived automatically and does not exist according to vc", candidates)
        return CheckResult("image", "warn", f"{image} does not exist according to vc; submission is expected to fail", candidates)
    if code_derived:
        return CheckResult("image", "fail", f"{image} was derived automatically and vc's probe reply was not recognized", candidates)
    return CheckResult("image", "warn", f"vc's probe reply was not recognized; {image} is unverified", candidates)


def get_user_partitions(run=subprocess.run):
    result = _run(["vc", "info", "-u"], 30, run)
    if result is None or result.returncode != 0:
        return set()
    partitions = set()
    in_block = False
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped == "[Partition]":
            in_block = True
            continue
        if in_block:
            if stripped.startswith("["):
                break
            if stripped and stripped.strip("-"):
                partitions.add(stripped)
    return partitions


def check_partition(partition, run=subprocess.run):
    allowed = get_user_partitions(run)
    if not allowed:
        return CheckResult("partition", "warn", "could not list user partitions through `vc info -u`")
    if partition in allowed:
        return CheckResult("partition", "pass", f"{partition} is available to this user")
    return CheckResult("partition", "fail", f"{partition} is not among this user's partitions", sorted(allowed))


def check_paths(paths, volume_mount):
    host_roots = []
    for volume in str(volume_mount).split(","):
        host = volume.split(":", 1)[0].strip()
        if host:
            host_roots.append(PurePosixPath(host))
    outside = []
    for value in paths:
        if not value:
            continue
        candidate = PurePosixPath(str(value))
        if not any(candidate.is_relative_to(root) for root in host_roots):
            outside.append(str(value))
    if outside:
        return CheckResult(
            "paths", "fail",
            "paths invisible inside the container (outside the vc volumes): " + ", ".join(outside),
            [str(root) for root in host_roots],
        )
    return CheckResult("paths", "pass", "all paths are under " + ", ".join(str(root) for root in host_roots))


def check_venv(image, expected_venv, run=subprocess.run):
    if not expected_venv:
        return CheckResult("venv", "skip", "deployment binding does not use a host/model .venv contract")
    if not image_is_local(image, run):
        return CheckResult("venv", "skip", f"{image} is not available locally; venv layout not verified")
    result = _run(["docker", "run", "--rm", "--entrypoint", "sh", image, "-c", "ls -d /opt/*"], 120, run)
    if result is None or result.returncode != 0:
        return CheckResult("venv", "warn", f"could not list /opt inside {image}")
    entries = [line.strip() for line in result.stdout.splitlines() if line.strip().startswith("/opt/")]
    if not entries:
        return CheckResult("venv", "warn", f"could not list /opt inside {image}")
    if expected_venv in entries:
        return CheckResult("venv", "pass", f"{expected_venv} exists inside {image}")
    return CheckResult("venv", "fail", f"{expected_venv} does not exist inside {image}", entries)


def check_resources(memory, gpus, cpus):
    problems = []
    if not MEMORY_PATTERN.match(str(memory)):
        problems.append(f"memory must look like 16G, got: {memory}")
    if not (isinstance(gpus, int) and 0 <= gpus <= MAX_GPUS):
        problems.append(f"gpus must be an integer between 0 and {MAX_GPUS}, got: {gpus}")
    if not (isinstance(cpus, int) and cpus >= 1):
        problems.append(f"cpus must be a positive integer, got: {cpus}")
    if problems:
        return CheckResult("resources", "fail", "; ".join(problems), ["-m 16G -g 1 -c 4"])
    return CheckResult("resources", "pass", f"memory={memory} gpus={gpus} cpus={cpus}")


def run_precheck(*, image, image_source, partition, memory, gpus, cpus, volume_mount, paths, expected_venv=None, run=subprocess.run, which=shutil.which):
    results = []
    if which("vc"):
        results.append(check_image(image, image_source, run))
        results.append(check_partition(partition, run))
    else:
        results.append(CheckResult("image", "skip", "vc is not on PATH; image existence not verified"))
        results.append(CheckResult("partition", "skip", "vc is not on PATH; partition not verified"))
    results.append(check_paths(paths, volume_mount))
    results.append(check_venv(image, expected_venv, run))
    results.append(check_resources(memory, gpus, cpus))
    return results


def precheck_passed(results):
    return all(result.status != "fail" for result in results)


def format_report(results):
    lines = []
    for result in results:
        lines.append(f"[{result.status.upper()}] {result.name}: {result.detail}")
        if result.candidates:
            lines.append("    candidates: " + ", ".join(result.candidates))
    return "\n".join(lines)


def as_payload(results):
    return {"passed": precheck_passed(results), "checks": [asdict(result) for result in results]}
