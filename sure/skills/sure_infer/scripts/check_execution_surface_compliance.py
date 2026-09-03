#!/usr/bin/env python3
"""
Check execution surface compliance.

The execution surface is written by scripts/run_infer.py and must point at the
bundled scripts/infer_entrypoint.py: the same file, the same digest, no
prior-run leakage, and a deployment binding that matches the approved input.
The agent never writes this artifact by hand.

The EXECUTION_READINESS unit runs this script through the Sure hook:

    python3 scripts/check_execution_surface_compliance.py \
        --run-dir <runDir> --produces <abs path under <runDir>/artifacts/>

When --produces is not execution_surface.json itself, the surface is read from
the same artifacts directory. run_infer.py also calls the check functions
directly before it launches anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from harness_runtime import HarnessRuntimeBindingError, harness_runtime_from_eval_input
from container_execution import resolve_container_harness_runtime
from deployment_binding import DEPLOYMENT_BINDING_V1, DEPLOYMENT_BINDING_V2


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
INFER_ENTRYPOINT = SCRIPT_DIR / "infer_entrypoint.py"

# This node could not start a container, so nothing was learned about the
# approved image. Kept apart from the failure classes that describe the image
# itself, which is what the probe is actually there to judge.
HOST_CANNOT_PROBE = "PROBE_HOST_CANNOT_RUN_CONTAINERS"


def expected_binding_summary(approved: dict[str, Any]) -> dict[str, Any]:
    """The deployment_binding block an execution surface must carry for ``approved``.

    run_infer.py writes it, check_inference_runtime and check_execution_result.py
    compare against it; one definition so the three cannot drift.
    """
    evidence = approved.get("evidence") if isinstance(approved.get("evidence"), dict) else {}
    policy = approved.get("policy") if isinstance(approved.get("policy"), dict) else {}
    if approved.get("schema") == DEPLOYMENT_BINDING_V1:
        return {
            "schema": DEPLOYMENT_BINDING_V1,
            "target_image_ref": approved.get("target_image_ref"),
            "bundle_identity_sha256": evidence.get("bundle_identity_sha256"),
            "execution_mode": "container_only",
            "model_mount_read_only": True,
            "result_mount_writable": True,
        }
    runtime_kind = str(approved.get("runtime_kind") or "container")
    summary: dict[str, Any] = {
        "schema": DEPLOYMENT_BINDING_V2,
        "runtime_kind": runtime_kind,
        "bundle_identity_sha256": evidence.get("bundle_identity_sha256"),
        "execution_mode": policy.get("execution_mode"),
        "model_mount_read_only": runtime_kind == "container",
        "model_integrity": policy.get("model_integrity", "image_digest"),
        "result_mount_writable": True,
    }
    if runtime_kind == "container":
        summary["target_image_ref"] = approved.get("target_image_ref")
    return summary


def binding_mismatches(declared: Any, expected: dict[str, Any]) -> list[str]:
    """Every expected field the declared binding does not carry verbatim."""
    if not isinstance(declared, dict):
        return ["execution surface must declare deployment_binding"]
    return [
        f"deployment_binding.{key} must equal approved value {value!r}"
        for key, value in expected.items()
        if declared.get(key) != value
    ]


def _load_surface(surface_path: Path) -> tuple[dict[str, Any] | None, str]:
    if not surface_path.exists():
        return None, f"{surface_path.name} not found"
    try:
        data = json.loads(surface_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(data, dict):
        return None, f"{surface_path.name} must be a JSON object"
    return data, ""


def check_entrypoint_provenance(surface_path: Path, expected_entrypoint: Path = INFER_ENTRYPOINT) -> dict[str, Any]:
    """The surface must run the bundled entrypoint, byte for byte, and nothing else."""
    data, error = _load_surface(surface_path)
    if data is None:
        return {"passed": False, "evidence": error}
    prov = data.get("source_provenance")
    if not isinstance(prov, dict):
        return {"passed": False, "evidence": "source_provenance field missing"}
    template_file = str(prov.get("template_file") or "")
    if not template_file:
        return {"passed": False, "evidence": "source_provenance.template_file is empty"}
    expected = expected_entrypoint.resolve()
    if not expected.is_file():
        return {"passed": False, "evidence": f"bundled entrypoint is missing: {expected}"}

    issues: list[str] = []
    declared_template = Path(template_file)
    if not declared_template.is_absolute():
        declared_template = SKILL_ROOT / declared_template
    if declared_template.resolve() != expected:
        issues.append(f"source_provenance.template_file must be the bundled entrypoint {expected}, got {template_file}")
    declared_sha = prov.get("template_sha256")
    actual_sha = _sha256_file(expected)
    if not declared_sha:
        issues.append("source_provenance.template_sha256 is missing")
    elif declared_sha != actual_sha:
        issues.append(f"source_provenance.template_sha256 is stale: declared={declared_sha} actual={actual_sha}")
    entrypoint = data.get("entrypoint_path") or data.get("entrypoint")
    if not isinstance(entrypoint, str) or not entrypoint:
        issues.append("execution surface must declare entrypoint_path")
    elif Path(entrypoint).resolve() != expected:
        issues.append(f"entrypoint_path must be the bundled entrypoint {expected}, got {entrypoint}")
    isolation = prov.get("isolation_compliance")
    if not isinstance(isolation, dict):
        issues.append("source_provenance.isolation_compliance must be an object")
    else:
        if isolation.get("eval_runs_referenced") is True:
            issues.append("eval_runs_referenced=true")
        if isolation.get("prior_run_scripts_copied") is True:
            issues.append("prior_run_scripts_copied=true")
    if issues:
        return {"passed": False, "entrypoint": str(expected), "evidence": "; ".join(issues)}
    return {"passed": True, "entrypoint": str(expected), "entrypoint_sha256": actual_sha, "evidence": "ok"}


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
    if not isinstance(approved, dict) or approved.get("schema") not in {
        DEPLOYMENT_BINDING_V1,
        DEPLOYMENT_BINDING_V2,
    }:
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
    issues.extend(binding_mismatches(declared_binding, expected_binding_summary(approved)))
    path_planned = str(execution.get("path_planned") or "")
    approved_python = approved.get("python") if isinstance(approved.get("python"), dict) else {}
    allowed_path = "local_python" if runtime_kind == "python" else "local_docker"
    if path_planned != allowed_path:
        issues.append(f"formal {runtime_kind} inference path must be {allowed_path}")
    if isinstance(env.get("SURE_EVAL_CONTAINER_IMAGE"), str) and env["SURE_EVAL_CONTAINER_IMAGE"] != approved.get("target_image_ref"):
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
    declared_node_python = env.get("SURE_EVAL_NODE_LOCAL_PYTHON")
    if isinstance(declared_node_python, str) and declared_node_python:
        if declared_node_python != approved_harness.get("python_executable"):
            issues.append("SURE_EVAL_NODE_LOCAL_PYTHON differs from the approved common Harness Runtime")

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
        # The probe's docker (or Model Python) is the execution environment
        # itself, so a node that cannot run it blocks here rather than a few
        # minutes later with no gate left to catch it.
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
        script = "import importlib,json;[importlib.import_module(n) for n in json.loads(" + repr(json.dumps(imports)) + ")]"
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "LANG", "LC_ALL", "CUDA_VISIBLE_DEVICES", "LD_LIBRARY_PATH"}
        }
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"})
        try:
            completed = run(
                [model_python, "-s", "-c", script],
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
            "runtime_id": python.get("runtime_id"),
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
            "probe_ran": False,
            "failure_class": "HARNESS_RUNTIME_NOT_READY",
            "exit_code": None,
            "evidence": str(exc),
        }
    container = binding.get("container") if isinstance(binding.get("container"), dict) else {}
    model_python = str(container.get("python_executable") or "python")
    harness_python = str(harness["python_executable"])
    node_probe = (
        "import os,subprocess;"
        "python=os.environ['SURE_EVAL_NODE_LOCAL_PYTHON'];"
        "subprocess.run([python,'-S','-c','import sys'],check=True)"
    )
    script = (
        f"test {shlex.quote(harness_python)} != {shlex.quote(model_python)} || exit 40; "
        f"{shlex.quote(harness_python)} -s -c "
        + shlex.quote("import pydantic,pydantic_settings,rich,structlog,typer,yaml")
        + " || exit 41; "
        + f"{shlex.quote(model_python)} -c "
        + shlex.quote("import sys; print(sys.executable)")
        + " || exit 42; "
        + f"export SURE_EVAL_NODE_LOCAL_PYTHON={shlex.quote(harness_python)}; "
        + f"{shlex.quote(harness_python)} -s -c {shlex.quote(node_probe)} || exit 43"
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
            "probe_ran": False,
            "failure_class": HOST_CANNOT_PROBE,
            "exit_code": None,
            "evidence": (
                f"{HOST_CANNOT_PROBE}: runtime probe could not start: {exc} "
                f"(docker on PATH: {shutil.which('docker') or 'not found'})"
            ),
        }
    categories = {
        40: "HARNESS_MODEL_RUNTIME_ALIAS",
        41: "HARNESS_RUNTIME_NOT_READY",
        42: "MODEL_RUNTIME_NEEDS_REPAIR",
        43: "EVALUATION_NODE_RUNTIME_NOT_READY",
    }
    # 40 to 43 come from the probe script, which only runs once the container
    # is up, so reaching one of them proves the image answered. Docker's own
    # 125, 126 and 127 mean it never got the chance: no daemon, an image that
    # will not pull, no bash to run. That is a fact about this node, not about
    # the image, and the two must not share a failure class.
    if completed.returncode in categories:
        probe_ran, failure_class = True, categories[completed.returncode]
    elif completed.returncode in {125, 126, 127}:
        probe_ran, failure_class = False, HOST_CANNOT_PROBE
    else:
        probe_ran, failure_class = True, "CONTAINER_RUNTIME_UNAVAILABLE"
    detail = (completed.stderr or completed.stdout or "").strip()
    return {
        "passed": completed.returncode == 0,
        "probe_ran": probe_ran,
        "failure_class": None if completed.returncode == 0 else failure_class,
        "exit_code": completed.returncode,
        "image_ref": binding.get("target_image_ref"),
        "harness_runtime_id": harness.get("runtime_id"),
        "harness_lock_sha256": harness.get("lock_sha256"),
        "harness_python": harness_python,
        "node_local_python": harness_python,
        "model_python": model_python,
        "harness_runtime_source": harness.get("execution_source"),
        "evidence": "exact-image Harness/Model/Node runtime probe passed"
        if completed.returncode == 0
        else f"{failure_class}: {detail or f'exit {completed.returncode}'}",
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
    parser.add_argument("--output", help="JSON output path")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()

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

    checks = {
        "entrypoint_provenance": check_entrypoint_provenance(surface_path),
        "inference_runtime": check_inference_runtime(surface_path),
    }

    all_passed = all(c["passed"] for c in checks.values())
    blocking_issues: list[str] = []
    warnings: list[str] = []
    for name, result in checks.items():
        if not result["passed"]:
            blocking_issues.append(f"{name}: {result.get('evidence', 'failed')}")
        for warning in result.get("warnings") or []:
            warnings.append(f"{name}: {warning}")

    report = {
        "run_id": run_dir.name,
        "compliance_passed": all_passed,
        "checks": checks,
        "blocking_issues": blocking_issues,
        "warnings": warnings,
    }

    # A check that passed without being able to run leaves a hole in the
    # report, and a hole nobody is told about is the same as no check at all.
    for warning in warnings:
        print(f"check_execution_surface_compliance warning: {warning}", file=sys.stderr)

    if all_passed:
        print(f"check_execution_surface_compliance OK: entrypoint {INFER_ENTRYPOINT}")
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
