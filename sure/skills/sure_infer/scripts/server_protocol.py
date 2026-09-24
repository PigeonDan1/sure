"""Shared model-server protocol resolution for sure_infer.

`transport` names the channel (``stdio`` = a local child process spoken to over
stdin/stdout); `protocol` names the framing of the bytes on that channel
(``stdio_jsonrpc`` = MCP JSON-RPC envelope, ``stdio_jsonl`` = one flat JSON
object per line). Resolution never infers framing from the channel: a server
block that omits ``protocol``/``mode`` defaults to MCP JSON-RPC. Line-oriented
servers must declare ``protocol: jsonl`` (or a jsonl alias) explicitly.
"""

from __future__ import annotations

from typing import Any

MCP_SERVER_PROTOCOL = "stdio_jsonrpc"
JSONL_SERVER_PROTOCOL = "stdio_jsonl"

_MCP_ALIASES = {"mcp", "jsonrpc", "json_rpc", "stdio_jsonrpc", "stdio_mcp"}
_JSONL_ALIASES = {"jsonl", "json_lines", "raw_stdio", "stdio", "stdio_jsonl"}


def resolve_server_protocol(server_cfg: dict[str, Any] | None) -> str:
    """Return the canonical framing protocol for a model's ``server`` block."""
    cfg = server_cfg if isinstance(server_cfg, dict) else {}
    configured = cfg.get("protocol") or cfg.get("mode")
    if configured in (None, ""):
        return MCP_SERVER_PROTOCOL
    value = str(configured).strip().lower().replace("-", "_")
    if value in _MCP_ALIASES:
        return MCP_SERVER_PROTOCOL
    if value in _JSONL_ALIASES:
        return JSONL_SERVER_PROTOCOL
    raise ValueError(
        "approved model server protocol is unsupported: "
        f"{configured!r}; expected MCP JSON-RPC or stdio JSONL"
    )
