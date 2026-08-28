#!/usr/bin/env python3
"""Resolve an approved SURE model from the read-only NFS trust root."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from deployment_binding import DeploymentBindingError, load_deployment_binding

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.site.loader import load_site_policy

_configured_policy = load_site_policy()
APPROVED_MODELS_ROOT = (
    Path(_configured_policy["policy"]["storage"]["approved_models_roots"][0])
    if _configured_policy
    else None
)
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SUCCESSFUL_VERDICT_STATUSES = frozenset({"pass", "passed", "success"})


def configured_approved_models_root() -> Path:
    resolved = load_site_policy(required=True)
    return Path(resolved["policy"]["storage"]["approved_models_roots"][0])


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _verdict_path(model_dir: Path | None) -> Path | None:
    if model_dir is None:
        return None
    for path in (model_dir / "artifacts" / "verdict.json", model_dir / "verdict.json"):
        if path.is_file() and _is_relative_to(path.resolve(), model_dir):
            return path
    return None


def verdict_is_ready(model_dir: Path | None) -> bool:
    """A bundle is only verdict-ready when the verdict says the model passed.

    The file existing proves a transformation ran, not that it succeeded; a
    bundle sealed alongside a failed verdict would otherwise resolve as ready.
    """
    ready, _, _ = _successful_verdict(_verdict_path(model_dir))
    return ready


def _checks(model_dir: Path | None) -> dict[str, bool]:
    if model_dir is None:
        return {
            "model_dir_exists": False,
            "config_yaml": False,
            "model_spec_yaml": False,
            "model_py": False,
            "server_py": False,
            "verdict_json": False,
            "artifacts_verdict_json": False,
            "deployment_ready_json": False,
            "runtime_inventory_json": False,
            "package_gate_json": False,
        }
    def approved_file(relative: str) -> bool:
        path = model_dir / relative
        return path.is_file() and _is_relative_to(path.resolve(), model_dir)

    return {
        "model_dir_exists": model_dir.is_dir(),
        "config_yaml": approved_file("config.yaml"),
        "model_spec_yaml": approved_file("model.spec.yaml"),
        "model_py": approved_file("model.py"),
        "server_py": approved_file("server.py"),
        "verdict_json": approved_file("verdict.json"),
        "artifacts_verdict_json": approved_file("artifacts/verdict.json"),
        "deployment_ready_json": approved_file("artifacts/deployment_ready.json"),
        "runtime_inventory_json": approved_file("artifacts/runtime_inventory.json"),
        "package_gate_json": approved_file("artifacts/package_gate.json"),
    }


def _successful_verdict(path: Path | None) -> tuple[bool, str | None, str | None]:
    if path is None:
        return False, None, "approved model verdict is missing"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, None, f"approved model verdict is invalid: {exc}"
    if not isinstance(value, dict):
        return False, None, "approved model verdict must be a JSON object"
    status = str(value.get("status") or "")
    if status.lower() not in SUCCESSFUL_VERDICT_STATUSES:
        return False, status or None, f"approved model verdict status is not successful: {status!r}"
    return True, status, None


def resolve_approved_model_identity(
    model: str,
    *,
    approved_root: Path | None = APPROVED_MODELS_ROOT,
) -> dict[str, Any]:
    """Resolve approved model identity without requiring an inference runtime."""

    if not MODEL_ID_RE.fullmatch(model):
        raise ValueError(f"invalid model id {model!r}; path separators and aliases are not allowed")
    root = (approved_root or configured_approved_models_root()).expanduser().resolve()
    requested = root / model
    if requested.is_symlink():
        raise ValueError(f"approved model id must name a real NFS directory, not a symlink alias: {requested}")
    model_dir = requested.resolve(strict=False)
    if not _is_relative_to(model_dir, root):
        raise ValueError(f"approved model path escapes NFS root: {requested} -> {model_dir}")
    selected = model_dir if model_dir.is_dir() else None
    verdict = _verdict_path(selected)
    checks = _checks(selected)
    verdict_ready, verdict_status, verdict_error = _successful_verdict(verdict)
    config_ready = checks["config_yaml"]
    identity_error = verdict_error
    if selected is not None and not config_ready:
        identity_error = "approved model config.yaml is missing"
    return {
        "schema": "sure.approved_model.identity.v1",
        "ok": bool(selected and config_ready and verdict_ready),
        "model": model,
        "model_dir": str(selected) if selected else None,
        "source": "approved_nfs_models",
        "approved_models_root": str(root),
        "config_path": str((selected / "config.yaml").resolve()) if selected and config_ready else None,
        "verdict_path": str(verdict.resolve()) if verdict else None,
        "verdict_status": verdict_status,
        "verdict_ready": verdict_ready,
        "identity_error": identity_error,
        "checks": checks,
    }


def resolve_approved_model(model: str, *, approved_root: Path | None = APPROVED_MODELS_ROOT) -> dict[str, Any]:
    identity = resolve_approved_model_identity(model, approved_root=approved_root)
    selected = Path(identity["model_dir"]) if identity["model_dir"] else None
    deployment_binding = None
    deployment_error = None
    if selected is not None:
        try:
            deployment_binding = load_deployment_binding(selected, model)
        except DeploymentBindingError as exc:
            deployment_error = str(exc)
    runtime_ready = deployment_binding is not None
    verdict_ready = bool(identity["verdict_ready"])
    return {
        "schema": "sure.eval.approved_model_resolution.v1",
        "ok": bool(identity["ok"] and runtime_ready),
        "model": model,
        "model_dir": identity["model_dir"],
        "source": identity["source"],
        "approved_models_root": identity["approved_models_root"],
        "verdict_path": identity["verdict_path"],
        "verdict_status": identity["verdict_status"],
        "runtime_ready": runtime_ready,
        "verdict_ready": verdict_ready,
        "deployment_binding": deployment_binding,
        "deployment_error": deployment_error,
        "identity_error": identity["identity_error"],
        "checks": identity["checks"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve a model from the approved NFS model root")
    parser.add_argument("--model", required=True)
    parser.add_argument("--require-verdict", action="store_true")
    parser.add_argument("--require-runtime-files", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()

    try:
        payload = resolve_approved_model(args.model)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    ok = bool(payload["ok"])
    if args.require_runtime_files:
        ok = ok and payload["runtime_ready"]
    if args.require_verdict:
        ok = ok and payload["verdict_ready"]
    payload["ok"] = bool(ok)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    if not ok:
        print(
            f"approved model is not ready under {payload['approved_models_root']}: {args.model}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
