#!/usr/bin/env python3
"""Generate and verify harness task capabilities from the pinned evaluation engine."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = ROOT / "sure" / "external" / "sure-evaluation"
TASKS_ROOT = ENGINE_ROOT / "src" / "sure_eval" / "evaluation" / "tasks"
PROFILE_PATH = ROOT / "sure" / "runtime" / "evaluation" / "harness-task-profiles.json"
OUTPUT_PATH = ROOT / "sure" / "runtime" / "evaluation" / "engine-capabilities.generated.json"
TS_OUTPUT_PATH = ROOT / "sure" / "runtime" / "evaluation" / "task-types.generated.ts"
TASK_SCHEMA_PATHS = (
    ROOT / "sure" / "skills" / "sure_feed" / "schemas" / "match_task_result.schema.json",
    ROOT / "sure" / "skills" / "sure_feed" / "schemas" / "model_input.schema.json",
    ROOT / "sure" / "skills" / "sure_onboard" / "schemas" / "classification.schema.json",
    ROOT / "sure" / "skills" / "sure_onboard" / "schemas" / "context_selection.schema.json",
    ROOT / "sure" / "skills" / "sure_onboard" / "schemas" / "model_input_resolved.schema.json",
)
SCHEMA = "sure.engine_capabilities.v1"
BRIDGE_REQUIRED_ROLES = {
    "text_pair": {"hyp", "ref"},
    "text_pair_prompt": {"hyp", "prompt_jsonl", "ref"},
    "sure_json": {"reference_jsonl", "sample_output"},
    "sv_trials": {"sample_output", "trial_manifest"},
}
AUDIO_SAMPLE_ENGINE_TASKS = {"se", "tse", "tts", "vc"}


def normalize_task(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected YAML object: {path}")
    return value


def engine_commit() -> str:
    git_file = ENGINE_ROOT / ".git"
    if not git_file.exists():
        return "unknown"
    result = subprocess.run(
        ["git", "-C", str(ENGINE_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def engine_tasks() -> dict[str, dict[str, Any]]:
    discovered: dict[str, dict[str, Any]] = {}
    for routes_path in sorted(TASKS_ROOT.glob("*/routes.yaml")):
        task_dir = routes_path.parent
        routes_doc = load_yaml(routes_path)
        manifest = load_yaml(task_dir / "manifest.yaml")
        engine_task = normalize_task(task_dir.name)
        contracts = manifest.get("input_contracts") or {}
        if not isinstance(contracts, dict):
            contracts = {}
        for raw_route in routes_doc.get("routes") or []:
            if not isinstance(raw_route, dict):
                continue
            public_task = normalize_task(str(raw_route.get("task_alias") or engine_task))
            contract_name = str(raw_route.get("input_contract") or "")
            contract = contracts.get(contract_name) or {}
            required_roles = list(contract.get("required_roles") or []) if isinstance(contract, dict) else []
            optional_roles = list(contract.get("optional_roles") or []) if isinstance(contract, dict) else []
            route = {
                "pipeline_id": str(raw_route.get("pipeline_id") or ""),
                "language": str(raw_route.get("language") or ("template" if routes_doc.get("language_sensitive") else "any")),
                "metric": str(raw_route.get("metric") or ""),
                "input_contract": contract_name,
                "required_roles": required_roles,
                "optional_roles": optional_roles,
            }
            item = discovered.setdefault(
                public_task,
                {
                    "engine_task": engine_task,
                    "language_sensitive": bool(routes_doc.get("language_sensitive", False)),
                    "routes": [],
                },
            )
            item["routes"].append(route)
    return discovered


def generated_payload() -> dict[str, Any]:
    profiles = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    if profiles.get("schema") != "sure.harness_task_profiles.v1":
        raise ValueError(f"Unsupported harness task profile schema: {profiles.get('schema')!r}")
    if (profiles.get("suites") or {}).get("speech_understanding", {}).get("membership") != "all_engine_tasks":
        raise ValueError("speech_understanding suite membership must be all_engine_tasks")
    configured = profiles.get("tasks") or {}
    discovered = engine_tasks()
    if set(configured) != set(discovered):
        missing = sorted(set(discovered) - set(configured))
        stale = sorted(set(configured) - set(discovered))
        raise ValueError(f"Harness profile drift: missing={missing}, stale={stale}")

    tasks: dict[str, Any] = {}
    for task in sorted(discovered):
        profile = dict(configured[task])
        engine = discovered[task]
        if profile.get("engine_task") != engine["engine_task"]:
            raise ValueError(
                f"{task}: profile engine_task={profile.get('engine_task')!r} "
                f"does not match engine {engine['engine_task']!r}"
            )
        bridge = str(profile.get("evaluation_bridge") or "")
        route_role_sets = [set(route["required_roles"]) for route in engine["routes"]]
        if bridge == "samples_jsonl":
            bridge_ready = engine["engine_task"] in AUDIO_SAMPLE_ENGINE_TASKS
        elif bridge in BRIDGE_REQUIRED_ROLES:
            required = BRIDGE_REQUIRED_ROLES[bridge]
            bridge_ready = any(required.issubset(roles) for roles in route_role_sets)
        else:
            bridge_ready = False
        if not bridge_ready:
            raise ValueError(
                f"{task}: evaluation_bridge={bridge!r} does not match engine route roles "
                f"{[sorted(roles) for roles in route_role_sets]}"
            )
        fixture_root = ROOT / str(profile.get("fixture_root") or "")
        requirements = {
            "engine_routes": bool(engine["routes"]),
            "evaluation_bridge": bridge_ready,
            "fixture_index": (ROOT / "fixtures" / "tasks" / task / "README.md").is_file(),
            "fixture_ground_truth": (fixture_root / "gt.jsonl").is_file(),
            "fixture_provenance": (fixture_root / "provenance.json").is_file(),
            "io_contract": bool(profile.get("io_contract", {}).get("primary_field")),
            "tool_name": bool(profile.get("tool_name")),
        }
        tasks[task] = {
            **engine,
            **profile,
            "metrics": sorted({route["metric"] for route in engine["routes"] if route["metric"]}),
            "required_roles": sorted(
                {role for route in engine["routes"] for role in route["required_roles"]}
            ),
            "readiness_requirements": requirements,
            "ready": all(requirements.values()),
        }

    return {
        "schema": SCHEMA,
        "engine": {
            "path": "sure/external/sure-evaluation",
            "commit": engine_commit(),
        },
        "aliases": profiles.get("aliases") or {},
        "tasks": tasks,
        "suites": {
            "speech_understanding": {
                "membership": sorted(tasks),
                "ready": all(item["ready"] for item in tasks.values()),
            }
        },
    }


def render_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def render_typescript(payload: dict[str, Any]) -> str:
    canonical = list(payload["tasks"])
    accepted = sorted({*canonical, *payload["aliases"], "speech_understanding"})
    return (
        "// Generated by scripts/sure-task-capabilities.py. Do not edit.\n"
        f"export const CANONICAL_TASK_TYPES: string[] = {json.dumps(canonical)};\n"
        f"export const TASK_TYPES: string[] = {json.dumps(accepted)};\n"
    )


def check_file(path: Path, expected: str) -> bool:
    actual = path.read_text(encoding="utf-8") if path.is_file() else ""
    if actual == expected:
        return True
    print(f"stale generated task capability file: {path.relative_to(ROOT)}", file=sys.stderr)
    return False


def accepted_tasks(payload: dict[str, Any]) -> set[str]:
    return {*payload["tasks"], *payload["aliases"], *payload["suites"]}


def task_enums(value: Any) -> list[list[str]]:
    found: list[list[str]] = []
    if isinstance(value, dict):
        enum = value.get("enum")
        if isinstance(enum, list) and "speech_understanding" in enum:
            found.append([str(item) for item in enum])
        for child in value.values():
            found.extend(task_enums(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(task_enums(child))
    return found


def check_task_schema_enums(payload: dict[str, Any]) -> bool:
    expected = accepted_tasks(payload)
    valid = True
    for path in TASK_SCHEMA_PATHS:
        document = json.loads(path.read_text(encoding="utf-8"))
        enums = task_enums(document)
        if len(enums) != 1 or set(enums[0]) != expected:
            actual = sorted(set(enums[0])) if enums else []
            print(
                f"stale task enum: {path.relative_to(ROOT)} "
                f"expected={sorted(expected)}, actual={actual}",
                file=sys.stderr,
            )
            valid = False
    return valid


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print the generated capability document")
    args = parser.parse_args()
    try:
        payload = generated_payload()
    except (OSError, ValueError, yaml.YAMLError, subprocess.CalledProcessError) as exc:
        print(f"task capability generation failed: {exc}", file=sys.stderr)
        return 1

    json_text = render_json(payload)
    ts_text = render_typescript(payload)
    if args.check:
        valid = check_file(OUTPUT_PATH, json_text) and check_file(TS_OUTPUT_PATH, ts_text)
        valid = check_task_schema_enums(payload) and valid
        if not valid:
            return 1
        not_ready = [task for task, item in payload["tasks"].items() if not item["ready"]]
        if not_ready:
            print(f"task capability check failed: not ready={not_ready}", file=sys.stderr)
            return 1
        print(f"ok   task capabilities: {len(payload['tasks'])} engine tasks ready")
    elif args.json:
        print(json_text, end="")
    else:
        OUTPUT_PATH.write_text(json_text, encoding="utf-8")
        TS_OUTPUT_PATH.write_text(ts_text, encoding="utf-8")
        print(f"updated {OUTPUT_PATH.relative_to(ROOT)}")
        print(f"updated {TS_OUTPUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
