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
    # A child of the run directory is separated from it by whichever separator
    # the writing platform uses; anything else after the prefix is a different
    # component and must be left alone.
    if isinstance(value, str) and value.startswith(source) and value[len(source) :][:1] in ("", "/", "\\"):
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


def finalize_bundle(run_dir: Path, published_run_dir: Path, model_dir: Path | None = None) -> list[str]:
    run_dir = run_dir.resolve()
    source = str(run_dir)
    published = str(published_run_dir)
    if not published_run_dir.is_absolute():
        raise ValueError("published run directory must be absolute")
    if model_dir is not None:
        inventory_path = model_dir / "artifacts" / "runtime_inventory.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        status = json.loads((run_dir / "prediction_generation_status.json").read_text(encoding="utf-8"))
        canonical_name = str((inventory.get("model") or {}).get("name") or "")
        if not canonical_name or inventory.get("status") != "ready":
            raise ValueError("model identity requires a ready approved runtime inventory")
        if status.get("model_name") != canonical_name:
            raise ValueError(
                "prediction model identity disagrees with the approved model: "
                f"prediction_generation_status.json says {status.get('model_name')!r}, runtime_inventory.json says {canonical_name!r}"
            )
        manifest_path = run_dir / "predictions" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("model_name") != canonical_name:
            raise ValueError(
                "prediction manifest identity disagrees with the approved model: "
                f"predictions/manifest.json says {manifest.get('model_name')!r}, runtime_inventory.json says {canonical_name!r}"
            )
        recorded_runtime = (status.get("runtime") or {}).get("runtime_inventory") or {}
        policy = inventory.get("policy") if isinstance(inventory.get("policy"), dict) else {}
        if policy.get("eval_runtime") == "container_only":
            expected_runtime = inventory.get("container_runtime") or {}
            recorded = recorded_runtime.get("container_runtime") or {}
            identity_fields = ("target_image_ref",)
        elif policy.get("eval_runtime") == "python":
            expected_runtime = inventory.get("model_runtime") or {}
            recorded = recorded_runtime.get("model_runtime") or {}
            identity_fields = ("runtime_id", "lock_sha256", "manifest_sha256")
        else:
            raise ValueError(
                "model identity requires a supported approved runtime: "
                f"runtime_inventory.json policy.eval_runtime is {policy.get('eval_runtime')!r}, expected 'container_only' or 'python'"
            )
        for identity_key in identity_fields:
            expected_value = expected_runtime.get(identity_key)
            if expected_value and recorded.get(identity_key) != expected_value:
                raise ValueError(
                    "prediction runtime identity disagrees with the approved model: "
                    f"{identity_key} is {recorded.get(identity_key)!r} in prediction_generation_status.json, {expected_value!r} in runtime_inventory.json"
                )
        protocol = yaml.safe_load((run_dir / "protocol.yaml").read_text(encoding="utf-8")) or {}
        protocol_name = (protocol.get("model") or {}).get("model_name")
        if protocol_name != canonical_name:
            raise ValueError(
                "protocol model identity disagrees with the approved model: "
                f"protocol.yaml says {protocol_name!r}, runtime_inventory.json says {canonical_name!r}"
            )
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
    parser.add_argument("--model-dir", type=Path)
    args = parser.parse_args()
    changed = finalize_bundle(Path(args.run_dir), Path(args.published_run_dir), args.model_dir)
    print(json.dumps({"changed_artifacts": changed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
