from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest import mock
import zipfile

import bootstrap
from bootstrap import ModelRuntimeError, materialize_runtime
from sure.runtime.uvenv import uv_binary


class BuildExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.cache = self.root / "shared-cache"
        self.cache.mkdir()
        self.lock = self.root / "requirements.lock"
        self.lock.write_text("")
        self.options = dict(runtime_root=self.root / "runtimes", source_python=Path(sys.executable), lock_path=self.lock)

    def test_reuse_ignores_unused_cache_and_config_but_new_build_rejects_bad_cache(self) -> None:
        missing = self.root / "missing"
        with self.assertRaisesRegex(ModelRuntimeError, "cache is not accessible"):
            materialize_runtime(**self.options, uv_cache_dir=missing)
        real_access = os.access
        with mock.patch.object(bootstrap.os, "access", wraps=real_access) as access:
            access.side_effect = lambda path, mode: False if path == self.cache else real_access(path, mode)
            with self.assertRaisesRegex(ModelRuntimeError, "cache is not accessible"):
                materialize_runtime(**self.options, uv_cache_dir=self.cache)
        first = materialize_runtime(**self.options, uv_cache_dir=self.cache)
        with mock.patch.object(bootstrap, "_run", side_effect=AssertionError("reuse must not rebuild")):
            second = materialize_runtime(**self.options, uv_cache_dir=missing, uv_config=missing)
        self.assertEqual(first["runtime_id"], second["runtime_id"])

    def test_default_cache_choice_is_announced_and_explicit_is_not(self) -> None:
        output = io.StringIO()
        with mock.patch.object(bootstrap.sys, "stderr", output):
            materialize_runtime(**self.options)
        self.assertIn("site default", output.getvalue())
        explicit_lock = self.root / "requirements-2.lock"
        explicit_lock.write_text("# distinct requirements\n")
        second = dict(self.options, lock_path=explicit_lock)
        output2 = io.StringIO()
        with mock.patch.object(bootstrap.sys, "stderr", output2):
            materialize_runtime(**second, uv_cache_dir=self.cache)
        self.assertNotIn("site default", output2.getvalue())

    def test_local_package_compile_sync_materialize_use_same_config_cache_and_hashes(self) -> None:
        wheel_dir = self.root / "wheels"
        wheel_dir.mkdir()
        wheel = wheel_dir / "m3_fixture-1.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("m3_fixture.py", "VALUE = 42\n")
            archive.writestr("m3_fixture-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: m3-fixture\nVersion: 1.0\n")
            archive.writestr("m3_fixture-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            archive.writestr("m3_fixture-1.0.dist-info/RECORD", "")
        config = self.root / "selected-uv.toml"
        config.write_text('no-index = true\nfind-links = [' + json.dumps(str(wheel_dir)) + ']\nindex-strategy = "first-index"\n')
        requirements = self.root / "requirements.in"
        requirements.write_text("m3-fixture==1.0\n")
        uv = uv_binary()
        common = ["--config-file", str(config), "--cache-dir", str(self.cache)]
        subprocess.run([uv, "pip", "compile", "--python", sys.executable, "--generate-hashes", *common,
                        str(requirements), "-o", str(self.lock)], cwd=self.root, check=True, capture_output=True)
        validation = self.root / "validation"
        subprocess.run([uv, "venv", "--no-python-downloads", "--python", sys.executable,
                        "--cache-dir", str(self.cache), str(validation)],
                       check=True, capture_output=True)
        subprocess.run([uv, "pip", "sync", "--python", str(validation / bootstrap.runtime_python_relative()),
                        "--require-hashes", "--strict", *common, str(self.lock)], check=True, capture_output=True)
        with mock.patch.object(bootstrap, "_run", wraps=bootstrap._run) as run:
            result = materialize_runtime(**self.options, uv_cache_dir=self.cache, uv_config=config)
        sync = next(call for call in run.call_args_list if call.args[0][1:3] == ["pip", "sync"])
        self.assertEqual(sync.kwargs["env"]["UV_CACHE_DIR"], str(self.cache))
        self.assertEqual(sync.kwargs["cwd"], self.lock.parent)
        self.assertIn(str(config), sync.args[0])
        self.assertIn("--require-hashes", sync.args[0])
        self.assertIn("--strict", sync.args[0])
        subprocess.run([result["python_executable_resolved"], "-c", "import m3_fixture; assert m3_fixture.VALUE == 42"], check=True)
        manifest = Path(result["manifest_path"]).read_text()
        self.assertNotIn(str(self.root), manifest)
        self.assertNotIn("uv_cache_dir", manifest)
        self.assertFalse((self.root / "runtimes" / ".uv-cache").exists())
        self.lock.write_text(self.lock.read_text().replace(hashlib.sha256(wheel.read_bytes()).hexdigest(), "0" * 64))
        with self.assertRaisesRegex(ModelRuntimeError, "command failed"):
            materialize_runtime(**self.options, uv_cache_dir=self.cache, uv_config=config)

    def test_output_is_visible_before_exit_and_stdout_stays_separate(self) -> None:
        visible = threading.Event()
        output = io.StringIO()
        original_write = output.write

        def write(value: str) -> int:
            if "early" in value:
                visible.set()
            return original_write(value)

        result = []
        with mock.patch.object(output, "write", side_effect=write), mock.patch.object(bootstrap.sys, "stderr", output):
            thread = threading.Thread(target=lambda: result.append(bootstrap._run(
                [sys.executable, "-u", "-c", "import sys,time;sys.stdout.write('early');sys.stdout.flush();time.sleep(0.7);sys.stderr.write('diagnostic')"],
                env=os.environ.copy(), timeout=3,
            )))
            thread.start()
            self.assertTrue(visible.wait(2))
            self.assertTrue(thread.is_alive())
            thread.join(3)
        self.assertEqual(result[0].stdout, "early")
        self.assertEqual(result[0].stderr, "diagnostic")
        with self.assertRaisesRegex(ModelRuntimeError, "existing command timeout"):
            bootstrap._run([sys.executable, "-c", "import time;time.sleep(1)"], env=os.environ.copy(), timeout=0.05)

    def test_timeout_reports_without_waiting_for_pipe_holding_grandchildren(self) -> None:
        # The orphaned `sleep 8` inherits both pipes; the timeout must be
        # reported now, not after the orphan closes them.
        started = time.monotonic()
        with self.assertRaisesRegex(ModelRuntimeError, "existing command timeout"):
            bootstrap._run(["/bin/sh", "-c", "sleep 8 & wait"], env=os.environ.copy(), timeout=0.5)
        self.assertLess(time.monotonic() - started, 5)


if __name__ == "__main__":
    unittest.main()
