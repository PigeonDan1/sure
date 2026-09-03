#!/usr/bin/env python3
"""Gate script for the SMOKE_TEST_UNIT.

The bounded smoke pass is a stage of scripts/infer_entrypoint.py, which
scripts/run_infer.py already ran when it wrote artifacts/execution_result.json.
This gate only reads that result and writes smoke_test_result.json: it launches
nothing. Called by the Sure hook with:
    python3 scripts/run_smoke.py --run-dir <runDir> --produces <abs>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from infer_entrypoint import STAGES

# A failure in one of these stages means the smoke pass never proved the model
# answers; a failure later (generate, validate, ...) happened after it did.
STAGES_THROUGH_SMOKE = STAGES[: STAGES.index("generate")]


def _excerpt(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _log_excerpt(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    path = Path(value)
    if not path.is_file():
        return ""
    return _excerpt(path.read_text(encoding="utf-8", errors="replace"))


def smoke_result(execution_result: dict[str, Any]) -> dict[str, Any]:
    status = str(execution_result.get("job_status") or "")
    exit_code = execution_result.get("exit_code")
    failed_stage = str(execution_result.get("failed_stage") or "")
    rows = execution_result.get("datasets") if isinstance(execution_result.get("datasets"), list) else []
    sample_count = sum(int(row.get("generated") or 0) for row in rows if isinstance(row, dict))
    failures: list[str] = []
    if status == "running":
        failures.append("inference is still running; wait for run_infer.py to finish")
    elif status == "succeeded":
        pass
    elif status in {"failed", "partial"}:
        if failed_stage in STAGES_THROUGH_SMOKE:
            failures.append(f"inference failed in stage {failed_stage!r} before the smoke pass proved the model answers")
        elif not failed_stage:
            failures.append("inference failed before any stage was reported; see the execution logs")
    else:
        failures.append(f"execution_result.json has invalid job_status: {status!r}")
    if status == "succeeded" and sample_count < 1:
        failures.append("no predictions were generated")
    return {
        "smoke_passed": not failures,
        "sample_count": sample_count,
        "exit_code": int(exit_code) if isinstance(exit_code, int) else 1,
        "stdout_excerpt": _log_excerpt(execution_result.get("stdout_log")),
        "stderr_excerpt": _log_excerpt(execution_result.get("stderr_log")),
        "failures": failures,
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True, help="absolute path to smoke_test_result.json")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    produces = Path(args.produces)
    result_path = run_dir / "artifacts" / "execution_result.json"
    execution_result = _read_json(result_path)
    if execution_result is None:
        message = f"execution_result.json not found or invalid at {result_path}; run scripts/run_infer.py first"
        _write(
            produces,
            {
                "smoke_passed": False,
                "sample_count": 0,
                "exit_code": 1,
                "stdout_excerpt": "",
                "stderr_excerpt": "",
                "failures": [message],
            },
        )
        print(message, file=sys.stderr)
        return 1

    payload = smoke_result(execution_result)
    _write(produces, payload)
    if not payload["smoke_passed"]:
        print("smoke_test gate failed.\n  - " + "\n  - ".join(payload["failures"]), file=sys.stderr)
        return 1
    print("smoke_test OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
