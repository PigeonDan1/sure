#!/usr/bin/env python3
"""Pre-submission gate for vc submit parameters.

Standalone form of the precheck that scripts/run_vc_execution.py enforces
before every real submission: image existence (local docker, then the vc
fake-queue probe), partition membership, container path visibility, image
venv layout, and resource argument shape. exit 0 = ready; exit 1 = the
stderr report explains each failure and lists real candidates.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sure_eval.agent import vc_precheck  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--image-source", default="cli_override")
    parser.add_argument("--partition", required=True)
    parser.add_argument("--memory", required=True)
    parser.add_argument("--gpus", type=int, default=1)
    parser.add_argument("--cpus", type=int, default=4)
    parser.add_argument("--volume", required=True)
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--expected-venv", default="")
    parser.add_argument("--output")
    args = parser.parse_args()

    results = vc_precheck.run_precheck(
        image=args.image,
        image_source=args.image_source,
        partition=args.partition,
        memory=args.memory,
        gpus=args.gpus,
        cpus=args.cpus,
        volume_mount=args.volume,
        paths=args.path,
        expected_venv=args.expected_venv,
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(vc_precheck.as_payload(results), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    report = vc_precheck.format_report(results)
    if vc_precheck.precheck_passed(results):
        print(report)
        return 0
    print(report, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
