#!/usr/bin/env python3
"""Publish container-local result metadata with stable host-side paths."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import yaml


def _replace(value: Any, source: str, published: str) -> Any:
    if isinstance(value, dict):
        return {key: _replace(item, source, published) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, source, published) for item in value]
    if isinstance(value, str) and (value == source or value.startswith(source + "/")):
        return published + value[len(source) :]
    return value


def _write_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _metadata_paths(root: Path) -> list[Path]:
    names = (
        "prepare_summary.json",
        "prediction_generation_status.json",
        "validation_payload.json",
        "evaluation_payload.json",
        "audio_evaluation_dataset_split.json",
        "evaluation_handoff.json",
        "protocol.yaml",
        "report.jsonl",
        "report_snapshot.md",
    )
    paths: set[Path] = set()
    for bundle_root in (root, root / "results"):
        paths.update(path for name in names if (path := bundle_root / name).is_file())
        paths.update(path for name in ("manifest.json", "conversion_manifest.json") if (path := bundle_root / "predictions" / name).is_file())
        for pattern in ("metrics/**/*.json", "sample_reports/**/*.jsonl", "evaluation_runs/**/*.json"):
            paths.update(path for path in bundle_root.glob(pattern) if path.is_file())
    return sorted(paths)


def finalize_bundle(run_dir: Path, published_run_dir: Path) -> list[str]:
    run_dir = run_dir.resolve()
    source = str(run_dir)
    published = str(published_run_dir)
    if not published_run_dir.is_absolute():
        raise ValueError("published run directory must be absolute")
    changed: list[str] = []
    for path in _metadata_paths(run_dir):
        suffix = path.suffix.lower()
        original = path.read_text(encoding="utf-8")
        if suffix == ".json":
            payload = _replace(json.loads(original), source, published)
            rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        elif suffix == ".jsonl":
            rows = [json.loads(line) for line in original.splitlines() if line.strip()]
            rendered = "".join(
                json.dumps(_replace(row, source, published), ensure_ascii=False) + "\n"
                for row in rows
            )
        elif suffix == ".yaml":
            payload = _replace(yaml.safe_load(original), source, published)
            rendered = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        else:
            rendered = original.replace(source, published)
        if rendered != original:
            _write_atomic(path, rendered)
            changed.append(path.relative_to(run_dir).as_posix())
    evidence = {
        "schema": "sure.eval.artifact_path_localization.v1",
        "container_run_dir": source,
        "published_run_dir": published,
        "changed_artifacts": changed,
        "prediction_content_modified": False,
    }
    _write_atomic(
        run_dir / "artifact_path_localization.json",
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n",
    )
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--published-run-dir", required=True)
    args = parser.parse_args()
    changed = finalize_bundle(Path(args.run_dir), Path(args.published_run_dir))
    print(json.dumps({"changed_artifacts": changed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
