from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bootstrap import ModelRuntimeError, materialize_runtime, probe, runtime_python_relative, verify_runtime


class ModelRuntimeBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime_root = self.root / "runtimes"
        self.lock = self.root / "requirements.lock"
        self.lock.write_text("", encoding="utf-8")

    def test_materializes_and_reuses_content_addressed_uv_runtime(self) -> None:
        first = materialize_runtime(
            runtime_root=self.runtime_root,
            source_python=Path(sys.executable),
            lock_path=self.lock,
        )
        second = materialize_runtime(
            runtime_root=self.runtime_root,
            source_python=Path(sys.executable),
            lock_path=self.lock,
        )

        self.assertEqual(first["runtime_id"], second["runtime_id"])
        self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])
        self.assertTrue(Path(first["python_executable_resolved"]).is_file())
        manifest = json.loads(Path(first["manifest_path"]).read_text(encoding="utf-8"))
        self.assertNotIn("runtime_root", manifest)
        self.assertNotIn(str(self.root), json.dumps(manifest))

    def test_manifest_keys_are_the_sealed_set(self) -> None:
        # Approved model bundles compare this manifest as a whole dict
        # (verify_runtime: `if actual != expected`). Adding or dropping a key
        # invalidates every existing approval, so the set is pinned here.
        contract = materialize_runtime(
            runtime_root=self.runtime_root,
            source_python=Path(sys.executable),
            lock_path=self.lock,
        )
        manifest = json.loads(Path(contract["manifest_path"]).read_text(encoding="utf-8"))

        self.assertEqual(
            sorted(manifest),
            sorted(
                [
                    "schema",
                    "runtime_id",
                    "runtime_type",
                    "backend",
                    "materialization",
                    "materialization_version",
                    "python_executable",
                    "python_version",
                    "python_abi",
                    "python_platform",
                    "base_python_sha256",
                    "lock_file",
                    "lock_sha256",
                    "installed_packages_file",
                    "installed_packages_sha256",
                ]
            ),
        )
        self.assertEqual(manifest["materialization"], "uv_venv")
        self.assertEqual(manifest["schema"], "sure.model.runtime.manifest.v1")

    def test_runtime_python_path_matches_host_platform(self) -> None:
        expected = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        self.assertEqual(runtime_python_relative(), expected)

    def test_model_python_probe_ignores_harness_interpreter_overrides(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "PYTHONHOME": "/invalid/harness",
                "PYTHONPATH": "/invalid/harness/site-packages",
                "PYTHONEXECUTABLE": "/invalid/harness/bin/python",
            },
            clear=False,
        ):
            identity = probe(Path(sys.executable))
        self.assertEqual(identity["base_python"], str(Path(sys.executable).resolve()))

    def test_rejects_tampered_package_inventory(self) -> None:
        contract = materialize_runtime(
            runtime_root=self.runtime_root,
            source_python=Path(sys.executable),
            lock_path=self.lock,
        )
        manifest = json.loads(Path(contract["manifest_path"]).read_text(encoding="utf-8"))
        (Path(contract["runtime_root"]) / "installed-packages.txt").write_text("tampered\n", encoding="utf-8")

        with self.assertRaisesRegex(ModelRuntimeError, "package inventory hash mismatch"):
            verify_runtime(self.runtime_root, manifest)

    def test_rejects_python_path_outside_content_addressed_runtime(self) -> None:
        contract = materialize_runtime(
            runtime_root=self.runtime_root,
            source_python=Path(sys.executable),
            lock_path=self.lock,
        )
        manifest_path = Path(contract["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["python_executable"] = "../outside-python"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (self.runtime_root / "outside-python").symlink_to(sys.executable)

        with self.assertRaisesRegex(ModelRuntimeError, "invalid Model Runtime python_executable"):
            verify_runtime(self.runtime_root, manifest)


if __name__ == "__main__":
    unittest.main()
