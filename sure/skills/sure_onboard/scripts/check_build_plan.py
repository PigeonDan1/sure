#!/usr/bin/env python3
"""Gate script for BUILD_PLAN.

The build plan must be an executable, auditable bridge from backend selection to
local validation. VC/HPC submission is intentionally not part of this core
harness chain; any required VC step in build_plan.json is rejected here.
"""
from __future__ import annotations

import argparse
import json
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
    if data.get("package_profile") not in PACKAGE_PROFILES:
        print(f"BUILD_PLAN gate: unsupported package_profile={data.get('package_profile')!r}", file=sys.stderr)
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
