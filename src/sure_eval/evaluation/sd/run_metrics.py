"""CLI entry point for Speaker Diarization RTTM metrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from .metrics import run_sd_eval
except ImportError:
    from metrics import run_sd_eval


def run_sd_metrics(data: dict[str, Any]) -> dict[str, Any]:
    try:
        return run_sd_eval(data)
    except ModuleNotFoundError as exc:
        if exc.name == "meeteval":
            raise RuntimeError(
                "meeteval is required to evaluate SD metrics. Install it with `pip install meeteval`."
            ) from exc
        raise


def _existing_file(path: str, label: str) -> str:
    candidate = Path(path)
    if not candidate.is_file():
        raise argparse.ArgumentTypeError(f"{label} does not exist: {path}")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate Speaker Diarization DER from RTTM files.")
    parser.add_argument(
        "--ref-rttm",
        required=True,
        help="Reference RTTM file path.",
    )
    parser.add_argument(
        "--hyp-rttm",
        required=True,
        help="Hypothesis RTTM file path.",
    )
    parser.add_argument(
        "--output",
        help="Optional JSON output path.",
    )
    parser.add_argument(
        "--collar",
        type=float,
        default=0.25,
        help="DER collar in seconds. Default: 0.25.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        ref_rttm = _existing_file(args.ref_rttm, "Reference RTTM file")
        hyp_rttm = _existing_file(args.hyp_rttm, "Hypothesis RTTM file")
        result = run_sd_metrics(
            {
                "ref_file": ref_rttm,
                "hyp_file": hyp_rttm,
                "collar": args.collar,
            }
        )
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    except RuntimeError as exc:
        parser.exit(1, f"error: {exc}\n")

    output_text = json.dumps(result, indent=2)
    print(output_text)

    if args.output:
        Path(args.output).write_text(output_text + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
