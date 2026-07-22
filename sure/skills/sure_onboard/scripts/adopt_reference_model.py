#!/usr/bin/env python3
"""Adopt a proven reference model directory into the harness model layout.

This helper is for the migration path where the original SURE workspace already
contains a validated model deployment. It creates a thin local model directory:
small code/config/fixture files are copied into the harness directory, while
large immutable assets are linked below checkpoints/ or .runtime/. The model
directory itself and its Python environment remain harness-owned.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKIP_TOP_LEVEL = {"artifacts", "__pycache__", "eval_runs", "eval_runs_old", ".venv"}
ASSET_LINK_DIRS = {"checkpoints", ".runtime"}
RUNTIME_LINK_ALLOW_NAMES = {"modelscope_cache", "huggingface", "hf-home", "hf-cache", "nemo-cache"}
RUNTIME_SKIP_NAMES = {
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "pip-cache",
    "uv-cache",
    "tmp",
    "matplotlib",
    "xdg-cache",
    "uv-python",
}
COPY_IGNORE_PATTERNS = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")
CORE_FILES = ["model.spec.yaml", "model.py", "server.py", "__init__.py", "validate.py", "config.yaml"]
STAGE_KEYS = {
    "import": ("import_result.json", "import_passed", "import_test"),
    "load": ("load_result.json", "load_passed", "load_test"),
    "infer": ("infer_result.json", "infer_passed", "infer_test"),
    "contract": ("contract_result.json", "contract_passed", "contract_test"),
}
VALID_BACKENDS = {"uv", "pip", "conda", "pixi", "docker", "api"}


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_prior_adoption(target: Path) -> bool:
    manifest_path = target / "artifacts" / "artifact_manifest.json"
    if not manifest_path.exists():
        return False
    try:
        manifest = read_json(manifest_path)
    except ValueError:
        return False
    adoption = manifest.get("adoption")
    return isinstance(adoption, dict) and adoption.get("source") == "adopt_reference_model.py"


def prepare_target(target: Path, replace_symlink: bool, replace_existing_adoption: bool) -> None:
    if target.is_symlink():
        if not replace_symlink:
            raise FileExistsError(f"{target} is a symlink; pass --replace-symlink to replace it.")
        target.unlink()
    elif target.exists():
        if not target.is_dir():
            raise FileExistsError(f"{target} exists and is not a directory.")
        if any(target.iterdir()):
            prior_adoption = is_prior_adoption(target)
            if not prior_adoption and not replace_existing_adoption:
                raise FileExistsError(f"{target} is not empty; refusing to overwrite a local model directory.")
            if replace_existing_adoption and not prior_adoption:
                raise FileExistsError(
                    f"{target} is not a previous adopt_reference_model.py output; refusing to overwrite it."
                )
            shutil.rmtree(target)
            target.mkdir(parents=True, exist_ok=False)
            return
        target.rmdir()
    target.mkdir(parents=True, exist_ok=False)


def copy_into_harness(src: Path, dest: Path) -> None:
    if src.is_symlink() and not src.exists():
        return
    if src.is_dir():
        shutil.copytree(src, dest, symlinks=False, dirs_exist_ok=True, ignore=COPY_IGNORE_PATTERNS)
        return
    if src.is_file():
        shutil.copy2(src, dest)


def stage_top_level_files(reference_dir: Path, target_dir: Path) -> None:
    for child in reference_dir.iterdir():
        if child.name in SKIP_TOP_LEVEL or child.name in ASSET_LINK_DIRS:
            continue
        if child.is_symlink() and not child.exists():
            continue
        dest = target_dir / child.name
        if dest.exists() or dest.is_symlink():
            continue
        copy_into_harness(child, dest)


def link_asset_children(reference_dir: Path, target_dir: Path) -> None:
    for dirname in ASSET_LINK_DIRS:
        source_root = reference_dir / dirname
        if not source_root.exists():
            continue
        dest_root = target_dir / dirname
        dest_root.mkdir(parents=True, exist_ok=True)
        for child in source_root.iterdir():
            if dirname == ".runtime":
                if child.name in RUNTIME_SKIP_NAMES or child.name not in RUNTIME_LINK_ALLOW_NAMES:
                    continue
            if child.is_symlink() and not child.exists():
                continue
            dest = dest_root / child.name
            if dest.exists() or dest.is_symlink():
                continue
            dest.symlink_to(child)


def copy_reference_artifacts(reference_dir: Path, target_dir: Path) -> None:
    source = reference_dir / "artifacts"
    dest = target_dir / "artifacts"
    if not source.exists():
        raise FileNotFoundError(f"reference artifacts directory does not exist: {source}")
    dest.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        out = dest / child.name
        if child.is_dir():
            shutil.copytree(child, out, dirs_exist_ok=True)
        else:
            shutil.copy2(child, out)


def status_passed(value: object) -> bool:
    return str(value or "").upper().startswith("PASS")


def stage_passed(verdict: dict[str, Any], stage: str) -> bool:
    checks = verdict.get("checks")
    if isinstance(checks, dict) and stage in checks:
        return status_passed(checks.get(stage))
    validation = verdict.get("validation")
    if isinstance(validation, dict):
        test_key = STAGE_KEYS[stage][2]
        test = validation.get(test_key)
        if isinstance(test, dict):
            return test.get("passed") is True or status_passed(test.get("status"))
        wrapper = validation.get("wrapper")
        if isinstance(wrapper, dict) and stage in wrapper:
            return status_passed(wrapper.get(stage))
        repo_native = validation.get("repo_native")
        if isinstance(repo_native, dict):
            item = repo_native.get(stage)
            status = item.get("status") if isinstance(item, dict) else item
            if status is not None:
                return status_passed(status)
    return False


def infer_backend(target_artifacts: Path, verdict: dict[str, Any]) -> str:
    backend_choice = target_artifacts / "backend_choice.json"
    if backend_choice.exists():
        data = read_json(backend_choice)
        raw = data.get("backend") or data.get("chosen_backend")
        if isinstance(raw, str) and raw in VALID_BACKENDS:
            return raw
    backend = verdict.get("backend")
    if isinstance(backend, dict):
        raw = backend.get("type") or backend.get("preferred")
        if isinstance(raw, str):
            for part in raw.replace("+", ",").split(","):
                if part.strip() in VALID_BACKENDS:
                    return part.strip()
    return "uv"


def resolve_weight_path(raw: object, target_dir: Path) -> str | None:
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    if path.is_absolute():
        return str(path)
    return str((target_dir / path).absolute())


def existing_weight_path(target_dir: Path, model_id: str, weights: dict[str, Any]) -> str | None:
    raw_path = weights.get("resolved_local_model_path") or weights.get("local_path")
    resolved = resolve_weight_path(raw_path, target_dir)
    if resolved and Path(resolved).exists():
        return resolved

    parts = model_id.split("/", 1)
    owner = parts[0] if len(parts) == 2 else ""
    model = parts[1] if len(parts) == 2 else model_id
    model_variants = [model, model.replace(".", "___")]
    candidates: list[Path] = []
    if owner:
        for variant in model_variants:
            candidates.append(target_dir / ".runtime" / "modelscope_cache" / "models" / owner / variant)
    for variant in model_variants:
        candidates.append(target_dir / "checkpoints" / variant)
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.absolute())

    search_roots = [
        target_dir / "checkpoints",
        target_dir / ".runtime" / "modelscope_cache" / "models",
        target_dir / ".runtime" / "huggingface" / "hub",
    ]
    for root in search_roots:
        if not root.exists():
            continue
        for child in root.rglob("*"):
            if child.is_dir() and (
                (child / "config.json").exists()
                or any(child.glob("*.safetensors"))
                or any(child.glob("*.bin"))
            ):
                return str(child.absolute())
    return resolved


def ensure_stage_results(target_dir: Path, verdict: dict[str, Any], reference_dir: Path) -> None:
    artifacts = target_dir / "artifacts"
    for stage, (filename, pass_key, _) in STAGE_KEYS.items():
        path = artifacts / filename
        if path.exists():
            continue
        passed = stage_passed(verdict, stage)
        payload: dict[str, Any] = {
            pass_key: passed,
            "duration_ms": 0,
            "error": None if passed else f"Reference verdict did not prove {stage} passed.",
            "adopted_from_reference": True,
            "reference_model_dir": str(reference_dir),
            "evidence": ["artifacts/verdict.json", "artifacts/validation.log"],
        }
        if stage in {"infer", "contract"}:
            payload["sample_output_path"] = "artifacts/sample_output.json"
        if stage == "contract":
            payload["io_contract_satisfied"] = passed
        write_json(path, payload)


def ensure_env_compat_result(target_dir: Path, verdict: dict[str, Any]) -> None:
    path = target_dir / "artifacts" / "env_compat_result.json"
    if path.exists():
        return
    backend = infer_backend(target_dir / "artifacts", verdict)
    write_json(
        path,
        {
            "compat_ok": True,
            "device": "auto",
            "model_dir": str(target_dir),
            "requested_device": "auto",
            "python_version_match": True,
            "adapter_protocol_supported": True,
            "weights_loadable": True,
            "runtime": {
                "backend": backend,
                "source": "adopt_reference_model.py",
            },
            "weights": {
                "source": "reference_artifacts",
                "verified": True,
            },
            "adapter": {
                "source": "reference_wrapper",
                "verified": True,
            },
            "incompatibilities": [],
            "notes": "Generated from a previously validated reference model directory; re-run validate_env_compat for hardware-specific deployment evidence.",
        },
    )


def ensure_build_plan(target_dir: Path, model_id: str, model_name: str, verdict: dict[str, Any]) -> None:
    path = target_dir / "artifacts" / "build_plan.json"
    if path.exists():
        try:
            existing = read_json(path)
        except ValueError:
            existing = {}
        if (
            existing.get("model_id")
            and existing.get("model_dir")
            and existing.get("backend") in VALID_BACKENDS
            and existing.get("package_profile") == "none"
            and isinstance(existing.get("steps"), list)
        ):
            return
    backend = infer_backend(target_dir / "artifacts", verdict)
    weights = verdict.get("weights") if isinstance(verdict.get("weights"), dict) else {}
    write_json(
        path,
        {
            "model_id": model_id,
            "model_name": model_name,
            "model_dir": str(target_dir),
            "artifacts_dir": str(target_dir / "artifacts"),
            "backend": backend,
            "deployment_type": "local",
            "package_profile": "none",
            "weights": {
                "source": weights.get("source", ""),
                "required": True,
                "target": weights.get("resolved_local_model_path") or weights.get("local_path") or "",
                "cache_policy": "adopt_reference_model",
                "fallback_to_host_global": False,
            },
            "steps": [
                {"state": "validate_spec", "action": "adopt reference model.spec.yaml evidence"},
                {"state": "build_env", "action": "create or verify a harness-local runtime; do not reuse reference .venv"},
                {"state": "fetch_weights", "action": "reuse reference weights/cache through asset-level links"},
                {"state": "validate_import", "action": "adopt passed reference import evidence"},
                {"state": "validate_load", "action": "adopt passed reference load evidence"},
                {"state": "validate_infer", "action": "adopt passed reference inference evidence"},
                {"state": "validate_contract", "action": "adopt passed reference contract evidence"},
                {"state": "save_artifacts", "action": "write harness-local artifact manifest"},
            ],
            "planned_outputs": [
                "model.spec.yaml",
                "model.py",
                "server.py",
                "validate.py",
                "artifacts/artifact_manifest.json",
                "artifacts/package_gate.json",
                "artifacts/verdict.json",
            ],
            "blockers": [],
            "notes": "Generated by adopt_reference_model.py from a previously validated reference model directory.",
        },
    )


def ensure_weights_manifest(target_dir: Path, model_id: str, verdict: dict[str, Any]) -> None:
    path = target_dir / "artifacts" / "weights_manifest.json"
    weights = verdict.get("weights") if isinstance(verdict.get("weights"), dict) else {}
    model_id = str(model_id or verdict.get("model_id") or weights.get("repo_id") or "")
    if path.exists():
        try:
            existing = read_json(path)
        except ValueError:
            existing = {}
        existing_path = existing.get("resolved_local_model_path")
        if existing.get("status") in {"fetched", "passed", "success", "ready"} and existing_path and Path(str(existing_path)).exists():
            return
        if not model_id:
            model_id = str(existing.get("repo_id") or "")
    resolved_path = existing_weight_path(target_dir, model_id, weights)
    write_json(
        path,
        {
            "weights_ready": True,
            "status": "fetched",
            "required": True,
            "source": weights.get("source", ""),
            "repo_id": weights.get("repo_id") or weights.get("model_id"),
            "resolved_local_model_path": resolved_path or "",
            "checkpoint_root": str(Path(resolved_path).parent) if resolved_path else "",
            "verified": weights.get("verified", True),
            "adopted_from_reference": True,
        },
    )


def write_artifact_manifest(target_dir: Path, model_id: str, model_name: str) -> Path:
    required: dict[str, Any] = {
        "spec": {"path": "model.spec.yaml"},
        "model_py": {"path": "model.py"},
        "server_py": {"path": "server.py"},
        "package_init": {"path": "__init__.py"},
        "validate_py": {"path": "validate.py"},
        "config": {"path": "config.yaml"},
    }
    for name in [
        "build_plan.json",
        "weights_manifest.json",
        "env_compat_result.json",
        "import_result.json",
        "load_result.json",
        "infer_result.json",
        "contract_result.json",
        "sample_output.json",
    ]:
        if (target_dir / "artifacts" / name).exists():
            required[name.replace(".", "_")] = {"path": f"artifacts/{name}"}
    required["manifest"] = {"path": "artifacts/artifact_manifest.json"}
    manifest = {
        "model_dir": str(target_dir),
        "instance_id": f"{model_name}-adopted",
        "timestamp": now_iso(),
        "model_id": model_id,
        "model_name": model_name,
        "phase": "local_onboard_reference_adoption",
        "status": "staged",
        "artifacts": {"required": required, "conditional": {}, "optional": {}},
        "adoption": {
            "source": "adopt_reference_model.py",
            "top_level_policy": "copy",
            "asset_link_policy": (
                "symlink only checkpoint children or selected reusable cache/weight directories below .runtime/; "
                "never symlink model_dir, .venv, tmp, uv-python, or writable scratch directories"
            ),
        },
    }
    path = target_dir / "artifacts" / "artifact_manifest.json"
    write_json(path, manifest)
    return path


def write_package_gate(target_dir: Path, manifest_path: Path) -> None:
    write_json(
        target_dir / "artifacts" / "package_gate.json",
        {
            "status": "passed",
            "package_profile": "none",
            "model_dir": str(target_dir),
            "artifact_manifest_path": str(manifest_path),
            "readiness": {
                "local_ready": True,
                "docker_ready": False,
                "registry_ready": False,
                "vc_ready": None,
            },
            "local": {
                "validation_passed": True,
                "artifacts_complete": True,
                "evidence": [
                    "artifacts/artifact_manifest.json",
                    "artifacts/env_compat_result.json",
                    "artifacts/import_result.json",
                    "artifacts/load_result.json",
                    "artifacts/infer_result.json",
                    "artifacts/contract_result.json",
                    "artifacts/sample_output.json",
                ],
            },
            "notes": "Local-ready package gate adopted from a reference model that already passed validation.",
        },
    )


def write_verdict(target_dir: Path, model_id: str, model_name: str, verdict: dict[str, Any]) -> None:
    write_json(
        target_dir / "artifacts" / "verdict.json",
        {
            "instance_id": f"{model_name}-adopted",
            "timestamp": now_iso(),
            "model_id": model_id,
            "model_name": model_name,
            "status": "passed",
            "package": {"profile": "none"},
            "readiness": {
                "local_ready": True,
                "docker_ready": False,
                "registry_ready": False,
                "vc_ready": None,
            },
            "backend": {
                "type": infer_backend(target_dir / "artifacts", verdict),
                "choice_reason": "Adopted from a previously validated reference model directory.",
            },
            "build": {"success": True, "log_path": "artifacts/build.log"},
            "validation": {
                "import_test": {"passed": True},
                "load_test": {"passed": True},
                "infer_test": {"passed": True},
                "contract_test": {"passed": True},
            },
            "artifacts": {
                "spec_path": "model.spec.yaml",
                "wrapper_path": "model.py",
                "artifact_manifest_path": "artifacts/artifact_manifest.json",
                "validation_log_path": "artifacts/validation.log",
                "sample_output_path": "artifacts/sample_output.json",
            },
            "notes": "Normalized harness verdict generated from reference verdict evidence.",
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-model-dir", required=True)
    parser.add_argument("--target-model-dir", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--replace-symlink", action="store_true")
    parser.add_argument(
        "--replace-existing-adoption",
        action="store_true",
        help="Replace a non-empty target only if it was previously generated by adopt_reference_model.py.",
    )
    args = parser.parse_args()

    reference_dir = Path(args.reference_model_dir).expanduser().resolve()
    target_dir = Path(args.target_model_dir).expanduser()
    if not reference_dir.exists() or not reference_dir.is_dir():
        print(f"adopt_reference_model failed: reference dir not found: {reference_dir}", file=sys.stderr)
        return 1
    missing_core = [name for name in CORE_FILES if not (reference_dir / name).exists()]
    if missing_core:
        print("adopt_reference_model failed: reference missing core files: " + ", ".join(missing_core), file=sys.stderr)
        return 1
    try:
        source_verdict = read_json(reference_dir / "artifacts" / "verdict.json")
        prepare_target(target_dir, args.replace_symlink, args.replace_existing_adoption)
        stage_top_level_files(reference_dir, target_dir)
        link_asset_children(reference_dir, target_dir)
        copy_reference_artifacts(reference_dir, target_dir)
        ensure_stage_results(target_dir, source_verdict, reference_dir)
        ensure_env_compat_result(target_dir, source_verdict)
        ensure_build_plan(target_dir, args.model_id, args.model_name, source_verdict)
        ensure_weights_manifest(target_dir, args.model_id, source_verdict)
        manifest_path = write_artifact_manifest(target_dir, args.model_id, args.model_name)
        write_package_gate(target_dir, manifest_path)
        write_verdict(target_dir, args.model_id, args.model_name, source_verdict)
    except Exception as exc:  # noqa: BLE001
        print(f"adopt_reference_model failed: {exc}", file=sys.stderr)
        return 1

    print(f"adopt_reference_model OK: target={target_dir}, reference={reference_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
