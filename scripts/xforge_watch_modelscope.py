#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xforge_sure_bridge.catalog import XForgeCatalog
from xforge_sure_bridge.modelscope_watcher import ModelScopeWatcher, process_candidates


def _load_candidates(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("candidates", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise ValueError(f"cannot load candidate list from {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Watch ModelScope for new task-relevant models/datasets and emit XForge-SURE bridge artifacts"
    )
    parser.add_argument("--task", default="ASR", help="Task keyword or ModelScope task tag")
    parser.add_argument("--since-days", type=int, default=1, help="Only keep resources updated within this many days")
    parser.add_argument("--max-items", type=int, default=20, help="Maximum resources to process")
    parser.add_argument(
        "--resource",
        choices=["model", "dataset", "all"],
        default="all",
        help="Resource type to watch",
    )
    parser.add_argument(
        "--catalog",
        default="data/artifacts/xforge/modelscope_catalog.json",
        help="Persistent watcher catalog JSON",
    )
    parser.add_argument(
        "--manifest-dir",
        default="data/artifacts/xforge/modelscope/manifests",
        help="Directory for emitted bridge manifests",
    )
    parser.add_argument(
        "--handoff-dir",
        default="data/artifacts/xforge/modelscope/handoff",
        help="Directory for SURE handoff events",
    )
    parser.add_argument("--api-base", default=None, help="Override ModelScope API base URL")
    parser.add_argument(
        "--candidates-json",
        help="Use local candidate JSON instead of querying ModelScope, for offline/XForge-discover handoff",
    )
    parser.add_argument("--no-manifests", action="store_true", help="Only update catalog; do not emit manifests")
    parser.add_argument("--no-handoff", action="store_true", help="Do not emit SURE handoff events")
    parser.add_argument("--summary", help="Optional summary JSON output")
    args = parser.parse_args()

    try:
        if args.candidates_json:
            candidates = _load_candidates(Path(args.candidates_json))
        else:
            resource_types = ["model", "dataset"] if args.resource == "all" else [args.resource]
            watcher = ModelScopeWatcher(api_base=args.api_base) if args.api_base else ModelScopeWatcher()
            candidates = watcher.search(
                task=args.task,
                resource_types=resource_types,
                since_days=args.since_days,
                max_items=args.max_items,
            )

        catalog = XForgeCatalog(args.catalog)
        summary = process_candidates(
            candidates=candidates,
            catalog=catalog,
            manifest_dir=args.manifest_dir,
            handoff_dir=args.handoff_dir,
            emit_manifests=not args.no_manifests,
            emit_handoff=not args.no_handoff,
        )
        summary["task"] = args.task
        summary["catalog"] = str(Path(args.catalog))

        if args.summary:
            summary_path = Path(args.summary)
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
