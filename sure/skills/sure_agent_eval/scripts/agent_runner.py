#!/usr/bin/env python3
"""Execute an agent chain over the resolved datasets and stage the bundle.

Reads ``agent_spec_resolved.json`` from the run artifacts, projects every
dataset (ASR or S2TT, per the dataset's own metadata), and runs the chain per
sample: stage 1 through the approved model's MCP server (JSON-RPC tools/call
with ``audio_path``), later stages through the API-model pattern
(``api.base_url`` + the key from the environment variable NAMED by
``api_key_env`` — the value is never written anywhere). The product is a
/sure_infer-compatible bundle: ``predictions/<dataset>.txt`` (key<TAB>text),
``predictions/manifest.json``, ``protocol.yaml``,
``prediction_generation_status.json`` and ``references/sure_benchmark/jsonl/``.
The terminal record is ``execution_result.json`` in the run artifacts.

A terminal failure (job_status=failed) is a valid outcome: partial products
stay on disk and the record names failed_stage/failed_dataset/error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
HARNESS_ROOT = Path(__file__).resolve().parents[4]
SURE_INFER_SCRIPTS = SCRIPT_DIR.parents[1] / "sure_infer" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SURE_INFER_SCRIPTS))
sys.path.insert(0, str(SCRIPT_DIR.parents[2] / "runtime" / "harness"))
sys.path.insert(0, str(HARNESS_ROOT))

from agent_spec import render_prompt  # noqa: E402
from model_child_env import model_child_env  # noqa: E402
from sure.site.loader import load_site_policy  # noqa: E402
from sure_eval.core.config import Config  # noqa: E402
from sure_eval.datasets.dataset_manager import DatasetManager  # noqa: E402

# Bound on one MCP response (model load included); without it a hung stage model
# blocks the whole run, since stdout.readline() has no deadline on any platform.
MCP_RESPONSE_TIMEOUT_SEC = 600.0

EXECUTION_RESULT_SCHEMA = "sure.agent_eval.execution_result.v1"
STATUS_SCHEMA = "sure.eval.prediction_generation_status.v2"
PROTOCOL_SCHEMA = "sure.agent_eval.inference_protocol.v1"

McpCaller = Callable[[dict[str, Any]], Any]
ApiCaller = Callable[[dict[str, Any], str], str]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _tsv_safe(text: str) -> str:
    return " ".join(str(text).split())


class McpToolClient:
    """Minimal JSON-RPC stdio client for a stage model's MCP server."""

    def __init__(self, stage: dict[str, Any], *, log_path: Path | None = None) -> None:
        command = [str(item) for item in stage.get("server_command") or []]
        if not command:
            raise RuntimeError(f"stage {stage.get('id')!r} carries no server_command")
        executable = command[0]
        if not Path(executable).is_absolute() and (os.sep in executable or "/" in executable):
            candidate = Path(stage["model_dir"]) / executable
            if candidate.exists():
                command[0] = str(candidate)
        self._tool = str((stage.get("tool_names") or [""])[0])
        if not self._tool:
            raise RuntimeError(f"stage {stage.get('id')!r} carries no tool name")
        # The stage model runs in its own Python (sealed Model Runtime or the
        # bundle's config.yaml command); leaking the harness interpreter's
        # PYTHONHOME/PYTHONPATH into the child breaks its path configuration.
        child_env = model_child_env(os.environ)
        child_env.update({str(key): str(value) for key, value in (stage.get("env") or {}).items()})
        # The server's own diagnostics belong in the run log; DEVNULL threw away
        # the only account of why a stage model failed to start.
        self._log = open(log_path, "a", encoding="utf-8", errors="replace") if log_path else None
        self._process = subprocess.Popen(
            command,
            cwd=str(stage.get("working_dir") or stage["model_dir"]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log or subprocess.DEVNULL,
            text=True,
            env=child_env,
        )
        self._next_id = 0
        # select() does not work on pipes on Windows, so stdout is drained by a
        # thread and every request waits on the queue instead of on readline().
        self._responses: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        try:
            self._request("initialize", {})
        except Exception:
            # A client that fails its handshake never reaches the caller's list,
            # so nothing would close this server; it keeps its (GPU) memory.
            self._process.kill()
            self._process.wait(timeout=10)
            self._close_log()
            raise

    def _pump_stdout(self) -> None:
        assert self._process.stdout is not None
        while True:
            line = self._process.stdout.readline()
            self._responses.put(line or None)
            if not line:
                return

    def _request(self, method: str, params: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        assert self._process.stdin is not None
        self._next_id += 1
        request_id = self._next_id
        self._process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n"
        )
        self._process.stdin.flush()
        limit = MCP_RESPONSE_TIMEOUT_SEC if timeout is None else timeout
        deadline = time.monotonic() + limit
        while True:
            try:
                line = self._responses.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                self._process.kill()
                raise RuntimeError(f"MCP {method} timed out after {limit:g}s; server killed") from None
            if line is None:
                raise RuntimeError(f"MCP server exited while answering {method}")
            line = line.strip()
            if not line:
                continue
            response = json.loads(line)
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise RuntimeError(f"MCP {method} failed: {response['error']}")
            result = response.get("result")
            return result if isinstance(result, dict) else {}

    def call(self, arguments: dict[str, Any]) -> Any:
        result = self._request("tools/call", {"name": self._tool, "arguments": arguments})
        content = result.get("content")
        if result.get("isError"):
            message = str(content[0].get("text") or "") if isinstance(content, list) and content else ""
            raise RuntimeError(message or "Tool call returned isError=true")
        if not isinstance(content, list) or not content:
            raise RuntimeError("MCP tools/call returned no content")
        text = str(content[0].get("text") or "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _close_log(self) -> None:
        if self._log is not None:
            self._log.close()
            self._log = None

    def close(self) -> None:
        try:
            self._request("shutdown", {}, timeout=10)
        except Exception:
            pass
        try:
            self._process.terminate()
            self._process.wait(timeout=10)
        except Exception:
            try:
                self._process.kill()
            except Exception:
                pass
        self._close_log()


def extract_text(result: Any) -> str:
    """Pull the stage's text answer out of a model.predict result payload."""
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        for field in ("text", "transcription", "prediction", "answer", "output", "result"):
            value = result.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def call_chat_completion(api: dict[str, Any], prompt: str, *, env: dict[str, str] | None = None) -> str:
    """One OpenAI-compatible chat completion against an API-model stage."""
    environ = env if env is not None else os.environ
    key_env = str(api.get("api_key_env") or "")
    key = environ.get(key_env, "")
    if not key:
        raise RuntimeError(
            f"API stage credential is missing: environment variable {key_env} is not set "
            "(set it in the shell; it is never written to any artifact)"
        )
    url = str(api["base_url"]).rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": str(api["model"]),
        "messages": [{"role": "user", "content": prompt}],
    }
    for field in ("temperature", "top_p", "max_tokens"):
        if field in api and api[field] is not None:
            payload[field] = api[field]
    body = json.dumps(payload).encode("utf-8")
    attempts = max(1, int(api.get("retry") or 3))
    timeout = float(api.get("timeout") or 120)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            choices = payload.get("choices") or []
            text = str((choices[0].get("message") or {}).get("content") or "").strip()
            if not text:
                raise RuntimeError("API stage returned an empty completion")
            return text
        except Exception as exc:  # noqa: BLE001 - retried, then reported as the stage failure
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"API stage call failed after {attempts} attempt(s): {last_error}")


