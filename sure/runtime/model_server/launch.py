"""Resolve the Python model server from the declared model contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def declared_server_command(config: dict[str, Any]) -> bool:
    server = config.get("server")
    return isinstance(server, dict) and bool(server.get("command"))


def resolve_python_server_command(
    model_dir: Path,
    config: dict[str, Any],
    python_executable: str,
    inventory_command: Any,
) -> list[str]:
    if not declared_server_command(config):
        adapter = Path(__file__).resolve().parents[2] / "skills" / "sure_infer" / "scripts" / "model_wrapper_mcp_server.py"
        return [python_executable, str(adapter), "--model-dir", str(model_dir)]
    if not isinstance(inventory_command, list) or not inventory_command or not all(
        isinstance(item, str) and item for item in inventory_command
    ):
        raise ValueError("approved Model Python server_command is invalid")
    return [python_executable, *inventory_command[1:]]
