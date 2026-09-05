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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    artifacts = Path(args.run_dir).resolve() / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
    original = read_object(artifacts / "original_inference_result.json")
    execution = read_object(artifacts / "execution_compat.json")
    validation_files = {
        "import": ("import_result.json", "import_passed"),
        "load": ("load_result.json", "load_passed"),
        "infer": ("infer_result.json", "infer_passed"),
        "contract": ("contract_result.json", "contract_passed"),
        "mcp": ("mcp_result.json", "mcp_passed"),
        "equivalent": ("equivalence_result.json", "equivalent"),
    }
    validations = {name: read_object(artifacts / filename) for name, (filename, _) in validation_files.items()}
    registry = read_object(artifacts / "docker_registry_result.json")
    runtime = read_object(artifacts / "runtime_inventory.json")
    source_image = read_object(artifacts / "source_image_result.json")
    framework = read_object(artifacts / "framework_detection.json")
    if execution.get("compat_ok") is not True or original.get("status") != "passed":
        raise ValueError("execution compatibility and original inference must pass")
    for name, (_, pass_key) in validation_files.items():
        if validations[name].get("status") != "passed" or validations[name].get(pass_key) is not True:
            raise ValueError(f"adapter validation stage did not pass: {name}")
    package_profile = str(resolved.get("package_profile") or "docker-registry")
    if registry.get("status") != "passed" or runtime.get("status") != "ready":
        raise ValueError("package and runtime inventory must be ready")
    if package_profile == "docker-registry" and registry.get("pull_verified") is not True:
        raise ValueError("docker-registry verdict requires digest-pinned pull verification")
    if framework.get("status") != "ready" or framework.get("framework_requirement_met") is not True:
        raise ValueError("computation framework must be verified as PyTorch")
    if framework.get("declared_framework") != resolved.get("framework") or framework.get(
        "declared_model_framework"
    ) != resolved.get("model_framework"):
        raise ValueError("framework detection declarations must match the resolved input")
    if framework.get("clarification_required") is True and not str(
        framework.get("architecture_clarification") or ""
    ).strip():
        raise ValueError("required model architecture clarification is missing")
    payload = {
        "schema": "sure.trans.verdict.v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "success",
        "instance_id": artifacts.parent.name,
        "model_id": resolved["model_name"],
        "model_name": resolved["model_name"],
        "package": {"profile": package_profile},
        "framework": {
            "computation": {
                "declared": framework.get("declared_framework"),
                "detected": framework.get("detected_framework"),
                "requirement_met": framework.get("framework_requirement_met"),
            },
            "model": {
                "declared": framework.get("declared_model_framework"),
                "detected": framework.get("detected_model_framework"),
                "matches": framework.get("model_framework_matches"),
                "transformers_preferred": framework.get("transformers_preferred"),
            },
            "architecture_signals": framework.get("architecture_signals", []),
            "architecture_clarification": framework.get("architecture_clarification"),
        },
        "readiness": {
            "local_ready": True,
            "docker_ready": package_profile == "docker-registry",
            "registry_ready": package_profile == "docker-registry" and registry.get("pull_verified") is True,
            "bundle_ready": True,
            "vc_ready": None,
        },
        "build": {
            "success": True,
            "runtime_kind": "python" if package_profile == "none" else "container",
            "source_image": source_image.get("image"),
            "source_image_id": source_image.get("image_id"),
            "target_image": registry.get("target_image"),
            "target_image_digest": registry.get("target_image_digest"),
            "target_image_ref": registry.get("target_image_ref"),
        },
        "validation": {
            "execution_compat": True,
            "original_inference": True,
            "adapter_import": validations["import"].get("import_passed") is True,
            "adapter_load": validations["load"].get("load_passed") is True,
            "adapter_infer": validations["infer"].get("infer_passed") is True,
            "adapter_contract": validations["contract"].get("contract_passed") is True,
            "mcp": validations["mcp"].get("mcp_passed") is True,
            "equivalent": validations["equivalent"].get("equivalent") is True,
            "digest_pull": registry.get("pull_verified") is True if package_profile == "docker-registry" else None,
        },
        "artifacts": {
            "runtime_inventory": "artifacts/runtime_inventory.json",
            "framework_detection": "artifacts/framework_detection.json",
            "registry": "artifacts/docker_registry_result.json",
            "execution_compat": "artifacts/execution_compat.json",
            "import": "artifacts/import_result.json",
            "load": "artifacts/load_result.json",
            "infer": "artifacts/infer_result.json",
            "contract": "artifacts/contract_result.json",
            "mcp": "artifacts/mcp_result.json",
            "equivalence": "artifacts/equivalence_result.json",
        },
    }
    output = artifacts / "verdict.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