def _dataset_manager(projection_root: Path, dataset_source_key: str) -> DatasetManager:
    config = Config()
    config.data.datasets = str(projection_root / "datasets")
    return DatasetManager(config=config, dataset_source_key=dataset_source_key)


def _projection_root(product_dir: Path) -> Path:
    policy = load_site_policy()
    if policy:
        configured = str(policy["policy"].get("datasets", {}).get("projection_root") or "")
        if configured:
            return Path(configured)
    return product_dir / "_datasets"


def run_agent(
    spec: dict[str, Any],
    run_dir: Path,
    *,
    max_samples: int = 0,
    mcp_caller_factory: Callable[[dict[str, Any]], McpCaller] | None = None,
    api_caller: ApiCaller | None = None,
) -> dict[str, Any]:
    """Run the chain; write the bundle and artifacts/execution_result.json."""
    run_dir = Path(run_dir)
    artifacts_dir = run_dir / "artifacts"
    product_dir = Path(spec["runtime"]["product_dir"])
    agent = spec["agent"]
    stages = list(spec["stages"])
    datasets = list(spec["datasets"])
    log_path = product_dir / "agent_runner.log"
    product_dir.mkdir(parents=True, exist_ok=True)
    (product_dir / "predictions").mkdir(parents=True, exist_ok=True)
    (product_dir / "references" / "sure_benchmark" / "jsonl").mkdir(parents=True, exist_ok=True)

    def log(message: str) -> None:
        line = f"[{_utc_now()}] {message}"
        print(line)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    effective_api_caller = api_caller or (lambda api, prompt: call_chat_completion(api, prompt))
    clients: list[McpToolClient] = []

    def default_mcp_caller(stage: dict[str, Any]) -> McpCaller:
        client = McpToolClient(stage, log_path=log_path)
        clients.append(client)
        return client.call

    caller_factory = mcp_caller_factory or default_mcp_caller

    result: dict[str, Any] = {
        "schema": EXECUTION_RESULT_SCHEMA,
        "job_status": "failed",
        "exit_code": 1,
        "failed_stage": None,
        "failed_dataset": None,
        "error": None,
        "product_dir": str(product_dir),
        "agent": {"name": agent["name"], "task": agent["task"]},
        "datasets": [],
        "stdout_log": str(log_path),
        "stderr_log": str(log_path),
        "created_at": _utc_now(),
    }
    status_rows: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {"schema": "sure.agent_eval.prediction_manifest.v1", "datasets": {}}

    try:
        manager = _dataset_manager(_projection_root(product_dir), str(spec["runtime"]["dataset_source_key"]))
        mcp_callers: dict[str, McpCaller] = {}
        for dataset in datasets:
            dataset_id = str(dataset["dataset"])
            log(f"dataset {dataset_id}: projecting from {dataset['source_root']}")
            try:
                # The resolver takes <path>[@<version>]; without the version a pool
                # with several versions is ambiguous and the projection fails here.
                jsonl_path = manager.download_and_convert(
                    f"{dataset['source_root']}@{dataset['version_id']}"
                )
                rows = [
                    json.loads(line)
                    for line in jsonl_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                if max_samples > 0:
                    rows = rows[:max_samples]
                reference_copy = product_dir / "references" / "sure_benchmark" / "jsonl" / f"{dataset_id}.jsonl"
                reference_copy.write_text(
                    "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8",
                )
            except Exception as exc:  # noqa: BLE001 - a failed record must still name a stage for the gate
                result["failed_stage"] = "dataset_projection"
                result["failed_dataset"] = dataset_id
                result["error"] = f"dataset {dataset_id}: {exc}"
                raise

            prediction_path = product_dir / "predictions" / f"{dataset_id}.txt"
            generated = 0
            with prediction_path.open("w", encoding="utf-8") as predictions:
                for row in rows:
                    key = str(row.get("key") or "")
                    position = 0
                    try:
                        value: Any = str(row.get("path") or "")
                        for position, stage in enumerate(stages):
                            if position == 0:
                                if stage["id"] not in mcp_callers:
                                    mcp_callers[stage["id"]] = caller_factory(stage)
                                answer = extract_text(mcp_callers[stage["id"]]({"audio_path": value}))
                            else:
                                prompt = render_prompt(
                                    str(stage.get("prompt_template") or ""),
                                    text=str(value),
                                    target_language=str(dataset.get("translation_language") or ""),
                                    source_language=str(dataset.get("language") or ""),
                                    dataset=dataset_id,
                                    key=key,
                                )
                                answer = effective_api_caller(dict(stage["api"]), prompt)
                            if not str(answer).strip():
                                raise RuntimeError(f"stage {stage['id']!r} returned an empty answer")
                            value = str(answer).strip()
                    except Exception as exc:  # noqa: BLE001 - recorded as the terminal failure
                        result["failed_stage"] = str(stages[min(position, len(stages) - 1)]["id"])
                        result["failed_dataset"] = dataset_id
                        result["error"] = f"sample {key}: {exc}"
                        raise
                    predictions.write(f"{key}\t{_tsv_safe(value)}\n")
                    generated += 1
            manifest["datasets"][dataset_id] = {
                "prediction_file": f"predictions/{dataset_id}.txt",
                "sha256": _sha256(prediction_path),
                "rows": generated,
            }
            status_rows.append(
                {
                    "dataset": dataset_id,
                    "status": "completed",
                    "num_expected_samples": len(rows),
                    "num_generated_samples": generated,
                    "prediction_file": f"predictions/{dataset_id}.txt",
                }
            )
            result["datasets"].append({"dataset": dataset_id, "expected": len(rows), "generated": generated})
            log(f"dataset {dataset_id}: {generated}/{len(rows)} samples generated")

        _write_json(product_dir / "predictions" / "manifest.json", manifest)
        protocol = {
            "schema": PROTOCOL_SCHEMA,
            "agent": {field: agent[field] for field in ("name", "task", "input", "output", "spec_sha256")},
            "stages": [
                {"id": stage["id"], "model": stage["model"], "mode": stage["mode"], "task": stage["task"]}
                for stage in stages
            ],
            "datasets": [str(dataset["dataset"]) for dataset in datasets],
            "provenance": {
                "generated_by": "scripts/agent_runner.py",
                "created_at": _utc_now(),
                "prediction_generation_status": "prediction_generation_status.json",
            },
        }
        (product_dir / "protocol.yaml").write_text(
            yaml.safe_dump(protocol, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        result["job_status"] = "succeeded"
        result["exit_code"] = 0
    except Exception as exc:  # noqa: BLE001 - every failure becomes a terminal failed record
        if result["error"] is None:
            result["error"] = str(exc)
    finally:
        for client in clients:
            client.close()
        _write_json(
            product_dir / "prediction_generation_status.json",
            {
                "schema": STATUS_SCHEMA,
                "agent": {"name": agent["name"], "task": agent["task"]},
                "generation": {
                    "generated_by": "scripts/agent_runner.py",
                    "stages": [
                        {"id": stage["id"], "model": stage["model"], "mode": stage["mode"]} for stage in stages
                    ],
                    "max_samples": max_samples,
                },
                "datasets": status_rows,
            },
        )
        _write_json(artifacts_dir / "execution_result.json", result)
    if result["job_status"] != "succeeded":
        log(f"agent run failed: stage={result['failed_stage']} dataset={result['failed_dataset']}: {result['error']}")
    return result


def _non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("--max-samples cannot be negative (0 means the whole dataset)")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an agent chain over the resolved datasets")
    parser.add_argument("--run-dir", required=True, help="Sure invocation run directory")
    parser.add_argument("--spec", help="Path to agent_spec_resolved.json (default: <run-dir>/artifacts/)")
    parser.add_argument(
        "--max-samples",
        type=_non_negative_int,
        help="Override the resolved plan's runtime.max_samples (0 means the whole dataset)",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    spec_path = Path(args.spec) if args.spec else run_dir / "artifacts" / "agent_spec_resolved.json"
    if not spec_path.is_file():
        print(f"agent_spec_resolved.json not found: {spec_path}; run scripts/resolve_agent.py first", file=sys.stderr)
        return 2
    spec = _read_json(spec_path)
    if spec.get("schema") != "sure.agent_eval.spec_resolved.v1":
        print(f"unsupported resolved-spec schema: {spec.get('schema')!r}", file=sys.stderr)
        return 2
    max_samples = args.max_samples if args.max_samples is not None else int(spec["runtime"].get("max_samples") or 0)
    result = run_agent(spec, run_dir, max_samples=max_samples)
    return 0 if result["job_status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
