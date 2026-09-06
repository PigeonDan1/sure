#!/usr/bin/env python3
"""Expose a model-local ModelWrapper/create_model object as a minimal MCP server."""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _flatten_config(config: dict[str, Any]) -> dict[str, Any]:
    flattened = dict(config)
    for section_name in ("model", "runtime", "io"):
        section = config.get(section_name)
        if isinstance(section, dict):
            flattened.update(section)
    return flattened


def _load_module(module_path: Path, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to import module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _create_model(model_dir: Path, config: dict[str, Any]) -> Any:
    if str(model_dir) not in sys.path:
        sys.path.insert(0, str(model_dir))
    flat_config = _flatten_config(config)
    server_path = model_dir / "server.py"
    if server_path.exists():
        server_module = _load_module(server_path, "_sure_model_server")
        create_model = getattr(server_module, "create_model", None)
        if create_model is not None:
            for args in ((flat_config,), (config,), tuple()):
                try:
                    return create_model(*args)
                except TypeError:
                    continue
    model_module = _load_module(model_dir / "model.py", "_sure_model_wrapper")
    wrapper_cls = getattr(model_module, "ModelWrapper")
    for args in ((flat_config,), (config,), tuple()):
        try:
            return wrapper_cls(*args)
        except TypeError:
            continue
    return wrapper_cls()


def _to_plain(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return _to_plain(value.to_dict())
    if dataclasses.is_dataclass(value):
        return _to_plain(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {"type": type(value).__name__, "repr": repr(value)[:500]}


def _model_task(config: dict[str, Any]) -> str:
    model = config.get("model") if isinstance(config.get("model"), dict) else {}
    return str(model.get("task") or config.get("task") or config.get("task_type") or "").strip().upper()


def _tool_names(config: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for tool in config.get("tools") or []:
        if isinstance(tool, dict) and tool.get("name"):
            names.append(str(tool["name"]))
    if names:
        return names
    defaults = {
        "ASR": "transcribe_audio",
        "S2TT": "translate_audio",
        "TTS": "synthesize_speech",
        "VC": "convert_voice",
    }
    return [defaults.get(_model_task(config), "predict")]


def _respond(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _tool_schema(task: str) -> dict[str, Any]:
    if task == "VC":
        required = ["source_audio_path", "reference_audio_path"]
        properties = {
            "source_audio_path": {"type": "string"},
            "reference_audio_path": {"type": "string"},
            "output_path": {"type": "string"},
        }
    elif task == "TTS":
        required = ["text"]
        properties = {
            "text": {"type": "string"},
            "prompt_audio_path": {"type": "string"},
            "output_path": {"type": "string"},
        }
    else:
        required = ["audio_path"]
        properties = {"audio_path": {"type": "string"}}
    return {"type": "object", "properties": properties, "required": required}


def main() -> int:
    parser = argparse.ArgumentParser(description="Wrap ModelWrapper/create_model as a minimal MCP server")
    parser.add_argument("--model-dir", required=True)
    args = parser.parse_args()

    model_dir = Path(args.model_dir).resolve()
    os.chdir(model_dir)
    config = _load_yaml(model_dir / "config.yaml")
    model = _create_model(model_dir, config)
    task = _model_task(config)
    tools = _tool_names(config)

    for line in sys.stdin:
        if not line.strip():
            continue
        request_id: Any = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            method = request.get("method")
            if method == "initialize":
                _respond(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "serverInfo": {"name": model_dir.name, "version": "model-wrapper-adapter"},
                            "capabilities": {"tools": {}},
                        },
                    }
                )
            elif method == "tools/list":
                _respond(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "tools": [
                                {
                                    "name": name,
                                    "description": f"Run {model_dir.name} ModelWrapper.predict",
                                    "inputSchema": _tool_schema(task),
                                }
                                for name in tools
                            ]
                        },
                    }
                )
            elif method == "tools/call":
                params = request.get("params") if isinstance(request.get("params"), dict) else {}
                name = str(params.get("name") or "")
                arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
                if name not in set(tools) | {"predict", "healthcheck"}:
                    raise ValueError(f"Unknown tool: {name}")
                if name == "healthcheck" and hasattr(model, "healthcheck"):
                    result = model.healthcheck()
                else:
                    result = model.predict(arguments)
                _respond(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(_to_plain(result), ensure_ascii=False),
                                }
                            ]
                        },
                    }
                )
            elif method == "shutdown":
                _respond({"jsonrpc": "2.0", "id": request_id, "result": {}})
                return 0
            else:
                raise ValueError(f"Unsupported method: {method}")
        except Exception as exc:
            print(traceback.format_exc(), file=sys.stderr)
            _respond(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": str(exc)},
                }
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
