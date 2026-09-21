#!/usr/bin/env python3
"""Regression tests for the imageio-ffmpeg fallback on the evaluation PATH."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

from run_eval import _ensure_ffmpeg


class EnsureFfmpegTest(unittest.TestCase):
    """The staged binary must be found by the same lookup that decides whether to stage it."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        vendored = self.root / "vendor" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        vendored.parent.mkdir(parents=True)
        vendored.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        vendored.chmod(0o755)
        module = types.ModuleType("imageio_ffmpeg")
        module.get_ffmpeg_exe = lambda: str(vendored)  # type: ignore[attr-defined]
        sys.modules["imageio_ffmpeg"] = module
        self.addCleanup(sys.modules.pop, "imageio_ffmpeg", None)

    def test_staged_binary_is_discoverable_on_the_returned_path(self) -> None:
        run_dir = self.root / "run"
        # An empty directory: the host's own ffmpeg, if any, must not answer the lookup.
        environment = {"PATH": str(self.root / "no_tools")}

        _ensure_ffmpeg(run_dir, environment)

        bin_dir = run_dir / "bin"
        self.assertEqual(environment["PATH"].split(os.pathsep)[0], str(bin_dir))
        self.assertEqual(
            [item.name for item in bin_dir.iterdir()],
            ["ffmpeg.exe" if os.name == "nt" else "ffmpeg"],
        )
        self.assertIsNotNone(shutil.which("ffmpeg", path=environment["PATH"]))


if __name__ == "__main__":
    unittest.main()
