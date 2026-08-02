#!/usr/bin/env python3
"""Write model-local runtime inventory evidence for /sure_onboard.

The inventory is the stable handoff from onboarding to later evaluation runs.
It summarizes the selected runtime and creates a small evidence link directory
without linking checkpoint payloads or cache contents.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "sure.onboard.runtime_inventory.v1"
SUCCESS_STATUSES = {"passed", "success", "PASS", "PASSED", "pass"}
CORE_FILES = ["model.spec.yaml", "model.py", "server.py", "__init__.py", "validate.py", "config.yaml"]
ARTIFACT_EVIDENCE = [
    "artifact_manifest.json",
    "backend_choice.json",
    "build_env_result.json",
    "build_plan.json",
    "env_compat_result.json",
    "weights_manifest.json",
    "package_gate.json",
    "verdict.json",
    "sample_output.json",
]
MODEL_EVIDENCE = [
    ("model_config.yaml", "config.yaml"),
    ("model_spec.yaml", "model.spec.yaml"),
    ("model_wrapper.py", "model.py"),
    ("server.py", "server.py"),
    ("validate.py", "validate.py"),
    ("dockerfile", "Dockerfile"),
    ("docker_image_tag.txt", "artifacts/docker_image_tag.txt"),
    ("uv_lock", "uv.lock"),
    ("requirements_lock", "requirements.lock"),
    ("pyproject.toml", "pyproject.toml"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a JSON object.")
    return data


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return read_json(path)


def first_json(*paths: Path) -> tuple[dict[str, Any] | None, Path | None]:
    for path in paths:
        data = load_json(path)
        if data is not None:
            return data, path
    return None, None


def compact_path(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def resolve_existing(raw: object, model_dir: Path, artifacts_dir: Path) -> Path | None:
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw).expanduser()
    candidates = [path] if path.is_absolute() else [model_dir / path, artifacts_dir / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def infer_model_identity(model_dir: Path, artifacts_dir: Path, build_env: dict[str, Any], weights: dict[str, Any]) -> dict[str, Any]:
    model_input = load_json(artifacts_dir / "model_input_resolved.json") or {}
    classification = load_json(artifacts_dir / "classification.json") or {}
    model_name = (
        model_input.get("model_name")
        or build_env.get("model_name")
        or weights.get("model_name")
        or model_dir.name
    )
    model_id = model_input.get("model_id") or build_env.get("model_id") or weights.get("model_id") or ""
    return {
        "model_id": model_id,
        "model_name": model_name,
        "model_dir": str(model_dir),
        "task_type": model_input.get("task_type") or classification.get("task_type") or "",
        "deployment_type": model_input.get("deployment_type") or "",
    }


def runtime_summary(model_dir: Path, artifacts_dir: Path, build_env: dict[str, Any]) -> dict[str, Any]:
    python_executable = resolve_existing(build_env.get("python_executable"), model_dir, artifacts_dir)
    lockfile = resolve_existing(build_env.get("lockfile_path"), model_dir, artifacts_dir)
    log_path = resolve_existing(build_env.get("log_path"), model_dir, artifacts_dir)
    docker_image = build_env.get("docker_image")
    docker_tag = (artifacts_dir / "docker_image_tag.txt")
    if not docker_image and docker_tag.exists():
        docker_image = docker_tag.read_text(encoding="utf-8").strip()
    return {
        "backend": build_env.get("backend") or "",
        "installer": build_env.get("installer") or build_env.get("backend") or "",
        "python_version": build_env.get("python_version") or "",
        "python_executable": str(python_executable) if python_executable else build_env.get("python_executable"),
        "lockfile_path": str(lockfile) if lockfile else build_env.get("lockfile_path"),
        "docker_image": docker_image,
        "log_path": str(log_path) if log_path else build_env.get("log_path"),
        "runtime_checks": build_env.get("runtime_checks") if isinstance(build_env.get("runtime_checks"), dict) else {},
        "runtime_probe": build_env.get("runtime_probe") if isinstance(build_env.get("runtime_probe"), dict) else {},
    }


def weights_summary(model_dir: Path, artifacts_dir: Path, weights: dict[str, Any]) -> dict[str, Any]:
    resolved = resolve_existing(weights.get("resolved_local_model_path"), model_dir, artifacts_dir)
    checkpoint_root = resolve_existing(weights.get("checkpoint_root"), model_dir, artifacts_dir)
    dependencies = []
    for item in weights.get("dependencies", []):
        if isinstance(item, dict):
            dependencies.append(
                {
                    "name": item.get("name"),
                    "local_path": item.get("local_path"),
                    "exists": item.get("exists"),
                    "link_type": item.get("link_type"),
                    "target_recorded": bool(item.get("target")),
                }
            )
    return {
        "weights_ready": weights.get("weights_ready") if "weights_ready" in weights else weights.get("status") == "fetched",
        "source": weights.get("source"),
        "repo_id": weights.get("repo_id"),
        "resolved_local_model_path": str(resolved) if resolved else weights.get("resolved_local_model_path"),
        "runtime_load_identity": weights.get("runtime_load_identity"),
        "checkpoint_root": str(checkpoint_root) if checkpoint_root else weights.get("checkpoint_root"),
        "cache_policy": weights.get("cache_policy"),
        "fallback_to_host_global": weights.get("fallback_to_host_global"),
        "dependencies": dependencies,
        "source_attempts": weights.get("source_attempts") if isinstance(weights.get("source_attempts"), list) else [],
    }


def mkdir_links_dir(links_dir: Path) -> None:
    links_dir.mkdir(parents=True, exist_ok=True)


def replace_link(link: Path, target: Path) -> tuple[str, str | None]:
    if link.exists() or link.is_symlink():
        if link.is_symlink() and link.resolve() == target.resolve():
            return "symlink", None
        link.unlink()
    relative_target = os.path.relpath(target, start=link.parent)
    try:
        link.symlink_to(relative_target, target_is_directory=target.is_dir())
        return "symlink", relative_target
    except OSError:
        return "path", str(target)


def write_links(model_dir: Path, artifacts_dir: Path, links_dir: Path) -> dict[str, Any]:
    mkdir_links_dir(links_dir)
    entries: dict[str, Any] = {}

    for name in ARTIFACT_EVIDENCE:
        source = artifacts_dir / name
        if not source.exists():
            continue
        link_name = name
        mode, target = replace_link(links_dir / link_name, source)
        entries[link_name] = {"mode": mode, "target": target, "source": str(source)}

    for link_name, relative in MODEL_EVIDENCE:
        source = model_dir / relative
        if not source.exists():
            continue
        mode, target = replace_link(links_dir / link_name, source)
        entries[link_name] = {"mode": mode, "target": target, "source": str(source)}

    manifest = {
        "schema": "sure.onboard.runtime_links_manifest.v1",
        "generated_at": now_iso(),
        "model_dir": str(model_dir),
        "links_dir": str(links_dir),
        "entries": entries,
        "policy": {
            "links_checkpoint_payloads": False,
            "checkpoint_payload_note": "Only manifests and small runtime evidence files are linked.",
        },
    }
    manifest_path = artifacts_dir / "runtime_links_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def success_verdict(verdict: dict[str, Any]) -> bool:
    return str(verdict.get("status", "")) in SUCCESS_STATUSES


def readiness_from(package_gate: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    if isinstance(package_gate.get("readiness"), dict):
        return package_gate["readiness"]
    if isinstance(verdict.get("readiness"), dict):
        return verdict["readiness"]
    return {}


def determine_status(
    *,
    model_dir: Path,
    build_env: dict[str, Any],
    weights: dict[str, Any],
    package_gate: dict[str, Any],
    verdict: dict[str, Any],
) -> tuple[str, list[str]]:
    missing: list[str] = []
    for name in CORE_FILES:
        if not (model_dir / name).exists():
            missing.append(name)
    if not build_env:
        missing.append("artifacts/build_env_result.json")
    if not weights:
        missing.append("artifacts/weights_manifest.json")
    if not package_gate:
        missing.append("artifacts/package_gate.json")
    if not verdict:
        missing.append("artifacts/verdict.json")

    ready = (
        not missing
        and build_env.get("env_ready") is True
        and (weights.get("weights_ready") is True or weights.get("status") == "fetched" or weights.get("required") is False)
    )
    if package_gate:
        ready = ready and package_gate.get("status") in {"passed", "skipped"}
    if verdict:
        ready = ready and success_verdict(verdict)
    return ("ready" if ready else "partial"), missing


def build_inventory(model_dir: Path, run_dir: Path | None = None, links_dir: Path | None = None) -> dict[str, Any]:
    artifacts_dir = model_dir / "artifacts"
    run_artifacts = run_dir / "artifacts" if run_dir else None
    build_env, _ = first_json(
        artifacts_dir / "build_env_result.json",
        *( [run_artifacts / "build_env_result.json"] if run_artifacts else [] ),
    )
    weights, _ = first_json(
        artifacts_dir / "weights_manifest.json",
        *( [run_artifacts / "weights_manifest.json"] if run_artifacts else [] ),
    )
    package_gate, _ = first_json(
        artifacts_dir / "package_gate.json",
        *( [run_artifacts / "package_gate.json"] if run_artifacts else [] ),
    )
    verdict, _ = first_json(
        artifacts_dir / "verdict.json",
        *( [run_artifacts / "verdict.json"] if run_artifacts else [] ),
    )
    build_env = build_env or {}
    weights = weights or {}
    package_gate = package_gate or {}
    verdict = verdict or {}
    status, missing = determine_status(
        model_dir=model_dir,
        build_env=build_env,
        weights=weights,
        package_gate=package_gate,
        verdict=verdict,
    )
    links_manifest = write_links(model_dir, artifacts_dir, links_dir or (artifacts_dir / "runtime_links"))
    return {
        "schema": SCHEMA,
        "generated_at": now_iso(),
        "status": status,
        "model": infer_model_identity(model_dir, artifacts_dir, build_env, weights),
        "runtime": runtime_summary(model_dir, artifacts_dir, build_env),
        "weights": weights_summary(model_dir, artifacts_dir, weights),
        "readiness": readiness_from(package_gate, verdict),
        "evidence": {
            "model_core_files": {name: str(model_dir / name) for name in CORE_FILES if (model_dir / name).exists()},
            "missing": missing,
            "links_manifest": str(artifacts_dir / "runtime_links_manifest.json"),
            "links_dir": links_manifest["links_dir"],
            "link_entries": sorted(links_manifest["entries"].keys()),
        },
        "policy": {
            "source_of_truth": "sure_onboard generated runtime evidence, not prediction raw_response.",
            "checkpoint_payload_links": False,
            "consumer": "sure_eval reads this file as model-level runtime evidence.",
        },
    }


def write_inventory(model_dir: Path, produces: Path | None = None, run_dir: Path | None = None) -> dict[str, Any]:
    model_dir = model_dir.expanduser().resolve()
    artifacts_dir = model_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    inventory = build_inventory(model_dir, run_dir=run_dir)
    output = produces.expanduser().resolve() if produces else artifacts_dir / "runtime_inventory.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return inventory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--run-dir")
    parser.add_argument("--produces")
    args = parser.parse_args()
    try:
        inventory = write_inventory(
            Path(args.model_dir),
            produces=Path(args.produces) if args.produces else None,
            run_dir=Path(args.run_dir).expanduser().resolve() if args.run_dir else None,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"write_runtime_inventory failed: {exc}", file=sys.stderr)
        return 1
    print(
        "write_runtime_inventory OK: "
        f"model_dir={inventory['model']['model_dir']}, status={inventory['status']}, "
        f"links={len(inventory['evidence']['link_entries'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
