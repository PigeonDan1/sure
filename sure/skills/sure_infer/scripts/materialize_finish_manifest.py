"""Write a final infer manifest from validated on-disk run evidence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


SUCCESS_STATUSES = {"success", "succeeded", "ok", "completed", "complete"}


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def materialize_finish_manifest(run_dir: Path, package_dir: Path, product_dir: Path) -> Path:
    run_dir = run_dir.resolve()
    run = _read_object(run_dir / "run.json")
    if run.get("skillName") != "sure_infer" or run.get("runId") != run_dir.name:
        raise ValueError("run.json does not identify this sure_infer run")
    if Path(str(run.get("runDir") or "")).resolve() != run_dir:
        raise ValueError("run.json runDir does not match the run directory")

    execution = _read_object(run_dir / "artifacts" / "execution_result.json")
    report = _read_object(run_dir / "artifacts" / "main_agent_run_report.json")
    if execution.get("job_status") != "succeeded" or execution.get("exit_code") != 0:
        raise ValueError("a successful finish requires successful execution_result.json")
    if str(report.get("status") or "").lower() not in SUCCESS_STATUSES or report.get("report_persisted") is not True:
        raise ValueError("a successful finish requires a persisted successful run report")
    if Path(str(execution.get("product_dir") or "")).resolve() != product_dir.resolve():
        raise ValueError("validated product directory disagrees with execution_result.json")

    input_path = run_dir / "artifacts" / "eval_input_resolved.json"
    resolved_input = _read_object(input_path) if input_path.is_file() else {}
    user_input = resolved_input.get("user_input")
    if not isinstance(user_input, dict):
        user_input = {"request": run.get("args", "")}
    skill = _read_object(package_dir / "sure.skill.json")
    artifacts = []
    for requirement in skill.get("artifacts", []):
        if not isinstance(requirement, dict) or requirement.get("required") is not True:
            continue
        relative_path = requirement.get("path")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("required artifact must declare a path")
        artifact_path = (run_dir / relative_path).resolve()
        if not artifact_path.is_relative_to(run_dir) or not artifact_path.is_file():
            raise ValueError(f"required infer artifact is missing or outside the run: {relative_path}")
        artifacts.append({"type": requirement["type"], "path": str(artifact_path)})

    manifest = {
        "schema_version": "sure.eval.manifest.v1",
        "run_id": run["runId"],
        "skill_name": "sure_infer",
        "status": "success",
        "created_at": run["startedAt"],
        "inputs": {key: user_input[key] for key in ("model", "datasets", "metrics", "execution", "request") if key in user_input},
        "outputs": {"run_dir": str(product_dir), "product_dir": str(product_dir), "execution_path_actual": report["execution_path_actual"]},
        "validation": {"gate": "run_report", "status": "pass"},
        "artifacts": artifacts,
    }
    target = run_dir / "manifest.json"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=run_dir, prefix=".manifest-", suffix=".tmp", encoding="utf-8", delete=False) as handle:
            temporary_path = Path(handle.name)
            json.dump(manifest, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.chmod(temporary_path, target.stat().st_mode & 0o777 if target.exists() else 0o644)
        temporary_path.replace(target)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return target
