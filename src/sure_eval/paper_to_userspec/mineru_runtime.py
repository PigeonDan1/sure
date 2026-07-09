"""MinerU runtime discovery and environment helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

DEFAULT_MINERU_TIMEOUT_SEC = 600
LEGACY_MINERU_HINT = Path(
    "/mnt/cloudstorfs/sjtu_home/bowen.wang/sure-eval-sandbox/.venv-mineru/bin/mineru"
)
PREFERRED_CLUSTER_CACHE = Path("/hpc_stor03/sjtu_home/bowen.wang/.cache")


def mineru_timeout_sec(env: Mapping[str, str] | None = None) -> int:
    source = os.environ if env is None else env
    raw = source.get("SURE_PAPER_MINERU_TIMEOUT_SEC")
    if not raw:
        return DEFAULT_MINERU_TIMEOUT_SEC
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MINERU_TIMEOUT_SEC
    return value if value > 0 else DEFAULT_MINERU_TIMEOUT_SEC


def discover_mineru_executable(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Discover a MinerU-compatible CLI without importing MinerU."""
    source = os.environ if env is None else env
    hints = _mineru_hints()
    configured = source.get("SURE_PAPER_MINERU_BIN")
    if configured:
        path = Path(configured).expanduser()
        if not path.exists():
            return _unavailable(
                "mineru_executable_missing",
                f"SURE_PAPER_MINERU_BIN points to a missing file: {path}",
                configured_path=configured,
                hints=hints,
            )
        if not path.is_file() or not os.access(path, os.X_OK):
            return _unavailable(
                "mineru_executable_not_executable",
                f"SURE_PAPER_MINERU_BIN is not an executable file: {path}",
                configured_path=configured,
                hints=hints,
            )
        resolved = path.resolve()
        return {
            "available": True,
            "name": _command_name(resolved),
            "path": str(resolved),
            "source": "SURE_PAPER_MINERU_BIN",
            "configured_path": configured,
            "hints": hints,
            "error_type": None,
            "error_message": None,
        }

    mineru = shutil.which("mineru", path=source.get("PATH"))
    if mineru:
        return _available("mineru", mineru, "PATH", hints)

    magic_pdf = shutil.which("magic-pdf", path=source.get("PATH"))
    if magic_pdf:
        return _available("magic-pdf", magic_pdf, "PATH", hints)

    return _unavailable(
        "mineru_executable_unavailable",
        "Could not find MinerU CLI command 'mineru' or legacy 'magic-pdf' on PATH. "
        "Set SURE_PAPER_MINERU_BIN to an executable MinerU CLI if it is installed elsewhere.",
        hints=hints,
    )


def build_mineru_env(base_env: Mapping[str, str] | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Build the subprocess env and return the selected MinerU-related keys."""
    env = dict(os.environ if base_env is None else base_env)
    model_source = (
        env.get("SURE_PAPER_MINERU_MODEL_SOURCE")
        or env.get("MINERU_MODEL_SOURCE")
        or "modelscope"
    )
    cache_home = (
        env.get("SURE_PAPER_MINERU_CACHE_HOME")
        or env.get("XDG_CACHE_HOME")
        or _default_cache_home(env)
    )
    modelscope_cache = (
        env.get("SURE_PAPER_MODELSCOPE_CACHE")
        or env.get("MODELSCOPE_CACHE")
        or str(Path(cache_home).expanduser() / "modelscope")
    )

    env["MINERU_MODEL_SOURCE"] = model_source
    env["XDG_CACHE_HOME"] = cache_home
    env["MODELSCOPE_CACHE"] = modelscope_cache
    selected = {
        "MINERU_MODEL_SOURCE": model_source,
        "XDG_CACHE_HOME": cache_home,
        "MODELSCOPE_CACHE": modelscope_cache,
    }
    return env, selected


def probe_mineru_version(
    executable: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    timeout: int = 15,
) -> dict[str, Any]:
    command = [str(executable), "--version"]
    try:
        proc = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            env=dict(env) if env is not None else None,
        )
    except Exception as exc:
        return {
            "command": command,
            "ok": False,
            "returncode": None,
            "version": None,
            "stdout": "",
            "stderr": "",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
    output = (proc.stdout or proc.stderr or "").strip()
    return {
        "command": command,
        "ok": proc.returncode == 0 and bool(output),
        "returncode": proc.returncode,
        "version": output.splitlines()[0] if output else None,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "error_type": None if proc.returncode == 0 else "mineru_version_failed",
        "error_message": None if proc.returncode == 0 else (proc.stderr or proc.stdout or "").strip(),
    }


def mineru_runtime_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = os.environ if env is None else env
    mineru_env, selected_env = build_mineru_env(source)
    discovery = discover_mineru_executable(source)
    version = None
    if discovery.get("available"):
        version = probe_mineru_version(discovery["path"], env=mineru_env)
    return {
        "python_executable": sys.executable,
        "mineru_discovery": discovery,
        "version_probe": version,
        "selected_env": selected_env,
        "timeout_sec": mineru_timeout_sec(source),
    }


def _available(name: str, path: str, source: str, hints: list[str]) -> dict[str, Any]:
    return {
        "available": True,
        "name": name,
        "path": str(Path(path).expanduser().resolve()),
        "source": source,
        "configured_path": None,
        "hints": hints,
        "error_type": None,
        "error_message": None,
    }


def _unavailable(
    error_type: str,
    error_message: str,
    *,
    configured_path: str | None = None,
    hints: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "name": None,
        "path": None,
        "source": None,
        "configured_path": configured_path,
        "hints": hints or [],
        "error_type": error_type,
        "error_message": error_message,
    }


def _command_name(path: Path) -> str:
    return "magic-pdf" if path.name == "magic-pdf" else "mineru"


def _mineru_hints() -> list[str]:
    return [str(LEGACY_MINERU_HINT)] if LEGACY_MINERU_HINT.exists() else []


def _default_cache_home(env: Mapping[str, str]) -> str:
    if PREFERRED_CLUSTER_CACHE.exists():
        return str(PREFERRED_CLUSTER_CACHE)
    home = env.get("HOME")
    return str(Path(home).expanduser() / ".cache") if home else str(Path.home() / ".cache")
