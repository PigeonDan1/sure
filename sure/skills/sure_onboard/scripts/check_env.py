#!/usr/bin/env python3
"""Gate script for the BUILD_ENV unit.

Verifies env_ready is true and (when declared) the lockfile/docker image exists.
Called by the Sure hook:
    python3 scripts/check_env.py --run-dir <runDir> --produces <abs>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "runtime" / "harness"))
from model_child_env import model_child_env


LOCAL_RUNTIME_BACKENDS = {"uv", "pip", "conda", "pixi"}


def repo_root_for(run_dir: Path) -> Path:
    resolved = run_dir.expanduser().resolve()
    if resolved.parent.name == "runs" and resolved.parent.parent.name == ".sure":
        return resolved.parent.parent.parent
    return Path.cwd().resolve()


def normalize_model_dir(model_dir: Path | None, repo_root: Path) -> Path | None:
    if model_dir is None:
        return None
    model_dir = model_dir.expanduser()
    return model_dir if model_dir.is_absolute() else repo_root / model_dir


def resolve_declared_path(
    raw: object,
    *,
    run_dir: Path,
    artifact_path: Path,
    model_dir: Path | None,
    repo_root: Path,
) -> Path | None:
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    if path.is_absolute():
        return path
    candidates = []
    model_base = normalize_model_dir(model_dir, repo_root)
    if len(path.parts) >= 2 and path.parts[0] == "sure" and path.parts[1] == "models":
        candidates.append(repo_root / path)
    if model_base:
        candidates.append(model_base / path)
    candidates.append(repo_root / path)
    candidates.append(run_dir / "artifacts" / path)
    candidates.append(run_dir / path)
    candidates.append(artifact_path.parent / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else path


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def runtime_checks(data: dict[str, Any]) -> dict[str, Any]:
    checks = data.get("runtime_checks")
    return checks if isinstance(checks, dict) else {}


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def resolved_model_input(run_dir: Path) -> dict[str, Any] | None:
    return load_json(run_dir / "artifacts" / "model_input_resolved.json")


def resolve_python_executable(
    data: dict[str, Any],
    *,
    backend: str,
    run_dir: Path,
    artifact_path: Path,
    model_dir: Path | None,
    repo_root: Path,
) -> tuple[Path | None, str | None]:
    raw = data.get("python_executable")
    if not raw and isinstance(data.get("runtime_probe"), dict):
        raw = data["runtime_probe"].get("python_executable")
    if raw:
        resolved = resolve_declared_path(
            raw,
            run_dir=run_dir,
            artifact_path=artifact_path,
            model_dir=model_dir,
            repo_root=repo_root,
        )
        if not resolved or not resolved.exists():
            return None, f"BUILD_ENV gate: declared python_executable does not exist: {raw}"
        return resolved, None

    model_base = normalize_model_dir(model_dir, repo_root)
    if model_base:
        local_python = model_base / ".venv" / "bin" / "python"
        if local_python.exists():
            return local_python, None
        if backend == "uv":
            return (
                None,
                "BUILD_ENV gate: backend=uv requires model-local .venv/bin/python. "
                f"Expected {local_python}",
            )
    return None, None


def required_imports_for(data: dict[str, Any], model_dir: Path | None, repo_root: Path) -> list[str]:
    checks = runtime_checks(data)
    imports = string_list(checks.get("required_imports")) or string_list(checks.get("imports"))
    seen: set[str] = set()
    normalized: list[str] = []
    for name in imports:
        if name not in seen:
            normalized.append(name)
            seen.add(name)

    model_base = normalize_model_dir(model_dir, repo_root)
    if model_base and (model_base / "model.py").exists() and "model" not in seen:
        normalized.append("model")
    return normalized


def requested_device(run_dir: Path) -> str:
    resolved = resolved_model_input(run_dir) or {}
    device = str(resolved.get("device") or "").strip().lower()
    return device


def require_cuda_for(data: dict[str, Any], run_dir: Path) -> bool:
    checks = runtime_checks(data)
    if checks.get("require_cuda") is True:
        return True
    return requested_device(run_dir) == "cuda"


def run_runtime_probe(
    *,
    python_executable: Path,
    cwd: Path,
    imports: list[str],
    require_cuda: bool,
) -> tuple[bool, str]:
    script = r"""
import importlib
import json
import sys
import traceback

