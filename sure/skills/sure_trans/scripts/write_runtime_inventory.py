#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def read_object_optional(path: Path) -> dict:
    if not path.is_file():
        return {}
    return read_object(path)


def identity_evidence(build_context: str) -> dict:
    """What the inventory can honestly claim about the embedded Harness Runtime.

    The runtime_id/lock_sha256 comparison in main() proves the adapter manifest
    is not stale against the currently active binding, which is worth checking
    after a runtime upgrade. It proves nothing about the image contents: both
    sides were copied out of runtime_binding.json when the adapter was
    scaffolded, so reporting that comparison as an identity match overstated it.

    Only an image-backed build context carries evidence about what was actually
    copied in, because scaffold_adapter checks the runtime labels build_image.py
    stamps before it accepts the reference. A directory build context is
    whatever the build command happened to point at.
    """
    image_backed = build_context.startswith("docker-image://")
    return {
        "embedded": True,
        "binding_current": True,
        "identity_source": "image-digest" if image_backed else "build-directory",
        "identity_verified": image_backed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--python-executable")
    parser.add_argument("--working-dir")
    parser.add_argument("--tool-name")
    parser.add_argument("--gpu-required", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    artifacts = run_dir / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
    registry = read_object(artifacts / "docker_registry_result.json")
    adapter_manifest = read_object(artifacts / "adapter_manifest.json")
    runtime_binding = read_object_optional(artifacts / "runtime_binding.json")
    validation_files = {
        "import": "import_result.json",
        "load": "load_result.json",
        "infer": "infer_result.json",
        "contract": "contract_result.json",
        "mcp": "mcp_result.json",
        "equivalence": "equivalence_result.json",
    }
    validations = {name: read_object(artifacts / filename) for name, filename in validation_files.items()}
    if registry.get("status") != "passed" or any(value.get("status") != "passed" for value in validations.values()):
        raise ValueError("registry and every adapter validation stage must pass before writing runtime inventory")
    mount_target = str(resolved["model_mount_target"])
    task_type = str(resolved.get("task_type") or "asr").lower()
    default_tools = {"tts": "synthesize_speech", "vc": "convert_voice", "s2tt": "translate_audio"}
    tool_name = args.tool_name or default_tools.get(task_type, "transcribe_audio")
    harness = runtime_binding.get("runtimes", {}).get("harness", {}) if isinstance(runtime_binding, dict) else {}
    harness_binding = harness.get("binding") if isinstance(harness, dict) else None
    embedded = adapter_manifest.get("harness_runtime_embedded") is True
    image_harness = adapter_manifest.get("harness_runtime")
    if not embedded or not isinstance(harness_binding, dict) or not isinstance(image_harness, dict):
        raise ValueError(
            "adapter image must embed the locked Harness Runtime; regenerate adapter/Dockerfile.sure "
            "with runtime_binding.json before writing runtime_inventory.json"
        )
    if image_harness.get("runtime_id") != harness_binding.get("runtime_id") or image_harness.get("lock_sha256") != harness_binding.get("lock_sha256"):
        raise ValueError(
            "adapter manifest was scaffolded against a different Harness Runtime than the active "
            "one; rerun scaffold_adapter.py and rebuild the adapter image"
        )
    manifest_python = str(adapter_manifest.get("container_python_executable") or "")
    python_executable = str(args.python_executable or manifest_python)
    if not PurePosixPath(python_executable).is_absolute():
        raise ValueError(
            "container Python executable must be absolute; rerun scaffold_adapter.py so it can "
            "probe the source image"
        )
    if not manifest_python or python_executable != manifest_python:
        raise ValueError(
            "--python-executable must match the source-image Python recorded by scaffold_adapter.py"
        )
    manifest_working_dir = str(adapter_manifest.get("working_dir") or "")
    working_dir = str(args.working_dir or manifest_working_dir)
    if not PurePosixPath(working_dir).is_absolute():
        raise ValueError("container working directory must be absolute")
    if working_dir != manifest_working_dir:
        raise ValueError("--working-dir must match the adapter working directory")
    declared_server_command = adapter_manifest.get("server_command")
    if (
        not isinstance(declared_server_command, list)
        or len(declared_server_command) < 2
        or not all(isinstance(item, str) and item for item in declared_server_command)
        or declared_server_command[0] != python_executable
    ):
        raise ValueError(
            "adapter manifest must declare a server_command that starts with its probed Python"
        )
    server_command = declared_server_command
    if not PurePosixPath(server_command[1]).is_absolute():
        raise ValueError("adapter server path must be absolute")
    build_context = str(adapter_manifest.get("harness_runtime_build_context") or "directory")
    image_backed = build_context.startswith("docker-image://")
    harness_runtime = {
        "required": True,
        "schema": "sure.harness.runtime.binding.v1",
        "runtime_id": image_harness.get("runtime_id"),
        "runtime_type": "harness_python",
        "python_executable": image_harness.get("python_executable"),
        "python_version": harness_binding.get("python_version"),
        "python_abi": harness_binding.get("python_abi"),
        "lock_sha256": image_harness.get("lock_sha256"),
        "manifest_path": image_harness.get("manifest_path"),
        "runtime_root": image_harness.get("runtime_root"),
        "materialization": "image_copy",
        "checks": identity_evidence(build_context),
    }
    mcp = validations["mcp"]
    equivalence = validations["equivalence"]
    payload = {
        "schema": "sure.onboard.runtime_inventory.v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "ready",
        "model": {
            "name": resolved["model_name"],
            "id": None,
            "task": resolved.get("task_type"),
            "deployment_type": "local",
            "bundle_root": ".",
            "producer": "sure_trans",
        },
        "local_runtime": {
            "purpose": "sure_trans validation workspace only; not an Eval execution surface.",
            "eligible_for_eval": False,
        },
        "model_runtime": {"required": True, "runtime_type": "container", "python_executable": python_executable, "checks": {name: True for name in validation_files}},
        "harness_runtime": harness_runtime,
        "container_runtime": {
            "required": True,
            "target_image": registry["target_image"],
            "target_image_digest": registry["target_image_digest"],
            "target_image_ref": registry["target_image_ref"],
            "python_executable": python_executable,
            "working_dir": working_dir,
            "server_command": server_command,
            "tool_names": [tool_name],
            "gpu_required": args.gpu_required,
            "mount_policy": {
                "nfs_models_read_only": True,
                "model_bundle": {"target": mount_target, "read_only": True},
                "result_workspace": {"target": "/sure-output", "read_only": False},
            },
        },
        "weights": {"required": True, "source": "model_bundle", "container_root": mount_target, "staged_manifest": "artifacts/model_payload_manifest.json"},
        "readiness": {
            "adapter_validated": True,
            "mcp_validated": mcp.get("mcp_passed") is True,
            "equivalence_validated": equivalence.get("equivalent") is True,
            "registry_pull_verified": registry.get("pull_verified") is True,
        },
        "evidence": [*[f"artifacts/{filename}" for filename in validation_files.values()], "artifacts/docker_registry_result.json", "artifacts/model_payload_manifest.json"],
        "policy": {"eval_runtime": "container_only", "host_python_fallback": False, "image_override_allowed": False, "nfs_models_mutable_by_eval": False},
    }
    output = artifacts / "runtime_inventory.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
