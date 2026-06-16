#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xforge_sure_bridge.bridge import BridgeError, load_manifest, process_dataset_manifest, write_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert an XForge raw dataset manifest to SURE JSONL")
    parser.add_argument("--manifest", required=True, help="XForge dataset processing manifest JSON")
    parser.add_argument("--output", required=True, help="Output SURE JSONL path")
    parser.add_argument("--summary", help="Optional processing summary JSON")
    args = parser.parse_args()

    try:
        summary = process_dataset_manifest(load_manifest(args.manifest), args.output)
        if args.summary:
            write_summary(args.summary, summary)
    except BridgeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
