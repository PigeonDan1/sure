#!/usr/bin/env python3
"""Write protocol.yaml for an inference run.

Lifted out of evaluate_predictions.py so the inference entrypoint can record
the inference protocol without importing the evaluator. The payload describes
the inference side only (model, runtime, parameters, constraints); the
``results`` rows are consulted just for the evaluation-engine provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sure_eval.core.logging import get_logger

from execution_provenance import load_execution_provenance_from_environment
from model_identity import canonical_model_name
from server_protocol import resolve_server_protocol

logger = get_logger(__name__)
SKILL_ROOT = Path(__file__).resolve().parent.parent
HARNESS_ROOT = Path(__file__).resolve().parents[4]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_run_id(run_dir: Path) -> str:
    return os.environ.get("RUN_ID") or run_dir.name


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _to_strict_jsonable(value: Any) -> Any:
    """Convert Python objects into strict-JSON-safe values."""
    if isinstance(value, dict):
        return {key: _to_strict_jsonable(subvalue) for key, subvalue in value.items()}
    if isinstance(value, list):
        return [_to_strict_jsonable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_model_sidecar(model_dir: Path | None, relative: str) -> dict[str, Any]:
    if model_dir is None:
        return {}
    return _read_json_file(model_dir / relative)


def _load_run_sidecar(run_dir: Path, name: str) -> dict[str, Any]:
    return _read_json_file(run_dir / name)


def _existing_path_or_none(path: Path) -> str | None:
    return str(path) if path.exists() else None


def _nested_dict(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return {}
        value = value.get(key)
    return value if isinstance(value, dict) else {}


def _nonempty_dict(*values: dict[str, Any]) -> dict[str, Any]:
    for value in values:
        if value:
            return value
    return {}



def _first_text(*values: Any) -> str | None:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return None


def _load_execution_provenance(results_dir: Path) -> dict[str, Any]:
    return _nonempty_dict(
        _load_run_sidecar(results_dir, "execution_provenance.json"),
        _load_run_sidecar(results_dir, "artifacts/execution_provenance.json"),
        load_execution_provenance_from_environment(),
    )


def _identity_provenance(
    *,
    results_dir: Path,
    generation_status: dict[str, Any],
    evaluation_runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    execution_provenance = _load_execution_provenance(results_dir)
    status_provenance = _nonempty_dict(
        _safe_dict(generation_status.get("execution_provenance")),
        _nested_dict(generation_status, "runtime", "execution_provenance"),
        _safe_dict(generation_status.get("provenance")),
    )
    unavailable = _safe_dict(execution_provenance.get("unavailable"))
    status_unavailable = _safe_dict(status_provenance.get("unavailable"))
    evaluation_runtime = evaluation_runtime or {}

    harness_commit = _first_text(
        execution_provenance.get("harness_commit"),
        status_provenance.get("harness_commit"),
        os.environ.get("SURE_HARNESS_COMMIT"),
    )
    evaluation_engine_commit = _first_text(
        execution_provenance.get("evaluation_engine_commit"),
        status_provenance.get("evaluation_engine_commit"),
        evaluation_runtime.get("engine_commit"),
        os.environ.get("SURE_EVALUATION_ENGINE_COMMIT"),
    )

    return {
        "execution_provenance": execution_provenance,
        "harness_commit": harness_commit,
        "harness_commit_unavailable_reason": (
            None
            if harness_commit
            else unavailable.get("harness_commit")
            or status_unavailable.get("harness_commit")
            or "host execution provenance did not provide harness_commit"
        ),
        "evaluation_engine_commit": evaluation_engine_commit,
        "evaluation_engine_commit_unavailable_reason": (
            None
            if evaluation_engine_commit
            else unavailable.get("evaluation_engine_commit")
            or status_unavailable.get("evaluation_engine_commit")
            or "Evaluation Runtime binding did not provide engine_commit"
        ),
    }


def write_protocol_yaml(
    results_dir: Path,
    protocol_id: str,
    model_dir: Path | None,
    *,
    results: list[dict[str, Any]] | None = None,
    tool_name: str | None = None,
) -> None:
    protocol_cfg: dict[str, Any] = {}
    server_cfg: dict[str, Any] = {}
    model_cfg: dict[str, Any] = {}
    config_yaml = model_dir / "config.yaml" if model_dir and model_dir.exists() else None
    try:
        import yaml

        if config_yaml and config_yaml.exists():
            model_cfg = yaml.safe_load(config_yaml.read_text(encoding="utf-8")) or {}
            protocols = model_cfg.get("protocols", {})
            protocol_cfg = protocols.get(protocol_id, {}) if isinstance(protocols, dict) else {}
            server_cfg = dict(model_cfg.get("server") or {})
    except Exception as exc:
        logger.warning("Failed to read model config for protocol.yaml", error=str(exc))

    model_section = _safe_dict(model_cfg.get("model"))
    tool_section = model_cfg.get("tools") if isinstance(model_cfg.get("tools"), list) else []
    first_tool = tool_section[0] if tool_section and isinstance(tool_section[0], dict) else {}
    selected_tool_name = tool_name or first_tool.get("name")
    server_env = _safe_dict(server_cfg.get("env"))
    server_env_keys = sorted(str(key) for key in server_env)
    sanitized_server = dict(server_cfg)
    sanitized_server.pop("env", None)
    sanitized_server["env_keys"] = server_env_keys

    weights_manifest = _load_model_sidecar(model_dir, "artifacts/weights_manifest.json")
    build_plan = _load_model_sidecar(model_dir, "artifacts/build_plan.json")
    standard_params = _safe_dict(protocol_cfg.get("standard_params") or protocol_cfg.get("standard"))
    resolved_model_params = _safe_dict(
        protocol_cfg.get("resolved_model_params")
        or protocol_cfg.get("model_params")
        or protocol_cfg.get("params")
    )
    unmapped = _safe_dict(protocol_cfg.get("unmapped"))
    protocol_definition_path = str(
        protocol_cfg.get("definition_path")
        or os.environ.get("SURE_EVAL_PROTOCOL_DEFINITION_PATH")
        or ""
    )
    template_file = SKILL_ROOT / "scripts" / "templates" / "protocol.yaml"
    generation_status = _load_run_sidecar(results_dir, "prediction_generation_status.json")
    prediction_reuse_manifest = _load_run_sidecar(results_dir, "prediction_reuse_manifest.json")
    runtime_inventory = _load_model_sidecar(model_dir, "artifacts/runtime_inventory.json")
    status_runtime = _safe_dict(generation_status.get("runtime"))
    status_env = _safe_dict(generation_status.get("environment"))
    status_generation = _safe_dict(generation_status.get("generation"))
    protocol_resolution = _safe_dict(status_generation.get("protocol_resolution"))
    status_runtime_inventory = _nested_dict(status_runtime, "runtime_inventory")
    harness_runtime = _safe_dict(status_runtime.get("harness_runtime"))
    inventory_container = _safe_dict(runtime_inventory.get("container_runtime"))
    inventory_model_runtime = _safe_dict(runtime_inventory.get("model_runtime"))
    inventory_local = _safe_dict(runtime_inventory.get("local_runtime"))
    inventory_policy = _safe_dict(runtime_inventory.get("policy"))
    runtime_kind = "python" if inventory_policy.get("eval_runtime") == "python" else "container"
    status_server_config = _safe_dict(status_runtime.get("server_config"))
    server_command = status_runtime.get("server_command") or sanitized_server.get("command", [])
    server_working_dir = status_runtime.get("server_working_dir") or sanitized_server.get("working_dir", ".")
    merged_server = {**(sanitized_server or {}), **(status_server_config or {})}
    server_protocol = resolve_server_protocol(merged_server)
    env_keys = status_env.get("env_keys") if isinstance(status_env.get("env_keys"), list) else server_env_keys
    safe_env_values = _safe_dict(status_env.get("safe_env_values"))
    redacted_env_keys = status_env.get("redacted_env_keys") if isinstance(status_env.get("redacted_env_keys"), list) else []
    selected_standard_params = _nonempty_dict(_safe_dict(protocol_resolution.get("standard_params")), standard_params)
    selected_model_params = _nonempty_dict(_safe_dict(protocol_resolution.get("model_params")), resolved_model_params)
    selected_unmapped = _nonempty_dict(_safe_dict(protocol_resolution.get("unmapped")), unmapped)
    explicit_tool_args = _safe_dict(status_generation.get("tool_args"))
    argument_policy = _safe_dict(status_generation.get("argument_policy"))
    raw_response_observation = _safe_dict(status_generation.get("observed_raw_response"))
    runtime_inventory_path = (
        _existing_path_or_none(model_dir / "artifacts" / "runtime_inventory.json")
        if model_dir
        else None
    )
    generation_status_path = _existing_path_or_none(results_dir / "prediction_generation_status.json")
    source_reuse = _safe_dict(prediction_reuse_manifest.get("source"))
    source_provenance_manifest = _safe_dict(prediction_reuse_manifest.get("source_inference_provenance"))
    source_inference_provenance = _nonempty_dict(
        _safe_dict(source_reuse.get("source_inference_provenance")),
        _safe_dict(source_provenance_manifest.get("source_inference_provenance")),
    )
    prediction_reuse_enabled = bool(prediction_reuse_manifest)
    engine_root = next(
        (
            Path(str(context["engine_root"]))
            for row in results or []
            if isinstance(row, dict)
            for context in [row.get("evaluation_context")]
            if isinstance(context, dict) and context.get("engine_root")
        ),
        None,
    )
    evaluation_runtime = next(
        (
            context.get("evaluation_runtime")
            for row in results or []
            if isinstance(row, dict)
            for context in [row.get("evaluation_context")]
            if isinstance(context, dict) and isinstance(context.get("evaluation_runtime"), dict)
        ),
        {},
    )
    execution_entrypoint = os.environ.get("SURE_EVAL_EXECUTION_ENTRYPOINT")

    identity_provenance = _identity_provenance(
        results_dir=results_dir,
        generation_status=generation_status,
        evaluation_runtime=evaluation_runtime if isinstance(evaluation_runtime, dict) else None,
    )
    payload = {
        "schema": "sure.eval.inference_protocol.v1",
        "protocol_id": protocol_id,
        "run": {
            "run_id": _artifact_run_id(results_dir),
            "run_dir": str(results_dir),
            "created_at": _utc_now(),
        },
        "model": {
            "model_name": canonical_model_name(model_dir, model_cfg) if model_dir else str(tool_name or "unknown"),
            "model_dir": str(model_dir) if model_dir else None,
            "model_source": model_section.get("source") or weights_manifest.get("model_id") or weights_manifest.get("source") or None,
            "weights_source": weights_manifest.get("snapshot_path") or weights_manifest.get("local_path") or weights_manifest.get("model_path") or None,
            "model_dir_source": build_plan.get("model_dir_source") or build_plan.get("source") or None,
            "mcp_tool_name": selected_tool_name,
            "server_config": {
                "command": server_command,
                "working_dir": server_working_dir,
                "timeout": status_server_config.get("timeout", sanitized_server.get("timeout")),
                "startup_timeout_sec": status_server_config.get("startup_timeout_sec", sanitized_server.get("startup_timeout_sec")),
                "env_keys": env_keys,
            },
        },
        "protocol_selection": {
            "protocol_id": protocol_id,
            "definition_path": protocol_definition_path,
            "model_protocol_config_path": str(config_yaml) if config_yaml and config_yaml.exists() else None,
            "is_default": protocol_id == str(model_cfg.get("default_protocol") or "standard_system"),
            "purpose": protocol_cfg.get("purpose") or "standardized model inference before route-backed evaluation",
            "standard_params": selected_standard_params,
            "resolved_model_params": selected_model_params,
            "unmapped": selected_unmapped,
            "parameter_status": _safe_dict(protocol_resolution.get("parameter_status")),
            "config_sources": protocol_resolution.get("config_sources") or [],
            "resolution_status": protocol_resolution.get("status"),
            "resolution_error": protocol_resolution.get("error"),
        },
        "inference_environment": {
            "execution_path": os.environ.get("SURE_EVAL_EXECUTION_PATH", "unknown"),
            "runtime_kind": runtime_kind,
            "container": {
                "image": inventory_container.get("target_image"),
                "image_digest": inventory_container.get("target_image_digest"),
                "image_ref": inventory_container.get("target_image_ref") or os.environ.get("SURE_EVAL_CONTAINER_IMAGE"),
                "dockerfile": os.environ.get("SURE_EVAL_DOCKERFILE") or model_cfg.get("dockerfile"),
                "repo_root": os.environ.get("SURE_EVAL_CONTAINER_REPO_ROOT") or str(HARNESS_ROOT),
                "model_dir": str(model_dir) if model_dir else None,
                "python_executable": inventory_container.get("python_executable"),
                "working_dir": inventory_container.get("working_dir"),
                "execution_mode": inventory_policy.get("eval_runtime"),
                "host_python_fallback": inventory_policy.get("host_python_fallback"),
            },
            "model_runtime": {
                "runtime_id": inventory_model_runtime.get("runtime_id"),
                "python_executable": os.environ.get("MODEL_PYTHON") if runtime_kind == "python" else None,
                "lock_sha256": inventory_model_runtime.get("lock_sha256"),
                "manifest_sha256": inventory_model_runtime.get("manifest_sha256"),
                "working_dir": os.environ.get("SURE_EVAL_MODEL_WORKING_DIR") if runtime_kind == "python" else None,
                "execution_mode": inventory_policy.get("eval_runtime"),
                "host_python_fallback": inventory_policy.get("host_python_fallback"),
            },
            "harness_runtime": {
                "schema": harness_runtime.get("schema"),
                "runtime_id": harness_runtime.get("runtime_id"),
                "runtime_type": harness_runtime.get("runtime_type"),
                "python_executable": harness_runtime.get("python_executable"),
                "process_python_executable": harness_runtime.get("process_python_executable"),
                "lock_sha256": harness_runtime.get("lock_sha256"),
                "manifest_path": harness_runtime.get("manifest_path"),
                "runtime_root": harness_runtime.get("runtime_root"),
            },
            "evaluation_runtime": evaluation_runtime,
            "server": {
                "transport": merged_server.get("transport") or "stdio",
                "protocol": server_protocol,
                "command": server_command,
                "working_dir": server_working_dir,
                "tool_name": selected_tool_name,
                "startup_timeout_sec": status_server_config.get("startup_timeout_sec", sanitized_server.get("startup_timeout_sec")),
                "timeout": status_server_config.get("timeout", sanitized_server.get("timeout")),
            },
            "env": {
                "device": os.environ.get("SURE_EVAL_DEVICE_ACTUAL") or os.environ.get("DEVICE") or os.environ.get("CUDA_VISIBLE_DEVICES") or None,
                "env_keys": env_keys,
                "safe_env_values": safe_env_values,
                "redacted_env_keys": redacted_env_keys,
                "modelscope_cache": os.environ.get("MODELSCOPE_CACHE"),
            },
            "runtime_inventory": {
                "path": runtime_inventory_path,
                "status": runtime_inventory.get("status") or status_runtime_inventory.get("status"),
                "schema": runtime_inventory.get("schema"),
                "local_evidence_backend": inventory_local.get("backend"),
                "execution_mode": inventory_policy.get("eval_runtime"),
                "target_image_ref": inventory_container.get("target_image_ref"),
            },
            "mount_policy": {
                "mount_stable_absolute_roots": [
                    str(path)
                    for path in (
                        model_dir,
                        HARNESS_ROOT / "data",
                    )
                    if path is not None
                ],
                "reject_repo_internal_runtime_mount_overlays": True,
                "nfs_models_read_only": (
                    False
                    if runtime_kind == "python"
                    else _nested_dict(inventory_container, "mount_policy").get("nfs_models_read_only")
                ),
                "model_integrity": "verify_before_after" if runtime_kind == "python" else "image_digest",
                "result_workspace": _nested_dict(_nested_dict(inventory_container, "mount_policy"), "result_workspace"),
            },
        },
        "inference_constraints": {
            "no_external_lm": True,
            "no_retrieval": True,
            "no_hotwords": True,
            "single_pass_decode": True,
            "no_prompt_engineering": True,
            "local_fallback_allowed": False,
            "metric_logic_in_inference_image_allowed": False,
            "required_preflight_checks": [
                "deterministic_prediction_contract",
                "execution_surface_isolation",
                "model_server_smoke",
            ],
        },
        "inference_parameters": {
            "source_priority": [
                "prediction_generation_status.json",
                "runtime_inventory.json",
                "model config.yaml protocols",
                "explicit MCP tool arguments",
            ],
            "protocol_id": protocol_id,
            "protocol_resolution": {
                "status": protocol_resolution.get("status"),
                "error": protocol_resolution.get("error"),
                "standard_params": selected_standard_params,
                "model_params": selected_model_params,
                "unmapped": selected_unmapped,
            },
            "explicit_tool_args": explicit_tool_args,
            "argument_policy": argument_policy,
            "raw_response_observation": raw_response_observation,
            "model_config_protocol": {
                "standard_params": standard_params,
                "resolved_model_params": resolved_model_params,
                "unmapped": unmapped,
            },
        },
        "execution_surface": {
            "materialized": bool(execution_entrypoint),
            "execution_surface_type": os.environ.get("SURE_EVAL_EXECUTION_SURFACE_TYPE") or "python_entrypoint",
            "entrypoint_path": execution_entrypoint or None,
            "generation_method": os.environ.get("SURE_EVAL_EXECUTION_GENERATION_METHOD") or "harness_template",
            "template_file": os.environ.get("SURE_EVAL_EXECUTION_TEMPLATE_FILE"),
            "template_sha256": os.environ.get("SURE_EVAL_EXECUTION_TEMPLATE_SHA256") or _sha256_file(template_file),
            "isolation_compliance": {
                "eval_runs_referenced": False,
                "prior_run_scripts_copied": False,
                "deviation_approved_by_user": False,
            },
        },
        "prediction_reuse": {
            "enabled": prediction_reuse_enabled,
            "generation_policy": "reused_predictions_no_inference" if prediction_reuse_enabled else "generated_by_model_server",
            "manifest": _existing_path_or_none(results_dir / "prediction_reuse_manifest.json"),
            "source_run_dir": source_reuse.get("source_run_dir"),
            "source_results_dir": source_reuse.get("source_results_dir"),
            "source_run_id": source_reuse.get("source_run_id"),
            "source_protocol": source_inference_provenance.get("source_protocol"),
            "source_prediction_generation_status": source_inference_provenance.get("source_prediction_generation_status"),
            "source_runtime_inventory": source_inference_provenance.get("source_runtime_inventory"),
            "old_evaluation_reused": False,
        },
        "prediction_contract": {
            "contract_path": "references/contracts/prediction_generation_contract.md",
            "compatibility_tsv": "predictions/<dataset>.txt",
            "structured_jsonl": "predictions/<dataset>.jsonl",
            "format_used": "jsonl+txt",
            "generated_by": os.environ.get("SURE_EVAL_PREDICTION_GENERATED_BY") or "scripts/generate_predictions_via_server.py",
            "protocol_argument": protocol_id,
        },
        "provenance": {
            "harness_commit": identity_provenance.get("harness_commit"),
            "harness_commit_unavailable_reason": identity_provenance.get("harness_commit_unavailable_reason"),
            "evaluation_engine": {
                "root": str(engine_root) if engine_root else None,
                "commit": identity_provenance.get("evaluation_engine_commit"),
                "commit_unavailable_reason": identity_provenance.get("evaluation_engine_commit_unavailable_reason"),
            },
            "execution_provenance": _existing_path_or_none(results_dir / "execution_provenance.json")
            or _existing_path_or_none(results_dir / "artifacts" / "execution_provenance.json")
            or os.environ.get("SURE_EVAL_EXECUTION_PROVENANCE"),
            "execution_provenance_schema": _safe_dict(identity_provenance.get("execution_provenance")).get("schema"),
            "prediction_generation_status": generation_status_path,
            "prediction_generation_status_schema": generation_status.get("schema"),
            "runtime_inventory": runtime_inventory_path,
            "runtime_inventory_schema": runtime_inventory.get("schema"),
            "deployment_ready": _existing_path_or_none(model_dir / "artifacts" / "deployment_ready.json") if model_dir else None,
            "package_gate": _existing_path_or_none(model_dir / "artifacts" / "package_gate.json") if model_dir else None,
            "source_inference_provenance_manifest": _existing_path_or_none(results_dir / "source_inference_provenance.json"),
            "source_protocol": source_inference_provenance.get("source_protocol"),
            "source_prediction_generation_status": source_inference_provenance.get("source_prediction_generation_status"),
            "source_runtime_inventory": source_inference_provenance.get("source_runtime_inventory"),
            "raw_response_source_of_truth": False,
            "notes": [
                "Inference parameters come from model config, CLI overrides, protocol resolver output, and the actual server call policy.",
                "raw_response is preserved in predictions JSONL as model output evidence only.",
            ],
        },
        "notes": [
            "This file records inference protocol, runtime environment, inference parameters, and inference constraints only.",
            "Dataset scope, evaluation routes, metric results, validation, and metric artifacts are recorded in report_snapshot.md and report.jsonl.",
        ],
    }
    protocol_yaml = results_dir / "protocol.yaml"
    protocol_yaml.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml

        text = yaml.safe_dump(_to_strict_jsonable(payload), allow_unicode=True, sort_keys=False)
    except Exception as exc:
        logger.warning("Falling back to JSON-compatible protocol.yaml", error=str(exc))
        text = json.dumps(_to_strict_jsonable(payload), indent=2, ensure_ascii=False) + "\n"
    protocol_yaml.write_text(text, encoding="utf-8")
    logger.info("Wrote protocol.yaml", path=str(protocol_yaml))


def main() -> int:
    parser = argparse.ArgumentParser(description="Write protocol.yaml for an inference run")
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--protocol-id", required=True)
    parser.add_argument("--model-dir")
    parser.add_argument("--tool-name")
    args = parser.parse_args()
    write_protocol_yaml(
        Path(args.results_dir),
        args.protocol_id,
        Path(args.model_dir) if args.model_dir else None,
        results=None,
        tool_name=args.tool_name,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
