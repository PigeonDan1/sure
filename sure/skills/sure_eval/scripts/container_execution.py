#!/usr/bin/env python3
"""Build the local Docker execution command from an approved deployment binding."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from harness_runtime import harness_runtime_from_eval_input
from evaluation_runtime import evaluation_runtime_from_eval_input


ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DOCKER_EXIT_RE = re.compile(r"(?m)^\s*Error: exit status ([1-9][0-9]*)\s*$")


def effective_container_exit_code(returncode: int, *output: str) -> int:
    """Recover the inner status from the site Docker wrapper when needed."""
    if returncode != 0:
        return returncode
    matches = DOCKER_EXIT_RE.findall("\n".join(output))
    return int(matches[-1]) if matches else 0


def deployment_binding(eval_input: dict[str, Any]) -> dict[str, Any]:
    model = eval_input.get("model") if isinstance(eval_input.get("model"), dict) else {}
    binding = model.get("deployment_binding")
    if not isinstance(binding, dict):
        runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
        binding = runtime.get("deployment_binding")
    if not isinstance(binding, dict) or binding.get("schema") != "sure.eval.deployment_binding.v1":
        raise ValueError("eval input does not contain an approved deployment binding")
    policy = binding.get("policy") if isinstance(binding.get("policy"), dict) else {}
    if policy.get("execution_mode") != "container_only" or policy.get("host_python_fallback") is not False:
        raise ValueError("approved deployment binding is not container-only")
    image_ref = str(binding.get("target_image_ref") or "")
    if "@sha256:" not in image_ref:
        raise ValueError("approved deployment image is not digest-pinned")
    return binding


def surface_env(surface: dict[str, Any]) -> dict[str, str]:
    raw = surface.get("env")
    if not isinstance(raw, dict):
        return {}
    return {
        key: str(value)
        for key, value in raw.items()
        if isinstance(key, str) and ENV_NAME_RE.fullmatch(key) and value is not None
    }


def dataset_projection_root_from_eval_input(eval_input: dict[str, Any]) -> Path | None:
    runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
    projection = runtime.get("dataset_projection")
    if projection is None:
        return None
    if not isinstance(projection, dict):
        raise ValueError("eval input dataset_projection must be an object")
    raw_root = str(projection.get("host_root") or "")
    root = Path(raw_root).expanduser()
    if not raw_root or not root.is_absolute():
        raise ValueError("eval input dataset_projection.host_root must be absolute")
    root = root.resolve()
    if not (root / "sure_benchmark" / "jsonl").is_dir():
        raise ValueError(
            "eval input dataset projection must contain sure_benchmark/jsonl: "
            f"{root}"
        )
    return root


def _mount(
    command: list[str],
    mounted_targets: dict[str, tuple[Path, bool]],
    source: Path,
    target: str,
    *,
    read_only: bool,
) -> None:
    source = source.resolve()
    existing = mounted_targets.get(target)
    if existing:
        if existing != (source, read_only):
            raise ValueError(f"conflicting container mount target: {target}")
        return
    spec = f"type=bind,src={source},dst={target}"
    if read_only:
        spec += ",readonly"
    command.extend(["--mount", spec])
    mounted_targets[target] = (source, read_only)


def _replace_prefix(value: str, source: Path, target: str) -> str:
    if not value.startswith("/"):
        return value
    path = Path(value)
    try:
        relative = path.relative_to(source)
    except ValueError:
        return value
    return str(Path(target) / relative)


def resolve_container_harness_runtime(
    binding: dict[str, Any],
    host_runtime: dict[str, Any],
    repo_root: Path,
) -> tuple[dict[str, Any], bool]:
    container = binding.get("container") if isinstance(binding.get("container"), dict) else {}
    image_runtime = container.get("harness_runtime")
    model_python = str(container.get("python_executable") or "python")
    if isinstance(image_runtime, dict):
        if image_runtime.get("runtime_id") != host_runtime.get("runtime_id"):
            raise ValueError("approved image Harness Runtime ID differs from the active common runtime")
        if image_runtime.get("lock_sha256") != host_runtime.get("lock_sha256"):
            raise ValueError("approved image Harness Runtime lock differs from the active common runtime")
        python = str(image_runtime.get("python_executable") or "")
        manifest = str(image_runtime.get("manifest_path") or "")
        root = str(image_runtime.get("runtime_root") or "")
        if not all(Path(value).is_absolute() for value in (python, manifest, root)):
            raise ValueError("approved image Harness Runtime paths must be absolute")
        if python == model_python:
            raise ValueError("Harness Python and Model Python must be separate execution roles")
        return {**image_runtime, "execution_source": "approved_image"}, False

    harness_root = Path(str(host_runtime["runtime_root"])).resolve()
    try:
        harness_root.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ValueError("external Harness Runtime must be materialized under the mounted repository") from exc
    return {**host_runtime, "execution_source": "mounted_common_runtime"}, True


def build_local_container_command(
    *,
    surface: dict[str, Any],
    eval_input: dict[str, Any],
    control_run_dir: Path,
    entrypoint: Path,
    repo_root: Path,
    device_request: str,
    extra_env: dict[str, str] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    binding = deployment_binding(eval_input)
    host_harness_runtime = harness_runtime_from_eval_input(eval_input)
    container = binding.get("container") if isinstance(binding.get("container"), dict) else {}
    model_mount = container.get("model_mount") if isinstance(container.get("model_mount"), dict) else {}
    result_mount = container.get("result_mount") if isinstance(container.get("result_mount"), dict) else {}
    model_source = Path(str(model_mount.get("source") or binding.get("model_dir") or "")).resolve()
    model_target = str(model_mount.get("target") or "")
    output_source = Path(str((eval_input.get("runtime") or {}).get("run_dir") or "")).resolve()
    output_target = str(result_mount.get("target") or "/sure-output")
    if not model_source.is_dir() or not Path(model_target).is_absolute():
        raise ValueError("approved deployment model mount is invalid")
    if not output_source.is_dir():
        output_source.mkdir(parents=True, exist_ok=True)
    if not Path(output_target).is_absolute():
        raise ValueError("approved deployment result mount target is invalid")
    harness_runtime, harness_mounted_from_repo = resolve_container_harness_runtime(
        binding,
        host_harness_runtime,
        repo_root,
    )
    harness_root = str(harness_runtime["runtime_root"])
    harness_python = str(harness_runtime["python_executable"])
    harness_manifest = str(harness_runtime["manifest_path"])
    evaluation_runtime = evaluation_runtime_from_eval_input(eval_input, prepare=False)
    dataset_projection_root = dataset_projection_root_from_eval_input(eval_input)

    command = ["docker", "run", "--rm", "--init", "--entrypoint", "bash"]
    mounted_targets: dict[str, tuple[Path, bool]] = {}
    lowered = (device_request or "auto").lower()
    if lowered != "cpu" and container.get("gpu_required") is True:
        if lowered.startswith("cuda:"):
            command.extend(["--gpus", f"device={lowered.split(':', 1)[1]}"])
        else:
            command.extend(["--gpus", "all"])

    _mount(command, mounted_targets, repo_root.resolve(), str(repo_root.resolve()), read_only=True)
    _mount(command, mounted_targets, control_run_dir.resolve(), str(control_run_dir.resolve()), read_only=False)
    _mount(command, mounted_targets, output_source, output_target, read_only=False)
    _mount(command, mounted_targets, model_source, str(model_source), read_only=True)
    if model_target != str(model_source):
        _mount(command, mounted_targets, model_source, model_target, read_only=True)
    if dataset_projection_root is not None:
        _mount(
            command,
            mounted_targets,
            dataset_projection_root,
            str(dataset_projection_root),
            read_only=False,
        )

    for item in eval_input.get("datasets", []):
        if not isinstance(item, dict):
            continue
        for key in ("source_root", "jsonl_path"):
            raw = item.get(key)
            if not isinstance(raw, str) or not raw.startswith("/"):
                continue
            declared_path = Path(raw).expanduser()
            path = declared_path.resolve()
            if key == "jsonl_path" and dataset_projection_root is not None:
                try:
                    path.relative_to(dataset_projection_root)
                except ValueError:
                    pass
                else:
                    continue
            mount_source = path if path.is_dir() else path.parent
            mount_target = declared_path if path.is_dir() else declared_path.parent
            if mount_source.exists():
                _mount(command, mounted_targets, mount_source, str(mount_target), read_only=True)

    env = surface_env(surface)
    source_provenance = surface.get("source_provenance") if isinstance(surface.get("source_provenance"), dict) else {}
    for key, value in list(env.items()):
        value = _replace_prefix(value, output_source, output_target)
        value = _replace_prefix(value, model_source, model_target)
        env[key] = value
    env.update(extra_env or {})
    env.update(
        {
            "MODEL_DIR": model_target,
            "SURE_EVAL_APPROVED_MODEL_DIR": model_target,
            "RUN_DIR": output_target,
            "SURE_EVAL_APPROVED_RESULT_DIR": output_target,
            "MODEL_PYTHON": str(container.get("python_executable") or "python"),
            "PYTHON_BIN": str(container.get("python_executable") or "python"),
            "HARNESS_PYTHON_BIN": harness_python,
            "SURE_EVAL_NODE_LOCAL_PYTHON": harness_python,
            "SURE_HARNESS_RUNTIME_ID": str(harness_runtime["runtime_id"]),
            "SURE_HARNESS_LOCK_SHA256": str(harness_runtime["lock_sha256"]),
            "SURE_HARNESS_MANIFEST_PATH": harness_manifest,
            "SURE_HARNESS_RUNTIME_ROOT": harness_root,
            "SURE_EVAL_CONTAINER_IMAGE": str(binding["target_image_ref"]),
            "SURE_EVAL_CONTAINER_WORKING_DIR": str(container.get("working_dir") or model_target),
            "SURE_EVAL_EXECUTION_SURFACE_TYPE": "main_flow_script",
            "SURE_EVAL_EXECUTION_ENTRYPOINT": str(entrypoint.resolve()),
            "SURE_EVAL_EXECUTION_GENERATION_METHOD": str(surface.get("generation_method") or "harness_template"),
            "SURE_EVAL_EXECUTION_TEMPLATE_FILE": str(source_provenance.get("template_file") or ""),
            "SURE_EVAL_EXECUTION_TEMPLATE_SHA256": str(source_provenance.get("template_sha256") or ""),
            "SURE_EVAL_PUBLISHED_RUN_DIR": str(output_source),
            "SURE_EVAL_WRITABLE_CACHE_ROOT": f"{output_target}/.runtime/cache",
            "SURE_EVAL_CACHE_DIR": f"{output_target}/.runtime/cache/sure-eval",
            "HF_HOME": f"{output_target}/.runtime/cache/huggingface",
            "HF_HUB_CACHE": f"{output_target}/.runtime/cache/huggingface/hub",
            "TRANSFORMERS_CACHE": f"{output_target}/.runtime/cache/huggingface/transformers",
            "MODELSCOPE_CACHE": f"{output_target}/.runtime/cache/modelscope",
            "TORCH_HOME": f"{output_target}/.runtime/cache/torch",
            "XDG_CACHE_HOME": f"{output_target}/.runtime/cache/xdg",
        }
    )
    if dataset_projection_root is not None:
        env["SURE_EVAL_DATASETS_ROOT"] = str(dataset_projection_root)
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
    for key in sorted(env):
        command.extend(["--env", f"{key}={env[key]}"])
    command.extend([str(binding["target_image_ref"]), str(entrypoint.resolve())])
    return command, {
        "image": binding["target_image"],
        "image_digest": binding["target_image_digest"],
        "image_ref": binding["target_image_ref"],
        "model_mount": {"source": str(model_source), "target": model_target, "read_only": True},
        "result_mount": {"source": str(output_source), "target": output_target, "read_only": False},
        "dataset_projection_mount": (
            {
                "source": str(dataset_projection_root),
                "target": str(dataset_projection_root),
                "read_only": False,
            }
            if dataset_projection_root is not None
            else None
        ),
        "harness_runtime": harness_runtime,
        "evaluation_runtime": evaluation_runtime,
        "evaluation_node_runtime": {
            "runtime_type": "evaluation_node_python",
            "python_executable": harness_python,
            "source": "approved_common_harness_runtime",
        },
        "harness_runtime_mounted_from_repo": harness_mounted_from_repo,
        "model_runtime": {
            "runtime_type": "model_python",
            "python_executable": str(container.get("python_executable") or "python"),
        },
        "host_python_fallback": False,
    }
