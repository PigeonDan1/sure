#!/usr/bin/env python3
"""Deterministic MCP protocol smoke driver for the SURE-TRANS adapter.

Runs inside the adapter image. Spawns the adapter MCP server and drives the
stdin/stdout JSON-RPC protocol with bounded deadlines: initialize,
tools/list, tools/call, shutdown. Every read is deadline-bounded and the
server process is killed on timeout, so this script always terminates and
always writes --produces evidence (even on failure).
"""
from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

SCHEMA = "sure.trans.mcp_smoke.v1"
STDERR_TAIL_LINES = 200


def tool_arguments(tool: str, audio: Path) -> dict[str, str]:
    if tool == "synthesize_speech":
        return {
            "text": "SURE smoke test",
            "prompt_audio_path": str(audio),
        }
    if tool == "convert_voice":
        return {
            "source_audio_path": str(audio),
            "reference_audio_path": str(audio),
        }
    return {"audio_path": str(audio)}


def primary_output_field(tool: str) -> str:
    return "audio_path" if tool in {"synthesize_speech", "convert_voice"} else "text"


def _read_line(fd: int, buffer: bytearray, deadline: float) -> str | None:
    """Read one line from fd before deadline; None on timeout or EOF."""
    while True:
        newline = buffer.find(b"\n")
        if newline >= 0:
            line = bytes(buffer[: newline + 1])
            del buffer[: newline + 1]
            return line.decode("utf-8", errors="replace").rstrip("\n")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        ready, _, _ = select.select([fd], [], [], min(remaining, 1.0))
        if not ready:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            return None
        buffer.extend(chunk)


def _drain_stderr(proc: subprocess.Popen, tail: deque, log_handle) -> None:
    if proc.stderr is None:
        return
    for raw in proc.stderr:
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        tail.append(line)
        while len(tail) > STDERR_TAIL_LINES:
            tail.popleft()
        if log_handle is not None:
            log_handle.write(line + "\n")
            log_handle.flush()


def _send(proc: subprocess.Popen, request: dict, deadline: float) -> bool:
    if proc.stdin is None:
        return False
    payload = json.dumps(request, ensure_ascii=False) + "\n"
    try:
        proc.stdin.write(payload.encode("utf-8"))
        proc.stdin.flush()
    except (BrokenPipeError, OSError):
        return False
    return time.monotonic() <= deadline


def _read_response(
    fd: int, buffer: bytearray, expected_id: int, deadline: float, junk_tail: deque[str]
) -> tuple[bool, dict]:
    while True:
        line = _read_line(fd, buffer, deadline)
        if line is None:
            return False, {}
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            junk_tail.append(line)
            continue
        if not isinstance(payload, dict) or payload.get("id") != expected_id:
            return False, payload
        if "error" in payload:
            return False, payload
        return "result" in payload, payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive the adapter MCP JSON-RPC protocol with bounded deadlines.")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--tool", default="transcribe_audio")
    parser.add_argument("--server-command", nargs="*", default=["python", "/opt/sure_trans/server.py"])
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--produces", required=True)
    parser.add_argument("--server-stderr-log")
    args = parser.parse_args()

    audio = Path(args.audio)
    produces = Path(args.produces)
    steps: dict = {
        "initialize": {"ok": False},
        "tools_list": {"ok": False},
        "tools_call": {"ok": False, "output_nonempty": False, "text_nonempty": False},
        "shutdown": {"ok": False},
    }
    server_stderr: deque[str] = deque()
    junk_tail: deque[str] = deque()
    error: str | None = None
    started = time.monotonic()
    deadline = started + args.timeout

    stderr_handle = None
    if args.server_stderr_log:
        try:
            stderr_handle = open(args.server_stderr_log, "a", encoding="utf-8")
        except OSError:
            stderr_handle = None

    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            args.server_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        threading.Thread(target=_drain_stderr, args=(proc, server_stderr, stderr_handle), daemon=True).start()
        stdout_buffer = bytearray()

        ok, payload = False, {}
        if _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, deadline):
            ok, payload = _read_response(proc.stdout.fileno(), stdout_buffer, 1, deadline, junk_tail)
        if ok:
            steps["initialize"] = {
                "ok": True,
                "protocolVersion": str((payload.get("result") or {}).get("protocolVersion") or ""),
            }
        else:
            raise RuntimeError(f"initialize step failed: {json.dumps(payload, ensure_ascii=False)[:500]}")

        if _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, deadline):
            ok, payload = _read_response(proc.stdout.fileno(), stdout_buffer, 2, deadline, junk_tail)
        tools = []
        if ok:
            tools = [item.get("name") for item in (payload.get("result") or {}).get("tools", []) if isinstance(item, dict)]
        steps["tools_list"] = {"ok": ok and args.tool in tools, "tools": tools}
        if not steps["tools_list"]["ok"]:
            raise RuntimeError(f"tools/list step failed for tool {args.tool!r}: tools={tools}")

        if _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": args.tool, "arguments": tool_arguments(args.tool, audio)},
            },
            deadline,
        ):
            ok, payload = _read_response(proc.stdout.fileno(), stdout_buffer, 3, deadline, junk_tail)
        text = ""
        if ok:
            try:
                content = (payload.get("result") or {}).get("content") or []
                text = json.loads(str(content[0].get("text") or ""))
            except (IndexError, KeyError, TypeError, json.JSONDecodeError):
                text = ""
        primary_field = primary_output_field(args.tool)
        primary_value = ""
        if isinstance(text, dict):
            primary_value = str(text.get(primary_field) or "")
        steps["tools_call"] = {
            "ok": ok and bool(primary_value),
            "primary_field": primary_field,
            "output_nonempty": bool(primary_value),
            "text_nonempty": bool(primary_value) if primary_field == "text" else False,
            primary_field: primary_value[:500],
        }
        if not steps["tools_call"]["ok"]:
            raise RuntimeError(f"tools/call step failed: {json.dumps(payload, ensure_ascii=False)[:500]}")

        if _send(proc, {"jsonrpc": "2.0", "id": 4, "method": "shutdown", "params": {}}, deadline):
            ok, payload = _read_response(proc.stdout.fileno(), stdout_buffer, 4, deadline, junk_tail)
        steps["shutdown"] = {"ok": ok}
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
        try:
            server_code = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            if server_code != 0 and not error:
                raise RuntimeError(f"server exited {server_code} after shutdown")
        except subprocess.TimeoutExpired:
            raise RuntimeError("server did not exit after shutdown within the deadline")
    except RuntimeError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - evidence must always be written
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()

    status = "passed" if error is None else "failed"
    payload = {
        "schema": SCHEMA,
        "status": status,
        "tool": args.tool,
        "audio": str(audio),
        "initialize": steps["initialize"],
        "tools_list": steps["tools_list"],
        "tools_call": steps["tools_call"],
        "shutdown": steps["shutdown"],
        "server_command": args.server_command,
        "server_stderr_tail": list(server_stderr)[-STDERR_TAIL_LINES:],
        "stdout_junk_count": len(junk_tail),
        "stdout_junk_tail": list(junk_tail)[-STDERR_TAIL_LINES:],
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "error": error,
    }
    try:
        produces.parent.mkdir(parents=True, exist_ok=True)
        produces.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as write_error:
        print(f"cannot write evidence {produces}: {write_error}", file=sys.stderr)
        return 1
    if stderr_handle is not None:
        stderr_handle.close()
    print(produces)
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
