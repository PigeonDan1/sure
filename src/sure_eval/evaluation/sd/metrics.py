"""Standalone SD evaluation extracted from the provided Evaluator.sd_eval branch.

Core calculation is intentionally kept the same as the original:
- load reference RTTM and hypothesis RTTM with meeteval.io.load
- compute DER with meeteval.der.dscore(..., collar=collar)
- print per-session error_rate / missed / false alarm / speaker error
- return the simple arithmetic mean of per-session error_rate values and num_sessions
"""

from __future__ import annotations

import argparse
from typing import Any, Dict


def run_sd_eval(data: Dict[str, Any]) -> Dict[str, Any]:
    import meeteval

    ref_rttm = data["ref_file"]
    hyp_rttm = data["hyp_file"]
    collar = data.get("collar", 0.25)

    print(f"[SD] Running DER evaluation with collar={collar}s")
    ref = meeteval.io.load(ref_rttm)
    hyp = meeteval.io.load(hyp_rttm)
    result_der = meeteval.der.dscore(ref, hyp, collar=collar)

    total_error_rate = 0
    total_sessions = 0
    for session, der in result_der.items():
        print(f"DER for {session}: {float(der.error_rate):.4f} "
            f"(missed: {float(der.missed_speaker_time):.4f}, "
            f"fa: {float(der.falarm_speaker_time):.4f}, "
            f"ser: {float(der.speaker_error_time):.4f})")
        total_error_rate += float(der.error_rate)
        total_sessions += 1

    avg_der = total_error_rate / total_sessions if total_sessions > 0 else 0
    print(f"[SD] Average DER: {avg_der:.4f}")

    return {
        "der": avg_der,
        "num_sessions": total_sessions
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone SD DER evaluation using the original sd_eval process.")
    parser.add_argument("--ref-file", required=True, help="Reference RTTM file path.")
    parser.add_argument("--hyp-file", required=True, help="Hypothesis RTTM file path.")
    parser.add_argument("--collar", type=float, default=0.25, help="DER collar in seconds. Default follows original code: 0.25.")
    args = parser.parse_args()

    run_sd_eval({
        "ref_file": args.ref_file,
        "hyp_file": args.hyp_file,
        "collar": args.collar,
    })


if __name__ == "__main__":
    main()
