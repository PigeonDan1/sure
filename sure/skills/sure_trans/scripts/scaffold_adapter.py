#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def render(source: Path, destination: Path, replacements: dict[str, str]) -> None:
    text = source.read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace(key, value)
    destination.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    artifacts = run_dir / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
    source_image = read_object(artifacts / "source_image_result.json")
    adapter_dir = run_dir / "adapter"
    adapter_dir.mkdir(parents=True, exist_ok=True)
    templates = Path(__file__).resolve().parent / "templates"
    model_py = adapter_dir / "model.py"
    if not model_py.exists():
        shutil.copyfile(templates / "model.py", model_py)
    shutil.copyfile(templates / "__init__.py", adapter_dir / "__init__.py")
    shutil.copyfile(Path(__file__).resolve().parent / "mcp_smoke.py", adapter_dir / "mcp_smoke.py")
    task_type = str(resolved.get("task_type") or "asr").lower()
    tool_name, input_schema = tool_contract(task_type)
    io_contract = {
        "input_type": "audio_path",
        "output_type": "json",
        "primary_field": "text",
        "required_fields": ["text"],
        "nonempty_fields": ["text"],
        "json_serializable": True,
    }
    replacements = {
        "__MODEL_NAME__": str(resolved["model_name"]),
        "__TASK_TYPE__": str(resolved.get("task_type") or "ASR").upper(),
        "__MODEL_MOUNT_TARGET__": str(resolved["model_mount_target"]),
        "__SOURCE_IMAGE__": str(source_image["image"] or source_image["image_id"]),
        "__TOOL_NAME__": tool_name,
        "__INPUT_SCHEMA__": json.dumps(input_schema, ensure_ascii=False, separators=(",", ":")),
        "__IO_CONTRACT_JSON__": json.dumps(io_contract, ensure_ascii=False, separators=(",", ":")),
    }
    render(templates / "server.py", adapter_dir / "server.py", replacements)
    render(templates / "config.yaml", adapter_dir / "config.yaml", replacements)
    render(templates / "model.spec.yaml", adapter_dir / "model.spec.yaml", replacements)
    render(templates / "Dockerfile.sure", adapter_dir / "Dockerfile.sure", replacements)
    render(templates / "validate.py", adapter_dir / "validate.py", replacements)
    manifest = {
        "schema": "sure.trans.adapter_manifest.v1",
        "status": "draft" if "NotImplementedError" in model_py.read_text(encoding="utf-8") else "ready",
        "strategy": "python-import",
        "model_py": str(model_py),
        "init_py": str(adapter_dir / "__init__.py"),
        "validate_py": str(adapter_dir / "validate.py"),
        "server_py": str(adapter_dir / "server.py"),
        "config_yaml": str(adapter_dir / "config.yaml"),
        "model_spec": str(adapter_dir / "model.spec.yaml"),
        "dockerfile": str(adapter_dir / "Dockerfile.sure"),
        "mcp_smoke_py": str(adapter_dir / "mcp_smoke.py"),
        "source_inference_entrypoint": resolved["inference_entrypoint"],
        "model_mount_target": resolved["model_mount_target"],
        "io_contract": io_contract,
    }
    output = artifacts / "adapter_manifest.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


def tool_contract(task_type: str) -> tuple[str, dict]:
    if task_type == "tts":
        return "synthesize_speech", {
            "type": "object",
            "properties": {"text": {"type": "string"}, "prompt_audio_path": {"type": "string"}, "output_path": {"type": "string"}},
            "required": ["text"],
        }
    if task_type == "vc":
        return "convert_voice", {
            "type": "object",
            "properties": {"source_audio_path": {"type": "string"}, "reference_audio_path": {"type": "string"}, "output_path": {"type": "string"}},
            "required": ["source_audio_path", "reference_audio_path"],
        }
    if task_type == "s2tt":
        return "translate_audio", {"type": "object", "properties": {"audio_path": {"type": "string"}}, "required": ["audio_path"]}
    return "transcribe_audio", {"type": "object", "properties": {"audio_path": {"type": "string"}}, "required": ["audio_path"]}


if __name__ == "__main__":
    raise SystemExit(main())
