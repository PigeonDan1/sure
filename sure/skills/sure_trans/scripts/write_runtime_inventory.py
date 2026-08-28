#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def read_object_optional(path: Path) -> dict:
    if not path.is_file():
        return {}
    return read_object(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--python-executable", default="python")
    parser.add_argument("--working-dir", default="/opt/sure_trans")
    parser.add_argument("--tool-name")
    parser.add_argument("--gpu-required", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    artifacts = run_dir / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
    registry = read_object(artifacts / "docker_registry_result.json")
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
    if isinstance(harness_binding, dict):
        harness_runtime = {
            "required": False,
            "reason": "The adapter image does not bake in a Harness Runtime; the site execution layer mounts the locked common Harness Runtime from the repository when required.",
            "runtime_id": harness_binding.get("runtime_id"),
            "lock_sha256": harness_binding.get("lock_sha256"),
            "python_version": harness_binding.get("python_version"),
            "python_abi": harness_binding.get("python_abi"),
        }
    else:
        harness_runtime = {
            "required": False,
            "reason": "Harness Runtime binding was not recorded in runtime_binding.json.",
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
        "model_runtime": {"required": True, "runtime_type": "container", "python_executable": args.python_executable, "checks": {name: True for name in validation_files}},
        "harness_runtime": harness_runtime,
        "container_runtime": {
            "required": True,
            "target_image": registry["target_image"],
            "target_image_digest": registry["target_image_digest"],
            "target_image_ref": registry["target_image_ref"],
            "python_executable": args.python_executable,
            "working_dir": args.working_dir,
            "server_command": [args.python_executable, "/opt/sure_trans/server.py"],
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
