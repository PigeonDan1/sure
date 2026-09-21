#!/usr/bin/env python3
"""Regression tests for the subprocess bridge in run_eval._run."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest import mock

from run_eval import _run


def _locale_text_pipes(encoding: str):
    """Pretend the host code page is `encoding` for every text pipe that names none.

    That is what `text=True` does on a non-UTF-8 Windows box, and forcing it
    here keeps the regression provable on a UTF-8 host too.
    """
    real_run = subprocess.run

    def run(*args, **kwargs):  # type: ignore[no-untyped-def]
        if (kwargs.get("text") or kwargs.get("universal_newlines")) and not kwargs.get("encoding"):
            kwargs["encoding"] = encoding
        return real_run(*args, **kwargs)

    return mock.patch("subprocess.run", run)


def _failing_child(escaped: str) -> str:
    """A child that names a non-ASCII path and fails, the way a gate script does.

    The snippet stays pure ASCII — the characters are \\u escapes the child
    itself decodes. _run echoes the command into its RuntimeError, so a literal
    here would satisfy the assertions without the child's output ever being
    decoded correctly.
    """
    return f"import sys; sys.stderr.write('artifact not found at {escaped}\\n'); sys.exit(1)"


class RunPipeEncodingTests(unittest.TestCase):
    """Both ends of the pipe must agree on UTF-8 or the failure is unreadable.

    Every _run caller discards the return value, so a child's diagnosis reaches
    the user only through this RuntimeError message.
    """

    def test_a_non_ascii_failure_survives_a_non_utf8_parent_encoding(self) -> None:
        with _locale_text_pipes("latin-1"):
            with self.assertRaises(RuntimeError) as caught:
                _run([sys.executable, "-c", _failing_child("\\u4e2d\\u6587 caf\\u00e9")])
        self.assertIn("中文 café", str(caught.exception))

    def test_a_non_ascii_failure_survives_a_caller_supplied_child_encoding(self) -> None:
        env = {**os.environ, "PYTHONIOENCODING": "latin-1"}
        with self.assertRaises(RuntimeError) as caught:
            _run([sys.executable, "-c", _failing_child("caf\\u00e9")], env=env)
        self.assertIn("café", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
