#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def harness_image_binding(artifacts: Path) -> dict[str, str] | None:
    path = artifacts / "runtime_binding.json"
    if not path.is_file():
        return None
    payload = read_object(path)
    runtimes = payload.get("runtimes") if isinstance(payload.get("runtimes"), dict) else {}
    harness = runtimes.get("harness") if isinstance(runtimes.get("harness"), dict) else {}
    binding = harness.get("binding") if isinstance(harness.get("binding"), dict) else {}
    runtime_id = str(binding.get("runtime_id") or "")
    lock_sha256 = str(binding.get("lock_sha256") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", runtime_id):
        raise ValueError("runtime_binding.json has no safe Harness Runtime ID")
    if not lock_sha256:
        raise ValueError("runtime_binding.json has no Harness Runtime lock hash")
    destination = f"/opt/sure-harness/{runtime_id}"
    return {
        "runtime_id": runtime_id,
        "lock_sha256": lock_sha256,
        "python_executable": f"{destination}/bin/python",
        "manifest_path": f"{destination}/runtime-manifest.json",
        "runtime_root": destination,
    }


def inspect_image(reference: str) -> dict:
    """Read `docker image inspect` output, or an empty object when it is unusable."""
    try:
        inspect = subprocess.run(
            ["docker", "image", "inspect", reference, "--format", "{{json .}}"],
            check=False, capture_output=True, text=True,
        )
    except OSError:
        return {}
    try:
        data = json.loads(inspect.stdout)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def image_carries_runtime(data: dict, harness: dict[str, str]) -> bool:
    """Check the labels build_image.py stamps onto a Harness Runtime image."""
    config = data.get("Config") if isinstance(data.get("Config"), dict) else {}
    labels = config.get("Labels") or {}
    if not isinstance(labels, dict):
        return False
    return (
        labels.get("org.sure.harness.runtime_id") == harness["runtime_id"]
        and labels.get("org.sure.harness.lock_sha256") == harness["lock_sha256"]
    )


def harness_runtime_build_context(harness: dict[str, str] | None) -> str:
    if harness is None:
        return "directory"
    image_ref = os.environ.get("SURE_HARNESS_RUNTIME_IMAGE", "").strip()
    verified = False
    config_path = Path(__file__).resolve().parents[4] / "sure" / "runtime" / "harness" / "runtime-image.json"
    if not image_ref and config_path.is_file():
        image_config = read_object(config_path)
        image_ref = str(image_config.get("image_ref") or "").strip()
        if image_config.get("runtime_id") != harness["runtime_id"] or image_config.get("lock_sha256") != harness["lock_sha256"]:
            raise ValueError("runtime image identity does not match the active Harness Runtime")
    if not image_ref:
        try:
            probe = subprocess.run(
                ["docker", "image", "ls", "--filter", f"label=org.sure.harness.runtime_id={harness['runtime_id']}", "--format", "{{.Repository}}:{{.Tag}}"],
                check=False, capture_output=True, text=True,
            )
        except OSError:
            probe = None
        if probe is None:
            return "directory"
        candidates = [line.strip() for line in probe.stdout.splitlines() if line.strip()]
        matches: list[str] = []
        for candidate in candidates:
            data = inspect_image(candidate)
            if not image_carries_runtime(data, harness):
                continue
            for repo_digest in data.get("RepoDigests", []):
                if isinstance(repo_digest, str) and re.fullmatch(r".+@sha256:[0-9a-f]{64}", repo_digest):
                    matches.append(repo_digest)
        if len(set(matches)) == 1:
            image_ref = matches[0]
            verified = True
        elif len(set(matches)) > 1:
            raise ValueError("multiple cached Harness Runtime images match the active runtime")
    if image_ref:
        if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", image_ref):
            raise ValueError("SURE_HARNESS_RUNTIME_IMAGE must be digest-pinned")
        if not verified and not image_carries_runtime(inspect_image(image_ref), harness):
            raise ValueError(
                f"{image_ref} does not carry the active Harness Runtime; pull it first, or "
                "rebuild it with sure/runtime/harness/build_image.py"
            )
        return f"docker-image://{image_ref}"
    return "directory"


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
    harness = harness_image_binding(artifacts)
    harness_context = harness_runtime_build_context(harness)
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
        "__FRAMEWORK__": str(resolved["framework"]),
        "__MODEL_FRAMEWORK__": str(resolved["model_framework"]),
        "__MODEL_MOUNT_TARGET__": str(resolved["model_mount_target"]),
        "__SOURCE_IMAGE__": str(source_image["image"] or source_image["image_id"]),
        "__HARNESS_RUNTIME_COPY__": (
            f"COPY --from=sure_harness_runtime / /opt/sure-harness/{harness['runtime_id']}/"
            if harness
            else ""
        ),
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
        "harness_runtime_embedded": harness is not None,
        "harness_runtime": harness,
        "harness_runtime_build_context": harness_context,
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
