#!/usr/bin/env python3
"""Resolve sure-evaluation route and node-environment readiness for a harness run."""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation_capabilities import discover_engine_capabilities, normalize_engine_task
from evaluation_runtime import ensure_evaluation_runtime
from resolve_evaluation_engine import resolve_engine_root


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _engine_commit(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


def _requested_metrics_for_dataset(dataset: dict[str, Any], resolved_input: dict[str, Any]) -> list[str]:
    metrics = [str(item).strip() for item in dataset.get("default_metrics") or [] if str(item).strip()]
    if metrics:
        return _dedupe(metrics)
    summary = resolved_input.get("task_summary") if isinstance(resolved_input.get("task_summary"), dict) else {}
    return _dedupe([str(item).strip() for item in summary.get("metrics") or [] if str(item).strip()])


def _selected_metrics(requested: list[str], capabilities: dict[str, Any]) -> list[str]:
    if requested:
        return requested
    return _dedupe([str(item) for item in capabilities.get("default_metrics") or []])



# SKILL.md is explicit that a run never builds a node environment: no `uv venv`,
# no `uv sync`, no searching storage for a wheel. The engine's plan hands back
# exactly that command for a missing node, and this artifact used to carry it
# through to the run, which followed it for twenty minutes and then hit its own
# timeout. What belongs here is who prepares the environment and with which
# supported command -- the same one the node itself names when it fails.
FORBIDDEN_SETUP_FRAGMENTS = ("uv venv", "uv sync", "pip install")


def _node_environment_blockers(plan: dict[str, Any]) -> list[dict[str, str]]:
    """The node environments a maintainer has to prepare before this route can run."""
    blockers: list[dict[str, str]] = []
    for route in plan.get("selected_routes") or []:
        if not isinstance(route, dict):
            continue
        for check in route.get("env_checks") or []:
            if not isinstance(check, dict) or not check.get("blocking"):
                continue
            node_id = str(check.get("node_id") or "").strip()
            if not node_id:
                continue
            blockers.append(
                {
                    "node_id": node_id,
                    "group": str(check.get("group") or ""),
                    "prepare_command": f"sure-eval env setup --node {node_id}",
                }
            )
    return blockers


def _maintainer_setup_commands(plan: dict[str, Any]) -> list[str]:
    """Commands for whoever prepares the checkout, never steps inside a run."""
    blockers = _node_environment_blockers(plan)
    steps = (
        [blocker["prepare_command"] for blocker in blockers]
        if blockers
        else [str(item).strip() for item in plan.get("next_steps") or []]
    )
    # The filter applies to both sources rather than only the fallback, so it is
    # a rule about what may leave this function, not a branch that can go stale.
    return [step for step in steps if step and not any(bad in step for bad in FORBIDDEN_SETUP_FRAGMENTS)]


def _scrubbed_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """The engine's plan with its repair commands replaced by the supported one.

    Each dataset entry carries selected_routes and the whole plan, so filtering
    only the top-level setup_commands left the forbidden command in the artifact
    the run actually reads.
    """
    scrubbed = copy.deepcopy(plan)
    for route in scrubbed.get("selected_routes") or []:
        if not isinstance(route, dict):
            continue
        for check in route.get("env_checks") or []:
            if not isinstance(check, dict) or not isinstance(check.get("setup"), dict):
                continue
            node_id = str(check.get("node_id") or "").strip()
            check["setup"]["command"] = f"sure-eval env setup --node {node_id}" if node_id else ""
    scrubbed["next_steps"] = _maintainer_setup_commands(plan)
    return scrubbed


def _dedupe_blockers(blockers: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for blocker in blockers:
        if blocker["node_id"] in seen:
            continue
        seen.add(blocker["node_id"])
        unique.append(blocker)
    return unique


def build_route_plan(
    input_path: Path,
    *,
    output_path: Path | None = None,
    evaluation_engine_root: str | None = None,
) -> dict[str, Any]:
    """Build route/env readiness from eval_input_resolved.json."""

    resolved_input = _load_json(input_path)
    engine_hint = evaluation_engine_root
    if engine_hint is None:
        evaluation = resolved_input.get("evaluation") if isinstance(resolved_input.get("evaluation"), dict) else {}
        engine = evaluation.get("engine") if isinstance(evaluation.get("engine"), dict) else {}
        engine_hint = str(engine.get("engine_root") or "") or None
    resolved_engine = resolve_engine_root(engine_hint)
    if resolved_engine is None:
        raise FileNotFoundError("Unable to resolve sure-evaluation engine root")
    engine_source, engine_root = resolved_engine
    evaluation_runtime = ensure_evaluation_runtime(engine_root, prepare=True)
    from evaluation_capabilities import _insert_engine_src

    _insert_engine_src(engine_root)
    from sure_eval.evaluation.agent_plan import build_agent_plan

    datasets = resolved_input.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("eval_input_resolved.json must contain a non-empty datasets array")

    dataset_plans: list[dict[str, Any]] = []
    blocking_issues: list[str] = []
    setup_commands: list[str] = []
    node_blockers: list[dict[str, str]] = []
    for dataset in datasets:
        if not isinstance(dataset, dict):
            blocking_issues.append("dataset entry is not an object")
            continue
        dataset_name = str(dataset.get("name") or dataset.get("requested_name") or "")
        task = str(dataset.get("task") or "")
        language = str(dataset.get("language") or "")
        requested_metrics = _requested_metrics_for_dataset(dataset, resolved_input)
        capabilities: dict[str, Any] = {}
        metrics: list[str] = requested_metrics
        try:
            engine_task = normalize_engine_task(task)
            capabilities = discover_engine_capabilities(engine_root, task, language)
            metrics = _selected_metrics(requested_metrics, capabilities)
            plan = build_agent_plan(
                engine_task,
                language=language or None,
                metrics=metrics,
            )
        except Exception as exc:
            issue = f"{dataset_name or '(unknown dataset)'}: {exc}"
            if "No configured route" in str(exc):
                issue += (
                    " (common cause: the dataset task does not match the model task,"
                    " e.g. an ASR model paired with a TTS dataset; check datasets="
                    " against the task declared in the model's config.yaml)"
                )
            blocking_issues.append(issue)
            dataset_plans.append(
                {
                    "dataset": dataset_name,
                    "task": task,
                    "language": language,
                    "requested_metrics": requested_metrics,
                    "selected_metrics": metrics,
                    "supported_metrics": capabilities.get("supported_metrics") or [],
                    "default_metrics": capabilities.get("default_metrics") or [],
                    "route_choices": capabilities.get("route_choices") or [],
                    "catalog_entries": capabilities.get("catalog_entries") or [],
                    "status": "failed",
                    "can_run_now": False,
                    "blocking_issues": [issue],
                }
            )
            continue

        prefixed = [
            f"{dataset_name}:{issue}" if dataset_name else issue
            for issue in plan.get("blocking_issues") or []
        ]
        blocking_issues.extend(prefixed)
        scrubbed_plan = _scrubbed_plan(plan)
        setup_commands.extend(_maintainer_setup_commands(plan))
        node_blockers.extend(_node_environment_blockers(plan))
        dataset_plans.append(
            {
                "dataset": dataset_name,
                "task": task,
                "engine_task": engine_task,
                "language": language,
                "requested_metrics": requested_metrics,
                "selected_metrics": metrics,
                "supported_metrics": capabilities.get("supported_metrics") or [],
                "default_metrics": capabilities.get("default_metrics") or [],
                "route_choices": capabilities.get("route_choices") or [],
                "catalog_entries": capabilities.get("catalog_entries") or [],
                "status": plan.get("status"),
                "can_run_now": bool(plan.get("can_run_now")),
                "selected_routes": scrubbed_plan.get("selected_routes") or [],
                "plan": scrubbed_plan,
            }
        )

    payload = {
        "schema": "sure.harness.evaluation_route_plan.v1",
        "generated_at": _utc_now(),
        "input_path": str(input_path),
        "engine": {
            "source": engine_source,
            "engine_root": str(engine_root),
            "commit": _engine_commit(engine_root),
            "runtime": evaluation_runtime,
        },
        "datasets": dataset_plans,
        "can_run_now": not blocking_issues,
        "blocking_issues": blocking_issues,
        "setup_commands": _dedupe(setup_commands),
        "node_environment_blockers": _dedupe_blockers(node_blockers),
    }
    if output_path is not None:
        _write_json(output_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve sure-evaluation route/env readiness")
    parser.add_argument("--input", required=True, help="Path to artifacts/eval_input_resolved.json")
    parser.add_argument("--output", help="Write evaluation_route_plan.json")
    parser.add_argument("--evaluation-engine-root")
    parser.add_argument("--fail-on-blocked", action="store_true")
    args = parser.parse_args()

    payload = build_route_plan(
        Path(args.input).resolve(),
        output_path=Path(args.output).resolve() if args.output else None,
        evaluation_engine_root=args.evaluation_engine_root,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if args.fail_on_blocked and not payload["can_run_now"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
