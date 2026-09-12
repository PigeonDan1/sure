#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath

import yaml

from vc_exec import default_partition


LEGACY_PATH = re.compile(r"/(?:mnt/cloudstorfs|hpc_stor\d+|hpc_\d+)/")
ANNOTATION_FIELDS = (
    "ground_truth",
    "target_text",
    "text",
    "segments",
    "label",
    "intent",
    "speaker_id",
)
TRANS_RESERVED_ROOTS = {
    "model.py",
    "server.py",
    "__init__.py",
    "validate.py",
    "config.yaml",
    "model.spec.yaml",
    "Dockerfile.sure",
    "Dockerfile",
    "artifacts",
    "fixture",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"artifact must be a JSON object: {path}")
    return value


def artifact_time(value: dict, label: str) -> datetime:
    raw = value.get("generated_at") or value.get("timestamp")
    require(isinstance(raw, str) and bool(raw.strip()), f"{label} must record generated_at or timestamp")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} timestamp is invalid: {raw}") from error


def has_annotation_value(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return value is not None


def validate_sv_fixture_manifest(value: dict) -> None:
    for key in (
        "model_dir",
        "staged_dir",
        "staged_path",
        "gt_jsonl",
        "samples",
        "trial_manifest",
        "trials_file",
        "provenance",
        "annotation_source",
    ):
        require(key in value, f"SV fixture manifest is missing {key}")
    model_dir = Path(str(value["model_dir"])).resolve()
    staged_dir = Path(str(value["staged_dir"])).resolve()
    gt_jsonl = Path(str(value["gt_jsonl"])).resolve()
    staged = Path(str(value["staged_path"])).resolve()
    require(model_dir.is_dir(), "SV fixture model_dir is missing")
    require(staged_dir.is_dir(), "SV fixture staged_dir is missing")
    require(
        staged_dir.is_relative_to(model_dir / "fixture"),
        "SV fixture staged_dir must stay under model_dir/fixture",
    )
    require(gt_jsonl.is_file() and gt_jsonl.parent == staged_dir, "SV gt_jsonl is missing")
    require(value.get("gt_sha256") == sha256_file(gt_jsonl), "SV gt_jsonl checksum changed")

    rows: list[dict] = []
    for line_no, line in enumerate(gt_jsonl.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"SV fixture gt_jsonl line {line_no} is invalid JSON: {error}") from error
        require(isinstance(row, dict), f"SV fixture gt_jsonl line {line_no} must be an object")
        require(str(row.get("task") or row.get("task_type") or "").upper() == "SV", f"SV fixture row {line_no} must declare task SV")
        require(bool(str(row.get("speaker_id") or "").strip()), f"SV fixture row {line_no} must declare speaker_id")
        audio = Path(str(row.get("audio") or ""))
        require(not audio.is_absolute() and ".." not in audio.parts, f"SV fixture row {line_no} audio path is invalid")
        require((staged_dir / audio).is_file(), f"SV fixture row {line_no} audio is missing")
        rows.append(row)

    samples = value.get("samples")
    require(isinstance(samples, list) and 1 <= len(samples) <= 5, "SV fixture must declare 1-5 samples")
    require(value.get("sample_count") == len(samples) == len(rows), "SV fixture sample count must match gt_jsonl")
    for index, (sample, row) in enumerate(zip(samples, rows), 1):
        require(isinstance(sample, dict), f"SV fixture sample {index} must be an object")
        audio = str(row["audio"])
        require(sample.get("audio") == audio, f"SV fixture sample {index} audio does not match gt_jsonl")
        require(
            Path(str(sample.get("audio_path") or "")).resolve() == (staged_dir / audio).resolve(),
            f"SV fixture sample {index} audio_path does not match gt_jsonl",
        )
        require(sample.get("speaker_id") == row.get("speaker_id"), f"SV fixture sample {index} speaker_id changed")
        require(sample.get("annotation_fields") == ["speaker_id"], f"SV fixture sample {index} annotation_fields are invalid")

    first_audio = (staged_dir / str(rows[0]["audio"])).resolve()
    require(staged == first_audio and staged.is_file(), "SV staged_path must select the first fixture audio")
    require(value.get("sha256") == sha256_file(staged), "SV staged audio checksum changed")
    require(value.get("size_bytes") == staged.stat().st_size, "SV staged audio size changed")

    trial_manifest_path = Path(str(value["trial_manifest"])).resolve()
    trials_path = Path(str(value["trials_file"])).resolve()
    provenance_path = Path(str(value["provenance"])).resolve()
    for path, label, hash_key in (
        (trial_manifest_path, "trial_manifest", "trial_manifest_sha256"),
        (trials_path, "trials_file", "trials_sha256"),
        (provenance_path, "provenance", "provenance_sha256"),
    ):
        require(path.is_file() and path.parent == staged_dir, f"SV {label} must be inside staged_dir")
        require(value.get(hash_key) == sha256_file(path), f"SV {label} checksum changed")

    trial_manifest = read_object(trial_manifest_path)
    require(trial_manifest.get("schema_version") == "sure.sv.trial_manifest.v1", "SV trial manifest schema is invalid")
    require(trial_manifest.get("trials_file") == trials_path.name, "SV trial manifest points to another trials file")
    require(trial_manifest.get("trials_sha256") == sha256_file(trials_path), "SV trial manifest trials checksum changed")
    for index, row in enumerate(rows, 1):
        require(row.get("trial_manifest") == trial_manifest_path.name, f"SV fixture row {index} trial_manifest changed")
        require(samples[index - 1].get("trial_manifest") == trial_manifest_path.name, f"SV fixture sample {index} trial_manifest changed")

    trial_lines = [line.split() for line in trials_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    require(trial_lines and trial_lines[0] == ["enroll_key", "test_key", "label", "condition"], "SV trials header is invalid")
    trials = trial_lines[1:]
    require(trial_manifest.get("trial_count") == len(trials), "SV trial_count does not match trials file")
    sample_keys = {str(sample.get("key") or "") for sample in samples}
    require(all(len(trial) == 4 for trial in trials), "SV trial rows must have four columns")
    require(all(trial[0] in sample_keys and trial[1] in sample_keys for trial in trials), "SV trials reference unknown sample keys")
    labels = [trial[2] for trial in trials]
    require(set(labels).issubset({"target", "nontarget"}), "SV trials contain an invalid label")
    require(trial_manifest.get("target_count") == labels.count("target"), "SV target_count is invalid")
    require(trial_manifest.get("nontarget_count") == labels.count("nontarget"), "SV nontarget_count is invalid")

    annotation_source = value.get("annotation_source")
    require(isinstance(annotation_source, dict), "SV annotation_source must be an object")
    require(
        annotation_source.get("type") == "task_registry_fixture"
        and annotation_source.get("fallback") is False,
        "SV ground truth must come from a task-registry fixture",
    )
    require(
        Path(str(annotation_source.get("staged_path") or "")).resolve() == gt_jsonl,
        "SV annotation source must point to the staged gt_jsonl",
    )


def validate_fixture_manifest(value: dict) -> None:
    require(value.get("status") == "ready", "fixture manifest is not ready")
    task = str(value.get("task_type") or "").replace("-", "_").lower()
    if task == "sv":
        validate_sv_fixture_manifest(value)
        return
    for key in ("model_dir", "staged_dir", "gt_jsonl", "samples", "annotation_source"):
        require(key in value, f"fixture manifest is missing {key}")
    model_dir = Path(str(value["model_dir"])).resolve()
    staged_dir = Path(str(value["staged_dir"])).resolve()
    staged = Path(str(value.get("staged_path", ""))).resolve()
    gt_jsonl = Path(str(value["gt_jsonl"])).resolve()
    require(model_dir.is_dir(), "fixture model_dir is missing")
    require(staged_dir.is_dir(), "fixture staged_dir is missing")
    require(staged_dir.is_relative_to(model_dir / "fixture"), "fixture staged_dir must stay under model_dir/fixture")
    require(staged.is_file(), "staged fixture is missing")
    require(staged.parent == staged_dir, "staged fixture must be directly inside staged_dir")
    require(value.get("sha256") == sha256_file(staged), "staged fixture checksum changed")
    require(gt_jsonl.is_file() and gt_jsonl.parent == staged_dir, "gt_jsonl must exist directly inside staged_dir")
    require(value.get("gt_sha256") == sha256_file(gt_jsonl), "fixture ground-truth checksum changed")

    samples = value.get("samples")
    require(isinstance(samples, list) and len(samples) == 1, "trans smoke fixture must declare one sample")
    sample = samples[0]
    require(isinstance(sample, dict), "fixture sample must be an object")
    require(sample.get("audio") == staged.name, "fixture sample must mirror staged_path")
    require(Path(str(sample.get("audio_path") or "")).resolve() == staged, "fixture sample audio_path must match staged_path")
    require(int(value.get("sample_count", 0)) == 1, "trans smoke fixture must contain exactly one bounded sample")

    rows = [line for line in gt_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    require(len(rows) == 1, "trans smoke fixture gt_jsonl must contain exactly one non-empty row")
    try:
        row = json.loads(rows[0])
    except json.JSONDecodeError as error:
        raise ValueError(f"fixture gt_jsonl is invalid JSON: {error}") from error
    require(isinstance(row, dict), "fixture gt_jsonl row must be an object")
    audio_field = "reference_audio" if task in {"tts", "vc"} else "audio"
    require(row.get(audio_field) == staged.name, f"fixture gt_jsonl {audio_field} must mirror staged_path")
    declared_annotations = sample.get("annotation_fields")
    actual_annotations = [
        field for field in ANNOTATION_FIELDS if field in row and has_annotation_value(row[field])
    ]
    require(actual_annotations, "fixture gt_jsonl must contain a non-empty reference annotation")
    require(declared_annotations == actual_annotations, "fixture sample annotation_fields must mirror gt_jsonl")
    if task == "tts":
        require(
            isinstance(row.get("prompt_text"), str) and bool(row["prompt_text"].strip()),
            "TTS fixture gt_jsonl requires non-empty prompt_text",
        )

    annotation_source = value.get("annotation_source")
    require(isinstance(annotation_source, dict), "fixture annotation_source must be an object")
    require(
        annotation_source.get("type") == "fixture_expected_sidecar"
        and annotation_source.get("fallback") is False,
        "fixture ground truth must come from a reference .expected.json sidecar",
    )
    expected_path = Path(str(annotation_source.get("staged_path") or "")).resolve()
    require(expected_path.is_file() and expected_path.parent == staged_dir, "staged fixture annotation sidecar is missing")
    require(value.get("expected_sha256") == sha256_file(expected_path), "fixture annotation sidecar checksum changed")
    expected = read_object(expected_path)
    for field in actual_annotations:
        require(row.get(field) == expected.get(field), f"fixture gt_jsonl {field} disagrees with reference sidecar")
    if task == "tts":
        require(row.get("prompt_text") == expected.get("prompt_text"), "fixture prompt_text disagrees with reference sidecar")


def infer_repo_root(run_dir: Path) -> Path:
    resolved = run_dir.expanduser().resolve()
    if resolved.parent.name == "runs" and resolved.parent.parent.name == ".sure":
        return resolved.parent.parent.parent
    return Path.cwd().resolve()


def harness_model_dir(run_dir: Path) -> Path:
    resolved = read_object(Path(run_dir) / "artifacts" / "trans_input_resolved.json")
    model_name = str(resolved.get("model_name") or "")
    if not model_name or "/" in model_name or "\\" in model_name:
        raise ValueError("model_name must be a single directory segment")
    path_policy = resolved.get("path_policy") if isinstance(resolved.get("path_policy"), dict) else {}
    raw_root = path_policy.get("allowed_model_root")
    if raw_root:
        allowed_root = Path(str(raw_root)).expanduser().resolve()
    else:
        allowed_root = (infer_repo_root(Path(run_dir)) / "sure" / "models").resolve()
    return (allowed_root / model_name).resolve()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    parser.add_argument("--kind", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    path = Path(args.produces)
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), "artifact must be a JSON object")
    kind = args.kind
    if kind == "input":
        source_kind = value.get("source_kind")
        require(source_kind in {"docker", "python"}, "source_kind must be docker or python")
        preferred_backend = value.get("preferred_backend")
        backend_hint = value.get("backend_hint")
        require(preferred_backend in {None, "uv", "conda", "docker"}, "preferred_backend is invalid")
        require(backend_hint in {"uv", "conda", "docker"}, "backend_hint is invalid")
        if preferred_backend is not None:
            require(backend_hint == preferred_backend, "backend_hint must honor preferred_backend")
        package_profile = value.get("package_profile")
        require(package_profile in {"docker-registry", "none"}, "package_profile is invalid")
        for key in ("build_context", "model_path", "inference_entrypoint"):
            candidate = Path(str(value.get(key, "")))
            require(candidate.is_absolute() and candidate.exists(), f"{key} must exist and be absolute")
        if source_kind == "docker":
            require(package_profile == "docker-registry", "Docker input requires package_profile=docker-registry")
            require(backend_hint == "docker", "Docker input requires backend_hint=docker")
            source_paths = ("dockerfile",)
        else:
            require(backend_hint in {"uv", "conda"}, "Python input requires uv or conda backend_hint")
            require(package_profile != "none" or backend_hint == "uv", "package=none requires uv")
            source_paths = ("dependency_file",)
        for key in source_paths:
            candidate = Path(str(value.get(key, "")))
            require(candidate.is_absolute() and candidate.is_file(), f"{key} must be an existing absolute file")
        if source_kind == "python" and value.get("python_executable") is not None:
            python_executable = Path(str(value["python_executable"]))
            require(
                python_executable.is_absolute() and python_executable.is_file(),
                "python_executable must be null or an existing absolute file",
            )
        require(value.get("framework") == "pytorch", "framework must normalize to pytorch")
        require(
            isinstance(value.get("model_framework"), str) and bool(value["model_framework"].strip()),
            "model_framework is required",
        )
        expected_model_dir = harness_model_dir(run_dir)
        declared_model_dir = Path(str(value.get("model_dir") or "")).expanduser()
        try:
            declared_model_dir = declared_model_dir.resolve()
        except OSError:
            declared_model_dir = declared_model_dir.absolute()
        if declared_model_dir.exists() and declared_model_dir.is_symlink():
            raise ValueError("model_dir must be a real harness-owned directory, not a whole-directory symlink")
        require(
            declared_model_dir == expected_model_dir,
            f"model_dir must be the harness-owned bundle {expected_model_dir}; got {declared_model_dir}",
        )
    elif kind == "backend_choice":
        resolved = read_object(run_dir / "artifacts" / "trans_input_resolved.json")
        backend = value.get("backend")
        require(backend in {"uv", "conda", "docker"}, "backend choice is invalid")
        require(value.get("package_profile") == resolved.get("package_profile"), "backend package_profile changed")
        preferred = resolved.get("preferred_backend")
        if preferred is not None:
            require(backend == preferred, "backend choice must honor preferred_backend")
        require(backend == resolved.get("backend_hint"), "backend choice disagrees with resolved backend evidence")
        require(
            isinstance(value.get("choice_reason"), str) and bool(value["choice_reason"].strip()),
            "backend choice requires a reason",
        )
        evidence = value.get("evidence")
        require(
            isinstance(evidence, list) and bool(evidence) and all(isinstance(item, str) and item.strip() for item in evidence),
            "backend choice requires non-empty evidence strings",
        )
    elif kind == "dependencies":
        require(value.get("status") == "ready", "dependency inspection is blocked")
        require(value.get("unresolved") == [], "dependency report contains unresolved paths")
        require(value.get("external_paths") == [], "dependency report contains undeclared external paths")
    elif kind == "framework":
        resolved = read_object(run_dir / "artifacts" / "trans_input_resolved.json")
        require(value.get("status") == "ready", "primary computation framework must be PyTorch")
        require(
            value.get("declared_framework") == resolved.get("framework") == "pytorch",
            "declared computation framework must match the resolved PyTorch input",
        )
        require(value.get("detected_framework") == "pytorch", "static inspection must detect PyTorch")
        require(value.get("framework_requirement_met") is True, "PyTorch framework requirement was not met")
        declared_model_framework = value.get("declared_model_framework")
        detected_model_framework = value.get("detected_model_framework")
        require(
            isinstance(declared_model_framework, str) and bool(declared_model_framework.strip()),
            "declared_model_framework is required",
        )
        require(
            declared_model_framework == resolved.get("model_framework"),
            "declared_model_framework must match the resolved input",
        )
        require(
            detected_model_framework in {"transformers", "custom"},
            "ready framework detection must identify the model framework category",
        )
        needs_clarification = (
            declared_model_framework != "transformers"
            or detected_model_framework != "transformers"
            or value.get("model_framework_matches") is not True
        )
        require(
            value.get("clarification_required") is needs_clarification,
            "clarification_required does not match the framework evidence",
        )
        clarification = value.get("architecture_clarification")
        if needs_clarification:
            require(
                isinstance(clarification, str) and bool(clarification.strip()),
                "non-Transformers or mismatched model frameworks require architecture clarification",
            )
        else:
            require(clarification is None, "matching Transformers models must not carry a stale clarification")
    elif kind == "fixture":
        validate_fixture_manifest(value)
    elif kind == "source_image":
        require(value.get("status") == "passed", "source image materialization did not pass")
        require(Path(str(value.get("source_image_log_path", ""))).is_file(), "source image log is missing")
        if value.get("source_kind") == "python":
            resolved = read_object(run_dir / "artifacts" / "trans_input_resolved.json")
            expected_backend = str(resolved.get("backend_hint") or "")
            choice_path = run_dir / "artifacts" / "backend_choice.json"
            if choice_path.is_file():
                expected_backend = str(read_object(choice_path).get("backend") or expected_backend)
            require(value.get("backend") == expected_backend, "source runtime backend changed after planning")
            require(value.get("backend") in {"uv", "conda"}, "Python source runtime backend must be uv or conda")
            require(value.get("runtime_mode") in {"existing-python", "materialize"}, "invalid source runtime mode")
            for key in ("python_executable", "dependency_file", "lockfile"):
                candidate = Path(str(value.get(key) or "")).resolve()
                require(candidate.is_file(), f"source runtime {key} is missing")
                require(value.get(f"{key}_sha256") == sha256_file(candidate), f"source runtime {key} hash changed")
            if value.get("runtime_mode") == "materialize":
                environment_dir = Path(str(value.get("environment_dir") or "")).resolve()
                require(
                    environment_dir.is_dir() and environment_dir.is_relative_to(run_dir.resolve()),
                    "materialized source environment must stay inside the run directory",
                )
            commands = value.get("materialization_commands")
            require(isinstance(commands, list), "source runtime materialization_commands must be a list")
            require(
                all(isinstance(command, dict) and command.get("exit_code") == 0 for command in commands),
                "source runtime materialization recorded a failed command",
            )
        else:
            require(value.get("source_image_policy") in {"load", "build"}, "source image policy must be load or build")
            require(value.get("source_image_policy") == value.get("requested_source_image_policy", value.get("source_image_policy")) or value.get("requested_source_image_policy") == "auto", "source image policy violates requested policy")
            require(value.get("image_id", "").startswith("sha256:"), "source image image_id must be a live sha256 ID")
            if value.get("source_image_policy") == "build":
                require(value.get("build_executed") is True, "source image build was not executed")
                require(value.get("build_exit_code") == 0, "docker build did not exit successfully")
                require(isinstance(value.get("build_command"), list) and value["build_command"][0:2] == ["docker", "build"], "source image must record docker build command")
                require(Path(str(value.get("build_log_path", ""))).is_file(), "source image build log is missing")
            else:
                require(value.get("load_executed") is True, "source image load was not executed")
                require(value.get("load_exit_code") == 0, "docker load did not exit successfully")
                require(isinstance(value.get("load_command"), list) and value["load_command"][0:2] == ["docker", "load"], "source image must record docker load command")
                image_tar = Path(str(value.get("image_tar", ""))).resolve()
                build_context = Path(str(value.get("build_context", ""))).resolve()
                require(image_tar.is_file() and image_tar.is_relative_to(build_context), "loaded image tar must be inside build context")
                require(value.get("tar_sha256") == sha256_file(image_tar), "loaded image tar checksum changed")
                require(value.get("load_verified") is True, "loaded image was not verified")
    elif kind == "adapter_image":
        require(value.get("status") == "passed", "adapter runtime materialization must pass")
        if value.get("runtime_kind") == "python":
            source_runtime = read_object(run_dir / "artifacts" / "source_image_result.json")
            for key in ("python_executable", "lockfile"):
                path = Path(str(value.get(key) or "")).resolve()
                require(path == Path(str(source_runtime.get(key) or "")).resolve(), f"adapter runtime {key} changed")
                require(path.is_file(), f"adapter runtime {key} is missing")
                require(value.get(f"{key}_sha256") == sha256_file(path), f"adapter runtime {key} hash changed")
            manifest = read_object(run_dir / "artifacts" / "adapter_manifest.json")
            require(value.get("server_command") == manifest.get("server_command"), "adapter server_command changed")
            require(value.get("working_dir") == manifest.get("working_dir"), "adapter working_dir changed")
            files = value.get("files")
            require(isinstance(files, dict), "Python adapter runtime must record file hashes")
            for key, digest in files.items():
                path = Path(str(manifest.get(key) or ""))
                require(path.is_file() and digest == sha256_file(path), f"adapter runtime file hash changed: {key}")
        else:
            manifest = read_object(run_dir / "artifacts" / "adapter_manifest.json")
            resolved = read_object(run_dir / "artifacts" / "trans_input_resolved.json")
            target_image = str(value.get("target_image") or "")
            delivery = resolved.get("container_delivery") if isinstance(resolved.get("container_delivery"), dict) else {}
            require(target_image == delivery.get("target_image"), "adapter target_image must match the resolved delivery target")
            require(str(value.get("image_id") or "").startswith("sha256:"), "adapter image_id must be a live sha256 ID")
            source_backend = str(manifest.get("source_backend") or "docker")
            if source_backend == "docker":
                source_image = str(value.get("source_image") or "")
                require(source_image == manifest.get("source_image_reference"), "adapter source_image must match the verified source image")
            else:
                require(source_backend in {"uv", "conda"}, "container adapter source_backend must be uv, conda, or docker")
                base_image = str(value.get("base_image") or "")
                require(base_image, "uv/conda adapter image must record its base_image")
                dockerfile = Path(str(manifest.get("dockerfile") or ""))
                require(dockerfile.is_file(), "adapter Dockerfile is missing")
                from_images = [
                    line.split()[1]
                    for line in dockerfile.read_text(encoding="utf-8").splitlines()
                    if line.strip().upper().startswith("FROM ") and len(line.split()) >= 2
                ]
                require(base_image in from_images, "adapter base_image must match a Dockerfile FROM image")
    elif kind == "registry":
        require(value.get("status") == "passed", "registry package must pass")
        if value.get("package_profile") == "none":
            require(value.get("schema") == "sure.trans.python_package_result.v1", "invalid Python package result schema")
            require(value.get("runtime_kind") == "python" and value.get("backend") == "uv", "Python package must use uv")
            manifest_path = run_dir / "artifacts" / "model_runtime_manifest.json"
            require(manifest_path.is_file(), "Python package is missing model_runtime_manifest.json")
            manifest = read_object(manifest_path)
            runtime = value.get("model_runtime") if isinstance(value.get("model_runtime"), dict) else {}
            require(runtime.get("runtime_id") == manifest.get("runtime_id"), "Python package runtime ID changed")
            require(value.get("lock_sha256") == manifest.get("lock_sha256"), "Python package lock hash changed")
            return 0
        require(value.get("pull_verified") is True, "registry package must prove exact digest pull verification")
        require("@sha256:" in str(value.get("target_image_ref", "")), "registry target_image_ref must be digest-pinned")
        require(str(value.get("target_image_digest", "")).startswith("sha256:"), "registry target_image_digest must be a sha256 digest")
        compat_path = Path(run_dir) / "artifacts" / "execution_compat.json"
        selected_device = "cpu"
        execution_surface = "vc"
        if compat_path.is_file():
            compat = read_object(compat_path)
            selected_device = str(compat.get("selected_device") or "cpu")
            execution_surface = str(compat.get("execution_surface") or "vc")
        if selected_device == "cuda":
            smoke = value.get("post_pull_smoke")
            require(
                isinstance(smoke, dict),
                "GPU-validated models must repeat the MCP smoke test after the exact digest pull and record post_pull_smoke evidence",
            )
            if execution_surface == "vc":
                require(smoke.get("vc_job_id"), "post_pull_smoke must record the vc job id")
                expected_partition = default_partition()
                require(
                    smoke.get("vc_partition") == expected_partition,
                    f"post-pull MCP smoke must run on the site's dedicated partition {expected_partition}",
                )
            else:
                require(
                    execution_surface == "local_docker"
                    and smoke.get("execution_surface") == "local_docker",
                    "local GPU post-pull MCP smoke must record execution_surface=local_docker",
                )
                require(not smoke.get("vc_job_id"), "local GPU post-pull MCP smoke cannot claim a VC job")
                require(
                    smoke.get("image_ref") == value.get("target_image_ref"),
                    "local GPU post-pull MCP smoke must run the exact digest-pinned target_image_ref",
                )
            # vc submit takes repo:tag only and answers 镜像不存在 to any
            # repo@sha256:... reference, so the job cannot carry the pin in the
            # reference it runs. Requiring that made this unit unsatisfiable on
            # GPU. The submission proves the pin instead: vc_exec.py resolves
            # what the tag serves and refuses to submit on a mismatch.
            require(
                str(smoke.get("resolved_digest", "")) == str(value.get("target_image_digest", "")),
                "post_pull_smoke.resolved_digest must equal target_image_digest",
            )
            require(smoke.get("exit_code") == 0, "post-pull MCP smoke must exit 0")
            smoke_log = Path(str(smoke.get("log_path") or "")).expanduser()
            require(
                smoke_log.exists(),
                f"post_pull_smoke log path is missing: {smoke_log}",
            )
            evidence = smoke_log / "mcp_smoke.json" if smoke_log.is_dir() else smoke_log.parent / "mcp_smoke.json"
            require(
                evidence.is_file(),
                "post_pull_smoke must record mcp_smoke.json protocol evidence (initialize/tools/list/tools/call)",
            )
            protocol = read_object(evidence)
            require(protocol.get("status") == "passed", "post-pull MCP smoke evidence must pass")
            for step in ("initialize", "tools_list", "tools_call"):
                entry = protocol.get(step)
                require(
                    isinstance(entry, dict) and entry.get("ok") is True,
                    f"post-pull MCP smoke must prove {step} passed",
                )
            require(
                bool((protocol.get("tools_call") or {}).get("output_nonempty"))
                or bool((protocol.get("tools_call") or {}).get("text_nonempty")),
                "post-pull MCP smoke must return a non-empty primary output from tools/call",
            )
    elif kind == "model_payload":
        require(value.get("status") == "ready", "model payload was not staged")
        require(Path(str(value.get("destination", ""))).is_dir(), "staged model directory is missing")
        require(int(value.get("file_count", 0)) > 0, "staged model payload is empty")
        expected_model_dir = harness_model_dir(run_dir)
        declared_destination = Path(str(value.get("destination", ""))).expanduser().resolve()
        require(
            declared_destination == expected_model_dir,
            f"model payload must land in the harness-owned bundle {expected_model_dir}; got {declared_destination}",
        )
        files = value.get("files")
        require(isinstance(files, dict) and files, "model payload manifest must list every staged file")
        require(len(files) == int(value.get("file_count", 0)), "model payload file_count must match files")
        verified_hashes: dict[str, str] = {}
        total_bytes = 0
        for raw_path, entry in files.items():
            relative = Path(str(raw_path))
            require(
                str(raw_path) and not relative.is_absolute() and ".." not in relative.parts,
                f"model payload path must be portable: {raw_path}",
            )
            require(isinstance(entry, dict), f"model payload entry must be an object: {raw_path}")
            target = (declared_destination / relative).resolve()
            require(
                target.is_relative_to(declared_destination) and target.is_file() and not target.is_symlink(),
                f"staged model payload file is missing or unsafe: {raw_path}",
            )
            size = target.stat().st_size
            digest = sha256_file(target)
            require(entry.get("size_bytes") == size, f"model payload size changed: {raw_path}")
            require(entry.get("sha256") == digest, f"model payload checksum changed: {raw_path}")
            verified_hashes[relative.as_posix()] = digest
            total_bytes += size
        require(value.get("total_bytes") == total_bytes, "model payload total_bytes must match files")
        identity = hashlib.sha256(
            json.dumps(verified_hashes, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        require(value.get("payload_identity_sha256") == identity, "model payload identity does not match files")
        actual_payload: set[str] = set()
        for target in declared_destination.rglob("*"):
            relative = target.relative_to(declared_destination)
            if relative.parts[0] in TRANS_RESERVED_ROOTS:
                continue
            require(not target.is_symlink(), f"model payload must not contain symlinks: {relative}")
            if target.is_file():
                actual_payload.add(relative.as_posix())
        require(actual_payload == set(verified_hashes), "model payload manifest must exactly cover staged payload files")
    elif kind == "adapter" and value.get("runtime_kind") == "python":
        require(value.get("status") == "ready", "adapter manifest must be ready")
        source_runtime = read_object(run_dir / "artifacts" / "source_image_result.json")
        python_executable = Path(str(value.get("python_executable") or "")).resolve()
        require(
            python_executable == Path(str(source_runtime.get("python_executable") or "")).resolve()
            and python_executable.is_file(),
            "adapter python_executable must match the resolved Python runtime",
        )
        server_command = value.get("server_command")
        server_py = Path(str(value.get("server_py") or "")).resolve()
        require(
            isinstance(server_command, list)
            and len(server_command) == 2
            and Path(str(server_command[0])).resolve() == python_executable
            and Path(str(server_command[1])).resolve() == server_py,
            "Python adapter server_command must use python_executable and server.py",
        )
        require(Path(str(value.get("working_dir") or "")).resolve().is_dir(), "adapter working_dir is missing")
        for key in ("model_py", "init_py", "validate_py", "server_py", "config_yaml", "model_spec", "mcp_smoke_py"):
            require(Path(str(value.get(key) or "")).is_file(), f"adapter file missing: {key}")
        config = yaml.safe_load(Path(str(value["config_yaml"])).read_text(encoding="utf-8"))
        require(
            isinstance(config, dict)
            and isinstance(config.get("server"), dict)
            and config["server"].get("command") == server_command,
            "adapter config server.command must match adapter manifest",
        )
        model_source = Path(str(value["model_py"])).read_text(encoding="utf-8")
        require("NotImplementedError" not in model_source and "TODO" not in model_source, "model.py is still a scaffold")
    elif kind == "adapter":
        require(value.get("status") == "ready", "adapter manifest must be ready")
        require(value.get("harness_runtime_embedded") is True, "adapter image must embed the common Harness Runtime")
        harness = value.get("harness_runtime") if isinstance(value.get("harness_runtime"), dict) else {}
        require(
            all(harness.get(key) for key in ("runtime_id", "lock_sha256", "python_executable", "manifest_path", "runtime_root")),
            "adapter manifest must declare the embedded Harness Runtime binding",
        )
        python_executable = str(value.get("container_python_executable") or "")
        require(
            PurePosixPath(python_executable).is_absolute(),
            "adapter manifest container_python_executable must be absolute",
        )
        server_command = value.get("server_command")
        require(
            isinstance(server_command, list)
            and len(server_command) >= 2
            and server_command[0] == python_executable
            and all(isinstance(item, str) and item for item in server_command),
            "adapter manifest server_command must start with container_python_executable",
        )
        require(
            PurePosixPath(server_command[1]).is_absolute(),
            "adapter manifest server path must be absolute",
        )
        require(
            PurePosixPath(str(value.get("working_dir") or "")).is_absolute(),
            "adapter manifest working_dir must be absolute",
        )
        source_reference = str(value.get("source_image_reference") or "")
        source_image = read_object(run_dir / "artifacts" / "source_image_result.json")
        source_backend = str(value.get("source_backend") or "docker")
        if source_backend == "docker":
            require(source_reference, "Docker-source adapter manifest requires source_image_reference")
            require(
                value.get("source_image_id") == source_image.get("image_id"),
                "adapter manifest source_image_id must match source image evidence",
            )
            source_local = str(source_image.get("image") or "")
            source_push = source_image.get("registry_push") if isinstance(source_image.get("registry_push"), dict) else {}
            source_registry = str(source_image.get("registry_ref") or "")
            source_digest = str(source_push.get("digest") or "")
            if source_registry and source_digest:
                repository = source_registry.rsplit(":", 1)[0]
                require(
                    source_reference == f"{repository}@{source_digest}",
                    "adapter source_image_reference must pin the source registry digest",
                )
            else:
                require(source_reference == source_local, "adapter source_image_reference must match the verified local source image")
        else:
            require(source_backend in {"uv", "conda"}, "container adapter source_backend must be uv, conda, or docker")
            require(not source_reference, "uv/conda source runtime must not be represented as a source image")
            require(value.get("source_image_id") is None, "uv/conda source runtime must not declare source_image_id")
            require(source_image.get("backend") == source_backend, "adapter source backend changed")
            resolved = read_object(run_dir / "artifacts" / "trans_input_resolved.json")
            require(
                Path(str(value.get("model_source_build_context") or "")).resolve()
                == Path(str(resolved.get("build_context") or "")).resolve(),
                "adapter model_source_build_context must match the resolved build context",
            )
            source_runtime_file = Path(str(value.get("source_runtime_file") or "")).resolve()
            require(source_runtime_file.is_file(), "adapter source runtime file is missing")
            require(
                sha256_file(source_runtime_file) == source_image.get("lockfile_sha256"),
                "adapter source runtime file must copy the validated source runtime lock",
            )
        for key in ("model_py", "init_py", "validate_py", "server_py", "config_yaml", "model_spec", "dockerfile", "mcp_smoke_py"):
            candidate = Path(str(value.get(key, "")))
            require(candidate.is_file(), f"adapter file missing: {key}")
        dockerfile = Path(str(value.get("dockerfile", "")))
        require(dockerfile.is_file(), "adapter Dockerfile is missing")
        dockerfile_text = dockerfile.read_text(encoding="utf-8")
        if source_backend == "docker":
            require(
                dockerfile_text.splitlines()[0] == f"FROM {source_reference}",
                "adapter Dockerfile base image must match source_image_reference",
            )
        else:
            require("SURE_TRANS_TODO" not in dockerfile_text, "adapter Dockerfile is still a draft")
            require("COPY --from=model_source" in dockerfile_text, "uv/conda adapter Dockerfile must copy the model source build context")
            require(Path(str(value["source_runtime_file"])).name in dockerfile_text, "adapter Dockerfile must copy its source runtime file")
            require(re.search(rf"\b{re.escape(source_backend)}\b", dockerfile_text, re.IGNORECASE) is not None, f"adapter Dockerfile must materialize the {source_backend} runtime")
        require(
            f"ENTRYPOINT {json.dumps(server_command)}" in dockerfile_text,
            "adapter Dockerfile ENTRYPOINT must match server_command",
        )
        config = yaml.safe_load(Path(str(value["config_yaml"])).read_text(encoding="utf-8"))
        require(
            isinstance(config, dict)
            and isinstance(config.get("server"), dict)
            and config["server"].get("command") == server_command,
            "adapter config server.command must match adapter manifest",
        )
        require(
            "COPY --from=sure_harness_runtime" in dockerfile_text,
            "adapter Dockerfile must copy the locked Harness Runtime with the sure_harness_runtime build context",
        )
        build_context = str(value.get("harness_runtime_build_context") or "directory")
        if build_context.startswith("docker-image://"):
            require(
                re.fullmatch(r"docker-image://.+@sha256:[0-9a-f]{64}", build_context) is not None,
                "image-backed Harness Runtime build context must be digest-pinned",
            )
        for key in ("model_py", "init_py", "server_py", "config_yaml", "model_spec", "validate_py", "mcp_smoke_py"):
            declared = Path(str(value.get(key, "")))
            require(
                declared.name in dockerfile_text,
                f"adapter Dockerfile must COPY {declared.name} into the image; the manifest declares {key} but "
                "the Dockerfile does not reference it. Fix the COPY line (templates/Dockerfile.sure), rebuild the "
                "adapter image, and re-run the import gate",
            )
        model_source = Path(str(value["model_py"])).read_text(encoding="utf-8")
        require("NotImplementedError" not in model_source and "TODO" not in model_source, "model.py is still a scaffold")
    elif kind == "runtime_inventory":
        require(value.get("schema") == "sure.onboard.runtime_inventory.v2", "runtime inventory schema is incompatible with sure_eval")
        require(value.get("status") == "ready", "runtime inventory is not ready")
        container = value.get("container_runtime") or {}
        model_runtime = value.get("model_runtime") or {}
        policy = value.get("policy") or {}
        if policy.get("eval_runtime") == "python":
            require(container.get("required") is False, "Python Eval runtime must not require a container")
            require(model_runtime.get("required") is True, "Python Eval runtime must declare Model Python")
            require(model_runtime.get("backend") == "uv", "Python Eval runtime must use uv")
            require(model_runtime.get("runtime_id"), "Python Eval runtime must declare runtime_id")
            require(model_runtime.get("manifest_path") == "artifacts/model_runtime_manifest.json", "Python runtime manifest path is invalid")
            require(policy.get("host_python_fallback") is False, "host Python fallback must be disabled")
            return 0
        require("@sha256:" in str(container.get("target_image_ref", "")), "runtime image must be digest-pinned")
        require(policy.get("eval_runtime") == "container_only", "Eval runtime must be container_only")
        require(policy.get("host_python_fallback") is False, "host Python fallback must be disabled")
        require(policy.get("nfs_models_mutable_by_eval") is False, "Eval must not mutate the approved model bundle")
        model_python = str(model_runtime.get("python_executable") or "")
        container_python = str(container.get("python_executable") or "")
        require(
            PurePosixPath(model_python).is_absolute(),
            "model runtime Python executable must be absolute",
        )
        require(
            PurePosixPath(container_python).is_absolute(),
            "container runtime Python executable must be absolute",
        )
        require(
            model_python == container_python,
            "model and container runtime Python executables must match",
        )
        require(
            PurePosixPath(str(container.get("working_dir") or "")).is_absolute(),
            "container working directory must be absolute",
        )
        server_command = container.get("server_command")
        require(
            isinstance(server_command, list)
            and len(server_command) >= 2
            and server_command[0] == container_python
            and all(isinstance(item, str) and item for item in server_command),
            "container server_command must start with its Python executable",
        )
        require(
            PurePosixPath(server_command[1]).is_absolute(),
            "container server path must be absolute",
        )
        harness = value.get("harness_runtime") if isinstance(value.get("harness_runtime"), dict) else {}
        require(harness.get("required") is True, "trans adapter image must embed the Harness Runtime")
        require(harness.get("schema") == "sure.harness.runtime.binding.v1", "required Harness Runtime binding must use the common schema")
        require(
            all(harness.get(key) for key in ("runtime_id", "lock_sha256", "python_executable", "manifest_path", "runtime_root")),
            "required Harness Runtime binding is missing identity or path fields",
        )
        require(
            not LEGACY_PATH.search(json.dumps(harness, ensure_ascii=False)),
            "host Harness Runtime paths cannot be declared as the container runtime",
        )
        mount_policy = container.get("mount_policy") or {}
        require((mount_policy.get("model_bundle") or {}).get("read_only") is True, "model bundle mount must be read-only")
        require((mount_policy.get("result_workspace") or {}).get("read_only") is False, "result workspace mount must be writable")
    elif kind == "verdict":
        require(value.get("status") == "success", "verdict is not terminal-success")
        readiness = value.get("readiness")
        profile = (value.get("package") or {}).get("profile") if isinstance(value.get("package"), dict) else None
        require(
            isinstance(readiness, dict)
            and readiness.get("bundle_ready") is True
            and (profile == "none" or readiness.get("registry_ready") is True),
            "verdict readiness must prove bundle readiness and registry readiness when packaged as a container",
        )
    elif kind == "deployment_ready":
        profile = str(value.get("package_profile") or "docker-registry")
        expected_schema = "sure.onboard.deployment_ready.v2" if profile == "none" else "sure.onboard.deployment_ready.v1"
        require(value.get("schema") == expected_schema, "deployment schema is incompatible with sure_eval")
        if value.get("status") == "blocked":
            require(
                str(value.get("blocked_reason") or "").strip() != "",
                "blocked deployment marker must record why the run stopped",
            )
            blocked_policy = value.get("execution_policy") if isinstance(value.get("execution_policy"), dict) else {}
            require(
                blocked_policy.get("container_only") is False,
                "blocked deployment marker must not claim container-only Eval readiness",
            )
            print(f"{kind} OK: {path}")
            return 0
        require(value.get("status") == "ready", "deployment is not ready")
        require(
            value.get("integrity_profile") == "manifest-complete-v1",
            "ready deployment must use the manifest-complete-v1 integrity profile",
        )
        if profile == "docker-registry":
            require("@sha256:" in str(value.get("target_image_ref", "")), "deployment image must be digest-pinned")
        model_dir = harness_model_dir(run_dir)
        model_copy = model_dir / "artifacts" / "deployment_ready.json"
        require(
            model_copy.is_file() and model_copy.read_bytes() == path.read_bytes(),
            "deployment_ready.json must be written identically to the run and model bundle",
        )
        policy = value.get("execution_policy") if isinstance(value.get("execution_policy"), dict) else {}
        if profile == "none":
            require(
                policy.get("container_only") is False
                and policy.get("eval_runtime") == "python"
                and policy.get("isolation") == "trusted_host"
                and policy.get("model_integrity") == "verify_before_after"
                and policy.get("model_bundle_mutation_allowed") is False
                and policy.get("host_python_fallback") is False,
                "final Python execution policy is invalid",
            )
        else:
            require(
                policy.get("container_only") is True
                and policy.get("nfs_models_read_only") is True
                and policy.get("host_python_fallback") is False
                and policy.get("approved_image_override") is False,
                "final execution policy must be container-only with NFS read-only and no host fallback",
            )
        hashes = value.get("required_artifact_sha256")
        require(isinstance(hashes, dict) and hashes, "required_artifact_sha256 must list finalized artifacts")
        for raw, expected in hashes.items():
            relative = Path(str(raw))
            require(not relative.is_absolute() and ".." not in relative.parts, f"invalid finalized artifact path: {raw}")
            artifact = (model_dir / relative).resolve()
            require(
                artifact.is_relative_to(model_dir) and artifact.is_file() and sha256_file(artifact) == expected,
                f"finalized artifact hash mismatch: {raw}",
            )
        bundle_hash = hashlib.sha256(
            json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        require(value.get("bundle_identity_sha256") == bundle_hash, "bundle_identity_sha256 does not match finalized artifact hashes")
        manifest = read_object(model_dir / "artifacts" / "artifact_manifest.json")
        require(
            manifest.get("status") == "finalized" and manifest.get("model_dir") == ".",
            "artifact_manifest.json must be refreshed into portable finalized form",
        )
        manifest_artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
        required_entries = manifest_artifacts.get("required") if isinstance(manifest_artifacts.get("required"), dict) else {}
        declared_paths = {
            str(entry.get("path"))
            for entry in required_entries.values()
            if isinstance(entry, dict) and entry.get("path") != "artifacts/deployment_ready.json"
        }
        require(
            declared_paths == set(hashes),
            "deployment_ready hashes must cover exactly every artifact_manifest required file except deployment_ready.json",
        )
        validate_fixture_manifest(read_object(model_dir / "artifacts" / "fixture_manifest.json"))
        package = read_object(model_dir / "artifacts" / "package_gate.json")
        require(package.get("status") == "passed", "package_gate must be passed")
        gate_readiness = package.get("readiness") if isinstance(package.get("readiness"), dict) else {}
        require(
            gate_readiness.get("bundle_ready") is True
            and (profile == "none" or gate_readiness.get("registry_ready") is True),
            "package_gate readiness must prove bundle readiness and registry readiness when required",
        )
        if profile == "docker-registry":
            docker = package.get("docker") if isinstance(package.get("docker"), dict) else {}
            dockerfile = model_dir / str(docker.get("dockerfile_path") or "Dockerfile.sure")
            require(
                dockerfile.is_file() and docker.get("dockerfile_sha256") == sha256_file(dockerfile),
                "package gate Dockerfile hash does not match the model bundle",
            )
        else:
            runtime_manifest = model_dir / "artifacts" / "model_runtime_manifest.json"
            require(runtime_manifest.is_file(), "Python bundle is missing model_runtime_manifest.json")
            model_runtime = value.get("model_runtime") if isinstance(value.get("model_runtime"), dict) else {}
            require(model_runtime.get("runtime_id") == read_object(runtime_manifest).get("runtime_id"), "deployment Model Runtime ID changed")
        inventory = read_object(model_dir / "artifacts" / "runtime_inventory.json")
        verdict = read_object(model_dir / "artifacts" / "verdict.json")
        timeline = [
            ("artifact_manifest", artifact_time(manifest, "artifact_manifest")),
            ("package_gate", artifact_time(package, "package_gate")),
            ("runtime_inventory", artifact_time(inventory, "runtime_inventory")),
            ("verdict", artifact_time(verdict, "verdict")),
            ("deployment_ready", artifact_time(value, "deployment_ready")),
        ]
        for (earlier_name, earlier), (later_name, later) in zip(timeline, timeline[1:]):
            require(earlier < later, f"terminal timeline is inverted: {earlier_name} must precede {later_name}")
        declared_binding = value.get("harness_runtime") if profile == "docker-registry" and isinstance(value.get("harness_runtime"), dict) else {}
        if declared_binding:
            source_binding = inventory.get("harness_runtime") if isinstance(inventory.get("harness_runtime"), dict) else {}
            projected = {key: source_binding.get(key) for key in declared_binding}
            require(declared_binding == projected, "deployment Harness Runtime binding disagrees with runtime inventory")
            require(
                declared_binding.get("schema") == "sure.harness.runtime.binding.v1" and declared_binding.get("runtime_id"),
                "ready deployment must expose the common Harness Runtime binding",
            )
            require(
                not LEGACY_PATH.search(json.dumps(declared_binding, ensure_ascii=False)),
                "deployment Harness Runtime binding must reference an in-image runtime, not host paths",
            )
        portable = [
            read_object(model_dir / "artifacts" / name)
            for name in ("runtime_inventory.json", "package_gate.json", "artifact_manifest.json", "deployment_ready.json")
        ]
        require(
            not LEGACY_PATH.search(json.dumps(portable, ensure_ascii=False)),
            "finalized deployment sidecars contain legacy host absolute paths",
        )
    print(f"{kind} OK: {path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error))
        raise SystemExit(1)
