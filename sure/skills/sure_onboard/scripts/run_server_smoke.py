#!/usr/bin/env python3
"""Drive a model's declared server over stdio and prove the declared framing answers.

A model declares both halves of the contract in its `config.yaml`:

    server:
      command: [".venv/bin/python", "server.py"]   # the channel: a local child process
      protocol: mcp                                # the framing on the channel

`protocol: mcp` (JSON-RPC, the default when omitted) is driven with
initialize / tools/list / tools/call / shutdown. `protocol: jsonl` is one flat
JSON object per line with a single JSON line back and no handshake. The
resolution and its alias table live in
`sure/skills/sure_infer/scripts/server_protocol.py`; the VALIDATE_INFER gate
resolves the declared protocol and passes it here, so this script only drives
what it is told and stays stdlib-only -- it runs under the harness Python or a
model venv, whichever the gate hands it.

Every read is deadline-bounded and the process is killed on timeout, so this
script always terminates and always writes `--produces` evidence, pass or fail.

Composed by the gate (sure/skills/sure_onboard/scripts/run_validate.py):

    run_server_smoke.py --server-command '["python","server.py"]' --cwd <model_dir> \
        --protocol jsonl --tool transcribe_audio \
        --arguments-json '{"audio_path": "/abs/fixture.wav"}' \
        --produces <run_dir>/artifacts/server_smoke.json
"""
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

# The environment a model server is started in has one home, the same module the
# infer client starts its server with. A runner that hands the child its own
# `os.environ` instead would judge the model under interpreter state the client
# never gives it -- and this runner is itself started under a harness runtime
# that exports its own `PYTHONHOME`, which a child on another Python then reads
# as its stdlib base.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "runtime" / "harness"))
from model_child_env import model_child_env

SCHEMA = "sure.onboard.server_smoke.v1"
STDERR_TAIL_LINES = 200
MCP_PROTOCOL = "mcp"
JSONL_PROTOCOL = "jsonl"
MCP_STEPS = ("initialize", "tools_list", "tools_call", "shutdown")


def output_is_nonempty(payload: Any) -> bool:
    """Whether a tool response proves the server produced something.

    A payload counts when it is substantive and every path it names exists as a
    non-empty file -- a server that returns a path to nothing has not produced
    output. ponytail: a payload whose only content is booleans passes; that is
    as much as a framing-agnostic driver can know. Sharpen per task if a model
    abuses it.
    """
    if isinstance(payload, str):
        return bool(payload.strip())
    if isinstance(payload, (list, dict)):
        return bool(payload)
    return payload is not None


def _path_fields_exist(payload: Any) -> str:
    """Return a violation message when a named output path is missing or empty."""
    if not isinstance(payload, dict):
        return ""
    for key, value in payload.items():
        if not isinstance(value, str) or not value:
            continue
        if key == "path" or key.endswith("_path"):
            candidate = Path(value)
            if not candidate.is_file():
                return f"{key} names a file that does not exist: {value}"
            if candidate.stat().st_size == 0:
                return f"{key} names an empty file: {value}"
    return ""


def extract_mcp_payload(response: dict[str, Any]) -> tuple[Any, str]:
    """The tool payload from an MCP tools/call response, or a failure message."""
    if "error" in response:
        return None, f"tools/call answered an error: {response['error']}"
    result = response.get("result")
    if isinstance(result, dict) and result.get("isError"):
        content = result.get("content") or []
        message = ""
        if content and isinstance(content[0], dict):
            message = str(content[0].get("text") or "")
        return None, f"tools/call returned isError: {message or 'no message'}"
    content = result.get("content") if isinstance(result, dict) else None
    if not content:
        return result, ""
    text = content[0].get("text") if isinstance(content[0], dict) else None
    try:
        return json.loads(text), ""
    except (TypeError, json.JSONDecodeError):
        return text, ""


def _drain(stream, sink) -> None:
    if stream is None:
        sink.put(None)
        return
    for raw in stream:
        sink.put(raw.decode("utf-8", errors="replace").rstrip("\n"))
    sink.put(None)


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


