#!/usr/bin/env python3
"""Gate the finalized model bundle and deployment-ready marker."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from deployment_contract import (
    normalize_harness_runtime,
    read_json,
    resolve_model_dir,
    sha256_file,
    validate_image_and_digest,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from sure.runtime.model.bootstrap import ModelRuntimeError, manifest_sha256, verify_runtime
from sure.site.loader import SitePolicyError, load_site_policy


LEGACY_PATH = re.compile(r"/(?:mnt/cloudstorfs|hpc_stor\d+|hpc_\d+)/")


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
        model_copy = model_dir / "artifacts" / "deployment_ready.json"
        if not model_copy.is_file() or model_copy.read_bytes() != path.read_bytes():
            raise ValueError("deployment_ready.json must be written identically to the run and model bundle")
        python_delivery = resolved.get("deployment_type") == "local" and resolved.get("package_profile") == "none"
        expected_schema = (
            "sure.onboard.deployment_ready.v2"
            if python_delivery
            else "sure.onboard.deployment_ready.v1"
        )
        if data.get("schema") != expected_schema:
            raise ValueError("invalid deployment-ready schema")
        policy = data.get("execution_policy") if isinstance(data.get("execution_policy"), dict) else {}
        if policy.get("host_python_fallback") is not False:
            raise ValueError("final execution policy must disable host fallback")
        if python_delivery and policy.get("model_bundle_mutation_allowed") is not False:
            raise ValueError("Python execution policy must disable model bundle mutation")
        if not python_delivery and policy.get("nfs_models_read_only") is not True:
            raise ValueError("legacy deployment policy must keep NFS models read-only")
        for raw, expected in data.get("required_artifact_sha256", {}).items():
            relative = str(raw).removeprefix("artifacts/")
            artifact = model_dir / "artifacts" / relative
            if not artifact.is_file() or sha256_file(artifact) != expected:
                raise ValueError(f"finalized artifact hash mismatch: {raw}")
        hashes = data.get("required_artifact_sha256", {})
        bundle_hash = hashlib.sha256(
            json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if data.get("bundle_identity_sha256") != bundle_hash:
            raise ValueError("bundle_identity_sha256 does not match finalized artifact hashes")
        manifest = read_json(model_dir / "artifacts" / "artifact_manifest.json")
        if manifest.get("status") != "finalized" or manifest.get("model_dir") != ".":
            raise ValueError("artifact_manifest.json was not refreshed into portable finalized form")
        if resolved.get("deployment_type") == "local" and resolved.get("package_profile") == "docker-registry":
            if (
                data.get("status") != "ready"
                or policy.get("container_only") is not True
                or policy.get("nfs_models_read_only") is not True
            ):
                raise ValueError("local docker-registry bundle must be container-only ready")
            image, digest, image_ref = validate_image_and_digest(
                data.get("target_image"), data.get("target_image_digest")
            )
            if data.get("target_image_ref") != image_ref:
                raise ValueError("deployment target_image_ref is not digest pinned")
            inventory = read_json(model_dir / "artifacts" / "runtime_inventory.json")
            declared = data.get("harness_runtime") if isinstance(data.get("harness_runtime"), dict) else {}
            source = inventory.get("harness_runtime") if isinstance(inventory.get("harness_runtime"), dict) else {}
            declared = normalize_harness_runtime(declared, allow_derive=False)
            source = normalize_harness_runtime(source, allow_derive=False)
            projected = {key: source.get(key) for key in declared}
            if declared != projected:
                raise ValueError("deployment Harness Runtime binding disagrees with runtime inventory")
            harness = declared
            if harness.get("schema") != "sure.harness.runtime.binding.v1" or not harness.get("runtime_id"):
                raise ValueError("ready deployment must expose the common Harness Runtime binding")
        if python_delivery:
            if (
                data.get("status") != "ready"
                or policy.get("container_only") is not False
                or policy.get("eval_runtime") != "python"
                or policy.get("isolation") != "trusted_host"
                or policy.get("model_integrity") != "verify_before_after"
                or policy.get("nfs_models_read_only") is not False
            ):
                raise ValueError("local package=none bundle must declare trusted-host Python readiness")
            configured = load_site_policy(required=True)
            assert configured is not None
            if "local" not in configured["policy"]["execution"]["surfaces"]:
                raise ValueError("local Python runtimes require local in execution.surfaces")
            if "python" not in configured["policy"]["execution"]["local_runtimes"]:
                raise ValueError("local Python runtimes are disabled by execution.local_runtimes")
            manifest = read_json(model_dir / "artifacts" / "model_runtime_manifest.json")
            runtime = data.get("model_runtime") if isinstance(data.get("model_runtime"), dict) else {}
            if runtime.get("runtime_id") != manifest.get("runtime_id"):
                raise ValueError("deployment Model Runtime ID disagrees with its manifest")
            if runtime.get("manifest_sha256") != manifest_sha256(manifest):
                raise ValueError("deployment Model Runtime manifest hash mismatch")
            verify_runtime(Path(configured["policy"]["storage"]["runtime_root"]) / "models", manifest)
        portable = [
            read_json(model_dir / "artifacts" / name)
            for name in ("runtime_inventory.json", "package_gate.json", "artifact_manifest.json", "deployment_ready.json")
        ]
        if LEGACY_PATH.search(json.dumps(portable, ensure_ascii=False)):
            raise ValueError("finalized deployment sidecars contain legacy host absolute paths")
    except (OSError, ValueError, ModelRuntimeError, SitePolicyError) as exc:
        print(f"FINALIZE_MODEL_BUNDLE failed: {exc}", file=sys.stderr)
        return 1
    print(f"check_finalized_bundle OK: status={data.get('status')}, model={data.get('model_name')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
