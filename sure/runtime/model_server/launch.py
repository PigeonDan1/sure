"""Resolve the Python model server from the declared model contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def declared_server_command(config: dict[str, Any]) -> bool:
    server = config.get("server")
    return isinstance(server, dict) and bool(server.get("command"))


ADAPTER_NAME = "model_wrapper_mcp_server.py"


def _adapter_path(model_dir: Path) -> Path:
    """The adapter to serve a commandless model from: the bundle copy first.

    The onboard gate copies the adapter into the bundle precisely so it stays
    self-contained; the harness checkout path is only the resolution of last
    resort for bundles sealed before the copy existed.
    """
    bundled = model_dir / ADAPTER_NAME
    if bundled.is_file():
        return bundled
    return Path(__file__).resolve().parents[2] / "skills" / "sure_infer" / "scripts" / ADAPTER_NAME


def resolve_python_server_command(
    model_dir: Path,
    config: dict[str, Any],
    python_executable: str,
    inventory_command: Any,
) -> list[str]:
    if not declared_server_command(config):
        adapter = _adapter_path(model_dir)
        if adapter.parent == model_dir:
            # The bundle-local copy is launched from the bundle's own working
            # directory, so a bare relative spelling keeps the bundle
            # relocatable: no absolute path of the approving host leaks into
            # the command that the eval site later launches.
            return [python_executable, ADAPTER_NAME, "--model-dir", str(model_dir)]
        return [python_executable, str(adapter), "--model-dir", str(model_dir)]
    if not isinstance(inventory_command, list) or not inventory_command or not all(
        isinstance(item, str) and item for item in inventory_command
    ):
        raise ValueError("approved Model Python server_command is invalid")
    return [python_executable, *inventory_command[1:]]