def _send(proc: subprocess.Popen, payload: dict, deadline: float) -> bool:
    if proc.stdin is None:
        return False
    try:
        proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        proc.stdin.flush()
    except (BrokenPipeError, OSError):
        return False
    return time.monotonic() <= deadline


def _next_json_line(lines: queue.Queue, deadline: float, junk: deque) -> tuple[bool, Any]:
    """The next parseable JSON line before the deadline; junk lines are recorded."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, None
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            return False, None
        if line is None:
            return False, None
        try:
            return True, json.loads(line)
        except json.JSONDecodeError:
            junk.append(line)
            continue


def contract_essentials(schema: Any) -> dict[str, Any]:
    """The part of a tool schema that decides which arguments a client may send.

    Mirrors `ToolContract.essential()` in `sure/runtime/model_server/tool_contract.py`.
    This script is stdlib-only and may be started under a model venv the harness
    Python never sees, so it cannot import that module; the projection is five
    lines, and comparing the two sides is worth more than the dependency.
    """
    schema = schema if isinstance(schema, dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    return {
        "type": str(schema.get("type") or "object"),
        "properties": sorted(str(key) for key in properties),
        "required": sorted(str(item) for item in required),
    }


def drive_mcp(proc: subprocess.Popen, lines: queue.Queue, junk: deque, deadline: float, tool: str,
              arguments: dict, input_schema: dict | None = None,
              list_only: bool = False) -> tuple[dict, str | None]:
    """initialize / tools/list / tools/call / shutdown, mirroring the sure_trans smoke.

    `input_schema` is the contract `config.yaml` declares for `tool`. When given,
    the schema the server advertises on `tools/list` must agree on the part that
    decides what may be sent -- a config and a server that describe different
    arguments to the same tool name are a bundle that cannot be called, and no
    later stage would notice. `list_only` stops after that check, for callers
    that have no fixture to drive a call with.
    """
    steps: dict[str, Any] = {name: {"ok": False} for name in MCP_STEPS}
    steps["tools_call"] = {"ok": False, "output_nonempty": False, "primary_output": ""}

    def rpc(request_id: int, method: str, params: dict | None = None) -> tuple[bool, Any]:
        if not _send(proc, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}, deadline):
            return False, None
        # A server may announce progress -- a notification carries no `id`, or one
        # of its own -- before it answers, and the infer client reads past those
        # until it finds the response to its request. Reading only the next line
        # fails a server here that inference would have driven fine.
        while True:
            ok, payload = _next_json_line(lines, deadline, junk)
            if not ok or not isinstance(payload, dict):
                return False, payload
            if payload.get("id") != request_id:
                continue
            if "error" in payload:
                return False, payload
            return "result" in payload, payload

    ok, payload = rpc(1, "initialize")
    if not ok:
        return steps, f"initialize failed: {json.dumps(payload, ensure_ascii=False)[:500]}"
    steps["initialize"] = {
        "ok": True,
        "protocolVersion": str((payload.get("result") or {}).get("protocolVersion") or ""),
    }

    ok, payload = rpc(2, "tools/list")
    items = ((payload or {}).get("result") or {}).get("tools", []) if ok else []
    tools = [item.get("name") for item in items if isinstance(item, dict)]
    steps["tools_list"] = {"ok": ok and tool in tools, "tools": tools}
    if not steps["tools_list"]["ok"]:
        return steps, f"tools/list failed for tool {tool!r}: tools={tools}"

    if input_schema is not None:
        advertised = next((item for item in items if isinstance(item, dict) and item.get("name") == tool), {})
        expected = contract_essentials(input_schema)
        found = contract_essentials(advertised.get("inputSchema"))
        steps["tools_list"]["expected_contract"] = expected
        steps["tools_list"]["advertised_contract"] = found
        if found != expected:
            steps["tools_list"]["ok"] = False
            return steps, (
                f"the server advertises a different contract for tool {tool!r} than "
                f"config.yaml declares; config={json.dumps(expected, sort_keys=True)} "
                f"server={json.dumps(found, sort_keys=True)}"
            )

    if list_only:
        steps["tools_call"] = {"ok": True, "skipped": "list_only"}
    else:
        ok, payload = rpc(3, "tools/call", {"name": tool, "arguments": arguments})
        if not ok:
            return steps, f"tools/call failed: {json.dumps(payload, ensure_ascii=False)[:500]}"
        output, violation = extract_mcp_payload(payload)
        if not violation:
            violation = _path_fields_exist(output)
        nonempty = not violation and output_is_nonempty(output)
        steps["tools_call"] = {
            "ok": nonempty,
            "output_nonempty": nonempty,
            "primary_output": json.dumps(output, ensure_ascii=False)[:500],
        }
        if not nonempty:
            return steps, f"tools/call returned no usable output: {violation or json.dumps(output)[:200]}"

    ok, payload = rpc(4, "shutdown")
    steps["shutdown"] = {"ok": ok, "response": json.dumps(payload, ensure_ascii=False)[:200] if not ok else ""}
    if not ok:
        return steps, "shutdown was not answered; the server must exit cleanly on shutdown"
    return steps, None


def drive_jsonl(proc: subprocess.Popen, lines: queue.Queue, junk: deque, deadline: float, tool: str,
                arguments: dict, input_schema: dict | None = None,
                list_only: bool = False) -> tuple[dict, str | None]:
    """One flat JSON request line in, one JSON response line out, no handshake.

    `tool` is unused: a jsonl request carries the arguments alone, with no tool
    envelope to name. `input_schema` and `list_only` are MCP concerns for the
    same reason -- there is no tool list to compare against or stop at. All three
    are taken so both drivers share one call signature.
    """
    steps: dict[str, Any] = {"single_request": {"ok": False, "output_nonempty": False, "primary_output": ""}}
    if not _send(proc, arguments, deadline):
        return steps, "the server accepted no request line"
    ok, payload = _next_json_line(lines, deadline, junk)
    if not ok:
        return steps, "no JSON response line arrived within the deadline"
    violation = _path_fields_exist(payload)
    nonempty = not violation and output_is_nonempty(payload)
    steps["single_request"] = {
        "ok": nonempty,
        "output_nonempty": nonempty,
        "primary_output": json.dumps(payload, ensure_ascii=False)[:500],
    }
    if not nonempty:
        return steps, f"the response carried no usable output: {violation or json.dumps(payload)[:200]}"
    return steps, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server-command", required=True, help="JSON array argv of the model server")
    parser.add_argument("--protocol", required=True, choices=[MCP_PROTOCOL, JSONL_PROTOCOL],
                        help="framing resolved from config.yaml by the caller")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--arguments-json", required=True, help="JSON object of tool arguments")
    parser.add_argument("--input-schema-json",
                        help="JSON object of the input_schema config.yaml declares for --tool; "
                             "when given, the schema the server advertises must agree with it")
    parser.add_argument("--list-only", action="store_true",
                        help="check the advertised tool contract and stop, without calling the tool")
    parser.add_argument("--env-json",
                        help="JSON object of the environment config.yaml declares for the server; "
                             "layered over the sanitized child environment the client starts it with")
    parser.add_argument("--cwd")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--produces", required=True)
    parser.add_argument("--server-stderr-log")
    args = parser.parse_args()

    produces = Path(args.produces)
    steps: dict = {}
    server_stderr: deque[str] = deque()
    junk: deque[str] = deque()
    error: str | None = None
    started = time.monotonic()
    deadline = started + args.timeout
    exit_code: int | None = None
    fatal: str | None = None
    server_command: list[str] = []
    arguments: dict = {}
    input_schema: dict | None = None
    declared_env: dict[str, str] = {}

    try:
        server_command = json.loads(args.server_command)
        if not isinstance(server_command, list) or not all(isinstance(item, str) for item in server_command):
            raise ValueError("--server-command must be a JSON array of strings")
        arguments = json.loads(args.arguments_json)
        if not isinstance(arguments, dict):
            raise ValueError("--arguments-json must be a JSON object")
        if args.input_schema_json:
            input_schema = json.loads(args.input_schema_json)
            if not isinstance(input_schema, dict):
                raise ValueError("--input-schema-json must be a JSON object")
        if args.env_json:
            raw_env = json.loads(args.env_json)
            if not isinstance(raw_env, dict):
                raise ValueError("--env-json must be a JSON object")
            declared_env = {
                str(key): str(value)
                for key, value in raw_env.items()
                if isinstance(value, (str, int, float, bool))
            }
        if args.list_only and args.protocol != MCP_PROTOCOL:
            raise ValueError("--list-only compares an MCP tools/list; a jsonl server has no tool list")
    except ValueError as exc:
        fatal = f"cannot read the smoke invocation: {exc}"

    stderr_handle = None
    stderr_thread: threading.Thread | None = None
    proc: subprocess.Popen | None = None
    if fatal is None:
        if args.server_stderr_log:
            try:
                stderr_handle = open(args.server_stderr_log, "a", encoding="utf-8")
            except OSError:
                stderr_handle = None
        try:
            proc = subprocess.Popen(
                server_command,
                cwd=args.cwd or None,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                # The sanitized child environment first, then the model's own
                # declared values over it -- the order the infer client starts
                # this server in. Layering them the other way would let a
                # declared PYTHONPATH or PYTHONHOME be stripped as if it were
                # harness interpreter state.
                env={
                    **model_child_env(),
                    **declared_env,
                    "PYTHONUNBUFFERED": "1",
                    "PYTHONIOENCODING": "utf-8",
                },
            )
            stderr_thread = threading.Thread(
                target=_drain_stderr, args=(proc, server_stderr, stderr_handle), daemon=True
            )
            stderr_thread.start()
            lines: queue.Queue = queue.Queue()
            threading.Thread(target=_drain, args=(proc.stdout, lines), daemon=True).start()

            driver = drive_mcp if args.protocol == MCP_PROTOCOL else drive_jsonl
            steps, error = driver(proc, lines, junk, deadline, args.tool, arguments,
                                  input_schema, list_only=args.list_only)

            if error is None and args.protocol == MCP_PROTOCOL:
                if proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
                try:
                    exit_code = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
                    if exit_code != 0:
                        error = f"the server exited {exit_code} after shutdown"
                except subprocess.TimeoutExpired:
                    error = "the server did not exit after shutdown within the deadline"
        except OSError as exc:
            error = f"cannot start the declared server {server_command}: {exc}"
        except Exception as exc:  # noqa: BLE001 - evidence must always be written
            error = f"{type(exc).__name__}: {exc}"
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
            # A server that died at startup has already written its reason to a
            # pipe the reader thread may not have drained yet; the evidence below
            # carries that stderr, and a tail that races with the writer would
            # report the failure without the line that explains it.
            if stderr_thread is not None:
                stderr_thread.join(timeout=5)
    else:
        error = fatal

    status = "passed" if error is None else "failed"
    evidence = {
        "schema": SCHEMA,
        "status": status,
        "protocol": args.protocol,
        "tool": args.tool,
        "arguments": arguments,
        "declared_input_schema": input_schema,
        "list_only": args.list_only,
        "server_command": server_command,
        "cwd": args.cwd or "",
        "server_exit_code": exit_code,
        "steps": steps,
        "server_stderr_tail": list(server_stderr)[-STDERR_TAIL_LINES:],
        "stdout_junk_count": len(junk),
        "stdout_junk_tail": list(junk)[-STDERR_TAIL_LINES:],
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "timeout_seconds": args.timeout,
        "error": error,
    }
    try:
        produces.parent.mkdir(parents=True, exist_ok=True)
        produces.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as write_error:
        print(f"cannot write evidence {produces}: {write_error}", file=sys.stderr)
        return 1
    if stderr_handle is not None:
        stderr_handle.close()
    print(produces)
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
