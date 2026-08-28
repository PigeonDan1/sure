#!/usr/bin/env python3
"""Tests for the Harness Runtime bootstrap."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "runtime" / "harness" / "bootstrap.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.runtime.harness.bootstrap import _shared_library_name


class SharedLibraryNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.libdir = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def test_keeps_the_declared_name_when_that_file_exists(self) -> None:
        (self.libdir / "libpython3.11.so.1.0").touch()
        self.assertEqual(
            _shared_library_name(self.libdir, "libpython3.11.so.1.0", "3.11"),
            "libpython3.11.so.1.0",
        )

    def test_falls_back_when_the_declared_name_is_a_missing_static_archive(self) -> None:
        (self.libdir / "libpython3.11.so.1.0").touch()
        self.assertEqual(
            _shared_library_name(self.libdir, "libpython3.11.a", "3.11"),
            "libpython3.11.so.1.0",
        )

    def test_falls_back_to_the_unversioned_shared_object(self) -> None:
        (self.libdir / "libpython3.11.so").touch()
        self.assertEqual(
            _shared_library_name(self.libdir, "libpython3.11.a", "3.11"),
            "libpython3.11.so",
        )

    def test_prefers_the_versioned_shared_object(self) -> None:
        (self.libdir / "libpython3.11.so").touch()
        (self.libdir / "libpython3.11.so.1.0").touch()
        self.assertEqual(
            _shared_library_name(self.libdir, "libpython3.11.a", "3.11"),
            "libpython3.11.so.1.0",
        )

    def test_keeps_the_declared_name_when_nothing_is_present(self) -> None:
        self.assertEqual(
            _shared_library_name(self.libdir, "libpython3.11.a", "3.11"),
            "libpython3.11.a",
        )

    def test_keeps_an_empty_name_when_nothing_is_present(self) -> None:
        self.assertEqual(_shared_library_name(self.libdir, "", "3.11"), "")


if __name__ == "__main__":
    unittest.main()
