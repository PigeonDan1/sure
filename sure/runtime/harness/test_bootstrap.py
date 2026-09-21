#!/usr/bin/env python3
"""Tests for the Harness Runtime bootstrap."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "runtime" / "harness" / "bootstrap.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.runtime.harness import bootstrap
from sure.runtime.harness.bootstrap import (
    HarnessRuntimeError,
    _load_spec,
    _probe,
    resolve_runtime,
)
from sure.runtime.uvenv import runtime_python_relative


# Materializing the runtime needs uv, and a fresh runtime root every time means a
# fresh uv cache every time: without uv this whole module used to fail rather than
# skip. CI puts uv on PATH, so nothing is lost there.
@unittest.skipUnless(
    shutil.which("uv") or os.environ.get("SURE_UV_BIN", "").strip(),
    "uv is not installed",
)
class HarnessRuntimeBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.runtime_root = Path(self._temporary.name) / "harness"

    def test_the_runtime_id_changes_with_the_materialization_version(self) -> None:
        # The id used to carry only the harness version, the ABI and the lock
        # hash, so a rebuilt runtime kept the same id while its manifest said
        # something else. Every gate compares the id by string equality.
        spec, _lock_path, lock_sha256, runtime_id = _load_spec()
        self.assertIn(f"-m{spec['materialization_version']}-", runtime_id)
        self.assertTrue(runtime_id.startswith("sure-harness-"))
        self.assertTrue(runtime_id.endswith(lock_sha256[:12]))

    def test_materializing_twice_yields_one_verified_runtime(self) -> None:
        first = resolve_runtime(self.runtime_root)
        second = resolve_runtime(self.runtime_root)

        self.assertEqual(first["runtime_id"], second["runtime_id"])
        self.assertEqual(first["lock_sha256"], second["lock_sha256"])
        self.assertEqual(first["status"], "ready")
        python = Path(first["python_executable"])
        self.assertTrue(python.is_file())
        # "Scripts/python.exe" on Windows, "bin/python" everywhere else.
        expected_directory, expected_name = runtime_python_relative().split("/")
        self.assertEqual(python.name, expected_name)
        self.assertEqual(python.parent.name, expected_directory)
        # The venv entry point, not the base interpreter its symlink leads to.
        self.assertTrue(python.is_relative_to(Path(first["runtime_root"])))
        manifest = json.loads(Path(first["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["materialization"], "uv_venv")
        self.assertEqual(len(manifest["base_python_sha256"]), 64)
        self.assertEqual(manifest["runtime_id"], first["runtime_id"])


class HarnessRuntimeSpecTests(unittest.TestCase):
    """A malformed runtime.json must fail the way the launcher can report.

    main() turns HarnessRuntimeError into HARNESS_RUNTIME_NOT_READY; anything
    else reaches the caller as a traceback.
    """

    def test_a_malformed_materialization_version_raises_a_harness_error(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        spec_dir = Path(temporary.name)
        (spec_dir / "requirements.lock.txt").write_text("", encoding="utf-8")
        # int() raises ValueError for a string it cannot read and TypeError for
        # the JSON array or object that `or 0` lets through when it is non-empty.
        for value in ("three", "1.5", ["1"], {"version": 1}):
            with self.subTest(value=value):
                (spec_dir / "runtime.json").write_text(
                    json.dumps({
                        "schema": "sure.harness.runtime.spec.v1",
                        "harness_version": "v1",
                        "python": "3.11",
                        "lock_file": "requirements.lock.txt",
                        "materialization_version": value,
                    }),
                    encoding="utf-8",
                )
                with mock.patch.object(bootstrap, "SPEC_DIR", spec_dir):
                    with self.assertRaises(HarnessRuntimeError):
                        _load_spec()


class HarnessRuntimeImportProbeTests(unittest.TestCase):
    """The probe must describe the runtime, not the environment it was called from.

    These need no uv: the probe is pointed at the interpreter running the tests.
    """

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)

    def _set_environment(self, name: str, value: str) -> None:
        previous = os.environ.get(name)

        def restore() -> None:
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous

        self.addCleanup(restore)
        os.environ[name] = value

    def test_a_host_python_home_does_not_reach_the_probe(self) -> None:
        # The deleted bash wrapper exported a PYTHONHOME of its own, which hid
        # whatever the host had set. A uv venv's interpreter is a real one and
        # exports nothing, so a host PYTHONHOME would kill the probe: the reuse
        # check would then call a healthy runtime invalid and quarantine and
        # rebuild it on every single resolve.
        self._set_environment("PYTHONHOME", str(Path(self._temporary.name) / "not-a-python"))
        self.assertTrue(_probe(Path(sys.executable), [])["version"])

    def test_a_host_python_path_cannot_satisfy_a_required_import(self) -> None:
        # -s only blocks the user site directory. An import answered by the
        # caller's PYTHONPATH would vouch for a package the lock never put in
        # the runtime.
        leaked = Path(self._temporary.name) / "leaked"
        leaked.mkdir()
        (leaked / "leaked_module.py").write_text("", encoding="utf-8")
        self._set_environment("PYTHONPATH", str(leaked))
        with self.assertRaises(HarnessRuntimeError):
            _probe(Path(sys.executable), ["leaked_module"])


if __name__ == "__main__":
    unittest.main()
