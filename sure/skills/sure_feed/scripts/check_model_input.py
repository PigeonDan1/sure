#!/usr/bin/env python3
"""Gate script for the SYNTHESIZE_MODEL_INPUT unit.

Verifies every synthesized candidate carries a complete MODEL_INPUT object for
/sure_onboard. This is intentionally stricter than the shallow JSON schema: the
schema keeps the artifact shape stable, while this gate checks onboarding
semantics and evidence provenance.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


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
WEIGHT_SOURCES = {
    "huggingface",
    "modelscope",
    "local",
    "api",
    "pip",
    "release_or_pypi",
}
EVIDENCE_SOURCES = {"modelscope", "huggingface", "github", "manual", "local"}
FIXTURE_SOURCES = {"task_registry", "model_specific"}
REQUIRED_MODEL_INPUT_EVIDENCE_FIELDS = {
    "repo.url",
    "weights.source",
    "environment_hint.preferred_backend",
    "environment_hint.python_version",
    "environment_hint.requires_gpu",
    "phase1_runtime_target",
    "entrypoints.import_test",
    "entrypoints.load_test",
    "entrypoints.infer_test",
    "fixture",
    "io_contract",
}


def is_obj(value: Any) -> bool:
    return isinstance(value, dict)


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def dotted_get(root: dict[str, Any], path: str) -> Any:
    current: Any = root
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def require_nonempty(root: dict[str, Any], path: str, errors: list[str]) -> None:
    value = dotted_get(root, path)
    if not nonempty_string(value):
        errors.append(f"{path} must be a non-empty string")


def require_bool(root: dict[str, Any], path: str, errors: list[str]) -> None:
    value = dotted_get(root, path)
    if not isinstance(value, bool):
        errors.append(f"{path} must be boolean")


def require_string_list(root: dict[str, Any], path: str, errors: list[str]) -> None:
    value = dotted_get(root, path)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        errors.append(f"{path} must be a list of strings")


def is_policy_resolved(value: Any) -> bool:
    return isinstance(value, str) and value.strip().startswith("policy_resolved:")


def validate_runtime_strategy(model_input: dict[str, Any], prefix: str, errors: list[str]) -> None:
    entrypoints = model_input.get("entrypoints")
    if not isinstance(entrypoints, dict):
        return
    policy_fields = [
        field
        for field in ("import_test", "load_test", "infer_test")
        if is_policy_resolved(entrypoints.get(field))
    ]
    strategy = model_input.get("runtime_strategy")
    if not policy_fields:
        return
    if not is_obj(strategy):
        errors.append(
            f"{prefix}.model_input.runtime_strategy is required when entrypoints use "
            f"policy_resolved values ({', '.join(policy_fields)})"
        )
        return
    for field in ("type", "framework", "load_surface", "inference_surface"):
        value = strategy.get(field)
        if not nonempty_string(value):
            errors.append(f"{prefix}.model_input.runtime_strategy.{field} must be a non-empty string")
    strategy_type = strategy.get("type")
    if strategy_type not in {"python_library", "cli_or_library", "api", "server"}:
        errors.append(
            f"{prefix}.model_input.runtime_strategy.type must be one of "
            "['python_library', 'cli_or_library', 'api', 'server']"
        )


def validate_model_input(model_input: Any, envelope_model_id: str, prefix: str) -> list[str]:
    errors: list[str] = []
    if not is_obj(model_input):
        return [f"{prefix}.model_input must be an object"]

    required_top = [
        "model_id",
        "model_name",
        "task_type",
        "deployment_type",
        "repo",
        "weights",
        "environment_hint",
        "phase1_runtime_target",
        "entrypoints",
        "fixture",
        "io_contract",
    ]
    for field in required_top:
        if field not in model_input:
            errors.append(f"{prefix}.model_input missing required field {field}")

    require_nonempty(model_input, "model_id", errors)
    require_nonempty(model_input, "model_name", errors)
    if nonempty_string(envelope_model_id) and model_input.get("model_id") != envelope_model_id:
        errors.append(
            f"{prefix}.model_id must match model_input.model_id "
            f"({envelope_model_id!r} != {model_input.get('model_id')!r})"
        )

    task_type = model_input.get("task_type")
    if task_type not in TASK_TYPES:
        errors.append(
            f"{prefix}.model_input.task_type must be one of {sorted(TASK_TYPES)} "
            f"(got {task_type!r})"
        )

    deployment_type = model_input.get("deployment_type")
    if deployment_type not in DEPLOYMENT_TYPES:
        errors.append(
            f"{prefix}.model_input.deployment_type must be one of "
            f"{sorted(DEPLOYMENT_TYPES)} (got {deployment_type!r})"
        )

    if not is_obj(model_input.get("repo")):
        errors.append(f"{prefix}.model_input.repo must be an object")
    else:
        require_nonempty(model_input, "repo.url", errors)
        if "commit" not in model_input["repo"]:
            errors.append(f"{prefix}.model_input.repo.commit must be present (string or null)")
        commit = dotted_get(model_input, "repo.commit")
        if commit is not None and not isinstance(commit, str):
            errors.append(f"{prefix}.model_input.repo.commit must be string or null")

    if not is_obj(model_input.get("weights")):
        errors.append(f"{prefix}.model_input.weights must be an object")
    else:
        source = dotted_get(model_input, "weights.source")
        if source not in WEIGHT_SOURCES:
            errors.append(
                f"{prefix}.model_input.weights.source must be one of "
                f"{sorted(WEIGHT_SOURCES)} (got {source!r}); github is a repo/evidence "
                "source, not a default weights source"
        )
        require_bool(model_input, "weights.required", errors)
        if "local_path" not in model_input["weights"]:
            errors.append(f"{prefix}.model_input.weights.local_path must be present (string or null)")
        require_nonempty(model_input, "weights.cache_policy", errors)
        require_nonempty(model_input, "weights.local_dir_name", errors)
        local_path = dotted_get(model_input, "weights.local_path")
        if local_path is not None and not isinstance(local_path, str):
            errors.append(f"{prefix}.model_input.weights.local_path must be string or null")

    if not is_obj(model_input.get("environment_hint")):
        errors.append(f"{prefix}.model_input.environment_hint must be an object")
    else:
        require_nonempty(model_input, "environment_hint.preferred_backend", errors)
        require_nonempty(model_input, "environment_hint.python_version", errors)
        require_bool(model_input, "environment_hint.requires_gpu", errors)
        require_string_list(model_input, "environment_hint.system_packages", errors)

    runtime_target = model_input.get("phase1_runtime_target")
    if not (
        nonempty_string(runtime_target)
        or (isinstance(runtime_target, list) and all(nonempty_string(item) for item in runtime_target))
    ):
        errors.append(
            f"{prefix}.model_input.phase1_runtime_target must be a non-empty string "
            "or a list of non-empty strings"
        )

    if not is_obj(model_input.get("entrypoints")):
        errors.append(f"{prefix}.model_input.entrypoints must be an object")
    else:
        require_nonempty(model_input, "entrypoints.import_test", errors)
        require_nonempty(model_input, "entrypoints.load_test", errors)
        require_nonempty(model_input, "entrypoints.infer_test", errors)
        validate_runtime_strategy(model_input, prefix, errors)

    if not is_obj(model_input.get("fixture")):
        errors.append(f"{prefix}.model_input.fixture must be an object")
    else:
        require_bool(model_input, "fixture.task_specific", errors)
        require_bool(model_input, "fixture.fallback_allowed", errors)
        fixture = model_input["fixture"]
        fixture_source = fixture.get("fixture_source")
        if fixture_source not in FIXTURE_SOURCES:
            errors.append(
                f"{prefix}.model_input.fixture.fixture_source must be one of "
                f"{sorted(FIXTURE_SOURCES)} (got {fixture_source!r}); "
                "read fixtures/tasks/<task>/README.md and select a task fixture instead of using an ad hoc path"
            )
        if fixture_source == "task_registry":
            require_nonempty(model_input, "fixture.fixture_id", errors)
            require_nonempty(model_input, "fixture.fixture_index", errors)
            require_nonempty(model_input, "fixture.fixture_root", errors)
            fixture_index = dotted_get(model_input, "fixture.fixture_index")
            fixture_root = dotted_get(model_input, "fixture.fixture_root")
            if nonempty_string(fixture_index) and not str(fixture_index).startswith("fixtures/tasks/"):
                errors.append(f"{prefix}.model_input.fixture.fixture_index must be under fixtures/tasks/")
            if nonempty_string(fixture_root) and not str(fixture_root).startswith("fixtures/tasks/"):
                errors.append(f"{prefix}.model_input.fixture.fixture_root must be under fixtures/tasks/")
        if not any(nonempty_string(fixture.get(field)) for field in ("audio", "text", "reference_audio")):
            errors.append(
                f"{prefix}.model_input.fixture must include at least one concrete "
                "fixture pointer: audio, text, or reference_audio"
            )

    if not is_obj(model_input.get("io_contract")):
        errors.append(f"{prefix}.model_input.io_contract must be an object")
    else:
        require_nonempty(model_input, "io_contract.input_type", errors)
        require_nonempty(model_input, "io_contract.output_type", errors)
        require_nonempty(model_input, "io_contract.primary_field", errors)
        require_string_list(model_input, "io_contract.required_fields", errors)
        require_string_list(model_input, "io_contract.nonempty_fields", errors)
        require_bool(model_input, "io_contract.json_serializable", errors)
        primary = dotted_get(model_input, "io_contract.primary_field")
        required = as_list(dotted_get(model_input, "io_contract.required_fields"))
        nonempty = as_list(dotted_get(model_input, "io_contract.nonempty_fields"))
        if nonempty_string(primary) and primary not in required:
            errors.append(f"{prefix}.model_input.io_contract.primary_field must be in required_fields")
        if nonempty_string(primary) and primary not in nonempty:
            errors.append(f"{prefix}.model_input.io_contract.primary_field must be in nonempty_fields")

    return errors


def validate_evidence(item: dict[str, Any], prefix: str) -> list[str]:
    errors: list[str] = []
    evidence = item.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return [f"{prefix}.evidence must be a non-empty list"]
    for idx, entry in enumerate(evidence):
        eprefix = f"{prefix}.evidence[{idx}]"
        if not is_obj(entry):
            errors.append(f"{eprefix} must be an object")
            continue
        source = entry.get("source")
        if source not in EVIDENCE_SOURCES:
            errors.append(f"{eprefix}.source must be one of {sorted(EVIDENCE_SOURCES)} (got {source!r})")
        if not nonempty_string(entry.get("field")):
            errors.append(f"{eprefix}.field must be a non-empty string")
        if "value" not in entry:
            errors.append(f"{eprefix}.value must be present")
    return errors


def validate_field_evidence(item: dict[str, Any], prefix: str) -> list[str]:
    evidence = item.get("evidence")
    if not isinstance(evidence, list):
        return []
    covered: set[str] = set()
    for entry in evidence:
        if not isinstance(entry, dict):
            continue
        model_input_field = entry.get("model_input_field")
        if isinstance(model_input_field, str) and model_input_field:
            covered.add(model_input_field)
    missing = sorted(REQUIRED_MODEL_INPUT_EVIDENCE_FIELDS - covered)
    if missing:
        return [
            f"{prefix}.evidence is missing retrieval evidence for MODEL_INPUT field(s): {', '.join(missing)}. "
            "Do not hand off template/default values; fetch the model card/README/API metadata and cite it per field."
        ]
    model_input = item.get("model_input")
    if isinstance(model_input, dict):
        fixture = model_input.get("fixture")
        if isinstance(fixture, dict) and fixture.get("fixture_source") == "task_registry":
            has_registry_fixture_evidence = any(
                isinstance(entry, dict)
                and entry.get("source") == "local"
                and str(entry.get("field") or "").startswith("fixture_registry.")
                and entry.get("model_input_field") == "fixture"
                for entry in evidence
            )
            if not has_registry_fixture_evidence:
                return [
                    f"{prefix}.evidence must cite the local fixture registry for MODEL_INPUT.fixture "
                    "(source=local, field=fixture_registry.*, model_input_field=fixture)"
                ]
    return []


def validate_model_input_path(item: dict[str, Any], run_dir: Path, prefix: str) -> list[str]:
    path_value = item.get("model_input_path")
    if path_value is None:
        return []
    if not nonempty_string(path_value):
        return [f"{prefix}.model_input_path must be a non-empty string when present"]
    path = Path(path_value)
    if not path.is_absolute():
        path = run_dir / path
    if not path.exists():
        return [f"{prefix}.model_input_path points to a missing file: {path}"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    path = Path(args.produces)
    if not path.exists():
        print(f"model_input_result.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"model_input_result.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    model_inputs = data.get("model_inputs") if isinstance(data, dict) else None
    if not isinstance(model_inputs, list) or not model_inputs:
        print("SYNTHESIZE_MODEL_INPUT gate: model_inputs array is empty.", file=sys.stderr)
        return 1

    errors: list[str] = []
    for idx, item in enumerate(model_inputs):
        prefix = f"model_inputs[{idx}]"
        if not is_obj(item):
            errors.append(f"{prefix} must be an object")
            continue
        model_id = item.get("model_id")
        if not nonempty_string(model_id):
            errors.append(f"{prefix}.model_id must be a non-empty string")
            model_id = ""
        errors.extend(validate_model_input(item.get("model_input"), str(model_id), prefix))
        errors.extend(validate_evidence(item, prefix))
        errors.extend(validate_field_evidence(item, prefix))
        errors.extend(validate_model_input_path(item, run_dir, prefix))
        for field in as_list(item.get("missing_or_weak_fields")):
            if isinstance(field, str) and field.lower().startswith(("missing:", "weak:")):
                errors.append(
                    f"{prefix}.missing_or_weak_fields contains fatal unresolved field marker {field!r}; "
                    "complete MODEL_INPUT from retrieved provider evidence before handoff"
                )

    if errors:
        print("SYNTHESIZE_MODEL_INPUT gate failed:\n  - " + "\n  - ".join(errors), file=sys.stderr)
        return 1
    print(f"check_model_input OK: {len(model_inputs)} MODEL_INPUT object(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
