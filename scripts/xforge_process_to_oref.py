#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xforge_sure_bridge.bridge import (
    BridgeError,
    load_manifest,
    process_dataset_manifest_to_oref,
    write_oref_registry_entry,
    write_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert an XForge raw dataset manifest to local OREF layout")
    parser.add_argument("--manifest", required=True, help="XForge dataset processing manifest JSON")
    parser.add_argument("--datasets-root", default="data/datasets", help="Root directory for OREF local datasets")
    parser.add_argument("--oref-config", default="config/oref_datasets.yaml", help="OREF dataset registry YAML")
    parser.add_argument("--update-registry", action="store_true", help="Add/update the dataset in the OREF registry")
    parser.add_argument(
        "--allow-missing-audio",
        action="store_true",
        help="Write OREF placeholder audio paths when raw audio files are unavailable",
    )
    parser.add_argument("--summary", help="Optional processing summary JSON")
    args = parser.parse_args()

    try:
        summary = process_dataset_manifest_to_oref(
            load_manifest(args.manifest),
            args.datasets_root,
            allow_missing_audio=args.allow_missing_audio,
        )
        if args.update_registry:
            summary["oref_registry_summary"] = write_oref_registry_entry(
                args.oref_config,
                summary["sure_name"],
                summary["registry_entry"],
            )
        if args.summary:
            write_summary(args.summary, summary)
    except BridgeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
