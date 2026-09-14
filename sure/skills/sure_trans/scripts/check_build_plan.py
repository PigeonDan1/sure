#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


BACKENDS = {"uv", "conda", "docker"}


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve())
    except ValueError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    plan = read_object(Path(args.produces).resolve())
    artifacts = run_dir / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
    choice = read_object(artifacts / "backend_choice.json")

    backend = str(plan.get("backend") or "")
    package = str(plan.get("package_profile") or "")
    if backend not in BACKENDS:
        raise ValueError(f"BUILD_PLAN gate: unsupported backend={backend!r}")
    if backend != choice.get("backend"):
        raise ValueError("BUILD_PLAN gate: backend disagrees with backend_choice.json")
    if backend != resolved.get("backend_hint"):
        raise ValueError("BUILD_PLAN gate: backend disagrees with the resolved input")
    if package != resolved.get("package_profile") or package != choice.get("package_profile"):
        raise ValueError("BUILD_PLAN gate: package_profile disagrees with resolved input or backend choice")
    if plan.get("model_name") != resolved.get("model_name") or plan.get("model_dir") != resolved.get("model_dir"):
        raise ValueError("BUILD_PLAN gate: model identity disagrees with trans_input_resolved.json")
    if package == "none" and backend != "uv":
        raise ValueError("BUILD_PLAN gate: package=none requires backend=uv")

    source = plan.get("source_runtime")
    if not isinstance(source, dict):
        raise ValueError("BUILD_PLAN gate: source_runtime must be an object")
    mode = source.get("mode")
    if backend == "docker":
        if mode != "container" or resolved.get("source_kind") != "docker":
            raise ValueError("BUILD_PLAN gate: docker backend requires source_runtime.mode=container")
    else:
        if mode not in {"existing-python", "materialize"} or resolved.get("source_kind") != "python":
            raise ValueError(f"BUILD_PLAN gate: {backend} requires an existing or materialized Python runtime")
        dependency_file = source.get("dependency_file")
        if not isinstance(dependency_file, str) or not Path(dependency_file).resolve().is_file():
            raise ValueError(f"BUILD_PLAN gate: {backend} requires an existing dependency_file")
        if mode == "existing-python":
            python_executable = source.get("python_executable")
            if not isinstance(python_executable, str) or not Path(python_executable).resolve().is_file():
                raise ValueError("BUILD_PLAN gate: existing-python requires an existing python_executable")
        else:
            environment_dir = source.get("environment_dir")
            if not isinstance(environment_dir, str) or not inside(Path(environment_dir), run_dir):
                raise ValueError("BUILD_PLAN gate: materialized environment_dir must stay under the run directory")
            if backend == "uv":
                lockfile_output = source.get("lockfile_output")
                if not isinstance(lockfile_output, str) or not inside(Path(lockfile_output), artifacts):
                    raise ValueError("BUILD_PLAN gate: uv materialization needs lockfile_output under run artifacts")

    delivery = plan.get("container_delivery")
    if package == "docker-registry":
        if not isinstance(delivery, dict):
            raise ValueError("BUILD_PLAN gate: docker-registry requires container_delivery")
        resolved_delivery = resolved.get("container_delivery")
        expected_target = resolved_delivery.get("target_image") if isinstance(resolved_delivery, dict) else None
        if not expected_target or delivery.get("target_image") != expected_target:
            raise ValueError("BUILD_PLAN gate: target_image must match the site-resolved target exactly")
        expected_dockerfile = run_dir / "adapter" / "Dockerfile.sure"
        if Path(str(delivery.get("dockerfile_path") or "")).resolve() != expected_dockerfile:
            raise ValueError(f"BUILD_PLAN gate: dockerfile_path must be {expected_dockerfile}")
        if delivery.get("registry_required") is not True:
            raise ValueError("BUILD_PLAN gate: docker-registry requires registry_required=true")
        if delivery.get("model_mount_read_only") is not True or delivery.get("result_mount_separate") is not True:
            raise ValueError("BUILD_PLAN gate: container delivery must use read-only model and separate result mounts")
    elif delivery is not None:
        raise ValueError("BUILD_PLAN gate: package=none must not declare container_delivery")

    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("BUILD_PLAN gate: steps must be a non-empty array")
    text = "\n".join(
        " ".join(str(step.get(key) or "") for key in ("state", "action", "command"))
        for step in steps if isinstance(step, dict)
    ).lower()
    if backend == "uv" and not re.search(r"\buv\b", text):
        raise ValueError("BUILD_PLAN gate: uv plan must include uv environment materialization or validation")
    if backend == "conda" and not re.search(r"\bconda\b", text):
        raise ValueError("BUILD_PLAN gate: conda plan must include conda environment materialization or validation")
    if package == "docker-registry":
        for label, pattern in {
            "Dockerfile": r"dockerfile",
            "image build": r"docker\s+(?:build|buildx)|build.{0,20}image",
            "registry push": r"docker\s+push|push.{0,20}registry",
            "digest pull verification": r"pull.{0,30}(?:digest|sha256)|digest.{0,30}pull",
        }.items():
            if not re.search(pattern, text):
                raise ValueError(f"BUILD_PLAN gate: missing {label} step")
    if plan.get("blockers"):
        raise ValueError("BUILD_PLAN gate: unresolved blockers remain")

    print(f"check_build_plan OK: backend={backend}, package={package}, steps={len(steps)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