imports = json.loads(sys.argv[1])
require_cuda = sys.argv[2] == "1"
result = {
    "python_executable": sys.executable,
    "python_version": sys.version.split()[0],
    "imports": {},
}
ok = True
for name in imports:
    try:
        importlib.import_module(name)
        result["imports"][name] = "ok"
    except BaseException as exc:
        ok = False
        result["imports"][name] = {
            "error": f"{type(exc).__name__}: {exc}",
            "traceback_tail": traceback.format_exc().splitlines()[-8:],
        }
if require_cuda:
    try:
        import torch
        cuda_available = bool(torch.cuda.is_available())
        result["torch"] = {
            "version": getattr(torch, "__version__", None),
            "cuda": getattr(getattr(torch, "version", None), "cuda", None),
            "cuda_available": cuda_available,
            "device_count": torch.cuda.device_count() if hasattr(torch, "cuda") else 0,
        }
        if not cuda_available:
            ok = False
    except BaseException as exc:
        ok = False
        result["torch"] = {
            "error": f"{type(exc).__name__}: {exc}",
            "traceback_tail": traceback.format_exc().splitlines()[-8:],
        }
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
sys.exit(0 if ok else 31)
"""
    env = model_child_env()
    env["PYTHONPATH"] = str(cwd)
    try:
        proc = subprocess.run(
            [str(python_executable), "-c", script, json.dumps(imports), "1" if require_cuda else "0"],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return False, (
            "BUILD_ENV gate: runtime probe timed out after 90s. "
            f"stdout={exc.stdout or ''}\nstderr={exc.stderr or ''}"
        )
    detail = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode != 0:
        return False, (
            "BUILD_ENV gate: model runtime probe failed. "
            f"python={python_executable}, cwd={cwd}, imports={imports}, "
            f"require_cuda={require_cuda}\n{detail}"
        )
    return True, detail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()

    path = Path(args.produces).expanduser().resolve()
    if not path.exists():
        print(f"build_env_result.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"build_env_result.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    if not data.get("env_ready"):
        failures = data.get("failures") or []
        detail = "\n  - " + "\n  - ".join(failures) if failures else ""
        print(f"BUILD_ENV gate failed: env_ready is false.{detail}", file=sys.stderr)
        return 1

    run_dir = Path(args.run_dir).expanduser().resolve()
    repo_root = repo_root_for(run_dir)
    model_dir = Path(str(data["model_dir"])).expanduser() if data.get("model_dir") else None

    # If a lockfile path is declared, it should exist. Relative paths are
    # resolved against model_dir first, then run artifacts.
    backend = data.get("backend", "")
    lockfile = data.get("lockfile_path")
    if lockfile and backend != "docker":
        resolved_lockfile = resolve_declared_path(
            lockfile,
            run_dir=run_dir,
            artifact_path=path,
            model_dir=model_dir,
            repo_root=repo_root,
        )
        if not resolved_lockfile or not resolved_lockfile.exists():
            print(f"BUILD_ENV gate: declared lockfile does not exist: {lockfile}", file=sys.stderr)
            return 1
    if backend == "docker" and not data.get("docker_image"):
        print("BUILD_ENV gate: backend=docker requires docker_image.", file=sys.stderr)
        return 1
    log_path = data.get("log_path")
    if log_path:
        resolved_log = resolve_declared_path(
            log_path,
            run_dir=run_dir,
            artifact_path=path,
            model_dir=model_dir,
            repo_root=repo_root,
        )
        if not resolved_log or not resolved_log.exists():
            print(f"BUILD_ENV gate: declared log_path does not exist: {log_path}", file=sys.stderr)
            return 1

    if backend in LOCAL_RUNTIME_BACKENDS:
        python_executable, python_error = resolve_python_executable(
            data,
            backend=backend,
            run_dir=run_dir,
            artifact_path=path,
            model_dir=model_dir,
            repo_root=repo_root,
        )
        if python_error:
            print(python_error, file=sys.stderr)
            return 1
        model_base = normalize_model_dir(model_dir, repo_root)
        if python_executable:
            probe_cwd = model_base if model_base and model_base.exists() else repo_root
            imports = required_imports_for(data, model_dir, repo_root)
            ok, detail = run_runtime_probe(
                python_executable=python_executable,
                cwd=probe_cwd,
                imports=imports,
                require_cuda=require_cuda_for(data, run_dir),
            )
            if not ok:
                print(detail, file=sys.stderr)
                return 1

    print(f"check_env OK: env_ready=true, backend={backend}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
