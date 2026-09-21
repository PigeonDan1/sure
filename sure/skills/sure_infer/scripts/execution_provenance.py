#!/usr/bin/env python3
"""Host-owned execution provenance for SURE-EVAL launches."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation_runtime import evaluation_child_environment


PROVENANCE_SCHEMA = "sure.eval.execution_provenance.v1"
PROVENANCE_ENV_KEYS = {
    "SURE_EVAL_EXECUTION_PROVENANCE": "path",
    "SURE_HARNESS_COMMIT": "harness_commit",
    "SURE_EVALUATION_ENGINE_COMMIT": "evaluation_engine_commit",
    "SURE_EVALUATION_RUNTIME_ID": "evaluation_runtime_id",
    "SURE_EVALUATION_LOCK_SHA256": "evaluation_runtime_lock_sha256",
    "SURE_EVAL_CONTAINER_IMAGE_DIGEST": "image_digest",
}


class ExecutionProvenanceError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_commit(root: Path) -> tuple[str | None, str | None]:
    if not root.exists():
        return None, f"path does not exist: {root}"
    try:
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=evaluation_child_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git unavailable: {exc}"
    value = completed.stdout.strip()
    if completed.returncode == 0 and value:
        return value, None
    detail = (completed.stderr or completed.stdout or f"exit={completed.returncode}").strip()
    return None, detail or "git rev-parse produced no commit"


def _required_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return str(value) if value not in (None, "") else None


def build_execution_provenance(
    *,
    harness_root: Path,
    evaluation_runtime: dict[str, Any] | None,
    image_digest: str | None = None,
    image_ref: str | None = None,
    require_harness_commit: bool = False,
    require_evaluation_runtime: bool = False,
) -> dict[str, Any]:
    harness_commit, harness_reason = _git_commit(harness_root)
    if require_harness_commit and not harness_commit:
        raise ExecutionProvenanceError(f"cannot resolve harness commit before submission: {harness_reason}")

    evaluation_runtime_payload = dict(evaluation_runtime or {})
    evaluation_engine_commit = _required_text(evaluation_runtime_payload, "engine_commit")
    unavailable: dict[str, str] = {}
    if harness_reason:
        unavailable["harness_commit"] = harness_reason
    if require_evaluation_runtime and not evaluation_runtime_payload:
        raise ExecutionProvenanceError("Evaluation Runtime binding is required before submission")
    if evaluation_runtime_payload and not evaluation_engine_commit:
        unavailable["evaluation_engine_commit"] = "Evaluation Runtime binding has no engine_commit"
    if require_evaluation_runtime and not evaluation_engine_commit:
        raise ExecutionProvenanceError(unavailable["evaluation_engine_commit"])

    return {
        "schema": PROVENANCE_SCHEMA,
        "generated_at": _utc_now(),
        "generated_by": "Harness host submission",
        "harness_commit": harness_commit,
        "evaluation_engine_commit": evaluation_engine_commit,
        "evaluation_runtime_id": _required_text(evaluation_runtime_payload, "runtime_id"),
        "evaluation_runtime_lock_sha256": _required_text(evaluation_runtime_payload, "lock_sha256"),
        "evaluation_runtime_manifest": _required_text(evaluation_runtime_payload, "manifest_path"),
        "evaluation_runtime_engine_root": _required_text(evaluation_runtime_payload, "engine_root"),
        "image_digest": image_digest,
        "image_ref": image_ref,
        "unavailable": unavailable,
    }


def write_execution_provenance(path: Path, provenance: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def execution_provenance_env(path: Path, provenance: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {"SURE_EVAL_EXECUTION_PROVENANCE": str(path)}
    for env_key, field in PROVENANCE_ENV_KEYS.items():
        if field == "path":
            continue
        value = provenance.get(field)
        if value not in (None, ""):
            values[env_key] = str(value)
    return values


def load_execution_provenance_from_environment() -> dict[str, Any]:
    path = os.environ.get("SURE_EVAL_EXECUTION_PROVENANCE", "").strip()
    if not path:
        return {}
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
