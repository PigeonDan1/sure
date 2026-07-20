#!/usr/bin/env python3
"""Run a bounded MCP smoke against one model-local server.

The script validates the inference service surface used by
generate_predictions_via_server.py:

1. resolve model directory
2. launch server.command from config.yaml
3. call initialize
4. call tools/list
5. optionally call one concrete tool with JSON arguments

It always writes a JSON artifact when --output is provided. A tool call may load
weights, so callers should keep --timeout explicit and use tiny fixture audio.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _legacy_models_dir(root: str | Path) -> Path:
    path = Path(root).expanduser()
    if path.name == "models":
        return path
    return path / "src" / "sure_eval" / "models"


def _candidate_model_dirs(model: str, explicit: str | None) -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    if explicit:
        candidates.append(("explicit_model_dir", Path(explicit).expanduser()))
    for key in ("SURE_MODELS_DIR", "SURE_MODEL_ROOT"):
        value = os.environ.get(key)
        if value:
            candidates.append((key, Path(value).expanduser() / model))
    value = os.environ.get("LEGACY_SURE_MODELS_DIR")
    if value:
        candidates.append(("LEGACY_SURE_MODELS_DIR", Path(value).expanduser() / model))
    value = os.environ.get("LEGACY_SURE_EVAL_ROOT")
    if value:
        candidates.append(("LEGACY_SURE_EVAL_ROOT", _legacy_models_dir(value) / model))
    candidates.append(("cwd_sure_models", Path.cwd() / "sure" / "models" / model))

    out: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for source, path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append((source, path))
    return out


def _resolve_model_dir(model: str | None, model_dir: str | None) -> tuple[str, Path]:
    if model_dir:
        path = Path(model_dir).expanduser()
        if path.is_dir():
            return "explicit_model_dir", path.resolve()
        raise FileNotFoundError(f"model_dir does not exist: {path}")
    if not model:
        raise ValueError("--model or --model-dir is required")
    for source, path in _candidate_model_dirs(model, model_dir):
        if path.is_dir():
            return source, path.resolve()
    raise FileNotFoundError(
        f"Unable to resolve model directory for {model}. "
        f"Searched: {[str(path) for _, path in _candidate_model_dirs(model, model_dir)]}"
    )


def _resolve_server_command(model_dir: Path, server_cfg: dict[str, Any], build_plan: dict[str, Any]) -> list[str]:
    if "command" not in server_cfg:
        adapter = Path(__file__).resolve().parent / "model_wrapper_mcp_server.py"
        command = ["python", str(adapter), "--model-dir", str(model_dir)]
    else:
        command = list(server_cfg.get("command", ["python", "server.py"]))
    if not command:
        raise ValueError("server.command must not be empty")

    server_script_override = os.environ.get("SURE_EVAL_SERVER_SCRIPT_OVERRIDE")
    if server_script_override:
        if len(command) == 1:
            command.append(server_script_override)
        else:
            command[-1] = server_script_override

    preferred_python = os.environ.get("MODEL_PYTHON")
    if preferred_python and Path(command[0]).name.startswith("python"):
        command[0] = preferred_python
    elif command[0] == "python":
        venv_python = model_dir / ".venv" / "bin" / "python"
        build_plan_python = build_plan.get("venv_path")
        if venv_python.exists():
            command[0] = str(venv_python)
        elif build_plan_python:
            command[0] = str(Path(build_plan_python) / "bin" / "python")

    first = Path(command[0]).expanduser()
    if not first.is_absolute() and (model_dir / first).exists():
        # Keep the venv executable path itself. Resolving it can follow the
        # venv's python symlink back to the base interpreter and drop the venv
        # site-packages context.
        command[0] = str(model_dir / first)
    return command


def _resolve_working_dir(model_dir: Path, server_cfg: dict[str, Any]) -> Path:
    return (model_dir / str(server_cfg.get("working_dir", "."))).resolve()


def _infer_hf_home(weights_manifest: dict[str, Any]) -> str | None:
    for key in ("hf_home", "cache_root", "cache_dir"):
        value = weights_manifest.get(key)
        if value:
            return str(value)
    for key in ("hub_cache_path", "snapshot_path"):
        value = weights_manifest.get(key)
        if not value:
            continue
        path = Path(str(value))
        if "hub" in path.parts:
            hub_index = path.parts.index("hub")
            return str(Path(*path.parts[:hub_index]))
    return None


def _resolve_local_model_path(weights_manifest: dict[str, Any]) -> str | None:
    for key in ("local_path", "model_path", "checkpoint_path", "snapshot_path"):
        value = weights_manifest.get(key)
        if value and Path(str(value)).exists():
            return str(value)
    return None


def _build_env(
    model_dir: Path,
    server_cfg: dict[str, Any],
    build_plan: dict[str, Any],
    weights_manifest: dict[str, Any],
    overrides: dict[str, str],
) -> dict[str, str]:
    env = os.environ.copy()
    for key, value in (server_cfg.get("env", {}) or {}).items():
        env[str(key)] = str(value)

    local_model_path = _resolve_local_model_path(weights_manifest)
    configured_model_path = env.get("MODEL_PATH")
    if local_model_path and (not configured_model_path or not Path(configured_model_path).exists()):
        env["MODEL_PATH"] = local_model_path

    inferred_hf_home = _infer_hf_home(weights_manifest)
    if inferred_hf_home and not env.get("HF_HOME"):
        env["HF_HOME"] = inferred_hf_home
    if build_plan.get("hf_cache_path") and not env.get("HF_HOME"):
        env["HF_HOME"] = str(build_plan["hf_cache_path"])
    env.setdefault("MODEL_DIR", str(model_dir))
    env.update(overrides)
    return env


def _load_tool_args(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.arguments_json_file:
        return json.loads(Path(args.arguments_json_file).read_text(encoding="utf-8"))
    if args.arguments_json:
        return json.loads(args.arguments_json)
    return None


def _send_request(process: subprocess.Popen[str], request: dict[str, Any], timeout: float) -> dict[str, Any]:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
    process.stdin.flush()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited before response id={request.get('id')} with code {process.returncode}")
        remaining = max(0.05, deadline - time.monotonic())
        ready, _, _ = select.select([process.stdout], [], [], min(0.5, remaining))
        if not ready:
            continue
        line = process.stdout.readline()
        if line == "":
            continue
        line = line.strip()
        if not line:
            continue
        try:
            response = json.loads(line)
        except json.JSONDecodeError:
            continue
        if response.get("id") == request.get("id"):
            return response
    raise TimeoutError(f"timed out waiting for response id={request.get('id')}")


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a model-local MCP smoke")
    parser.add_argument("--model", help="Model name to resolve under configured model roots")
    parser.add_argument("--model-dir", help="Explicit model directory")
    parser.add_argument("--tool-name", help="Optional tool to call after initialize/tools-list")
    parser.add_argument("--arguments-json", help="JSON object for the optional tool call")
    parser.add_argument("--arguments-json-file", help="File containing JSON object for the optional tool call")
    parser.add_argument(
        "--device",
        help=(
            "Override DEVICE in server env, e.g. cpu or cuda:0. "
            "When set to cpu, CUDA_VISIBLE_DEVICES is hidden unless explicitly overridden."
        ),
    )
    parser.add_argument("--env", action="append", default=[], help="Extra KEY=VALUE env override; repeatable")
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-request timeout in seconds")
    parser.add_argument("--output", help="Write JSON report")
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc).isoformat()
    report: dict[str, Any] = {
        "schema": "sure.harness_mcp_smoke.v1",
        "started_at": started_at,
        "finished_at": None,
        "ok": False,
        "model": args.model,
        "model_dir": None,
        "source": None,
        "command": [],
        "working_dir": None,
        "tool_name": args.tool_name,
        "responses": [],
        "stderr": "",
        "error": None,
    }

    process: subprocess.Popen[str] | None = None
    try:
        source, model_dir = _resolve_model_dir(args.model, args.model_dir)
        report["source"] = source
        report["model_dir"] = str(model_dir)
        model_cfg = _load_yaml(model_dir / "config.yaml")
        build_plan = _load_json(model_dir / "artifacts" / "build_plan.json")
        if not build_plan:
            build_plan = _load_json(model_dir / "build_plan.json")
        weights_manifest = _load_json(model_dir / "artifacts" / "weights_manifest.json")
        server_cfg = model_cfg.get("server", {})
        command = _resolve_server_command(model_dir, server_cfg, build_plan)
        working_dir = _resolve_working_dir(model_dir, server_cfg)
        env_overrides: dict[str, str] = {}
        if args.device:
            env_overrides["DEVICE"] = args.device
        for item in args.env:
            if "=" not in item:
                raise ValueError(f"--env must use KEY=VALUE, got: {item}")
            key, value = item.split("=", 1)
            if not key:
                raise ValueError(f"--env key must not be empty: {item}")
            env_overrides[key] = value
        if env_overrides.get("DEVICE", "").lower() == "cpu" and "CUDA_VISIBLE_DEVICES" not in env_overrides:
            env_overrides["CUDA_VISIBLE_DEVICES"] = ""
        env = _build_env(model_dir, server_cfg, build_plan, weights_manifest, env_overrides)
        report["command"] = command
        report["working_dir"] = str(working_dir)
        report["env_overrides"] = env_overrides

        process = subprocess.Popen(
            command,
            cwd=working_dir,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        tool_args = _load_tool_args(args)
        if args.tool_name:
            requests.append(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": args.tool_name, "arguments": tool_args or {}},
                }
            )

        for request in requests:
            response = _send_request(process, request, args.timeout)
            report["responses"].append(response)
            if "error" in response:
                raise RuntimeError(response["error"].get("message", "MCP response returned error"))

        report["ok"] = True
    except Exception as exc:
        report["error"] = {"type": exc.__class__.__name__, "message": str(exc)}
    finally:
        if process is not None:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except Exception:
                    pass
            _terminate(process)
            if process.stderr is not None:
                try:
                    report["stderr"] = process.stderr.read()
                except Exception:
                    report["stderr"] = ""
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False))

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
