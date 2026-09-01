#!/usr/bin/env python3
"""Gate the v2 runtime inventory and its model-local copy."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

from deployment_contract import (
    normalize_harness_runtime,
    read_json,
    require_timestamp_after,
    resolve_model_dir,
    validate_image_and_digest,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from sure.runtime.model.bootstrap import ModelRuntimeError, manifest_sha256, verify_runtime
from sure.site.loader import SitePolicyError, load_site_policy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).expanduser().resolve()
    path = Path(args.produces).expanduser().resolve()
    try:
        data = read_json(path)
        model_dir, resolved = resolve_model_dir(run_dir)
        model_copy = model_dir / "artifacts" / "runtime_inventory.json"
        if not model_copy.is_file() or model_copy.read_bytes() != path.read_bytes():
            raise ValueError("runtime_inventory.json must be copied identically into the model bundle")
        package = read_json(model_dir / "artifacts" / "package_gate.json")
        if (data.get("generated_at") or data.get("timestamp")) and (
            package.get("generated_at") or package.get("timestamp")
        ):
            require_timestamp_after(
                "runtime_inventory.json",
                data,
                ("package_gate.json", package),
            )
        if data.get("schema") != "sure.onboard.runtime_inventory.v2":
            raise ValueError("runtime inventory schema must be sure.onboard.runtime_inventory.v2")
        policy = data.get("policy") if isinstance(data.get("policy"), dict) else {}
        if policy.get("host_python_fallback") is not False or policy.get("image_override_allowed") is not False:
            raise ValueError("host Python fallback and image override must both be disabled")
        if policy.get("nfs_models_mutable_by_eval") is not False:
            raise ValueError("Eval must not mutate NFS model bundles")
        deployment_type = resolved.get("deployment_type")
        if deployment_type == "local" and resolved.get("package_profile") == "docker-registry":
            if data.get("status") != "ready" or policy.get("eval_runtime") != "container_only":
                raise ValueError("docker-registry local model must emit a container-only ready inventory")
            container = data.get("container_runtime") if isinstance(data.get("container_runtime"), dict) else {}
            image, digest, image_ref = validate_image_and_digest(
                container.get("target_image"), container.get("target_image_digest")
            )
            if container.get("target_image_ref") != image_ref:
                raise ValueError("container runtime target_image_ref is not digest pinned")
            mounts = container.get("mount_policy") if isinstance(container.get("mount_policy"), dict) else {}
            if mounts.get("nfs_models_read_only") is not True:
                raise ValueError("container runtime must declare nfs_models_read_only=true")
            if not container.get("server_command") or not container.get("tool_names"):
                raise ValueError("container runtime must declare server_command and tool_names")
            model_runtime = data.get("model_runtime") if isinstance(data.get("model_runtime"), dict) else {}
            harness_runtime = data.get("harness_runtime") if isinstance(data.get("harness_runtime"), dict) else {}
            if model_runtime.get("runtime_type") != "model_python" or model_runtime.get("required") is not True:
                raise ValueError("ready inventory must declare required model_runtime")
            if harness_runtime.get("schema") != "sure.harness.runtime.binding.v1" or harness_runtime.get("required") is not True:
                raise ValueError("ready inventory must declare required common harness_runtime")
            normalize_harness_runtime(harness_runtime, allow_derive=False)
            if model_runtime.get("python_executable") == harness_runtime.get("python_executable"):
                raise ValueError("Harness Python and Model Python must remain distinct")
            expected_id = os.environ.get("SURE_HARNESS_RUNTIME_ID")
            expected_lock = os.environ.get("SURE_HARNESS_LOCK_SHA256")
            if expected_id and harness_runtime.get("runtime_id") != expected_id:
                raise ValueError("inventory Harness Runtime ID differs from the active common runtime")
            if expected_lock and harness_runtime.get("lock_sha256") != expected_lock:
                raise ValueError("inventory Harness Runtime lock differs from the active common runtime")
        elif deployment_type == "local" and resolved.get("package_profile") == "none":
            if data.get("status") != "ready" or policy.get("eval_runtime") != "python":
                raise ValueError("package=none local model must emit a Python-ready inventory")
            configured = load_site_policy(required=True)
            assert configured is not None
            if "local" not in configured["policy"]["execution"]["surfaces"]:
                raise ValueError("local Python runtimes require local in execution.surfaces")
            if "python" not in configured["policy"]["execution"]["local_runtimes"]:
                raise ValueError("local Python runtimes are disabled by execution.local_runtimes")
            manifest_path = model_dir / "artifacts" / "model_runtime_manifest.json"
            manifest = read_json(manifest_path)
            runtime = data.get("model_runtime") if isinstance(data.get("model_runtime"), dict) else {}
            if runtime.get("required") is not True or runtime.get("runtime_id") != manifest.get("runtime_id"):
                raise ValueError("Python-ready inventory Model Runtime ID mismatch")
            if runtime.get("manifest_sha256") != manifest_sha256(manifest):
                raise ValueError("Python-ready inventory Model Runtime manifest hash mismatch")
            verify_runtime(Path(configured["policy"]["storage"]["runtime_root"]) / "models", manifest)
            lockfile = model_dir / str(runtime.get("lockfile_path") or "")
            if not lockfile.is_file() or runtime.get("lock_sha256") != hashlib.sha256(lockfile.read_bytes()).hexdigest():
                raise ValueError("Python-ready inventory lockfile hash mismatch")
            command = runtime.get("server_command")
            if not isinstance(command, list) or not command or command[0] != runtime.get("python_executable"):
                raise ValueError("Python-ready inventory server_command must start with the bound Model Python")
            if not runtime.get("tool_names"):
                raise ValueError("Python-ready inventory must declare tool_names")
            container = data.get("container_runtime") if isinstance(data.get("container_runtime"), dict) else {}
            if container.get("required") is not False:
                raise ValueError("package=none must not require a container runtime")
            harness = data.get("harness_runtime") if isinstance(data.get("harness_runtime"), dict) else {}
            if harness.get("required") is not True or harness.get("binding_source") != "active_eval_site":
                raise ValueError("Python Eval must resolve the common Harness Runtime from the active site")
        elif deployment_type == "local" and data.get("status") not in {"local_only", "partial"}:
            raise ValueError("non-registry local model cannot claim Eval-ready status")
    except (OSError, ValueError, ModelRuntimeError, SitePolicyError) as exc:
        print(f"RUNTIME_INVENTORY failed: {exc}", file=sys.stderr)
        return 1
    print(f"check_runtime_inventory OK: status={data.get('status')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
