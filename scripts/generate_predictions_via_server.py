#!/usr/bin/env python3
"""
Generate prediction files for one dataset by calling a model-local MCP server.

This script is the execution surface for the `wait_for_predictions` step when
the main flow chooses `direct_server_use`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sure_eval.core.config import Config
from sure_eval.core.logging import configure_logging, get_logger
from sure_eval.datasets import DatasetManager

configure_logging(level="INFO")
logger = get_logger(__name__)

SURE_SUITES_ROOT = Path("data/datasets/sure_benchmark/SURE_Test_Suites")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            samples.append(json.loads(line))
    return samples


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _load_build_plan(model_dir: Path) -> dict[str, Any]:
    build_plan_path = model_dir / "artifacts" / "build_plan.json"
    if not build_plan_path.exists():
        return {}
    try:
        return json.loads(build_plan_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_weights_manifest(model_dir: Path) -> dict[str, Any]:
    weights_manifest_path = model_dir / "artifacts" / "weights_manifest.json"
    if not weights_manifest_path.exists():
        return {}
    try:
        return json.loads(weights_manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_server_command(
    model_dir: Path,
    server_cfg: dict[str, Any],
    build_plan: dict[str, Any],
) -> list[str]:
    command = list(server_cfg.get("command", ["python", "server.py"]))
    if not command:
        raise ValueError("server.command must not be empty")

    if command[0] == "python":
        preferred_python = os.environ.get("MODEL_PYTHON")
        venv_python = model_dir / ".venv" / "bin" / "python"
        build_plan_python = build_plan.get("venv_path")
        if preferred_python:
            command[0] = preferred_python
        elif venv_python.exists():
            command[0] = str(venv_python)
        elif build_plan_python:
            command[0] = str(Path(build_plan_python) / "bin" / "python")

    return command


def _infer_hf_home(weights_manifest: dict[str, Any]) -> str | None:
    for key in ("hf_home", "cache_root", "cache_dir"):
        value = weights_manifest.get(key)
        if value:
            return str(value)

    hub_cache_path = weights_manifest.get("hub_cache_path")
    if hub_cache_path:
        hub_cache = Path(str(hub_cache_path))
        if hub_cache.name == "hub":
            return str(hub_cache.parent)
        if "hub" in hub_cache.parts:
            hub_index = hub_cache.parts.index("hub")
            return str(Path(*hub_cache.parts[:hub_index]))

    snapshot_path = weights_manifest.get("snapshot_path")
    if snapshot_path:
        snapshot = Path(str(snapshot_path))
        if "hub" in snapshot.parts:
            hub_index = snapshot.parts.index("hub")
            return str(Path(*snapshot.parts[:hub_index]))

    return None


def _resolve_local_model_path(weights_manifest: dict[str, Any]) -> str | None:
    for key in ("local_path", "model_path", "checkpoint_path", "snapshot_path"):
        value = weights_manifest.get(key)
        if value and Path(str(value)).exists():
            return str(value)
    return None


def _resolve_working_dir(model_dir: Path, server_cfg: dict[str, Any]) -> Path:
    working_dir = server_cfg.get("working_dir", ".")
    return (model_dir / working_dir).resolve()


def _resolve_audio_path(repo_root: Path, sample: dict[str, Any]) -> Path:
    sample_path = Path(sample.get("path", ""))
    if sample_path.is_absolute():
        return sample_path

    sure_candidate = repo_root / SURE_SUITES_ROOT / sample_path
    if sure_candidate.exists():
        return sure_candidate

    relative_candidate = repo_root / sample_path
    if relative_candidate.exists():
        return relative_candidate

    raise FileNotFoundError(f"Unable to resolve audio path for sample: {sample}")


def _send_request(
    process: subprocess.Popen[str],
    request: dict[str, Any],
) -> dict[str, Any]:
    assert process.stdin is not None
    assert process.stdout is not None

    process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
    process.stdin.flush()

    while True:
        line = process.stdout.readline()
        if line == "":
            raise RuntimeError("Server exited before returning a response")
        line = line.strip()
        if not line:
            continue
        try:
            response = json.loads(line)
        except json.JSONDecodeError:
            # Ignore non-JSON stderr-like spillovers accidentally written to stdout.
            continue
        if response.get("id") == request.get("id"):
            return response


def _extract_prediction_text(response: dict[str, Any]) -> str:
    if "error" in response:
        raise RuntimeError(response["error"].get("message", "Unknown server error"))

    result = response.get("result", {})
    content = result.get("content", [])
    if not content:
        return ""

    text = content[0].get("text", "")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text

    if isinstance(parsed, dict):
        return str(parsed.get("text", ""))
    return str(parsed)


def _load_existing_predictions(path: Path) -> dict[str, str]:
    predictions: dict[str, str] = {}
    if not path.exists():
        return predictions
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if "\t" in line:
                key, value = line.split("\t", 1)
            else:
                parts = line.split(None, 1)
                key = parts[0]
                value = parts[1] if len(parts) > 1 else ""
            predictions[key] = value
    return predictions


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate predictions by calling a model-local MCP server")
    parser.add_argument("--model-dir", required=True, help="Model directory under src/sure_eval/models")
    parser.add_argument("--dataset", required=True, help="Canonical dataset name")
    parser.add_argument("--run-dir", required=True, help="Run directory under eval_runs")
    parser.add_argument("--tool-name", help="Tool name to call; defaults to the first configured tool")
    parser.add_argument("--argument-name", default="audio_path", help="Argument name for the audio path")
    parser.add_argument("--language", help="Optional language argument passed through to the tool")
    parser.add_argument("--max-samples", type=int, default=0, help="Optional limit for quick tests")
    parser.add_argument("--resume", action="store_true", help="Resume and skip keys already present in the prediction file")
    parser.add_argument("--config", help="Optional sure-eval config path")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    model_dir = Path(args.model_dir).resolve()
    run_dir = Path(args.run_dir).resolve()
    predictions_dir = run_dir / "predictions"
    logs_dir = predictions_dir / "logs"
    predictions_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    cfg = Config.from_yaml(args.config) if args.config else Config.from_env()
    dataset_manager = DatasetManager(cfg)
    canonical_dataset = dataset_manager.normalize_dataset_name(args.dataset)
    jsonl_path = dataset_manager.get_jsonl_path(canonical_dataset)
    if not jsonl_path.exists():
        jsonl_path = dataset_manager.download_and_convert(canonical_dataset)

    samples = _load_jsonl(jsonl_path)
    if args.max_samples > 0:
        samples = samples[: args.max_samples]

    model_cfg = _load_yaml(model_dir / "config.yaml")
    build_plan = _load_build_plan(model_dir)
    weights_manifest = _load_weights_manifest(model_dir)
    server_cfg = model_cfg.get("server", {})
    command = _resolve_server_command(model_dir, server_cfg, build_plan)
    working_dir = _resolve_working_dir(model_dir, server_cfg)
    env = os.environ.copy()
    for key, value in (server_cfg.get("env", {}) or {}).items():
        env[str(key)] = str(value)

    local_model_path = _resolve_local_model_path(weights_manifest)
    configured_model_path = env.get("MODEL_PATH")
    if local_model_path and (not configured_model_path or not Path(configured_model_path).exists()):
        env["MODEL_PATH"] = local_model_path

    inferred_hf_home = _infer_hf_home(weights_manifest)
    if inferred_hf_home and not env.get("HF_HOME"):
        env["HF_HOME"] = inferred_hf_home
    if build_plan.get("hf_cache_path") and not env.get("HF_HOME"):
        env["HF_HOME"] = str(build_plan["hf_cache_path"])

    tools = model_cfg.get("tools", [])
    tool_name = args.tool_name or (tools[0]["name"] if tools else None)
    if not tool_name:
        raise ValueError("No tool name provided and config.yaml has no tools entry")

    prediction_path = predictions_dir / f"{canonical_dataset}.txt"
    log_path = logs_dir / f"{canonical_dataset}.log"
    status_path = run_dir / "prediction_generation_status.json"

    existing_predictions = _load_existing_predictions(prediction_path) if args.resume else {}
    prediction_map = dict(existing_predictions)

    status_payload: dict[str, Any] = {
        "run_id": run_dir.name,
        "model_name": model_dir.name,
        "execution_path": "direct_server_use",
        "tool_name": tool_name,
        "datasets": [
            {
                "dataset": canonical_dataset,
                "prediction_file": str(prediction_path),
                "status": "running",
                "num_expected_samples": len(samples),
                "num_generated_samples": len(existing_predictions),
                "log_path": str(log_path),
                "error": None,
            }
        ],
    }
    status_path.write_text(json.dumps(status_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    with open(log_path, "w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=str(working_dir),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log_handle,
            text=True,
            bufsize=1,
        )

        try:
            initialize = _send_request(
                process,
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            if "error" in initialize:
                raise RuntimeError(initialize["error"].get("message", "initialize failed"))

            tools_list = _send_request(
                process,
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            if "error" in tools_list:
                raise RuntimeError(tools_list["error"].get("message", "tools/list failed"))

            next_id = 3
            for sample in samples:
                key = str(sample.get("key", ""))
                if args.resume and key in prediction_map:
                    continue

                audio_path = _resolve_audio_path(repo_root, sample)
                arguments: dict[str, Any] = {args.argument_name: str(audio_path)}
                if args.language:
                    arguments["language"] = args.language

                response = _send_request(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "id": next_id,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": arguments},
                    },
                )
                next_id += 1
                prediction_map[key] = _extract_prediction_text(response)

                status_payload["datasets"][0]["num_generated_samples"] = len(prediction_map)
                status_path.write_text(
                    json.dumps(status_payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )

            with open(prediction_path, "w", encoding="utf-8") as handle:
                for sample in samples:
                    key = str(sample.get("key", ""))
                    handle.write(f"{key}\t{prediction_map.get(key, '')}\n")

            status_payload["datasets"][0]["status"] = "completed"
            status_payload["datasets"][0]["num_generated_samples"] = len(samples)
            status_path.write_text(
                json.dumps(status_payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        except Exception as exc:
            status_payload["datasets"][0]["status"] = "failed"
            status_payload["datasets"][0]["error"] = str(exc)
            status_payload["datasets"][0]["num_generated_samples"] = len(prediction_map)
            status_path.write_text(
                json.dumps(status_payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            raise
        finally:
            try:
                _send_request(
                    process,
                    {"jsonrpc": "2.0", "id": 999999, "method": "shutdown", "params": {}},
                )
            except Exception:
                pass
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=30)

    logger.info(
        "Generated predictions via model-local server",
        dataset=canonical_dataset,
        prediction_file=str(prediction_path),
        status_file=str(status_path),
    )
    print(
        json.dumps(
            {
                "dataset": canonical_dataset,
                "prediction_file": str(prediction_path),
                "status_file": str(status_path),
                "num_samples": len(samples),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
