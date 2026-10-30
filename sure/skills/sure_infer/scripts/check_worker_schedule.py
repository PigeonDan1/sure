"""Read-only validation of the controller's GPU leases and dataset sessions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def schedule_errors(product: Path, schedule: dict[str, Any], names: list[str],
                    budget: int, devices: list[str], strategy: str) -> list[str]:
    try:
        if (schedule["schema"] != "sure.infer.backfill_schedule.v1"
                or schedule["status"] != "completed" or schedule["strategy"] != strategy
                or schedule["gpu_budget"] != budget or schedule["gpu_devices"] != devices
                or len(devices) != budget or len(set(devices)) != budget
                or [row["dataset"] for row in schedule["datasets"]] != names):
            raise ValueError("schedule differs from the final run plan")
        plans = {}
        for row in schedule["datasets"]:
            name = row["dataset"]
            root = product / "parallel_inference" / name
            plan = json.loads((root / "plan.json").read_text())
            report = json.loads((root / "merge_report.json").read_text())
            if (plan["session"] != row["session"] or report["session"] != row["session"]
                    or plan["planned_workers"] != row["planned_workers"]
                    or plan["effective_workers"] != row["effective_workers"]
                    or report["effective_workers"] != row["effective_workers"]
                    or not 0 <= row["effective_workers"] <= row["planned_workers"] <= budget):
                raise ValueError(f"dataset session/Worker plan mismatch: {name}")
            keys = [key for shard in plan["shards"] for key in shard["keys"]]
            if (len(keys) != len(set(keys)) or len(keys) + plan["reused"] != plan["expected"]
                    or len(plan["shards"]) != plan["effective_workers"]
                    or [shard["gpu"] for shard in plan["shards"]] != plan["gpu_devices"]):
                raise ValueError(f"invalid shard partition: {name}")
            plans[name] = plan
        active: dict[str, list[str]] = {}
        started: set[str] = set()
        finished: set[str] = set()
        for event in schedule["events"]:
            name, lease = event["dataset"], event["gpu_devices"]
            if name not in plans or lease != plans[name]["gpu_devices"]:
                raise ValueError("event lease differs from dataset plan")
            if event["event"] == "started":
                occupied = {gpu for other in active.values() for gpu in other}
                if (name in started or len(lease) != plans[name]["effective_workers"]
                        or len(set(lease)) != len(lease) or not set(lease).issubset(devices)
                        or occupied.intersection(lease) or (strategy == "serial" and active)):
                    raise ValueError("duplicate dataset or overlapping GPU lease")
                started.add(name)
                active[name] = lease
            elif event["event"] == "finished":
                if name not in active or event["status"] != "completed":
                    raise ValueError("unmatched or failed dataset completion")
                del active[name]
                finished.add(name)
            else:
                raise ValueError("unknown scheduling event")
        if active or started != set(names) or finished != set(names):
            raise ValueError("incomplete schedule lifecycle")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"Invalid Worker schedule: {exc}"]
    return []
