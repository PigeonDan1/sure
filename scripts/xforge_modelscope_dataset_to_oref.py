#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modelscope.hub.snapshot_download import snapshot_download

from xforge_sure_bridge.bridge import (
    BridgeError,
    process_dataset_manifest_to_oref,
    write_oref_registry_entry,
    write_summary,
)
from xforge_sure_bridge.modelscope_fetch import (
    build_selected_candidate,
    emit_selected_resource_artifacts,
    emit_sure_integration_plan,
)
from xforge_sure_bridge.modelscope_watcher import slugify


def _first_csv(snapshot_dir: Path, explicit_csv: str | None) -> Path:
    if explicit_csv:
        path = Path(explicit_csv)
        if not path.is_absolute():
            path = snapshot_dir / path
        if not path.exists():
            raise BridgeError(f"CSV file does not exist: {path}")
        return path
    csv_files = sorted(snapshot_dir.glob("*.csv"))
    if not csv_files:
        raise BridgeError(f"no CSV metadata file found in snapshot: {snapshot_dir}")
    return csv_files[0]


def _write_raw_jsonl_from_csv(
    csv_path: Path,
    raw_root: Path,
    audio_field: str,
    text_field: str,
    metadata_field: str | None,
    language: str,
) -> tuple[Path, int]:
    raw_root.mkdir(parents=True, exist_ok=True)
    output = raw_root / "samples.jsonl"
    rows = 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as src, output.open("w", encoding="utf-8") as dst:
        reader = csv.DictReader(src)
        if not reader.fieldnames:
            raise BridgeError(f"CSV has no header: {csv_path}")
        missing = [field for field in (audio_field, text_field) if field not in reader.fieldnames]
        if missing:
            raise BridgeError(f"CSV missing field(s): {', '.join(missing)}")
        for row in reader:
            audio = row.get(audio_field, "").strip()
            text = row.get(text_field, "").strip()
            if not audio:
                continue
            record = {
                "id": Path(audio).stem,
                "audio": audio,
                "text": text,
                "language": language,
            }
            if metadata_field and metadata_field in row:
                record["metadata"] = row.get(metadata_field, "").strip()
            dst.write(json.dumps(record, ensure_ascii=False) + "\n")
            rows += 1
    return output, rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Download a ModelScope dataset and materialize local OREF layout")
    parser.add_argument("--id", required=True, help="ModelScope dataset id, e.g. damotest/dataTangDemo")
    parser.add_argument("--task", required=True, help="SURE task, e.g. asr")
    parser.add_argument("--name", help="Display name")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--sure-name", help="OREF/SURE dataset name")
    parser.add_argument("--work-root", default="data/xforge_oref_smoke")
    parser.add_argument("--datasets-root")
    parser.add_argument("--oref-config")
    parser.add_argument("--manifest-dir")
    parser.add_argument("--handoff-dir")
    parser.add_argument("--sure-plan-dir")
    parser.add_argument("--cache-dir", help="Writable ModelScope cache directory")
    parser.add_argument("--csv", help="CSV metadata file inside the dataset snapshot")
    parser.add_argument("--audio-field", default="Input:FILE")
    parser.add_argument("--text-field", default="Info:FILE")
    parser.add_argument("--metadata-field", default="Metadata:FILE")
    parser.add_argument("--allow-missing-audio", action="store_true")
    parser.add_argument("--summary", help="Optional summary JSON")
    args = parser.parse_args()

    try:
        work_root = Path(args.work_root)
        work_root.mkdir(parents=True, exist_ok=True)
        internal_root = work_root / "_xforge_internal"
        datasets_root = Path(args.datasets_root) if args.datasets_root else work_root
        oref_config = Path(args.oref_config) if args.oref_config else work_root / "oref_datasets.yaml"
        manifest_dir = Path(args.manifest_dir) if args.manifest_dir else internal_root / "artifacts" / "manifests"
        handoff_dir = Path(args.handoff_dir) if args.handoff_dir else internal_root / "artifacts" / "handoff"
        sure_plan_dir = Path(args.sure_plan_dir) if args.sure_plan_dir else internal_root / "artifacts" / "sure_plans"
        stem = slugify(args.id)
        snapshot_root = internal_root / "modelscope_raw" / stem
        cache_dir = Path(args.cache_dir) if args.cache_dir else internal_root / "modelscope_cache"
        snapshot_root.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)

        snapshot_path = Path(
            snapshot_download(
                repo_id=args.id,
                repo_type="dataset",
                local_dir=str(snapshot_root),
                cache_dir=str(cache_dir),
                max_workers=4,
            )
        )
        csv_path = _first_csv(snapshot_path, args.csv)
        raw_root = internal_root / "raw_jsonl" / stem
        raw_jsonl, raw_rows = _write_raw_jsonl_from_csv(
            csv_path=csv_path,
            raw_root=raw_root,
            audio_field=args.audio_field,
            text_field=args.text_field,
            metadata_field=args.metadata_field,
            language=args.language,
        )

        sure_name = args.sure_name or stem
        task = args.task.upper()
        manifest = {
            "resource_type": "dataset",
            "dataset_id": args.id,
            "sure_name": sure_name,
            "task": task,
            "language": args.language,
            "raw_root": str(raw_root),
            "raw_jsonl": raw_jsonl.name,
            "field_mapping": {"key": "id", "path": "audio", "target": "text"},
            "source": {"provider": "modelscope", "id": args.id},
        }

        candidate = build_selected_candidate(
            resource_type="dataset",
            task=args.task,
            resource_id=args.id,
            name=args.name,
            language=args.language,
        )
        artifacts = emit_selected_resource_artifacts(candidate, manifest_dir, handoff_dir)
        plan_path = emit_sure_integration_plan(
            resource_type="dataset",
            manifest={**manifest, "raw_root": str(raw_root)},
            manifest_path=artifacts["manifest_path"],
            handoff_path=artifacts["handoff_path"],
            sure_plan_dir=sure_plan_dir,
            model_dir=Path("src/sure_eval/models") / stem,
            sure_dataset_dir=datasets_root,
        )

        oref_summary = process_dataset_manifest_to_oref(
            manifest,
            datasets_root,
            allow_missing_audio=args.allow_missing_audio,
        )
        registry_summary = write_oref_registry_entry(
            oref_config,
            oref_summary["sure_name"],
            oref_summary["registry_entry"],
        )
        payload = {
            "provider": "modelscope",
            "resource_type": "dataset",
            "resource_id": args.id,
            "task": args.task,
            "snapshot_path": str(snapshot_path),
            "csv_path": str(csv_path),
            "raw_jsonl": str(raw_jsonl),
            "raw_rows": raw_rows,
            "manifest_path": artifacts["manifest_path"],
            "handoff_path": artifacts["handoff_path"],
            "sure_plan_path": plan_path,
            "oref_processing_summary": oref_summary,
            "oref_registry_summary": registry_summary,
        }
        if args.summary:
            write_summary(args.summary, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
