#!/usr/bin/env python3
"""
Check execution surface compliance.

The execution surface MUST be generated from an approved harness template.
The bundled main-flow reference mirror is audit-only and is not a runtime
template source. No prior-run script/prediction/report leakage is accepted.

This script MUST be called by the EXECUTION_READINESS unit before any run
is approved for execution. The Sure hook invokes it as:

    python3 scripts/check_execution_surface_compliance.py \
        --run-dir <runDir> --produces <abs path to execution_surface.json>

The --produces path points at the execution_surface.json artifact under
<runDir>/artifacts/. run_evaluation.sh is read from <runDir>/artifacts/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from resolve_evaluation_route_plan import build_route_plan
from harness_runtime import HarnessRuntimeBindingError, harness_runtime_from_eval_input
from container_execution import resolve_container_harness_runtime


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
CANONICAL_TEMPLATE_ROOT = SCRIPT_DIR / "templates"
ALLOWED_TEMPLATE_ROOTS = (CANONICAL_TEMPLATE_ROOT,)


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _maybe_check_declared_hash(path: Path, declared: str | None, label: str) -> list[str]:
    if not declared:
        return []
    if not path.exists():
        return [f"{label} hash declared but file does not exist: {path}"]
    actual = _sha256_file(path)
    if actual != declared:
        return [f"{label} hash mismatch for {path}: declared={declared} actual={actual}"]
    return []


def check_template_source(
    surface_path: Path,
    expected_template: Path | None,
) -> dict[str, Any]:
    """Check that execution_surface.json declares an approved template source."""
    if not surface_path.exists():
        return {
            "passed": False,
            "template_declared": "",
            "template_exists": False,
            "under_approved_template_root": False,
            "matches_expected": False,
            "evidence": f"{surface_path.name} not found",
        }

    try:
        data = json.loads(surface_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {
            "passed": False,
            "template_declared": "",
            "template_exists": False,
            "under_approved_template_root": False,
            "matches_expected": False,
            "evidence": f"invalid JSON: {e}",
        }

    prov = data.get("source_provenance", {})
    template_file = prov.get("template_file", "")

    if not template_file:
        return {
            "passed": False,
            "template_declared": "",
            "template_exists": False,
            "under_approved_template_root": False,
            "matches_expected": False,
            "evidence": "source_provenance.template_file is empty",
        }

    template_path = Path(template_file)
    if not template_path.is_absolute():
        template_path = (SKILL_ROOT / template_path).resolve()
    exists = template_path.exists()
    under_approved_root = any(_path_is_under(template_path, root) for root in ALLOWED_TEMPLATE_ROOTS)
    matches_expected = False
    if expected_template is not None:
        matches_expected = template_path.resolve() == expected_template.resolve()

    hash_errors: list[str] = []
    hash_errors.extend(_maybe_check_declared_hash(template_path, prov.get("template_sha256"), "template"))
    for source_key, hash_key, label in (
        ("source_template_file", "source_template_sha256", "source_template"),
        ("mirror_template_file", "mirror_template_sha256", "mirror_template"),
    ):
        source_value = prov.get(source_key)
        if source_value:
            source_path = Path(source_value)
            if not source_path.is_absolute():
                source_path = (SKILL_ROOT / source_path).resolve()
            if not any(_path_is_under(source_path, root) for root in ALLOWED_TEMPLATE_ROOTS):
                hash_errors.append(f"{label} path is not under an approved template root: {source_value}")
            hash_errors.extend(_maybe_check_declared_hash(source_path, prov.get(hash_key), label))

    passed = under_approved_root and exists and not hash_errors
    if expected_template is not None:
        passed = passed and matches_expected

    evidence_parts = []
    if not under_approved_root:
        allowed = ", ".join(str(root) for root in ALLOWED_TEMPLATE_ROOTS)
        evidence_parts.append(f"template '{template_file}' is not under an approved template root: {allowed}")
    if not exists:
        evidence_parts.append(f"template '{template_file}' does not exist")
    if expected_template is not None and not matches_expected:
        evidence_parts.append(
            f"expected template '{expected_template}', got '{template_file}'"
        )
    evidence_parts.extend(hash_errors)

    return {
        "passed": passed,
        "template_declared": str(template_path),
        "template_exists": exists,
        "under_approved_template_root": under_approved_root,
        "canonical_template_root": str(CANONICAL_TEMPLATE_ROOT),
        "matches_expected": matches_expected,
        "evidence": "ok" if passed else "; ".join(evidence_parts),
    }


def check_source_provenance(surface_path: Path) -> dict[str, Any]:
    """Check execution_surface.json has source_provenance with template_file."""
    if not surface_path.exists():
        return {
            "passed": False,
            "evidence": f"{surface_path.name} not found",
        }

    try:
        data = json.loads(surface_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {
            "passed": False,
            "evidence": f"invalid JSON: {e}",
        }

    prov = data.get("source_provenance")
    if not prov:
        return {
            "passed": False,
            "evidence": "source_provenance field missing",
        }

    template_file = prov.get("template_file", "")
    if not template_file:
        return {
            "passed": False,
            "evidence": "source_provenance.template_file is empty",
        }

    return {
        "passed": True,
        "template_file": template_file,
        "evidence": "source_provenance present",
    }


def check_no_prior_run_leakage(surface_path: Path) -> dict[str, Any]:
    if not surface_path.exists():
        return {"passed": False, "evidence": f"{surface_path.name} not found"}

    try:
        data = json.loads(surface_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"passed": False, "evidence": f"invalid JSON: {e}"}

    prov = data.get("source_provenance")
    if not isinstance(prov, dict):
        return {"passed": False, "evidence": "source_provenance missing"}
    isolation = prov.get("isolation_compliance")
    if not isinstance(isolation, dict):
        return {"passed": False, "evidence": "source_provenance.isolation_compliance must be an object"}

    leaked = []
    if isolation.get("eval_runs_referenced") is True:
        leaked.append("eval_runs_referenced=true")
    if isolation.get("prior_run_scripts_copied") is True:
        leaked.append("prior_run_scripts_copied=true")
    if leaked:
        return {"passed": False, "evidence": "; ".join(leaked)}
    return {"passed": True, "evidence": "no prior-run leakage declared"}


def check_inference_runtime(surface_path: Path) -> dict[str, Any]:
    if not surface_path.exists():
        return {"passed": False, "evidence": f"{surface_path.name} not found"}
    try:
        data = json.loads(surface_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"passed": False, "evidence": f"invalid JSON: {e}"}

    eval_input_path = surface_path.parent / "eval_input_resolved.json"
    try:
        eval_input = json.loads(eval_input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"passed": False, "evidence": f"approved eval input is unavailable: {exc}"}
    model = eval_input.get("model") if isinstance(eval_input.get("model"), dict) else {}
    approved = model.get("deployment_binding")
    declared_binding = data.get("deployment_binding")
    if not isinstance(approved, dict) or approved.get("schema") != "sure.eval.deployment_binding.v1":
        return {"passed": False, "evidence": "eval input has no approved deployment binding"}
    if not isinstance(declared_binding, dict):
        return {"passed": False, "evidence": "execution surface must declare deployment_binding"}
    env = data.get("env") if isinstance(data.get("env"), dict) else {}
    resolved_inputs = data.get("resolved_inputs") if isinstance(data.get("resolved_inputs"), dict) else {}
    execution = data.get("execution") if isinstance(data.get("execution"), dict) else {}
    issues: list[str] = []
    try:
        approved_harness = harness_runtime_from_eval_input(eval_input)
    except HarnessRuntimeBindingError as exc:
        issues.append(str(exc))
        approved_harness = {}

    runtime_kind = str(approved.get("runtime_kind") or "container")
    approved_python = approved.get("python") if isinstance(approved.get("python"), dict) else {}
    expected_fields = {
        "schema": approved.get("schema"),
        "runtime_kind": runtime_kind,
        "target_image_ref": approved.get("target_image_ref") if runtime_kind == "container" else None,
        "model_python": approved_python.get("python_executable") if runtime_kind == "python" else None,
        "bundle_identity_sha256": (approved.get("evidence") or {}).get("bundle_identity_sha256"),
        "execution_mode": "python" if runtime_kind == "python" else "container_only",
        "model_mount_read_only": True,
        "result_mount_writable": True,
    }
    for key, expected in expected_fields.items():
        if declared_binding.get(key) != expected:
            issues.append(f"deployment_binding.{key} must equal approved value {expected!r}")
    path_planned = str(execution.get("path_planned") or "")
    allowed_paths = {"local_python"} if runtime_kind == "python" else {"local_docker", "vc_submit"}
    if path_planned not in allowed_paths:
        issues.append(f"formal {runtime_kind} inference path must be one of {sorted(allowed_paths)}")
    if runtime_kind == "container" and isinstance(env.get("SURE_EVAL_CONTAINER_IMAGE"), str) and env["SURE_EVAL_CONTAINER_IMAGE"] != approved.get("target_image_ref"):
        issues.append("execution surface image differs from the approved digest-pinned image")
    for key in ("MODEL_PYTHON", "PYTHON_BIN"):
        value = env.get(key)
        if runtime_kind == "python" and isinstance(value, str) and value and value != approved_python.get("python_executable"):
            issues.append(f"{key} differs from the approved Model Python")
        elif runtime_kind == "container" and isinstance(value, str) and (".venv" in value or value == "/usr/bin/python3"):
            issues.append(f"{key} must not bind a host interpreter; the container runner injects approved runtime Python")
    declared_harness_python = env.get("HARNESS_PYTHON_BIN")
    if isinstance(declared_harness_python, str) and declared_harness_python:
        if declared_harness_python != approved_harness.get("python_executable"):
            issues.append("HARNESS_PYTHON_BIN differs from the approved common Harness Runtime")
    if declared_harness_python and declared_harness_python in {
        env.get("MODEL_PYTHON"),
        env.get("PYTHON_BIN"),
    }:
        issues.append("Harness Python and Model Python must be separate execution roles")

    declared_tool = next(
        (value for value in (env.get("TOOL_NAME"), resolved_inputs.get("tool_name")) if isinstance(value, str) and value),
        "",
    )
    approved_runtime = approved_python if runtime_kind == "python" else (approved.get("container") or {})
    approved_tools = approved_runtime.get("tool_names") or []
    if declared_tool and declared_tool not in approved_tools:
        issues.append(f"tool name {declared_tool!r} is not in the approved deployment binding: {approved_tools}")

    if issues:
        return {"passed": False, "evidence": "; ".join(issues)}
    live_probe = _live_runtime_probe(approved, approved_harness)
    if not live_probe["passed"]:
        return {
            "passed": False,
            "failure_class": live_probe["failure_class"],
            "live_runtime_probe": live_probe,
            "evidence": live_probe["evidence"],
        }
    return {
        "passed": True,
        "live_runtime_probe": live_probe,
        "evidence": f"approved {runtime_kind} deployment verified",
    }


def _live_runtime_probe(
    binding: dict[str, Any],
    host_harness: dict[str, Any],
    *,
    run=subprocess.run,
) -> dict[str, Any]:
    if binding.get("runtime_kind") == "python":
        python = binding.get("python") if isinstance(binding.get("python"), dict) else {}
        model_python = str(python.get("python_executable") or "")
        harness_python = str(host_harness.get("python_executable") or "")
        if not model_python or not harness_python or model_python == harness_python:
            return {
                "passed": False,
                "failure_class": "HARNESS_MODEL_RUNTIME_ALIAS",
                "exit_code": None,
                "evidence": "approved Harness Python and Model Python must be distinct",
            }
        imports = [str(item) for item in python.get("required_imports") or [] if isinstance(item, str)]
        script = (
            "import importlib,json; "
            f"[importlib.import_module(name) for name in json.loads({json.dumps(imports)!r})]"
        )
        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            completed = run(
                [model_python, "-c", script],
                cwd=str(python.get("working_dir") or binding.get("model_dir") or ""),
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {
                "passed": False,
                "failure_class": "MODEL_RUNTIME_NOT_MATERIALIZED",
                "exit_code": None,
                "evidence": f"Python runtime probe could not start: {exc}",
            }
        detail = (completed.stderr or completed.stdout or "").strip()
        return {
            "passed": completed.returncode == 0,
            "failure_class": None if completed.returncode == 0 else "MODEL_RUNTIME_NEEDS_REPAIR",
            "exit_code": completed.returncode,
            "model_python": model_python,
            "backend": python.get("backend"),
            "required_imports": imports,
            "evidence": "approved Python runtime probe passed"
            if completed.returncode == 0
            else f"MODEL_RUNTIME_NEEDS_REPAIR: {detail or f'exit {completed.returncode}'}",
        }
    repo_root = Path(__file__).resolve().parents[4]
    try:
        harness, mounted = resolve_container_harness_runtime(binding, host_harness, repo_root)
    except ValueError as exc:
        return {
            "passed": False,
            "failure_class": "HARNESS_RUNTIME_NOT_READY",
            "exit_code": None,
            "evidence": str(exc),
        }
    container = binding.get("container") if isinstance(binding.get("container"), dict) else {}
    model_python = str(container.get("python_executable") or "python")
    harness_python = str(harness["python_executable"])
    script = (
        f"test {shlex.quote(harness_python)} != {shlex.quote(model_python)} || exit 40; "
        f"{shlex.quote(harness_python)} -s -c "
        + shlex.quote("import pydantic,pydantic_settings,rich,structlog,typer,yaml")
        + " || exit 41; "
        + f"{shlex.quote(model_python)} -c "
        + shlex.quote("import sys; print(sys.executable)")
        + " || exit 42"
    )
    command = ["docker", "run", "--rm", "--entrypoint", "bash"]
    if mounted:
        command.extend(["--mount", f"type=bind,src={repo_root},dst={repo_root},readonly"])
    command.extend([str(binding["target_image_ref"]), "-lc", script])
    try:
        completed = run(command, capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "passed": False,
            "failure_class": "CONTAINER_RUNTIME_UNAVAILABLE",
            "exit_code": None,
            "evidence": f"runtime probe could not start: {exc}",
        }
    categories = {
        40: "HARNESS_MODEL_RUNTIME_ALIAS",
        41: "HARNESS_RUNTIME_NOT_READY",
        42: "MODEL_RUNTIME_NEEDS_REPAIR",
    }
    failure_class = categories.get(completed.returncode, "CONTAINER_RUNTIME_UNAVAILABLE")
    detail = (completed.stderr or completed.stdout or "").strip()
    return {
        "passed": completed.returncode == 0,
        "failure_class": None if completed.returncode == 0 else failure_class,
        "exit_code": completed.returncode,
        "image_ref": binding.get("target_image_ref"),
        "harness_runtime_id": harness.get("runtime_id"),
        "harness_lock_sha256": harness.get("lock_sha256"),
        "harness_python": harness_python,
        "model_python": model_python,
        "harness_runtime_source": harness.get("execution_source"),
        "evidence": "exact-image Harness/Model runtime probe passed"
        if completed.returncode == 0
        else f"{failure_class}: {detail or f'exit {completed.returncode}'}",
    }


REQUIRED_EVALUATE_ARGS = [
    "--results-dir",
    "--protocol-id",
    "--model-dir",
    "--evaluation-backend",
]


def check_evaluate_predictions_args(shell_path: Path) -> dict[str, Any]:
    """Ensure run_evaluation.sh preserves required evaluate_predictions.py args."""
    if not shell_path.exists():
        return {
            "passed": True,
            "evidence": "run_evaluation.sh not found, nothing to check",
        }

    content = shell_path.read_text(encoding="utf-8")

    # Only check if this script actually calls evaluate_predictions.py
    if "evaluate_predictions.py" not in content:
        return {
            "passed": True,
            "evidence": "no evaluate_predictions.py call found",
        }

    # Main-flow templates usually declare EVAL_ARGS/MERGE_ARGS arrays and then
    # expand them at the evaluate_predictions.py call site. Check the whole
    # script so array-declared arguments are accepted.
    missing = [arg for arg in REQUIRED_EVALUATE_ARGS if arg not in content]
    if missing:
        return {
            "passed": False,
            "evidence": f"missing required args in evaluate_predictions.py call: {missing}",
        }

    return {
        "passed": True,
        "evidence": "all required evaluate_predictions.py args present",
    }


def check_evaluation_route_plan(artifacts_dir: Path) -> dict[str, Any]:
    """Resolve sure-evaluation route/env readiness for selected datasets."""

    input_path = artifacts_dir / "eval_input_resolved.json"
    output_path = artifacts_dir / "evaluation_route_plan.json"
    if not input_path.exists():
        return {
            "passed": False,
            "plan_path": str(output_path),
            "can_run_now": False,
            "blocking_issues": [f"missing eval_input_resolved.json: {input_path}"],
            "evidence": "eval_input_resolved.json is required before execution readiness",
        }
    try:
        payload = build_route_plan(input_path, output_path=output_path)
    except Exception as exc:
        return {
            "passed": False,
            "plan_path": str(output_path),
            "can_run_now": False,
            "blocking_issues": [str(exc)],
            "evidence": f"failed to resolve evaluation route plan: {exc}",
        }
    blocking_issues = [str(item) for item in payload.get("blocking_issues") or []]
    passed = bool(payload.get("can_run_now")) and not blocking_issues
    return {
        "passed": passed,
        "plan_path": str(output_path),
        "can_run_now": bool(payload.get("can_run_now")),
        "engine": payload.get("engine"),
        "blocking_issues": blocking_issues,
        "setup_commands": payload.get("setup_commands") or [],
        "evidence": "ok" if passed else "; ".join(blocking_issues),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check execution surface compliance before run approval"
    )
    parser.add_argument("--run-dir", required=True, help="Path to the run directory")
    parser.add_argument(
        "--produces",
        help=(
            "Absolute path to the execution_surface.json artifact "
            "(under <run-dir>/artifacts/). The Sure hook passes this. "
            "If omitted, falls back to <run-dir>/execution_surface.json for "
            "back-compat with direct CLI use."
        ),
    )
    parser.add_argument(
        "--expected-template",
        help=(
            "Expected template path under the bundled scripts/templates/ "
            "directory (e.g., templates/run_single_model_single_dataset.sh)"
        ),
    )
    parser.add_argument("--output", help="JSON output path")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    expected_template = Path(args.expected_template).resolve() if args.expected_template else None

    # The hook materializes the execution_surface under <run-dir>/artifacts/.
    # Prefer the --produces path; fall back to the run-dir root for direct use.
    surface_path: Path
    if args.produces:
        surface_path = Path(args.produces).resolve()
    else:
        surface_path = run_dir / "execution_surface.json"

    # The hook passes the current unit's artifact as --produces. For the
    # execution_readiness unit this is execution_readiness_report.json, but
    # this script audits the execution_surface.json materialized in the same
    # artifacts directory. Fall back to it when the provided path is not the
    # execution surface artifact.
    if surface_path.name != "execution_surface.json":
        fallback = surface_path.parent / "execution_surface.json"
        if fallback.exists():
            surface_path = fallback

    # run_evaluation.sh lives alongside the surface artifact (same dir).
    shell_path = surface_path.parent / "run_evaluation.sh"
    artifacts_dir = surface_path.parent

    checks = {
        "template_source": check_template_source(surface_path, expected_template),
        "source_provenance": check_source_provenance(surface_path),
        "prior_run_leakage": check_no_prior_run_leakage(surface_path),
        "evaluate_predictions_args": check_evaluate_predictions_args(shell_path),
        "inference_runtime": check_inference_runtime(surface_path),
        "evaluation_route_plan": check_evaluation_route_plan(artifacts_dir),
    }

    all_passed = all(c["passed"] for c in checks.values())
    blocking_issues: list[str] = []
    for name, result in checks.items():
        if not result["passed"]:
            blocking_issues.append(f"{name}: {result.get('evidence', 'failed')}")

    report = {
        "run_id": run_dir.name,
        "compliance_passed": all_passed,
        "checks": checks,
        "blocking_issues": blocking_issues,
    }

    if all_passed:
        print(
            "check_execution_surface_compliance OK: template under "
            f"{CANONICAL_TEMPLATE_ROOT}"
        )
    else:
        print(
            "EXECUTION_SURFACE_ISOLATION red line: " + "; ".join(blocking_issues),
            file=sys.stderr,
        )

    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
