#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

try:
    from sure.site.container_delivery import resolve_container_image, resolve_container_repository
    from sure.site.container_registry import resolve_image_version
    from sure.site.loader import load_site_policy
except ImportError as error:
    raise RuntimeError(
        "the SURE site resolver is not bundled; run materialize_trans_inputs.py "
        "from a complete sure-harness checkout"
    ) from error

FRAMEWORK_ALIASES = {
    "pytorch": "pytorch",
    "torch": "pytorch",
}
MODEL_FRAMEWORK_ALIASES = {
    "transformer": "transformers",
    "transformers": "transformers",
    "huggingface": "transformers",
    "huggingface-transformers": "transformers",
    "huggingface_transformers": "transformers",
    "pytorch-transformers": "transformers",
    "pytorch_transformers": "transformers",
}
MODEL_FRAMEWORK = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")

TASK_TYPES = {"asr", "s2tt", "tts", "vc"}
TASK_MARKERS = {
    "asr": ("asr", "transcribe", "speech recognition", "speech_recognition"),
    "s2tt": ("s2tt", "speech translation", "translate_audio", "speech_to_text_translation"),
    "tts": ("tts", "text to speech", "text-to-speech", "synthesize_speech"),
    "vc": ("voice conversion", "voice_conversion", "convert_voice", "reference_audio_path"),
}

try:
    from vc_exec import DEFAULT_GPUS, DEFAULT_MEMORY_GB, default_partition
except ImportError:  # kept standalone when vc_exec.py is not bundled
    DEFAULT_GPUS = 1
    DEFAULT_MEMORY_GB = 32

    def default_partition() -> str:
        raise ValueError("vc_exec.py is not bundled; pass --vc-partition explicitly")

