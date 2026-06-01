"""Validators for main-flow agent artifacts.

Provides deterministic, code-level enforcement of contracts defined in
docs/contracts/main_agent_script_routing_unit.md and related docs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Contract constants – single source of truth
# ---------------------------------------------------------------------------

ALLOWED_STEP_TYPES: set[str] = {
    "prepare_dataset",
    "materialize_templates",
    "validate_execution_shell",
    "wait_for_predictions",
    "validate_predictions",
    "evaluate_predictions",
    "refresh_report",
}

ALLOWED_SCRIPT_PREFIX: str = "scripts/"
FORBIDDEN_SCRIPT_PREFIXES: tuple[str, ...] = ("demo/", "src/", "tests/", "examples/")

# ---------------------------------------------------------------------------
# JSON Schema for script_routing.json (draft-07 compatible)
# ---------------------------------------------------------------------------

SCRIPT_ROUTING_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["steps", "routing_reason", "wait_points", "stop_condition"],
    "properties": {
        "run_id": {"type": "string"},
        "timestamp": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "script", "inputs", "outputs", "completion_criteria"],
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": list(ALLOWED_STEP_TYPES),
                        "description": "Must be one of the Allowed Step Types defined in main_agent_script_routing_unit.md",
                    },
                    "script": {
                        "type": "string",
                        "description": "Must be a path under scripts/ directory",
                    },
                    "inputs": {"type": "object"},
                    "outputs": {"type": "object"},
                    "completion_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "dataset_context": {"type": "object"},
                },
            },
        },
        "routing_reason": {"type": "string"},
        "wait_points": {
            "type": "array",
            "items": {"type": "string"},
        },
        "stop_condition": {"type": "string"},
    },
}

# ---------------------------------------------------------------------------
# Low-level validators
# ---------------------------------------------------------------------------

def validate_script_path(script: str, step_index: int) -> list[str]:
    """Check that a script path complies with directory constraints."""
    errors: list[str] = []

    # Must start with scripts/
    if not script.startswith(ALLOWED_SCRIPT_PREFIX):
        errors.append(
            f"Step {step_index}: script path '{script}' must start with '{ALLOWED_SCRIPT_PREFIX}'. "
            f"Agent Flow scripts are restricted to the deterministic scripts directory."
        )

    # Must NOT start with forbidden prefixes
    for forbidden in FORBIDDEN_SCRIPT_PREFIXES:
        if script.startswith(forbidden):
            errors.append(
                f"Step {step_index}: script path '{script}' uses forbidden prefix '{forbidden}'. "
                f"demo/, examples/, tests/ scripts are NOT allowed in Agent Flow routing."
            )

    # File must exist (relative to repo root)
    repo_root = Path(__file__).parent.parent.parent.parent
    resolved = repo_root / script
    if not resolved.exists():
        errors.append(
            f"Step {step_index}: script '{script}' does not exist at {resolved}. "
            f"Only existing deterministic scripts may be routed."
        )

    return errors


def validate_step_type(name: str, step_index: int) -> list[str]:
    """Check that a step name is in the Allowed Step Types whitelist."""
    errors: list[str] = []
    if name not in ALLOWED_STEP_TYPES:
        allowed_str = ", ".join(sorted(ALLOWED_STEP_TYPES))
        errors.append(
            f"Step {step_index}: step name '{name}' is not in Allowed Step Types. "
            f"Allowed values are: {allowed_str}."
        )
    return errors


def validate_script_routing(data: dict[str, Any], strict: bool = True) -> list[str]:
    """Validate a parsed script_routing.json against the full contract.

    Args:
        data: Parsed JSON content of script_routing.json.
        strict: If True, also verify script files exist on disk.

    Returns:
        List of human-readable error messages. Empty list means valid.
    """
    errors: list[str] = []
    steps = data.get("steps", [])

    if not steps:
        errors.append("No steps found in script_routing.json. At least one step is required.")

    for i, step in enumerate(steps):
        name = step.get("name", "")
        script = step.get("script", "")

        # 1. Validate step type (Allowed Step Types whitelist)
        errors.extend(validate_step_type(name, i))

        # 2. Validate script path (scripts/ directory constraint)
        if strict:
            errors.extend(validate_script_path(script, i))
        else:
            # Schema-only mode: just check prefix, no disk access
            if not script.startswith(ALLOWED_SCRIPT_PREFIX):
                errors.append(
                    f"Step {i}: script path '{script}' must start with '{ALLOWED_SCRIPT_PREFIX}'."
                )
            for forbidden in FORBIDDEN_SCRIPT_PREFIXES:
                if script.startswith(forbidden):
                    errors.append(
                        f"Step {i}: script path '{script}' uses forbidden prefix '{forbidden}'."
                    )

        # 3. Validate completion_criteria presence
        if not step.get("completion_criteria"):
            errors.append(f"Step {i}: missing completion_criteria. Every step must define how completion is determined.")

    # 4. Validate routing_reason presence
    if not data.get("routing_reason", "").strip():
        errors.append("Missing routing_reason. Must explain why this route was chosen.")

    return errors


def validate_execution_surface(data: dict[str, Any]) -> list[str]:
    """Validate execution_surface.json for compliance.

    Specifically checks that the entrypoint does not reference demo/ scripts.
    """
    errors: list[str] = []
    entrypoint = data.get("entrypoint_path", "")

    if entrypoint:
        for forbidden in FORBIDDEN_SCRIPT_PREFIXES:
            if forbidden in entrypoint:
                errors.append(
                    f"Execution surface entrypoint '{entrypoint}' contains forbidden path '{forbidden}'. "
                    f"Execution surfaces must not reference demo/, examples/, or tests/ artifacts."
                )

    return errors


def validate_execution_readiness(data: dict[str, Any]) -> list[str]:
    """Validate execution_readiness_report.json for compliance.

    Checks GPU preflight section when model requires GPU.
    """
    errors: list[str] = []
    gpu_preflight = data.get("gpu_preflight")

    if gpu_preflight is not None:
        model_requires_gpu = gpu_preflight.get("model_requires_gpu", False)
        gpu_check_passed = gpu_preflight.get("gpu_check_passed", False)
        recommended_device = gpu_preflight.get("recommended_device", "")
        cpu_fallback_ready = gpu_preflight.get("cpu_fallback_ready", False)

        if model_requires_gpu:
            if not recommended_device:
                errors.append(
                    "gpu_preflight: model_requires_gpu is true but recommended_device is empty. "
                    "Must specify cuda:N or cpu."
                )
            if not gpu_check_passed and not cpu_fallback_ready:
                errors.append(
                    "gpu_preflight: GPU check failed and cpu_fallback_ready is false. "
                    "Must have a valid fallback path."
                )

    return errors


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate main-flow agent artifacts against contracts."
    )
    parser.add_argument("file", type=Path, help="Path to script_routing.json or execution_surface.json")
    parser.add_argument(
        "--strict",
        action="store_true",
        default=True,
        help="Verify script files exist on disk (default: True)",
    )
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="Disable disk existence checks (schema-only validation)",
    )
    args = parser.parse_args()

    strict = not args.no_strict

    if not args.file.exists():
        print(f"ERROR: File not found: {args.file}", file=sys.stderr)
        return 1

    data = json.loads(args.file.read_text(encoding="utf-8"))

    # Dispatch based on filename heuristic
    filename = args.file.name
    if "script_routing" in filename:
        errors = validate_script_routing(data, strict=strict)
    elif "execution_surface" in filename:
        errors = validate_execution_surface(data)
    elif "execution_readiness" in filename:
        errors = validate_execution_readiness(data)
    else:
        # Try all
        errors = validate_script_routing(data, strict=strict)
        errors.extend(validate_execution_surface(data))
        errors.extend(validate_execution_readiness(data))

    if errors:
        print(f"VALIDATION FAILED: {len(errors)} error(s) found in {args.file}", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"OK: {args.file} passed all contract validations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
