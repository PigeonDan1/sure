#!/usr/bin/env python3
"""Tests for the smoke gate guard that catches dropped dataset @version suffixes."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_smoke

AISHELL = "/srv/sure/datasets/group/store/ds_pool/example-asr-test"


class FindDroppedVersionTest(unittest.TestCase):
    def test_detects_dropped_suffix(self) -> None:
        got = run_smoke._find_dropped_version([AISHELL], [f"{AISHELL}@v1.0.2"])
        self.assertEqual(got, (AISHELL, f"{AISHELL}@v1.0.2"))

    def test_full_match_is_clean(self) -> None:
        self.assertIsNone(run_smoke._find_dropped_version([f"{AISHELL}@v1.0.2"], [f"{AISHELL}@v1.0.2"]))

    def test_unversioned_user_input_is_clean(self) -> None:
        self.assertIsNone(run_smoke._find_dropped_version([AISHELL], [AISHELL]))

    def test_unrelated_candidate_is_clean(self) -> None:
        self.assertIsNone(
            run_smoke._find_dropped_version(["demo_speech_zh_test__v1.0.2"], [f"{AISHELL}@v1.0.2"])
        )

    def test_trailing_slash_candidate_still_detected(self) -> None:
        got = run_smoke._find_dropped_version([f"{AISHELL}/"], [f"{AISHELL}@v1.0.2"])
        self.assertEqual(got, (f"{AISHELL}/", f"{AISHELL}@v1.0.2"))

    def test_empty_inputs_are_clean(self) -> None:
        self.assertIsNone(run_smoke._find_dropped_version([], [f"{AISHELL}@v1.0.2"]))
        self.assertIsNone(run_smoke._find_dropped_version([AISHELL], []))

    def test_prediction_name_uses_canonical_dataset_identity(self) -> None:
        eval_input = {
            "datasets": [
                {
                    "name": "demo_speech_zh_test__v1.0.2",
                    "source_root": f"{AISHELL}@v1.0.2",
                }
            ]
        }
        self.assertEqual(
            run_smoke._canonical_dataset(eval_input, f"{AISHELL}@v1.0.2"),
            "demo_speech_zh_test__v1.0.2",
        )


class SmokeGuardMainTest(unittest.TestCase):
    def test_main_fails_before_entrypoint_when_version_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            marker = Path(tmp) / "entrypoint_ran.marker"
            entrypoint = Path(tmp) / "entrypoint.sh"
            entrypoint.write_text(f"#!/usr/bin/env bash\ntouch '{marker}'\n", encoding="utf-8")
            (artifacts / "execution_surface.json").write_text(
                json.dumps(
                    {
                        "entrypoint": str(entrypoint),
                        "env": {"DATASET": AISHELL},
                        "resolved_inputs": {
                            "dataset": AISHELL,
                            "datasets": [AISHELL],
                            "run_dir": str(Path(tmp) / "eval_run"),
                        },
                    }
                ),
                encoding="utf-8",
            )
            (artifacts / "eval_input_resolved.json").write_text(
                json.dumps({"user_input": {"datasets": [f"{AISHELL}@v1.0.2"]}}),
                encoding="utf-8",
            )
            produces = artifacts / "smoke_test_result.json"
            old_argv = sys.argv
            try:
                sys.argv = ["run_smoke.py", "--run-dir", str(run_dir), "--produces", str(produces)]
                rc = run_smoke.main()
            finally:
                sys.argv = old_argv
            self.assertEqual(rc, 1)
            self.assertFalse(marker.exists(), "entrypoint must not run when the guard fires")
            payload = json.loads(produces.read_text(encoding="utf-8"))
            self.assertFalse(payload["smoke_passed"])
            self.assertTrue(
                any("@version" in failure for failure in payload["failures"]),
                f"failures should name the dropped @version suffix: {payload['failures']}",
            )


if __name__ == "__main__":
    unittest.main()
