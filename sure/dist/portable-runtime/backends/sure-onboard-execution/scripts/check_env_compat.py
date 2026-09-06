#!/usr/bin/env python3
"""Gate script for the VALIDATE_ENV_COMPAT unit.

Verifies the built env can actually load the resolved weights on the selected
device, the python version matches, and the adapter protocol is supported.
When a local run requests device=auto and host CUDA is visible, the selected
device must be cuda first. CPU fallback is accepted only after recorded CUDA
attempts and CUDA environment repair attempts fail enough times to justify the
downgrade.
Called by the Sure hook:
    python3 scripts/check_env_compat.py --run-dir <runDir> --produces <abs>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ACTUAL_DEVICES = {"cuda", "cpu", "mps"}
CUDA_FIRST_REQUESTS = {"auto", "cuda"}
DEFAULT_CPU_FALLBACK_AFTER_CUDA_FAILURES = 3
DEFAULT_CUDA_REPAIR_ATTEMPTS_BEFORE_CPU = 3


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must be a JSON object.")
    return data


def env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return None


def host_cuda_available() -> bool:
    override = env_bool("SURE_HOST_CUDA_AVAILABLE")
    if override is not None:
        return override
    try:
        proc = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def get_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def get_device_policy(data: dict[str, Any]) -> dict[str, Any]:
    policy = data.get("device_policy")
    return policy if isinstance(policy, dict) else {}


def fallback_failures(data: dict[str, Any], policy: dict[str, Any]) -> list[Any]:
    failures = policy.get("cuda_failures")
    if not isinstance(failures, list):
        failures = data.get("cuda_failures")
    return failures if isinstance(failures, list) else []


def fallback_reason(data: dict[str, Any], policy: dict[str, Any]) -> str:
    for value in (
        policy.get("fallback_reason"),
        data.get("fallback_reason"),
        data.get("notes"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def repair_attempts(data: dict[str, Any], policy: dict[str, Any]) -> list[Any]:
    attempts = policy.get("cuda_repair_attempts")
    if not isinstance(attempts, list):
        attempts = data.get("cuda_repair_attempts")
    return attempts if isinstance(attempts, list) else []


def repair_attempt_is_countable(attempt: Any) -> bool:
    if isinstance(attempt, str):
        return bool(attempt.strip())
    if not isinstance(attempt, dict):
        return False
    status = str(attempt.get("status") or "").strip().lower()
    if not status or status in {"skipped", "not_attempted", "none"}:
        return False
    action = str(attempt.get("action") or attempt.get("command") or attempt.get("target") or "").strip()
    evidence = str(
        attempt.get("evidence")
        or attempt.get("message")
        or attempt.get("error")
        or attempt.get("log_path")
        or attempt.get("result")
        or ""
    ).strip()
    target_text = " ".join(
        str(attempt.get(key) or "") for key in ("action", "target", "command", "package", "index_url")
    ).lower()
    if "cpu" in target_text and "cuda" not in target_text and "cu1" not in target_text:
        return False
    return bool(action and evidence)


def min_cuda_attempts(resolved: dict[str, Any] | None, policy: dict[str, Any]) -> int:
    candidates = [
        policy.get("min_cuda_attempts_before_cpu"),
        policy.get("cpu_fallback_after_cuda_failures"),
        resolved.get("cpu_fallback_after_cuda_failures") if resolved else None,
    ]
    resolved_policy = resolved.get("device_policy") if resolved else None
    if isinstance(resolved_policy, dict):
        candidates.append(resolved_policy.get("cpu_fallback_after_cuda_failures"))
    for candidate in candidates:
        value = get_int(candidate, 0)
        if value > 0:
            return value
    return DEFAULT_CPU_FALLBACK_AFTER_CUDA_FAILURES


def min_cuda_repair_attempts(resolved: dict[str, Any] | None, policy: dict[str, Any]) -> int:
    candidates = [
        policy.get("min_cuda_repair_attempts_before_cpu"),
        policy.get("cuda_repair_attempts_before_cpu"),
        resolved.get("cuda_repair_attempts_before_cpu") if resolved else None,
    ]
    resolved_policy = resolved.get("device_policy") if resolved else None
    if isinstance(resolved_policy, dict):
        candidates.append(resolved_policy.get("cuda_repair_attempts_before_cpu"))
    for candidate in candidates:
        value = get_int(candidate, 0)
        if value > 0:
            return value
    return DEFAULT_CUDA_REPAIR_ATTEMPTS_BEFORE_CPU


def cuda_first_violation(data: dict[str, Any], run_dir: Path) -> str | None:
    resolved = load_json(run_dir / "artifacts" / "model_input_resolved.json")
    if not resolved:
        return None
    if resolved.get("deployment_type") == "api":
        return None

    requested_device = str(resolved.get("device") or "auto").lower()
    selected_device = str(data.get("device") or "").lower()
    cuda_available = data.get("cuda_available")
    if cuda_available is None:
        cuda_available = host_cuda_available()
    if not cuda_available:
        return None

    if requested_device == "cpu":
        return None
    if requested_device == "mps":
        return None
    if requested_device == "cuda" and selected_device != "cuda":
        return (
            "VALIDATE_ENV_COMPAT gate failed: device=cuda is an explicit CUDA-only "
            f"request, but env_compat_result.device={selected_device or '<missing>'!r}."
        )
    if requested_device not in CUDA_FIRST_REQUESTS:
        return None

    if selected_device == "cuda":
        return None
    if selected_device != "cpu":
        return (
            "VALIDATE_ENV_COMPAT gate failed: host CUDA is available and requested "
            f"device={requested_device!r}, so env_compat_result.device must be "
            "'cuda' or a justified CPU fallback; got "
            f"{selected_device or '<missing>'!r}."
        )

    policy = get_device_policy(data)
    attempts = get_int(policy.get("cuda_attempts", data.get("cuda_attempts")), 0)
    failures = fallback_failures(data, policy)
    minimum = min_cuda_attempts(resolved, policy)
    repairs = repair_attempts(data, policy)
    valid_repairs = [attempt for attempt in repairs if repair_attempt_is_countable(attempt)]
    minimum_repairs = min_cuda_repair_attempts(resolved, policy)
    reason = fallback_reason(data, policy)
    if attempts < minimum or len(failures) < minimum or len(valid_repairs) < minimum_repairs or not reason:
        return (
            "VALIDATE_ENV_COMPAT gate failed: host CUDA is available, so CPU "
            "fallback requires recorded CUDA-first evidence and CUDA environment "
            "repair attempts before passing. "
            f"Need at least {minimum} CUDA attempts, {minimum} cuda_failures, "
            f"{minimum_repairs} cuda_repair_attempts, and a non-empty "
            f"fallback_reason; got attempts={attempts}, "
            f"cuda_failures={len(failures)}, "
            f"cuda_repair_attempts={len(valid_repairs)}, "
            f"fallback_reason={'present' if reason else 'missing'}."
        )
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    path = Path(args.produces)
    if not path.exists():
        print(f"env_compat_result.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = load_json(path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if data is None:
        print(f"env_compat_result.json not found at {path}", file=sys.stderr)
        return 1

    if not data.get("compat_ok"):
        incompat = data.get("incompatibilities") or []
        detail = "\n  - " + "\n  - ".join(incompat) if incompat else ""
        print(
            "VALIDATE_ENV_COMPAT gate failed: compat_ok is false. Confirm the "
            "built env loads the resolved weights on the available device, the "
            f"python version matches, and the adapter protocol is supported.{detail}",
            file=sys.stderr,
        )
        return 1

    device_violation = cuda_first_violation(data, run_dir)
    if device_violation:
        print(device_violation, file=sys.stderr)
        return 1

    false_fields = [
        field
        for field in ("python_version_match", "adapter_protocol_supported", "weights_loadable")
        if data.get(field) is False
    ]
    if false_fields:
        print(
            "VALIDATE_ENV_COMPAT gate failed: compat_ok=true conflicts with false compatibility fields: "
            + ", ".join(false_fields),
            file=sys.stderr,
        )
        return 1

    print(
        f"check_env_compat OK: compat_ok=true, device={data.get('device')}, "
        f"python_match={data.get('python_version_match')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
