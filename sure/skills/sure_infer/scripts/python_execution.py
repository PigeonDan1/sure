#!/usr/bin/env python3
"""Build an explicitly approved trusted-host Model Python launch."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Mapping

from container_execution import surface_env, surface_env_refuses
from deployment_binding import DEPLOYMENT_BINDING_V2
from harness_runtime import harness_runtime_from_eval_input


ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
HOST_ENV_ALLOW = {
    "CUDA_VISIBLE_DEVICES",
    "LANG",
    "LC_ALL",
    "LD_LIBRARY_PATH",
    "NVIDIA_DRIVER_CAPABILITIES",
    "NVIDIA_VISIBLE_DEVICES",
    "PATH",
    "TERM",
    "TZ",
}
SENSITIVE_PARTS = (
    "ACCESS_KEY",
    "API_KEY",
    "AUTH",
    "COOKIE",
    "CREDENTIAL",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
)


def deployment_binding(eval_input: dict[str, Any]) -> dict[str, Any]:
    model = eval_input.get("model") if isinstance(eval_input.get("model"), dict) else {}
    binding = model.get("deployment_binding")
    if not isinstance(binding, dict):
        runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
        binding = runtime.get("deployment_binding")
    policy = binding.get("policy") if isinstance(binding, dict) and isinstance(binding.get("policy"), dict) else {}
    if (
        not isinstance(binding, dict)
        or binding.get("schema") != DEPLOYMENT_BINDING_V2
        or binding.get("runtime_kind") != "python"
        or policy.get("execution_mode") != "python"
        or policy.get("host_python_fallback") is not False
    ):
        raise ValueError("eval input does not contain an explicitly approved Python deployment binding")
    return binding


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_model_integrity(binding: dict[str, Any]) -> dict[str, str]:
    model_dir = Path(str(binding.get("model_dir") or "")).expanduser().resolve()
    evidence = binding.get("evidence") if isinstance(binding.get("evidence"), dict) else {}
    declared = evidence.get("model_core_sha256")
    if not isinstance(declared, dict) or not declared:
        raise ValueError("approved Python binding has no model integrity baseline")
    verified: dict[str, str] = {}
    for relative, expected in declared.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ValueError("approved Python model integrity entry is invalid")
        path = (model_dir / relative).resolve()
        try:
            path.relative_to(model_dir)
        except ValueError as exc:
            raise ValueError(f"model integrity path escapes approved model: {relative}") from exc
        if not path.is_file() or _sha256(path) != expected:
            raise ValueError(f"approved model changed after onboarding: {relative}")
        verified[relative] = expected
    return verified


def _safe_environment(source: Mapping[str, str], declared: Mapping[str, str]) -> dict[str, str]:
    env = {key: value for key, value in source.items() if key in HOST_ENV_ALLOW}
    for key, value in declared.items():
        if not ENV_NAME_RE.fullmatch(key) or any(part in key.upper() for part in SENSITIVE_PARTS):
            continue
        if not surface_env_refuses(key):
            env[key] = str(value)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def build_local_python_command(
    *,
    surface: dict[str, Any],
    eval_input: dict[str, Any],
    entrypoint: Path,
    repo_root: Path,
    extra_env: dict[str, str] | None = None,
) -> tuple[list[str], dict[str, str], dict[str, Any]]:
    binding = deployment_binding(eval_input)
    python = binding.get("python") if isinstance(binding.get("python"), dict) else {}
    model_python = Path(str(python.get("python_executable") or "")).expanduser()
    model_dir = Path(str(binding.get("model_dir") or "")).expanduser().resolve()
    working_dir = Path(str(python.get("working_dir") or model_dir)).expanduser().resolve()
    if not model_python.is_file() or not os.access(model_python, os.X_OK):
        raise ValueError("approved Model Python is not materialized")
    if not model_dir.is_dir() or not working_dir.is_dir():
        raise ValueError("approved Python model paths are not materialized")
    verified = verify_model_integrity(binding)

    harness_runtime = harness_runtime_from_eval_input(eval_input)
    harness_python = Path(str(harness_runtime["python_executable"])).expanduser()
    if model_python.parent.resolve() / model_python.name == harness_python.parent.resolve() / harness_python.name:
        raise ValueError("Harness Python and Model Python must remain separate execution roles")
    runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
    output_dir = Path(str(runtime.get("run_dir") or "")).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    skill_root = (repo_root / "sure" / "skills" / "sure_infer").resolve()
    if not (skill_root / "scripts").is_dir():
        raise ValueError(f"SURE-INFER skill root is invalid: {skill_root}")
    cache_root = output_dir / ".runtime" / "cache"
    source_provenance = surface.get("source_provenance") if isinstance(surface.get("source_provenance"), dict) else {}

    declared = surface_env(surface)
    declared.update(extra_env or {})
    env = _safe_environment(os.environ, declared)
    env.update(
        {
            "MODEL_DIR": str(model_dir),
            "REPO_ROOT": str(skill_root),
            "SURE_EVAL_APPROVED_MODEL_DIR": str(model_dir),
            "RUN_DIR": str(output_dir),
            "MODEL_PYTHON": str(model_python),
            "PYTHON_BIN": str(model_python),
            "HARNESS_PYTHON_BIN": str(harness_python),
            "SURE_HARNESS_RUNTIME_ID": str(harness_runtime["runtime_id"]),
            "SURE_HARNESS_LOCK_SHA256": str(harness_runtime["lock_sha256"]),
            "SURE_HARNESS_MANIFEST_PATH": str(harness_runtime["manifest_path"]),
            "SURE_HARNESS_RUNTIME_ROOT": str(harness_runtime["runtime_root"]),
            "SURE_EVAL_EXECUTION_SURFACE_TYPE": str(surface.get("execution_surface_type") or "python_entrypoint"),
            "SURE_EVAL_EXECUTION_ENTRYPOINT": str(entrypoint.resolve()),
            "SURE_EVAL_EXECUTION_GENERATION_METHOD": str(surface.get("generation_method") or "harness_template"),
            "SURE_EVAL_EXECUTION_TEMPLATE_FILE": str(source_provenance.get("template_file") or ""),
            "SURE_EVAL_EXECUTION_TEMPLATE_SHA256": str(source_provenance.get("template_sha256") or ""),
            "SURE_EVAL_PUBLISHED_RUN_DIR": str(output_dir),
            "SURE_EVAL_MODEL_RUNTIME_KIND": "python",
            "SURE_EVAL_MODEL_RUNTIME_ID": str(python.get("runtime_id") or ""),
            "SURE_EVAL_MODEL_WORKING_DIR": str(working_dir),
            "SURE_EVAL_WRITABLE_CACHE_ROOT": str(cache_root),
            "SURE_EVAL_CACHE_DIR": str(cache_root / "sure-eval"),
            "HF_HOME": str(cache_root / "huggingface"),
            "HF_HUB_CACHE": str(cache_root / "huggingface" / "hub"),
            "TRANSFORMERS_CACHE": str(cache_root / "huggingface" / "transformers"),
            "MODELSCOPE_CACHE": str(cache_root / "modelscope"),
            "TORCH_HOME": str(cache_root / "torch"),
            "XDG_CACHE_HOME": str(cache_root / "xdg"),
        }
    )
    return [str(harness_python), str(entrypoint.resolve())], env, {
        "runtime_kind": "python",
        "model_runtime": python,
        "harness_runtime": harness_runtime,
        "model_dir": str(model_dir),
        "working_dir": str(working_dir),
        "model_core_sha256": verified,
        "host_python_fallback": False,
    }
