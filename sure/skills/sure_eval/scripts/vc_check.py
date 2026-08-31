#!/usr/bin/env python3
"""Gate script for the SUBMIT_EXECUTION_UNIT.

Probes vc availability (`which vc && vc info`) and validates that the produced
submit_result.json matches the user-requested execution surface:
- execution=vc must submit through vc.
- execution=local must run through the approved local runtime binding.
- execution=auto prefers vc when available; local auto fallback must explain why.
The environment-derived vc_available field is stamped into the artifact.

Called by the Sure hook with:
    python3 scripts/vc_check.py --run-dir <runDir> --produces <abs>

exit 0 = pass; non-zero = fail (stderr carries the repair text).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def evaluation_binding_mismatch(claimed: object, resolved: object) -> list[str]:
    """Fields where the submitted binding and an independently resolved one differ.

    Empty when there is nothing to compare: a host that cannot resolve the
    binding here records that and lets the run continue, because a gate that
    blocks on its own inability to answer is how runs deadlock.
    """
    if not isinstance(claimed, dict) or not isinstance(resolved, dict):
        return []
    return [
        f"{field} submitted={claimed.get(field)!r} resolved={resolved.get(field)!r}"
        for field in ("runtime_id", "lock_sha256", "engine_commit")
        if claimed.get(field) != resolved.get(field)
    ]


def claimed_evaluation_binding(data: dict) -> object:
    """Where the submission records it.

    The vc route puts it at the top level; the local docker and python routes
    keep the whole launch binding under deployment_binding. Looking in only one
    place made the comparison a no-op on two of the three routes.
    """
    claimed = data.get("evaluation_runtime")
    if claimed is None and isinstance(data.get("deployment_binding"), dict):
        claimed = data["deployment_binding"].get("evaluation_runtime")
    return claimed


def resolved_evaluation_binding(run_dir: Path) -> dict | None:
    """Resolve the run's Evaluation Runtime from this process, or None if it cannot.

    The hook spawns this script with the coding agent's own process
    environment, so what it resolves here does not inherit an `export` from the
    agent's shell.
    """
    eval_input_path = run_dir / "artifacts" / "eval_input_resolved.json"
    if not eval_input_path.is_file():
        return None
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from evaluation_runtime import EvaluationRuntimeError, _expected_binding
        from resolve_evaluation_engine import resolve_engine_root
    except ImportError:
        return None
    try:
        eval_input = json.loads(eval_input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    evaluation = eval_input.get("evaluation") if isinstance(eval_input.get("evaluation"), dict) else {}
    if evaluation.get("backend") not in (None, "external"):
        return None
    engine = evaluation.get("engine") if isinstance(evaluation.get("engine"), dict) else {}
    resolved = resolve_engine_root(str(engine.get("engine_root") or "") or None)
    if resolved is None:
        return None
    try:
        # _expected_binding, not ensure_evaluation_runtime: the latter falls back
        # to the attested environment, which is where a forged claim comes from.
        # A check that can answer from its own subject is not a check.
        return _expected_binding(resolved[1])
    except EvaluationRuntimeError:
        return None


def vc_is_available() -> bool:
    """Return True only when `which vc` AND `vc info` both succeed.

    Per the execution policy gate, `vc --version` MUST NOT be used to
    determine availability.
    """
    if not shutil.which("vc"):
        return False
    try:
        result = subprocess.run(
            ["vc", "info"], capture_output=True, text=True, timeout=30
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _execution_requested(data: dict, run_dir: Path) -> str:
    execution = data.get("execution")
    if isinstance(execution, dict) and isinstance(execution.get("requested"), str):
        return execution["requested"]
    if isinstance(data.get("execution_requested"), str):
        return data["execution_requested"]

    eval_input = _read_json(run_dir / "artifacts" / "eval_input_resolved.json")
    runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
    resolved_execution = runtime.get("execution") if isinstance(runtime.get("execution"), dict) else {}
    if isinstance(resolved_execution.get("requested"), str):
        return resolved_execution["requested"]

    execution_path = str(data.get("execution_path") or "")
    if execution_path == "vc_submit":
        return "vc"
    if execution_path in {"local_bash", "local_docker", "local_python"}:
        return "local"
    return "auto"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True, help="absolute path to submit_result.json")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    path = Path(args.produces)
    if not path.exists():
        print(f"submit_result.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"submit_result.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    available = vc_is_available()
    # Stamp the environment-derived availability into the artifact so the
    # in-process gate agrees with reality. Never trust a model-supplied
    # vc_available value; a forged false must not bypass local execution checks.
    if data.get("vc_available") != available:
        data["vc_available"] = available
        try:
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    # The submission records the Evaluation Runtime it carried; resolve one here
    # and say so when the two disagree. Same reasoning as vc_available above:
    # what the submission claims about itself is not evidence on its own.
    mismatch = evaluation_binding_mismatch(
        claimed_evaluation_binding(data), resolved_evaluation_binding(run_dir)
    )
    if mismatch:
        print(
            "SUBMIT_EXECUTION gate: the submitted Evaluation Runtime is not the one "
            "this host resolves: " + "; ".join(mismatch),
            file=sys.stderr,
        )
        return 1

    execution_path = data.get("execution_path", "")
    requested = _execution_requested(data, run_dir)
    local_paths = {"local_docker", "local_python"}

    if execution_path == "vc_submit":
        required = ("vc_job_id", "submission_token", "terminal_status_path")
        missing = [
            field
            for field in required
            if not isinstance(data.get(field), str) or not data[field].strip()
        ]
        if missing:
            print(
                "SUBMIT_EXECUTION gate: vc_submit requires non-empty control fields: "
                + ", ".join(missing),
                file=sys.stderr,
            )
            return 1

    if requested == "vc" and execution_path != "vc_submit":
        print(
            "SUBMIT_EXECUTION gate: user requested execution=vc, so "
            "submit_result.json.execution_path must be vc_submit.",
            file=sys.stderr,
        )
        return 1
    if requested == "vc" and not available:
        print(
            "SUBMIT_EXECUTION gate: user requested execution=vc, but `which vc && vc info` did not pass.",
            file=sys.stderr,
        )
        return 1
    if requested == "local" and execution_path not in local_paths:
        print(
            "SUBMIT_EXECUTION gate: user requested execution=local, so execution_path must be "
            "the approved local_docker or local_python route.",
            file=sys.stderr,
        )
        return 1
    if requested == "auto" and available and execution_path != "vc_submit":
        fallback_approved = bool(data.get("fallback_approved"))
        fallback_reason = data.get("local_fallback_reason", "")
        if not fallback_approved or not fallback_reason:
            print(
                "SUBMIT_EXECUTION gate: execution=auto and vc is available "
                "(`which vc && vc info` passed), so execution_path must be vc_submit. "
                "If this is an intentional local fallback, set fallback_approved=true "
                "and a non-empty local_fallback_reason.",
                file=sys.stderr,
            )
            return 1
    if requested == "auto" and not available and execution_path in local_paths and not data.get("local_fallback_reason"):
        data["local_fallback_reason"] = "execution=auto selected local execution because vc is unavailable"
        try:
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    print(f"vc_check OK: requested={requested}, vc_available={available}, execution_path={execution_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
