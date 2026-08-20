#!/usr/bin/env python3
"""Gate an executable local adaptation and Docker delivery plan."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BACKENDS = {"uv", "pip", "conda", "pixi", "docker", "api"}
PACKAGE_PROFILES = {"none", "docker-local", "docker-registry"}
FORBIDDEN_CORE_STAGE_TOKENS = ("vc_submit", "vc_validate", "hpc_submit", "hpc_validate")


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must be a JSON object.")
    return data


def step_text(step: object) -> str:
    if isinstance(step, dict):
        parts = [str(step.get(key, "")) for key in ("state", "action", "command", "name", "description", "commands")]
        return " ".join(parts).lower()
    return str(step).lower()


def has_auditable_step_shape(step: dict) -> bool:
    if step.get("state") and step.get("action"):
        return True
    has_label = bool(step.get("name") or step.get("description"))
    commands = step.get("commands")
    has_commands = isinstance(commands, list) and any(str(command).strip() for command in commands)
    return has_label and has_commands


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()

    path = Path(args.produces)
    if not path.exists():
        print(f"build_plan.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = load_json(path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    missing = [field for field in ("model_id", "model_dir", "backend", "steps", "package_profile") if not data.get(field)]
    if missing:
        print("BUILD_PLAN gate: missing required fields: " + ", ".join(missing), file=sys.stderr)
        return 1

    if data.get("backend") not in BACKENDS:
        print(f"BUILD_PLAN gate: unsupported backend={data.get('backend')!r}", file=sys.stderr)
        return 1
    package_profile = data.get("package_profile")
    if package_profile not in PACKAGE_PROFILES:
        print(f"BUILD_PLAN gate: unsupported package_profile={data.get('package_profile')!r}", file=sys.stderr)
        return 1

    resolved_path = Path(args.run_dir).expanduser() / "artifacts" / "model_input_resolved.json"
    try:
        resolved = load_json(resolved_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"BUILD_PLAN gate: cannot read model_input_resolved.json: {exc}", file=sys.stderr)
        return 1
    deployment_type = data.get("deployment_type") or resolved.get("deployment_type")
    if package_profile != resolved.get("package_profile"):
        print("BUILD_PLAN gate: package_profile disagrees with model_input_resolved.json.", file=sys.stderr)
        return 1

    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        print("BUILD_PLAN gate: steps must be a non-empty array.", file=sys.stderr)
        return 1

    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            print(f"BUILD_PLAN gate: step #{index} must be an object.", file=sys.stderr)
            return 1
        if not has_auditable_step_shape(step):
            print(
                f"BUILD_PLAN gate: step #{index} must include either state/action "
                "or name/description plus non-empty commands.",
                file=sys.stderr,
            )
            return 1
        lowered = step_text(step)
        if any(token in lowered for token in FORBIDDEN_CORE_STAGE_TOKENS):
            print(
                "BUILD_PLAN gate: VC/HPC submission is not part of core /sure_onboard. "
                "Move VC deployment to a future deployment plugin or optional command.",
                file=sys.stderr,
            )
            return 1

    if deployment_type == "local" and package_profile in {"docker-local", "docker-registry"}:
        delivery = data.get("container_delivery") if isinstance(data.get("container_delivery"), dict) else {}
        missing_delivery = [
            key for key in ("dockerfile_path", "target_image") if not delivery.get(key)
        ]
        if missing_delivery:
            print(
                "BUILD_PLAN gate: local Docker delivery is missing container_delivery fields: "
                + ", ".join(missing_delivery),
                file=sys.stderr,
            )
            return 1
        if package_profile == "docker-registry" and delivery.get("registry_required") is not True:
            print("BUILD_PLAN gate: docker-registry requires container_delivery.registry_required=true.", file=sys.stderr)
            return 1
        if delivery.get("model_mount_read_only") is not True or delivery.get("result_mount_separate") is not True:
            print(
                "BUILD_PLAN gate: container delivery must use a read-only model mount and a separate result mount.",
                file=sys.stderr,
            )
            return 1
        plan_text = "\n".join(step_text(step) for step in steps)
        requirements = {
            "Dockerfile adaptation": r"dockerfile",
            "target image build": r"docker\s+build|buildx\s+build",
            "container validation": r"docker\s+run|container.{0,30}validat|docker.{0,30}validat",
        }
        if package_profile == "docker-registry":
            requirements.update(
                {
                    "registry push": r"docker\s+push|registry.{0,30}push",
                    "digest pull verification": r"pull.{0,30}(digest|sha256)|digest.{0,30}pull",
                }
            )
        missing_steps = [label for label, pattern in requirements.items() if not re.search(pattern, plan_text)]
        if missing_steps:
            print("BUILD_PLAN gate: missing required Docker steps: " + ", ".join(missing_steps), file=sys.stderr)
            return 1
    blockers = data.get("blockers") or []
    if blockers:
        print("BUILD_PLAN gate: unresolved blockers: " + "; ".join(map(str, blockers)), file=sys.stderr)
        return 1

    weights = data.get("weights") if isinstance(data.get("weights"), dict) else {}
    if weights.get("fallback_to_host_global") and not weights.get("fallback_reason"):
        print(
            "BUILD_PLAN gate: weights.fallback_to_host_global=true requires fallback_reason.",
            file=sys.stderr,
        )
        return 1

    print(
        f"check_build_plan OK: backend={data.get('backend')}, "
        f"package={data.get('package_profile')}, steps={len(steps)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
