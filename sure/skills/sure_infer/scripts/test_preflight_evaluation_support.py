#!/usr/bin/env python3
"""Tests for preflight_evaluation_support.py.

Every case runs the script in a subprocess: the engine capability lookup
inserts the engine's src/ at the front of sys.path, which cannot coexist
with the harness-local sure_eval package inside a shared test process.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
ENGINE_ROOT = REPO_ROOT / "sure" / "external" / "sure-evaluation"
SCRIPT = Path(__file__).resolve().parent / "preflight_evaluation_support.py"

REASON_UNSUPPORTED = (
    "evaluation package unsupported: sure-evaluation does not support the requested evaluation route"
)


def _payload(datasets, engine_root=ENGINE_ROOT) -> dict:
    engine = {"source": "submodule", "engine_root": str(engine_root)} if engine_root else None
    return {
        "schema": "sure.eval.input_resolved.v1",
        "datasets": datasets,
        "evaluation": {"backend": "external", "engine": engine},
    }


def _dataset(task: str, language: str, metrics: list[str]) -> dict:
    return {"name": "fixture-dataset", "task": task, "language": language, "default_metrics": metrics}


def _capabilities(task: str, language: str) -> dict:
    """Query engine capabilities in a clean helper process."""

    code = (
        "import json, sys;"
        "sys.path.insert(0, sys.argv[1]);"
        "from evaluation_capabilities import discover_engine_capabilities;"
        "print(json.dumps(discover_engine_capabilities(__import__('pathlib').Path(sys.argv[2]), sys.argv[3], sys.argv[4])))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(SCRIPT.parent), str(ENGINE_ROOT), task, language],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"capability probe failed: {result.stderr}")
    return json.loads(result.stdout)


def _run_preflight(payload: dict):
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "eval_input_resolved.json"
        output_path = Path(tmp) / "evaluation_preflight.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(input_path), "--output", str(output_path)],
            capture_output=True,
            text=True,
        )
        written = json.loads(output_path.read_text(encoding="utf-8")) if output_path.is_file() else None
        return result, written


@unittest.skipUnless(ENGINE_ROOT.is_dir(), "sure-evaluation submodule is not checked out")
class PreflightCliTests(unittest.TestCase):
    def test_supported_route_exits_zero_and_writes_artifact(self):
        metrics = _capabilities("ASR", "zh")["supported_metrics"]
        self.assertTrue(metrics, "engine must expose at least one ASR zh metric")
        result, written = _run_preflight(_payload([_dataset("ASR", "zh", [metrics[0]])]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(written)
        self.assertEqual(written["schema"], "sure.harness.evaluation_preflight.v1")
        self.assertTrue(written["supported"])
        self.assertEqual(written["reason_code"], "SUPPORTED")
        self.assertTrue(written["checks"][0]["supported"])

    def test_pipeline_id_is_accepted(self):
        rows = _capabilities("ASR", "zh")["catalog_entries"]
        pipeline_ids = [row["pipeline_id"] for row in rows if row.get("pipeline_id")]
        self.assertTrue(pipeline_ids, "catalog must expose at least one ASR zh pipeline_id")
        result, written = _run_preflight(_payload([_dataset("ASR", "zh", [pipeline_ids[0]])]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(written["supported"], written["checks"])

    def test_unsupported_metric_exits_three_with_fixed_reason(self):
        result, written = _run_preflight(_payload([_dataset("ASR", "zh", ["definitely_not_a_metric"])]))
        self.assertEqual(result.returncode, 3)
        self.assertIn(REASON_UNSUPPORTED, result.stderr)
        self.assertIsNotNone(written)
        self.assertFalse(written["supported"])
        self.assertEqual(written["reason_code"], "EVALUATION_PACKAGE_UNSUPPORTED")
        self.assertEqual(written["reason"], REASON_UNSUPPORTED)
        self.assertIn("No configured route found", written["checks"][0]["detail"])

    def test_unknown_task_is_unsupported(self):
        result, written = _run_preflight(_payload([_dataset("UNKNOWN", "zh", ["cer"])]))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(written["reason_code"], "EVALUATION_PACKAGE_UNSUPPORTED")
        self.assertIn("Unsupported evaluation task", written["checks"][0]["detail"])

    def test_unsupported_language_is_unsupported(self):
        result, written = _run_preflight(_payload([_dataset("ASR", "xx-nonexistent", ["cer"])]))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(written["reason_code"], "EVALUATION_PACKAGE_UNSUPPORTED")

    def test_missing_engine_skips_preflight(self):
        result, written = _run_preflight(_payload([_dataset("ASR", "zh", ["cer"])], engine_root=None))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(written["supported"])
        self.assertEqual(written["reason_code"], "PREFLIGHT_SKIPPED_ENGINE_UNAVAILABLE")

    def test_missing_input_exits_two(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", "/nonexistent/eval_input_resolved.json"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("resolved input not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
