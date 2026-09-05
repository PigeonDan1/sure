#!/usr/bin/env python3
"""Materialize early /sure_onboard artifacts from MODEL_INPUT.

This helper intentionally emits only deterministic early artifacts:

- model_input_resolved.json
- context_selection.json

It does not emit backend_choice.json or build_plan.json. Those remain
agent-research-first artifacts because they must be backed by repository
evidence, install/load/inference documentation, and observed constraints.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.site.container_delivery import resolve_container_image, resolve_container_repository
from sure.site.container_registry import resolve_image_version
from sure.site.loader import load_site_policy

try:
    import yaml
except Exception as exc:  # noqa: BLE001
    yaml = None
    YAML_IMPORT_ERROR = exc
else:
    YAML_IMPORT_ERROR = None


TASK_TYPES = {
    "asr",
    "s2tt",
    "sd",
    "ser",
    "tts",
    "vc",
    "kws",
    "slu",
    "gr",
    "speech_understanding",
    "sa-asr",
    "sa_asr",
}
DEPLOYMENT_TYPES = {"local", "api"}
PACKAGE_PROFILES = {"none", "docker-local", "docker-registry"}
WEIGHTS_LINK_POLICIES = {"auto", "copy", "symlink", "reuse-existing", "no-reuse"}
DEVICES = {"auto", "cuda", "cpu", "mps"}

ALL_TASK_PLAYBOOKS = [
    "references/task_playbooks/ASR.md",
    "references/task_playbooks/SPEECH_UNDERSTANDING.md",
    "references/task_playbooks/TTS.md",
    "references/task_playbooks/VC.md",
    "references/task_playbooks/KWS.md",
]
ALL_ENV_PLAYBOOKS = [
    "references/playbooks/env_uv.md",
    "references/playbooks/env_pip.md",
    "references/playbooks/env_conda.md",
    "references/playbooks/env_pixi.md",
    "references/playbooks/env_docker.md",
    "references/playbooks/model_api.md",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify_model_name(value: str) -> str:
    slug = re.sub(r"/+", "__", value.strip())
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", slug)
    slug = re.sub(r"^[._-]+|[._-]+$", "", slug)
    return slug or "model"


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def read_model_input(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError(f"PyYAML is required to parse MODEL_INPUT: {YAML_IMPORT_ERROR}")
    if not path.exists():
        raise FileNotFoundError(f"MODEL_INPUT path does not exist: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001
        data = parse_scalar_fallback(text)
        data["_parse_warning"] = (
            "MODEL_INPUT was not strict YAML; parsed only core scalar fields. "
            f"Original parser error: {exc}"
        )
        data["_parsed_with"] = "scalar_fallback"
        return data
    if not isinstance(data, dict):
        raise ValueError("MODEL_INPUT YAML must parse to an object.")
    return data


def clean_scalar(raw: str) -> Any:
    value = raw.strip()
    if not value or value in {"null", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return re.sub(r"\s+#.*$", "", value)


def set_nested(out: dict[str, Any], dotted_key: str, value: Any) -> None:
    keys = dotted_key.split(".")
    current = out
    for key in keys[:-1]:
        nested = current.get(key)
        if not isinstance(nested, dict):
            nested = {}
            current[key] = nested
        current = nested
    current[keys[-1]] = value


def parse_scalar_fallback(text: str) -> dict[str, Any]:
    """Best-effort parser for legacy handoffs with unquoted multiline code.

    It intentionally extracts only scalar keys needed by LOAD_MODEL_INPUT and
    CONTEXT_SELECTION. The full artifact remains a warning-bearing partial
    object so the agent can later repair MODEL_INPUT formatting if needed.
    """

    values: dict[str, Any] = {}
    stack: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("- "):
            continue
        match = re.match(r"^(\s*)([A-Za-z0-9_-]+):(?:\s*(.*))?$", raw_line)
        if not match:
            continue
        indent = len(match.group(1))
        key = match.group(2)
        raw_value = match.group(3) or ""
        while stack and stack[-1][0] >= indent:
            stack.pop()
        dotted = ".".join([entry[1] for entry in stack] + [key])
        if raw_value.strip():
            set_nested(values, dotted, clean_scalar(raw_value))
        else:
            stack.append((indent, key))
    return values


def get_nested(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_model_dir_isolation(model_dir: Path, repo_root: Path) -> dict[str, Any]:
    """Ensure the model entity directory is harness-owned.

    Whole-directory symlinks to the original SURE workspace are unsafe because
    build_env may create .venv or runtime caches and mutate the reference model.
    Asset-level symlinks are still allowed later under checkpoints/ or .runtime/.
    """

    allowed_root = (repo_root / "sure" / "models").resolve()
    model_dir_abs = model_dir.expanduser()
    if not model_dir_abs.is_absolute():
        model_dir_abs = repo_root / model_dir_abs

    if model_dir_abs.exists() and model_dir_abs.is_symlink():
        raise ValueError(
            "model_dir must be a harness-owned directory, not a whole-directory symlink: "
            f"{model_dir_abs} -> {model_dir_abs.resolve()}. "
            "Replace this with a real sure/models/<model_name>/ directory and symlink only "
            "large immutable assets such as checkpoints/ or .runtime/*."
        )

    resolved_model_dir = model_dir_abs.resolve(strict=False)
    if not is_relative_to(resolved_model_dir, allowed_root):
        raise ValueError(
            "model_dir must stay under the harness repo-level sure/models directory. "
            f"got model_dir={model_dir_abs}, resolved={resolved_model_dir}, allowed_root={allowed_root}. "
            "Use sure/models/<model_name>/ for generated wrappers and only reference the original "
            "SURE-EVAL model through evidence fields or asset-level symlinks."
        )

    return {
        "model_dir_is_harness_owned": True,
        "model_dir_symlink_allowed": False,
        "allowed_model_root": str(allowed_root),
        "asset_symlink_policy": (
            "allowed only for immutable checkpoint files below checkpoints/ or selected reusable cache/weight "
            "directories below .runtime/; never symlink model_dir, .venv, tmp, or writable scratch directories"
        ),
    }


def normalize_required_string(value: Any, field: str) -> str:
    if value is None:
        raise ValueError(f"MODEL_INPUT missing required field: {field}")
    text = str(value).strip()
    if not text:
        raise ValueError(f"MODEL_INPUT field {field} is empty.")
    return text


def task_playbooks_for(task_type: str) -> list[str]:
    task = task_type.lower()
    if task == "asr":
        return ["references/task_playbooks/ASR.md"]
    if task in {"sa-asr", "sa_asr"}:
        return [
            "references/task_playbooks/SPEECH_UNDERSTANDING.md",
            "references/task_playbooks/ASR.md",
        ]
    if task in {"s2tt", "sd", "ser", "slu", "gr", "speech_understanding"}:
        return ["references/task_playbooks/SPEECH_UNDERSTANDING.md"]
    if task == "tts":
        return ["references/task_playbooks/TTS.md"]
    if task == "vc":
        return ["references/task_playbooks/VC.md"]
    if task == "kws":
        return ["references/task_playbooks/KWS.md"]
    return []


def env_playbooks_for(deployment_type: str, preferred_backend: str | None) -> list[str]:
    if deployment_type == "api":
        return ["references/playbooks/model_api.md"]
    backend = (preferred_backend or "").strip().lower()
    mapping = {
        "uv": "references/playbooks/env_uv.md",
        "pip": "references/playbooks/env_pip.md",
        "conda": "references/playbooks/env_conda.md",
        "pixi": "references/playbooks/env_pixi.md",
        "docker": "references/playbooks/env_docker.md",
        "api": "references/playbooks/model_api.md",
    }
    selected = [mapping[backend]] if backend in mapping else []
    if "references/playbooks/env_docker.md" not in selected:
        selected.append("references/playbooks/env_docker.md")
    return selected


def handoff_dir_for(model_input_path: Path) -> Path | None:
    parent = model_input_path.parent
    return parent if parent.name and parent.parent.name == "handoffs" else None


def make_model_input_resolved(
    model_input: dict[str, Any],
    *,
    model_input_path: Path,
    repo_root: Path,
    package_profile: str,
    weights_link_policy: str,
    device: str,
    force_repair: bool,
    skip_download: bool,
    max_retries: int,
    cpu_fallback_after_cuda_failures: int,
    cuda_repair_attempts_before_cpu: int,
    raw_args: str,
    existing_model_dir: str | None,
    image_version: str | None,
) -> dict[str, Any]:
    model_id = normalize_required_string(model_input.get("model_id"), "model_id")
    model_name = str(model_input.get("model_name") or slugify_model_name(model_id)).strip()
    model_name = slugify_model_name(model_name)
    task_type = normalize_required_string(model_input.get("task_type"), "task_type")
    deployment_type = normalize_required_string(model_input.get("deployment_type"), "deployment_type")
    repo_url = normalize_required_string(get_nested(model_input, "repo", "url"), "repo.url")

    if task_type not in TASK_TYPES:
        raise ValueError(f"Unsupported task_type={task_type!r}.")
    if deployment_type not in DEPLOYMENT_TYPES:
        raise ValueError(f"Unsupported deployment_type={deployment_type!r}.")
    if package_profile not in PACKAGE_PROFILES:
        raise ValueError(f"Unsupported package_profile={package_profile!r}.")
    if weights_link_policy not in WEIGHTS_LINK_POLICIES:
        raise ValueError(f"Unsupported weights_link_policy={weights_link_policy!r}.")
    if device not in DEVICES:
        raise ValueError(f"Unsupported device={device!r}.")
    if max_retries <= 0:
        raise ValueError("max_retries must be a positive integer.")
    if cpu_fallback_after_cuda_failures <= 0:
        raise ValueError("cpu_fallback_after_cuda_failures must be a positive integer.")
    if cuda_repair_attempts_before_cpu <= 0:
        raise ValueError("cuda_repair_attempts_before_cpu must be a positive integer.")

    model_dir = Path(existing_model_dir).expanduser() if existing_model_dir else repo_root / "sure" / "models" / model_name
    path_policy = validate_model_dir_isolation(model_dir, repo_root)
    handoff_dir = handoff_dir_for(model_input_path)
    cuda_first = deployment_type == "local" and device in {"auto", "cuda"}

    payload = {
        "timestamp": now_iso(),
        "model_input_path": str(model_input_path),
        "model_id": model_id,
        "model_name": model_name,
        "model_dir": str(model_dir),
        "repo_url": repo_url,
        "repo_commit": get_nested(model_input, "repo", "commit"),
        "task_type": task_type,
        "deployment_type": deployment_type,
        "package_profile": package_profile,
        "weights_link_policy": weights_link_policy,
        "force_repair": force_repair,
        "existing_model_dir": str(model_dir) if existing_model_dir else None,
        "skip_download": skip_download,
        "device": device,
        "max_retries": max_retries,
        "cpu_fallback_after_cuda_failures": cpu_fallback_after_cuda_failures,
        "cuda_repair_attempts_before_cpu": cuda_repair_attempts_before_cpu,
        "device_policy": {
            "requested_device": device,
            "cuda_first": cuda_first,
            "cpu_fallback_after_cuda_failures": cpu_fallback_after_cuda_failures,
            "cuda_repair_attempts_before_cpu": cuda_repair_attempts_before_cpu,
            "cpu_fallback_allowed": device == "auto",
        },
        "path_policy": path_policy,
        "source": {
            "handoff_dir": str(handoff_dir) if handoff_dir else None,
            "handoff_artifacts_dir": str(handoff_dir / "artifacts") if handoff_dir else None,
            "raw_args": raw_args,
        },
        "normalized_model_input": model_input,
    }
    if package_profile == "docker-registry":
        site = load_site_policy(repository_root=repo_root, required=True) or {}
        policy = site.get("policy")
        if not isinstance(policy, dict):
            raise ValueError("site policy did not resolve to an object")
        repository = resolve_container_repository(
            policy, task_type=task_type, model_name=model_name
        )
        version, version_resolution = resolve_image_version([repository], image_version)
        payload["container_delivery"] = {
            "repository": repository,
            "image_version": version,
            "target_image": resolve_container_image(
                policy,
                task_type=task_type,
                model_name=model_name,
                version=version,
            ),
            "image_version_resolution": version_resolution,
            "site_policy_path": site.get("path"),
            "site_policy_sha256": site.get("sha256"),
        }
    return payload


def make_context_selection(resolved: dict[str, Any], model_input: dict[str, Any]) -> dict[str, Any]:
    task_type = str(resolved["task_type"])
    preferred_backend = get_nested(model_input, "environment_hint", "preferred_backend")
    deployment_type = str(resolved["deployment_type"])
    task_playbooks = task_playbooks_for(task_type)
    env_playbooks = env_playbooks_for(deployment_type, str(preferred_backend) if preferred_backend else None)

    selected = {
        "default": [
            "references/AGENTS.md",
            "references/memory/COMMON.md",
            "references/task_playbooks/ROUTING.md",
            "references/playbooks/env_ROUTING.md",
        ],
        "task_playbooks": task_playbooks,
        "environment_playbooks": env_playbooks,
        "contracts": [
            "references/contracts/spec_validation.md",
            "references/contracts/minimal_validation.md",
            "references/specs/wrapper_contract.md",
            "references/contracts/fixture_policy.md",
            "references/contracts/model_local_checkpoint_rule.md",
            "references/templates/validate_metric_enrichment.md",
        ],
        "memory": ["references/memory/COMMON.md"],
        "policies": [
            "references/policies/constitution.md",
            "references/policies/evidence_priority.md",
            "references/policies/backend_selection.md",
            "references/policies/retry_and_escalation.md",
            "references/policies/phase1_target_policy.md",
        ],
    }
    skipped = sorted((set(ALL_TASK_PLAYBOOKS) | set(ALL_ENV_PLAYBOOKS)) - set(task_playbooks) - set(env_playbooks))
    backend_note = (
        f"preferred backend is {preferred_backend!r}"
        if preferred_backend
        else "backend is not fixed yet; keep environment routing narrow until PLAN"
    )
    return {
        "timestamp": now_iso(),
        "model_id": resolved["model_id"],
        "model_name": resolved["model_name"],
        "task_type": task_type,
        "preferred_backend": str(preferred_backend) if preferred_backend else "",
        "selected_references": selected,
        "skipped_references": skipped,
        "rationale": (
            f"Selected from MODEL_INPUT.task_type={task_type!r}, "
            f"deployment_type={deployment_type!r}, and {backend_note}."
        ),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-input-path", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--package-profile", choices=sorted(PACKAGE_PROFILES))
    parser.add_argument("--weights-link-policy", default="auto", choices=sorted(WEIGHTS_LINK_POLICIES))
    parser.add_argument("--device", default="auto", choices=sorted(DEVICES))
    parser.add_argument("--force-repair", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--cpu-fallback-after-cuda-failures", type=int, default=3)
    parser.add_argument("--cuda-repair-attempts-before-cpu", type=int, default=3)
    parser.add_argument("--existing-model-dir")
    parser.add_argument("--image-version")
    parser.add_argument("--raw-args", default="")
    args = parser.parse_args()

    model_input_path = Path(args.model_input_path).expanduser().resolve()
    run_dir = Path(args.run_dir).expanduser().resolve()
    repo_root = Path(args.repo_root).expanduser().resolve()
    artifacts_dir = run_dir / "artifacts"

    try:
        model_input = read_model_input(model_input_path)
        deployment_type = normalize_required_string(model_input.get("deployment_type"), "deployment_type")
        package_profile = args.package_profile or ("none" if deployment_type == "api" else "docker-registry")
        resolved = make_model_input_resolved(
            model_input,
            model_input_path=model_input_path,
            repo_root=repo_root,
            package_profile=package_profile,
            weights_link_policy=args.weights_link_policy,
            device=args.device,
            force_repair=bool(args.force_repair),
            skip_download=bool(args.skip_download),
            max_retries=args.max_retries,
            cpu_fallback_after_cuda_failures=args.cpu_fallback_after_cuda_failures,
            cuda_repair_attempts_before_cpu=args.cuda_repair_attempts_before_cpu,
            raw_args=args.raw_args,
            existing_model_dir=args.existing_model_dir,
            image_version=args.image_version,
        )
        context_selection = make_context_selection(resolved, model_input)
    except Exception as exc:  # noqa: BLE001
        print(f"materialize_onboard_inputs failed: {exc}", file=sys.stderr)
        return 1

    # LOAD_MODEL_INPUT should not claim the concrete model directory. Later
    # units may create it, or replace it with a symlink to a proven local model.
    if not args.existing_model_dir:
        Path(str(resolved["model_dir"])).parent.mkdir(parents=True, exist_ok=True)
    write_json(artifacts_dir / "model_input_resolved.json", resolved)
    write_json(artifacts_dir / "context_selection.json", context_selection)
    print(
        "materialize_onboard_inputs OK: "
        f"{artifacts_dir / 'model_input_resolved.json'} "
        f"{artifacts_dir / 'context_selection.json'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
