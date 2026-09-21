#!/usr/bin/env python3
"""Regression tests for the standalone sure-evaluation engine resolver."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import resolve_evaluation_engine
from resolve_evaluation_engine import _smoke_describe


class SmokeEnvironmentTest(unittest.TestCase):
    """git_environment keeps the caller's PYTHONPATH, so the engine src must be prepended to it."""

    def test_pythonpath_is_joined_with_the_platform_separator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            captured: dict[str, object] = {}

            def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
                captured.update(kwargs)
                return subprocess.CompletedProcess(command, 0, "{}", "")

            with mock.patch.dict(os.environ, {"PYTHONPATH": "caller-entry"}), mock.patch.object(
                resolve_evaluation_engine.subprocess, "run", fake_run
            ):
                result = _smoke_describe(root, "ASR", "en", "wer")

        self.assertTrue(result["ok"])
        environment = captured["env"]
        assert isinstance(environment, dict)
        self.assertEqual(
            environment["PYTHONPATH"].split(os.pathsep),
            [str(root / "src"), "caller-entry"],
        )


if __name__ == "__main__":
    unittest.main()
