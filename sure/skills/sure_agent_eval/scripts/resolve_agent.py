#!/usr/bin/env python3
"""Resolve an agent spec into the run plan /sure_agent_eval executes.

Reads the agent spec (agent.yaml), resolves every stage to its approved model
(config.yaml + successful verdict below the site policy's approved models
root, deployment binding when the bundle is sealed), and resolves the dataset
set against the configured allowed_source_roots. Writes
``agent_spec_resolved.json`` (schema sure.agent_eval.spec_resolved.v1).

Credential red line: an API stage records only the NAME of the environment
variable that holds its key — never the value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
HARNESS_ROOT = Path(__file__).resolve().parents[4]
SURE_INFER_SCRIPTS = SCRIPT_DIR.parents[1] / "sure_infer" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SURE_INFER_SCRIPTS))
sys.path.insert(0, str(HARNESS_ROOT))

from agent_spec import (  # noqa: E402
    AgentSpecError,
    load_agent_spec,
    stage_mode,
    validate_agent_spec,
)
from deployment_binding import DeploymentBindingError, load_deployment_binding  # noqa: E402
from resolve_eval_input import _resolve_output_dir  # noqa: E402
from resolve_model_dir import resolve_approved_model_identity  # noqa: E402
from sure_eval.datasets.source_resolver import (  # noqa: E402
    is_source_entry,
    read_source_metadata,
    resolve_site_source_entry,
)

RESOLVED_SCHEMA = "sure.agent_eval.spec_resolved.v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _resolve_api_section(config: dict[str, Any], stage_label: str) -> dict[str, Any]:
    api = config.get("api") if isinstance(config.get("api"), dict) else {}
    base_url = str(api.get("base_url") or "").strip()
    api_key_env = str(api.get("api_key_env") or "").strip()
    if not base_url:
        raise ValueError(f"{stage_label}: api.base_url is required for an API-mode stage")
    if not api_key_env:
        raise ValueError(f"{stage_label}: api.api_key_env is required for an API-mode stage")
    model = str(api.get("model") or (config.get("model") or {}).get("name") or "").strip()
    if not model:
        raise ValueError(f"{stage_label}: api.model (or model.name) is required for an API-mode stage")
    resolved: dict[str, Any] = {
        "base_url": base_url,
        "api_key_env": api_key_env,
        "model": model,
        "timeout": float(api.get("timeout") or 120),
        # A declared retry: 0 means fail fast; `or 3` turned it into three tries.
        "retry": 3 if api.get("retry") is None else int(api["retry"]),
    }
    if "temperature" in api and api["temperature"] is not None:
        temperature = float(api["temperature"])
        if temperature < 0:
            raise ValueError(f"{stage_label}: api.temperature must be >= 0")
        resolved["temperature"] = temperature
    if "top_p" in api and api["top_p"] is not None:
        top_p = float(api["top_p"])
        if not 0 < top_p <= 1:
            raise ValueError(f"{stage_label}: api.top_p must be in (0, 1]")
        resolved["top_p"] = top_p
    if "max_tokens" in api and api["max_tokens"] is not None:
        max_tokens = int(api["max_tokens"])
        if max_tokens <= 0:
            raise ValueError(f"{stage_label}: api.max_tokens must be > 0")
        resolved["max_tokens"] = max_tokens
    return resolved


def _resolve_stage(
    stage: dict[str, Any],
    *,
    position: int,
    approved_root: Path | None,
) -> dict[str, Any]:
    stage_id = str(stage["id"])
    model = str(stage["model"])
    label = f"stage {stage_id!r}"
    identity = resolve_approved_model_identity(model, approved_root=approved_root)
    if not identity.get("ok"):
        raise ValueError(f"{label}: approved model {model!r} is not ready: {identity.get('identity_error')}")
    model_dir = Path(str(identity["model_dir"]))
    config_path = Path(str(identity["config_path"]))
    config = _load_yaml(config_path)
    mode = stage_mode(config)
    if position == 0 and mode != "mcp_tool":
        raise ValueError(
            f"{label}: the first stage must be an MCP-tool model (config.yaml with server.command), "
            "not an API model; scripts/agent_runner.py drives position 0 through the stage model's "
            "MCP server over the dataset's audio"
        )
    if position > 0 and mode != "api":
        raise ValueError(
            f"{label}: only the first stage may be an MCP-tool model; later stages chain text "
            "through the API-model pattern (config.yaml with api.base_url)"
        )

    server = config.get("server") if isinstance(config.get("server"), dict) else {}
    server_command = [str(item) for item in server.get("command") or [] if str(item).strip()]
    tool_names = [
        str(tool["name"])
        for tool in config.get("tools") or []
        if isinstance(tool, dict) and str(tool.get("name") or "").strip()
    ]
    # The stage model's own environment (MODEL_PATH, DEVICE, cache roots): without
    # it the server starts blind to everything its config.yaml declares.
    server_env = {str(key): str(value) for key, value in (server.get("env") or {}).items()}
    # A relative server.working_dir names a directory inside the approved bundle.
    working_dir = str((model_dir / str(server.get("working_dir") or "")).resolve())

    deployment_bound = False
    deployment_error: str | None = None
    try:
        binding = load_deployment_binding(model_dir, model)
        deployment_bound = True
        python_binding = binding.get("python") if isinstance(binding.get("python"), dict) else None
        if python_binding is not None:
            # The sealed Python binding carries the verified interpreter and tool list.
            server_command = [str(item) for item in python_binding.get("server_command") or server_command]
            tool_names = [str(item) for item in python_binding.get("tool_names") or tool_names]
            working_dir = str(python_binding.get("working_dir") or working_dir)
    except DeploymentBindingError as exc:
        # An approved-but-unsealed bundle still runs from its config.yaml surface;
        # the gap is recorded, never hidden.
        deployment_error = str(exc)

    if mode == "mcp_tool" and not server_command:
        raise ValueError(f"{label}: MCP-tool stage model {model!r} declares no server.command in config.yaml")
    if mode == "mcp_tool" and not tool_names:
        raise ValueError(f"{label}: MCP-tool stage model {model!r} declares no tools[].name in config.yaml")

    model_config = config.get("model") if isinstance(config.get("model"), dict) else {}
    return {
        "id": stage_id,
        "model": model,
        "mode": mode,
        "task": str(model_config.get("task") or config.get("task") or "").strip().upper(),
        "model_dir": str(model_dir),
        "config_path": str(config_path),
        "verdict_path": str(identity["verdict_path"]),
        "tool_names": tool_names,
        "server_command": server_command,
        "working_dir": working_dir,
        "env": server_env,
        "api": _resolve_api_section(config, label) if mode == "api" else None,
        "prompt_template": stage.get("prompt_template") if isinstance(stage.get("prompt_template"), str) else None,
        "deployment_bound": deployment_bound,
        "deployment_error": deployment_error,
    }


def _resolve_dataset(entry: str, *, dataset_source_key: str | None) -> dict[str, Any]:
    if not is_source_entry(entry):
        raise ValueError(
            f"dataset {entry!r} is not a source path; /sure_agent_eval takes absolute source roots "
            "below a configured allowed_source_roots entry"
        )
    ref = resolve_site_source_entry(entry, dataset_source_key=dataset_source_key)
    meta = read_source_metadata(ref)
    sample_jsonl = Path(ref.sample_jsonl)
    num_samples = sum(1 for line in sample_jsonl.read_text(encoding="utf-8").splitlines() if line.strip())
    return {
        "dataset": ref.dataset_id,
        "source_root": ref.source_root,
        "source_dataset_name": ref.source_dataset_name,
        "version_id": ref.version_id,
        "task": meta["task"],
        "language": meta["language"] or "auto",
        "translation_language": meta["translation_language"],
        "sample_jsonl": ref.sample_jsonl,
        "ds_jsonl": ref.ds_jsonl,
        "raw_dir": ref.raw_dir,
        "num_samples": num_samples,
    }


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value).split(",") if item.strip()]


def resolve_agent(
    args: argparse.Namespace,
    *,
    approved_root: Path | None = None,
) -> dict[str, Any]:
    spec_path = Path(args.agent).expanduser().resolve()
    spec = load_agent_spec(spec_path)
    errors = validate_agent_spec(spec)
    if errors:
        raise AgentSpecError("invalid agent spec: " + "; ".join(errors))
    agent = dict(spec["agent"])
    datasets = _split_csv(args.datasets)
    if not datasets:
        raise ValueError("at least one dataset source is required")
    metrics = _split_csv(args.metrics)
    if not metrics:
        raise ValueError("at least one metric is required")
    max_samples = int(args.max_samples or 0)
    if max_samples < 0:
        raise ValueError("--max-samples cannot be negative (0 means the whole dataset)")

    stages = [
        _resolve_stage(stage, position=index, approved_root=approved_root)
        for index, stage in enumerate(spec["stages"])
    ]
    dataset_source_key = args.dataset_source_key or None
    resolved_datasets = [_resolve_dataset(entry, dataset_source_key=dataset_source_key) for entry in datasets]
    names = [item["dataset"] for item in resolved_datasets]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate dataset ids after resolution: {names}")

    # The same boundary /sure_infer applies to output_dir: absolute, outside the
    # configured forbidden output roots, creatable and writable. The extension
    # checks it too, but a direct script call must not get past it.
    staged_dir = HARNESS_ROOT / "sure" / "results" / "agents" / str(agent["name"]) / str(args.run_id)
    product_dir = str(_resolve_output_dir(args.output_dir, staged_dir))
    output_dir = product_dir if args.output_dir else None
    return {
        "schema": RESOLVED_SCHEMA,
        "run_id": str(args.run_id),
        "created_at": _utc_now(),
        "agent": {
            "name": str(agent["name"]),
            "task": str(agent["task"]),
            "input": str(agent["input"]),
            "output": str(agent["output"]),
            "spec_path": str(spec_path),
            "spec_sha256": _sha256(spec_path),
        },
        "stages": stages,
        "datasets": resolved_datasets,
        "metrics": metrics,
        "runtime": {
            "product_dir": product_dir,
            "output_dir": output_dir,
            "dataset_source_key": str(args.dataset_source_key or "default"),
            "max_samples": max_samples,
            "device": str(args.device or "cpu"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve a SURE agent spec into the run plan")
    parser.add_argument("--agent", required=True, help="Path to the agent spec (agent.yaml)")
    parser.add_argument("--datasets", required=True, help="Comma-separated source paths, each <path>[@<version>]")
    parser.add_argument("--metrics", required=True, help="Comma-separated metrics, e.g. bleu,chrf")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dataset-source-key")
    parser.add_argument("--output-dir")
    parser.add_argument("--max-samples", type=int, default=0, help="Sample cap per dataset; 0 means the whole dataset")
    parser.add_argument("--device", default="cpu", help="Evaluation device recorded in the plan")
    parser.add_argument("--approved-models-root", help=argparse.SUPPRESS)  # test hook
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    approved_root = Path(args.approved_models_root).expanduser().resolve() if args.approved_models_root else None
    try:
        payload = resolve_agent(args, approved_root=approved_root)
    except (AgentSpecError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
