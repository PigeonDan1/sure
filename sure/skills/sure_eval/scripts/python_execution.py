#!/usr/bin/env python3
"""Build a local host-Python command from an approved deployment binding."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from container_execution import surface_env
from evaluation_runtime import evaluation_runtime_from_eval_input
from harness_runtime import harness_runtime_from_eval_input


def deployment_binding(eval_input: dict[str, Any]) -> dict[str, Any]:
    model = eval_input.get("model") if isinstance(eval_input.get("model"), dict) else {}
    binding = model.get("deployment_binding")
    if not isinstance(binding, dict):
        runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
        binding = runtime.get("deployment_binding")
    if not isinstance(binding, dict) or binding.get("schema") != "sure.eval.deployment_binding.v1":
        raise ValueError("eval input does not contain an approved deployment binding")
    policy = binding.get("policy") if isinstance(binding.get("policy"), dict) else {}
    if binding.get("runtime_kind") != "python" or policy.get("execution_mode") != "python":
        raise ValueError("approved deployment binding is not a Python runtime")
    if policy.get("host_python_fallback") is not False:
        raise ValueError("approved Python runtime must be selected explicitly")
    return binding


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
    model_dir = Path(str(binding.get("model_dir") or "")).resolve()
    working_dir = Path(str(python.get("working_dir") or model_dir)).resolve()
    if not model_python.is_file() or not model_dir.is_dir() or not working_dir.is_dir():
        raise ValueError("approved Python deployment paths are not materialized")

    harness_runtime = harness_runtime_from_eval_input(eval_input)
    harness_python = Path(str(harness_runtime["python_executable"])).expanduser()
    if model_python == harness_python:
        raise ValueError("Harness Python and Model Python must be separate execution roles")
    evaluation_runtime = evaluation_runtime_from_eval_input(eval_input, prepare=False)
    output_dir = Path(str((eval_input.get("runtime") or {}).get("run_dir") or "")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    skill_root = (repo_root / "sure" / "skills" / "sure_eval").resolve()
    if not (skill_root / "scripts").is_dir():
        raise ValueError(f"SURE-EVAL skill root is invalid: {skill_root}")
    cache_root = output_dir / ".runtime" / "cache"
    source_provenance = surface.get("source_provenance") if isinstance(surface.get("source_provenance"), dict) else {}

    env = surface_env(surface)
    env.update(extra_env or {})
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
            "SURE_EVAL_EXECUTION_SURFACE_TYPE": "main_flow_script",
            "SURE_EVAL_EXECUTION_ENTRYPOINT": str(entrypoint.resolve()),
            "SURE_EVAL_EXECUTION_GENERATION_METHOD": str(surface.get("generation_method") or "harness_template"),
            "SURE_EVAL_EXECUTION_TEMPLATE_FILE": str(source_provenance.get("template_file") or ""),
            "SURE_EVAL_EXECUTION_TEMPLATE_SHA256": str(source_provenance.get("template_sha256") or ""),
            "SURE_EVAL_PUBLISHED_RUN_DIR": str(output_dir),
            "SURE_EVAL_MODEL_RUNTIME_KIND": "python",
            "SURE_EVAL_MODEL_WORKING_DIR": str(working_dir),
            "SURE_EVAL_WRITABLE_CACHE_ROOT": str(cache_root),
            "SURE_EVAL_CACHE_DIR": str(cache_root / "sure-eval"),
            "HF_HOME": str(cache_root / "huggingface"),
            "HF_HUB_CACHE": str(cache_root / "huggingface" / "hub"),
            "TRANSFORMERS_CACHE": str(cache_root / "huggingface" / "transformers"),
            "MODELSCOPE_CACHE": str(cache_root / "modelscope"),
            "TORCH_HOME": str(cache_root / "torch"),
            "XDG_CACHE_HOME": str(cache_root / "xdg"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    if evaluation_runtime is not None:
        env.update(
            {
                "SURE_EVALUATION_PYTHON": str(evaluation_runtime["python_executable"]),
                "SURE_EVALUATION_RUNTIME_ID": str(evaluation_runtime["runtime_id"]),
                "SURE_EVALUATION_LOCK_SHA256": str(evaluation_runtime["lock_sha256"]),
                "SURE_EVALUATION_RUNTIME_MANIFEST": str(evaluation_runtime["manifest_path"]),
                "SURE_EVALUATION_HOME": str(evaluation_runtime["engine_root"]),
            }
        )
    return ["bash", str(entrypoint.resolve())], env, {
        "runtime_kind": "python",
        "model_runtime": python,
        "harness_runtime": harness_runtime,
        "evaluation_runtime": evaluation_runtime,
        "model_dir": str(model_dir),
        "working_dir": str(working_dir),
    }
