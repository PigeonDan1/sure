#!/usr/bin/env python3
"""Gate the v2 runtime inventory and its model-local copy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from deployment_contract import read_json, resolve_model_dir, validate_image_and_digest

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.site.loader import load_site_policy


LEGACY_PATH = re.compile(r"/(?:mnt/cloudstorfs|hpc_stor\d+|hpc_\d+)/")
PYTHON_BACKENDS = {"uv", "pip", "conda", "pixi"}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _runtime_path(raw: object, scope: object, model_dir: Path, *, executable: bool = False) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ValueError("Python runtime path is missing")
    if scope == "model_relative":
        path = Path(raw)
        if path.is_absolute():
            raise ValueError("model_relative Python runtime path must be relative")
        candidate = model_dir / path
        resolved = candidate.parent.resolve() / candidate.name if executable else candidate.resolve()
        if not _inside(resolved, model_dir.resolve()):
            raise ValueError("model_relative Python runtime path escapes the model bundle")
        return resolved
    if scope == "site_runtime":
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise ValueError("site_runtime Python runtime path must be absolute")
        policy = load_site_policy(required=True)["policy"]
        runtime_root = Path(policy["storage"]["runtime_root"]).expanduser().resolve()
        resolved = path.parent.resolve() / path.name if executable else path.resolve()
        if not _inside(resolved, runtime_root):
            raise ValueError(f"site_runtime path must stay below configured runtime_root: {runtime_root}")
        return resolved
    raise ValueError(f"unsupported Python runtime path_scope: {scope!r}")


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
        if data.get("schema") != "sure.onboard.runtime_inventory.v2":
            raise ValueError("runtime inventory schema must be sure.onboard.runtime_inventory.v2")
        policy = data.get("policy") if isinstance(data.get("policy"), dict) else {}
        if policy.get("host_python_fallback") is not False or policy.get("image_override_allowed") is not False:
            raise ValueError("host Python fallback and image override must both be disabled")
        if policy.get("nfs_models_mutable_by_eval") is not False:
            raise ValueError("Eval must not mutate NFS model bundles")
        if LEGACY_PATH.search(json.dumps(data, ensure_ascii=False)):
            raise ValueError("runtime inventory contains a legacy host/staging absolute path")
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
            if model_runtime.get("python_executable") == harness_runtime.get("python_executable"):
                raise ValueError("Harness Python and Model Python must remain distinct")
            expected_id = os.environ.get("SURE_HARNESS_RUNTIME_ID")
            expected_lock = os.environ.get("SURE_HARNESS_LOCK_SHA256")
            if expected_id and harness_runtime.get("runtime_id") != expected_id:
                raise ValueError("inventory Harness Runtime ID differs from the active common runtime")
            if expected_lock and harness_runtime.get("lock_sha256") != expected_lock:
                raise ValueError("inventory Harness Runtime lock differs from the active common runtime")
        elif deployment_type == "local" and resolved.get("package_profile") == "none":
            site_policy = load_site_policy(required=True)["policy"]
            if "python" not in site_policy["execution"]["local_runtimes"]:
                raise ValueError(
                    "local Python Eval is disabled by site policy; add python to execution.local_runtimes"
                )
            if data.get("status") != "ready" or policy.get("eval_runtime") != "python_only":
                raise ValueError("package=none local model must emit a Python-ready inventory")
            local = data.get("local_runtime") if isinstance(data.get("local_runtime"), dict) else {}
            if local.get("eligible_for_eval") is not True or local.get("backend") not in PYTHON_BACKENDS:
                raise ValueError("Python-ready inventory must declare an eligible uv/pip/conda/pixi runtime")
            python = _runtime_path(
                local.get("python_executable"),
                local.get("path_scope"),
                model_dir,
                executable=True,
            )
            if not python.is_file() or not os.access(python, os.X_OK):
                raise ValueError(f"approved Model Python is missing or not executable: {python}")
            lockfiles = local.get("lockfiles") if isinstance(local.get("lockfiles"), list) else []
            lock_scopes = local.get("lockfile_scopes") if isinstance(local.get("lockfile_scopes"), dict) else {}
            lock_hashes = local.get("lockfile_sha256") if isinstance(local.get("lockfile_sha256"), dict) else {}
            if not lockfiles:
                raise ValueError("Python-ready inventory must bind at least one lockfile")
            for lockfile in lockfiles:
                lock_path = _runtime_path(lockfile, lock_scopes.get(lockfile), model_dir)
                if not lock_path.is_file() or lock_hashes.get(lockfile) != hashlib.sha256(lock_path.read_bytes()).hexdigest():
                    raise ValueError(f"Python runtime lockfile hash mismatch: {lockfile}")
            command = local.get("server_command")
            tools = local.get("tool_names")
            if not isinstance(command, list) or not command or command[0] != local.get("python_executable"):
                raise ValueError("Python runtime server_command must start with the approved Model Python")
            if not isinstance(tools, list) or not tools:
                raise ValueError("Python runtime must declare at least one tool name")
            model_runtime = data.get("model_runtime") if isinstance(data.get("model_runtime"), dict) else {}
            if model_runtime.get("required") is not True or model_runtime.get("python_executable") != local.get("python_executable"):
                raise ValueError("Python-ready inventory model_runtime disagrees with local_runtime")
            harness_runtime = data.get("harness_runtime") if isinstance(data.get("harness_runtime"), dict) else {}
            if harness_runtime.get("required") is not False:
                raise ValueError("host Harness Runtime is resolved by Eval and must not be bundled with Model Python")
        elif deployment_type == "local" and data.get("status") not in {"local_only", "partial"}:
            raise ValueError("non-registry local model cannot claim Eval-ready status")
    except (OSError, ValueError) as exc:
        print(f"RUNTIME_INVENTORY failed: {exc}", file=sys.stderr)
        return 1
    print(f"check_runtime_inventory OK: status={data.get('status')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
