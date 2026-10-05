#!/usr/bin/env python3
"""Explicit slash-command options must survive input materialization."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from check_model_input import explicit_option_mismatch


class InputOptionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        (self.run_dir / "run.json").write_text(
            json.dumps({"args": "model_input_path=handoff.yaml package=none device=cuda skip_download=true force_repair=true"}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_rejects_dropped_device(self) -> None:
        mismatch = explicit_option_mismatch(self.run_dir, {
            "package_profile": "none", "device": "auto", "skip_download": True, "force_repair": True,
        })
        self.assertIn("device='auto' disagrees", mismatch or "")

    def test_rejects_dropped_repair_flags(self) -> None:
        mismatch = explicit_option_mismatch(self.run_dir, {
            "package_profile": "none", "device": "cuda", "skip_download": False, "force_repair": True,
        })
        self.assertIn("skip_download=False disagrees", mismatch or "")

    def test_accepts_forwarded_options(self) -> None:
        self.assertIsNone(explicit_option_mismatch(self.run_dir, {
            "package_profile": "none", "device": "cuda", "skip_download": True, "force_repair": True,
        }))

    def test_accepts_hook_style_flags_and_values(self) -> None:
        (self.run_dir / "run.json").write_text(json.dumps({
            "args": "handoff.yaml --package none --device cpu --skip_download --force_repair",
        }), encoding="utf-8")
        self.assertIsNone(explicit_option_mismatch(self.run_dir, {
            "package_profile": "none", "device": "cpu", "skip_download": True, "force_repair": True,
        }))
        mismatch = explicit_option_mismatch(self.run_dir, {
            "package_profile": "none", "device": "auto", "skip_download": True, "force_repair": True,
        })
        self.assertIn("device='auto' disagrees", mismatch or "")

    def test_package_profile_alias_has_hook_precedence(self) -> None:
        (self.run_dir / "run.json").write_text(json.dumps({
            "args": "package=docker-local package_profile=none device=cpu",
        }), encoding="utf-8")
        self.assertIsNone(explicit_option_mismatch(self.run_dir, {
            "package_profile": "none", "device": "cpu",
        }))

    def test_invalid_boolean_is_rejected_by_materializer(self) -> None:
        (self.run_dir / "run.json").write_text(json.dumps({
            "args": "package=none skip_download=typo",
        }), encoding="utf-8")
        result = self.materialize()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("skip_download", result.stderr)
        self.assertFalse((self.run_dir / "artifacts" / "model_input_resolved.json").exists())

    def test_conflicting_helper_option_is_rejected_before_writing(self) -> None:
        result = self.materialize("--device", "cpu")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("device", result.stderr)
        self.assertFalse((self.run_dir / "artifacts" / "model_input_resolved.json").exists())

    def materialize(self, *flags: str) -> subprocess.CompletedProcess[str]:
        model_input = self.run_dir / "handoff.yaml"
        model_input.write_text(
            "model_id: example/demo\n"
            "model_name: example__demo\n"
            "task_type: sd\n"
            "deployment_type: local\n"
            "repo:\n  url: https://example.invalid/demo\n",
            encoding="utf-8",
        )
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent / "materialize_onboard_inputs.py"),
                "--model-input-path", str(model_input),
                "--run-dir", str(self.run_dir),
                "--repo-root", str(self.run_dir),
                *flags,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def test_materializer_recovers_omitted_helper_options(self) -> None:
        result = self.materialize()
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        resolved = json.loads(
            (self.run_dir / "artifacts" / "model_input_resolved.json").read_text(encoding="utf-8")
        )
        self.assertEqual(resolved["package_profile"], "none")
        self.assertEqual(resolved["device"], "cuda")
        self.assertTrue(resolved["skip_download"])
        self.assertTrue(resolved["force_repair"])
        self.assertIsNone(explicit_option_mismatch(self.run_dir, resolved))
        gate = subprocess.run([
            sys.executable,
            str(Path(__file__).parent / "check_model_input.py"),
            "--run-dir", str(self.run_dir),
            "--produces", str(self.run_dir / "artifacts" / "model_input_resolved.json"),
        ], capture_output=True, text=True, timeout=30)
        self.assertEqual(gate.returncode, 0, gate.stderr or gate.stdout)

    def test_materializer_recovers_space_separated_flags(self) -> None:
        (self.run_dir / "run.json").write_text(json.dumps({
            "args": "handoff.yaml --package none --device cpu --skip_download --force_repair",
        }), encoding="utf-8")
        result = self.materialize()
        self.assertEqual(result.returncode, 0, result.stderr)
        resolved = json.loads((self.run_dir / "artifacts" / "model_input_resolved.json").read_text(encoding="utf-8"))
        self.assertEqual(resolved["device"], "cpu")
        self.assertTrue(resolved["skip_download"])
        self.assertTrue(resolved["force_repair"])
        self.assertIsNone(explicit_option_mismatch(self.run_dir, resolved))

    def test_explicit_false_flags_are_preserved(self) -> None:
        (self.run_dir / "run.json").write_text(json.dumps({
            "args": "package=none device=cpu skip_download=off force_repair=no",
        }), encoding="utf-8")
        result = self.materialize()
        self.assertEqual(result.returncode, 0, result.stderr)
        resolved = json.loads((self.run_dir / "artifacts" / "model_input_resolved.json").read_text(encoding="utf-8"))
        self.assertFalse(resolved["skip_download"])
        self.assertFalse(resolved["force_repair"])
        self.assertIsNone(explicit_option_mismatch(self.run_dir, resolved))

    def test_absent_options_keep_previous_defaults(self) -> None:
        (self.run_dir / "run.json").write_text(json.dumps({"args": ""}), encoding="utf-8")
        result = self.materialize("--package-profile", "none")
        self.assertEqual(result.returncode, 0, result.stderr)
        resolved = json.loads((self.run_dir / "artifacts" / "model_input_resolved.json").read_text(encoding="utf-8"))
        self.assertEqual(resolved["device"], "auto")
        self.assertFalse(resolved["skip_download"])
        self.assertFalse(resolved["force_repair"])

    def test_last_repeated_option_wins_like_hook(self) -> None:
        (self.run_dir / "run.json").write_text(json.dumps({
            "args": "package=none device=cuda device=cpu",
        }), encoding="utf-8")
        result = self.materialize()
        self.assertEqual(result.returncode, 0, result.stderr)
        resolved = json.loads((self.run_dir / "artifacts" / "model_input_resolved.json").read_text(encoding="utf-8"))
        self.assertEqual(resolved["device"], "cpu")
        self.assertIsNone(explicit_option_mismatch(self.run_dir, resolved))


if __name__ == "__main__":
    unittest.main()
