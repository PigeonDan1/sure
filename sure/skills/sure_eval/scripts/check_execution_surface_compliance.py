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
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from resolve_evaluation_route_plan import build_route_plan


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


INTERPRETER_ENV_KEYS = ("MODEL_PYTHON", "PYTHON_BIN", "HARNESS_PYTHON_BIN")

# The model interpreter must be pinned to one venv, so a bare command name is
# not acceptable. The harness interpreter is resolved from PATH by the
# template, so a bare command name is the normal value there.
MODEL_INTERPRETER_ENV_KEYS = ("MODEL_PYTHON", "PYTHON_BIN")

TASK_DEFAULT_TOOLS = {
    "ASR": "transcribe_audio",
    "S2TT": "translate_audio",
    "TTS": "synthesize_speech",
    "VC": "convert_voice",
}


def _interpreter_candidates(model_dir: Any) -> list[str]:
    if not isinstance(model_dir, str) or not model_dir:
        return []
    root = Path(model_dir).expanduser()
    if not root.is_dir():
        return []
    found: list[str] = []
    for pattern in ("venv*/bin/python*", ".venv/bin/python*"):
        found.extend(str(path) for path in sorted(root.glob(pattern)))
    return found[:5]


def _model_config(model_dir: Any) -> dict[str, Any]:
    if not isinstance(model_dir, str) or not model_dir:
        return {}
    config_path = Path(model_dir).expanduser() / "config.yaml"
    if not config_path.is_file():
        return {}
    try:
        import yaml

        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return config if isinstance(config, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        values = value.replace(",", " ").split()
    elif isinstance(value, list):
        values = value
    else:
        return []
    out: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _expected_tool_names(model_dir: Any) -> list[str]:
    config = _model_config(model_dir)
    if not config:
        return []
    names = [str(tool["name"]) for tool in config.get("tools") or [] if isinstance(tool, dict) and tool.get("name")]
    if names:
        return names
    model = config.get("model") if isinstance(config.get("model"), dict) else {}
    task = str(model.get("task") or config.get("task") or config.get("task_type") or "").strip().upper()
    return [TASK_DEFAULT_TOOLS.get(task, "predict")]


def _required_imports(surface: dict[str, Any], model_dir: Any) -> list[str]:
    required: list[str] = []
    inference_runtime = surface.get("inference_runtime") if isinstance(surface.get("inference_runtime"), dict) else {}
    required.extend(_string_list(inference_runtime.get("required_imports")))
    config = _model_config(model_dir)
    for section_name in ("runtime", "environment", "execution"):
        section = config.get(section_name) if isinstance(config.get(section_name), dict) else {}
        required.extend(_string_list(section.get("required_imports")))
    required.extend(_string_list(config.get("required_imports")))
    return _string_list(required)


def _resolve_interpreter(key: str, value: str) -> tuple[str, str]:
    path = Path(value).expanduser()
    if not path.is_absolute():
        if key in MODEL_INTERPRETER_ENV_KEYS:
            return "", f"{key} must be an absolute path, got: {value}"
        found = shutil.which(value)
        if not found:
            return "", f"{key} is not resolvable on PATH: {value}"
        path = Path(found)
    if not path.exists():
        return "", f"{key} does not exist: {value}"
    if not os.access(path, os.X_OK):
        return "", f"{key} is not executable: {value}"
    return str(path), ""


def _check_required_imports(model_python: str, imports: list[str]) -> list[str]:
    issues: list[str] = []
    for module in imports:
        try:
            probe = subprocess.run(
                [
                    model_python,
                    "-c",
                    "import importlib, sys; importlib.import_module(sys.argv[1])",
                    module,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if probe.returncode == 0:
                continue
            detail = ((probe.stderr or probe.stdout or "").strip() or f"import {module} failed").splitlines()[-1]
        except subprocess.TimeoutExpired:
            detail = f"import {module} timed out after 60s"
        issues.append(f"MODEL_PYTHON cannot import required module '{module}' ({detail})")
    return issues


def check_inference_runtime(surface_path: Path) -> dict[str, Any]:
    if not surface_path.exists():
        return {"passed": False, "evidence": f"{surface_path.name} not found"}
    try:
        data = json.loads(surface_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"passed": False, "evidence": f"invalid JSON: {e}"}

    execution = data.get("execution") if isinstance(data.get("execution"), dict) else {}
    if str(execution.get("requested") or "local") == "vc" or str(execution.get("path_planned") or "") == "vc_submit":
        return {"passed": True, "evidence": "vc execution: container runtime is validated at vc submit preflight"}

    env = data.get("env") if isinstance(data.get("env"), dict) else {}
    inference_runtime = data.get("inference_runtime") if isinstance(data.get("inference_runtime"), dict) else {}
    resolved_inputs = data.get("resolved_inputs") if isinstance(data.get("resolved_inputs"), dict) else {}
    issues: list[str] = []

    declared = {key: str(env[key]) for key in INTERPRETER_ENV_KEYS if isinstance(env.get(key), str) and env[key]}
    if not (declared.get("MODEL_PYTHON") or declared.get("PYTHON_BIN")):
        issues.append("env must declare MODEL_PYTHON (absolute path to the model interpreter) for local execution")
    model_python = ""
    for key, value in declared.items():
        resolved, issue = _resolve_interpreter(key, value)
        if issue:
            issues.append(issue)
        elif key in MODEL_INTERPRETER_ENV_KEYS and not model_python:
            model_python = resolved

    import_probe_policy = str(inference_runtime.get("import_probe_policy") or "declared_only").strip().lower()
    required_imports = _required_imports(data, resolved_inputs.get("model_dir"))
    if import_probe_policy not in {"declared_only", "skip", "required"}:
        issues.append(
            f"inference_runtime.import_probe_policy must be one of declared_only, skip, required; got: {import_probe_policy}"
        )
    elif import_probe_policy == "skip":
        required_imports = []
    elif import_probe_policy == "required" and not required_imports:
        issues.append("inference_runtime.import_probe_policy=required needs required_imports from the surface or model config")
    if model_python and required_imports:
        import_issues = _check_required_imports(model_python, required_imports)
        if import_issues:
            candidates = _interpreter_candidates(resolved_inputs.get("model_dir"))
            suffix = f"; interpreter candidates: {', '.join(candidates)}" if candidates else ""
            issues.extend(f"{issue}{suffix}" for issue in import_issues)

    declared_tool = ""
    for value in (env.get("TOOL_NAME"), resolved_inputs.get("tool_name")):
        if isinstance(value, str) and value:
            declared_tool = value
            break
    if declared_tool:
        expected = _expected_tool_names(resolved_inputs.get("model_dir"))
        if expected and declared_tool not in expected:
            issues.append(f"tool name '{declared_tool}' is not declared by the model; expected one of: {', '.join(expected)}")

    if issues:
        return {"passed": False, "evidence": "; ".join(issues)}
    if required_imports:
        return {"passed": True, "evidence": f"interpreter, required imports ({', '.join(required_imports)}), and tool name verified"}
    return {"passed": True, "evidence": "interpreter and tool name verified; import probe skipped (no required_imports declared)"}


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