SAFE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def normalized_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "__", value).strip("._-")
    if not name or "/" in name or "\\" in name:
        raise ValueError(f"invalid model name: {value!r}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*__[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ValueError("model_name must use <organization>__<model_name>")
    return name


def existing_absolute(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute: {value}")
    path = path.resolve()
    if not path.exists():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def resolve_task_type(explicit: str | None, inference_entrypoint: Path, model_path: Path) -> str:
    if explicit:
        task_type = explicit.strip().lower()
        if task_type not in TASK_TYPES:
            raise ValueError(f"unsupported task type {explicit!r}; expected one of {sorted(TASK_TYPES)}")
        return task_type
    source = inference_entrypoint.read_text(encoding="utf-8", errors="replace")[:2_000_000].lower()
    corpus = f"{inference_entrypoint} {model_path} {source}"
    scores = {
        task_type: sum(corpus.count(marker) for marker in markers)
        for task_type, markers in TASK_MARKERS.items()
    }
    highest = max(scores.values())
    winners = [task_type for task_type, score in scores.items() if score == highest and score > 0]
    if len(winners) != 1:
        raise ValueError(
            "task_type could not be inferred unambiguously; pass task_type=asr|s2tt|tts|vc"
        )
    return winners[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dockerfile")
    parser.add_argument("--python-executable")
    parser.add_argument("--lockfile")
    parser.add_argument(
        "--package", "--package-profile", dest="package_profile",
        choices=("docker-registry", "none"), default="docker-registry",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--inference-entrypoint", required=True)
    parser.add_argument("--framework", required=True)
    parser.add_argument("--model-framework", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--build-context")
    parser.add_argument("--model-name")
    parser.add_argument("--task-type")
    parser.add_argument("--fixture")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--source-image-policy", choices=("auto", "build", "load"), default="auto")
    parser.add_argument("--image-tar")
    parser.add_argument("--model-mount-target")
    parser.add_argument("--model-stage-policy", choices=("copy", "hardlink", "auto"), default="auto")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--vc-partition")
    parser.add_argument("--vc-memory-gb", type=int)
    parser.add_argument("--vc-gpus", type=int)
    parser.add_argument("--image-version")
    args = parser.parse_args()

    if bool(args.dockerfile) == bool(args.python_executable):
        raise ValueError("provide exactly one of --dockerfile or --python-executable")
    source_kind = "docker" if args.dockerfile else "python"
    dockerfile = existing_absolute(args.dockerfile, "dockerfile") if args.dockerfile else None
    if dockerfile is not None and not dockerfile.is_file():
        raise ValueError(f"dockerfile must be a file: {dockerfile}")
    python_executable = (
        existing_absolute(args.python_executable, "python executable")
        if args.python_executable else None
    )
    if python_executable is not None and not python_executable.is_file():
        raise ValueError(f"python executable must be a file: {python_executable}")
    lockfile = existing_absolute(args.lockfile, "lockfile") if args.lockfile else None
    if source_kind == "python" and lockfile is None:
        raise ValueError("Python input requires --lockfile")
    if lockfile is not None and not lockfile.is_file():
        raise ValueError(f"lockfile must be a file: {lockfile}")
    if source_kind == "docker" and args.package_profile == "none":
        raise ValueError("package=none requires Python input")
    model_path = existing_absolute(args.model, "model")
    inference_entrypoint = existing_absolute(args.inference_entrypoint, "inference entrypoint")
    if not inference_entrypoint.is_file():
        raise ValueError(f"inference entrypoint must be a file: {inference_entrypoint}")
    build_context = (
        existing_absolute(args.build_context, "build context")
        if args.build_context
        else dockerfile.parent if dockerfile is not None else inference_entrypoint.parent
    )
    if not build_context.is_dir():
        raise ValueError(f"build context must be a directory: {build_context}")
    try:
        if dockerfile is not None:
            dockerfile.relative_to(build_context)
    except ValueError as error:
        raise ValueError("dockerfile must be inside build context") from error

    framework = FRAMEWORK_ALIASES.get(args.framework.strip().lower())
    if framework is None:
        raise ValueError(f"unsupported framework {args.framework!r}; framework must be pytorch")
    raw_model_framework = args.model_framework.strip().lower()
    model_framework = MODEL_FRAMEWORK_ALIASES.get(raw_model_framework, raw_model_framework)
    if not MODEL_FRAMEWORK.fullmatch(model_framework):
        raise ValueError(
            "model_framework must be a non-empty lowercase identifier using letters, digits, '.', '_' or '-'"
        )
    model_name = normalized_name(args.model_name) if args.model_name else re.sub(r"[^A-Za-z0-9._-]+", "__", model_path.name).strip("._-")
    task_type = resolve_task_type(args.task_type, inference_entrypoint, model_path)
    site = {}
    policy = {}
    source_repository = None
    target_repository = None
    if args.package_profile == "docker-registry":
        site = load_site_policy(required=True) or {}
        policy = site.get("policy")
        if not isinstance(policy, dict):
            raise ValueError("site policy did not resolve to an object")
        if source_kind == "docker":
            source_repository = resolve_container_repository(
                policy, task_type=task_type, model_name=model_name, stage="source"
            )
        target_repository = resolve_container_repository(
            policy, task_type=task_type, model_name=model_name
        )
    fixture_path = existing_absolute(args.fixture, "fixture") if args.fixture else None
    image_tar = existing_absolute(args.image_tar, "image tar") if args.image_tar else None
    if image_tar is not None and not image_tar.is_file():
        raise ValueError(f"image tar must be a file: {image_tar}")
    if image_tar is not None:
        try:
            image_tar.relative_to(build_context)
        except ValueError as error:
            raise ValueError("image tar must be inside build context") from error
    source_text = inference_entrypoint.read_text(encoding="utf-8", errors="replace").lower()
    gpu_required = any(marker in source_text for marker in ("torch.cuda.is_available", "cuda is unavailable", "use_gpu=true", "use_gpu = true"))
    bf16_required = any(marker in source_text for marker in ("is_bf16_supported", "bfloat16", "bf16"))
    repo_root = Path(args.repo_root).expanduser().resolve()
    model_dir = repo_root / "sure" / "models" / model_name
    mount_target = args.model_mount_target or f"/models/{model_name}"
    if not mount_target.startswith("/"):
        raise ValueError("model mount target must be absolute inside the container")
    if args.max_retries < 1:
        raise ValueError("max retries must be positive")
    vc_partition = args.vc_partition or (default_partition() if source_kind == "docker" else None)
    if vc_partition is not None and not SAFE_TAG.fullmatch(vc_partition):
        raise ValueError(f"invalid vc partition: {vc_partition!r}")
    vc_gpus = args.vc_gpus if args.vc_gpus is not None else DEFAULT_GPUS
    vc_memory_gb = args.vc_memory_gb if args.vc_memory_gb is not None else DEFAULT_MEMORY_GB
    if vc_gpus < 1:
        raise ValueError("vc gpus must be positive")
    if vc_memory_gb < 1:
        raise ValueError("vc memory must be positive")
    image_version = None
    image_version_resolution = None
    container_delivery = None
    if target_repository is not None:
        repositories = [target_repository]
        if source_repository is not None:
            repositories.insert(0, source_repository)
        image_version, image_version_resolution = resolve_image_version(repositories, args.image_version)
        if not SAFE_TAG.fullmatch(image_version):
            raise ValueError(f"invalid image version: {image_version!r}")
        container_delivery = {
            "target_repository": target_repository,
            "target_image": resolve_container_image(
                policy, task_type=task_type, model_name=model_name, version=image_version
            ),
            "site_policy_path": site.get("path"),
            "site_policy_sha256": site.get("sha256"),
        }
        if source_repository is not None:
            container_delivery.update({
                "source_repository": source_repository,
                "source_image": resolve_container_image(
                    policy, task_type=task_type, model_name=model_name,
                    version=image_version, stage="source",
                ),
            })
    gpu_surface = source_kind == "docker" and args.device != "cpu"

    payload = {
        "schema": "sure.trans.input.v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_kind": source_kind,
        "dockerfile": str(dockerfile) if dockerfile else None,
        "python_executable": str(python_executable) if python_executable else None,
        "lockfile": str(lockfile) if lockfile else None,
        "build_context": str(build_context),
        "model_path": str(model_path),
        "inference_entrypoint": str(inference_entrypoint),
        "framework": framework,
        "model_framework": model_framework,
        "model_name": model_name,
        "model_dir": str(model_dir),
        "task_type": task_type,
        "fixture_path": str(fixture_path) if fixture_path else None,
        "device": args.device,
        "execution_surface": "local_python" if source_kind == "python" else "vc" if gpu_surface else "local_docker",
        "gpu_required": gpu_required,
        "bf16_required": bf16_required,
        "source_image_policy": args.source_image_policy,
        "image_tar": str(image_tar) if image_tar else None,
        "package_profile": args.package_profile,
        "model_mount_target": mount_target,
        "model_stage_policy": args.model_stage_policy,
        "max_retries": args.max_retries,
        "image_version": image_version,
        "image_version_resolution": image_version_resolution,
        "container_delivery": container_delivery,
        "path_policy": {
            "model_read_only": True,
            "source_paths_read_only": True,
            "allowed_model_root": str(repo_root / "sure" / "models"),
            "generated_files_root": str(Path(args.run_dir).resolve() / "adapter"),
        },
    }
    if gpu_surface:
        payload["vc_partition"] = vc_partition
        payload["vc_memory_gb"] = vc_memory_gb
        payload["vc_gpus"] = vc_gpus
    output = Path(args.run_dir).resolve() / "artifacts" / "trans_input_resolved.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
