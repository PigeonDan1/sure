#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xforge_sure_bridge.bridge import BridgeError, collect_dataset_manifest, load_manifest, write_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect an XForge dataset resource into local raw storage")
    parser.add_argument("--manifest", required=True, help="XForge dataset manifest JSON")
    parser.add_argument("--raw-root", required=True, help="Output raw dataset directory")
    parser.add_argument("--summary", help="Optional collection summary JSON")
    args = parser.parse_args()

    try:
        summary = collect_dataset_manifest(load_manifest(args.manifest), args.raw_root)
        if args.summary:
            write_summary(args.summary, summary)
    except BridgeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
