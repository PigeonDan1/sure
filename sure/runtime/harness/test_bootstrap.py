#!/usr/bin/env python3
"""Tests for the Harness Runtime bootstrap."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
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
    BASE_PYTHON_NAME,
    HarnessRuntimeError,
    _load_spec,
    _probe,
    resolve_runtime,
)
from sure.runtime.uvenv import child_environment, runtime_python_relative


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

    def test_a_junction_alias_is_not_counted_as_a_second_interpreter(self) -> None:
        # Production runs bootstrap.py under the 3.11 interpreter it prepares,
        # and Path.is_junction only arrived in 3.12. The alias uv writes beside
        # the real install is a junction, so _is_link must see it through the
        # reparse point tag, and _relocate_base_python must not count it as a
        # second real interpreter. This is the fast, interpreter-agnostic
        # version of the failure; the materialized-runtime test below proves the
        # real uv tree end to end.
        if os.name != "nt":
            self.skipTest("uv's junction alias is a Windows surface")
        install_dir = Path(self._temporary.name) / "install"
        install_dir.mkdir()
        real = install_dir / "cpython-3.11.15-windows-x86_64-none"
        real.mkdir()
        (real / "python.exe").write_bytes(b"MZ")
        alias = install_dir / "cpython-3.11-windows-x86_64-none"
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias), str(real)],
            capture_output=True,
            check=True,
        )
        self.assertTrue(bootstrap._is_link(alias))
        self.assertFalse(bootstrap._is_link(real))
        self.assertEqual(bootstrap._aliases_of(install_dir, real), {real.name, alias.name})

    def test_a_link_spelled_through_uv_s_alias_follows_the_interpreter_in(self) -> None:
        # uv names the entry point through its version-agnostic alias
        # (cpython-3.11-...) while installing into the release-named directory
        # (cpython-3.11.17-...). The alias is unlinked before the relocation
        # re-points links, so resolving the old spelling no longer reaches the
        # real directory and the link has to be rebuilt against the in-tree
        # copy. Carrying the alias segment over publishes a dangling entry
        # point, which is a runtime that cannot start.
        if os.name == "nt":
            self.skipTest("the POSIX layout spells its entry point through the alias")
        install_dir = Path(self._temporary.name) / "install"
        real = install_dir / "cpython-3.11.17-linux-x86_64-gnu"
        (real / "bin").mkdir(parents=True)
        (real / "bin" / "python3.11").write_bytes(b"#!/bin/sh\n")
        alias = install_dir / "cpython-3.11-linux-x86_64-gnu"
        alias.symlink_to(real, target_is_directory=True)
        staging = Path(self._temporary.name) / "staging"
        (staging / "bin").mkdir(parents=True)
        (staging / "pyvenv.cfg").write_text(f"home = {real / 'bin'}\n", encoding="utf-8")
        entry = staging / "bin" / "python"
        entry.symlink_to(alias / "bin" / "python3.11")

        bootstrap._relocate_base_python(staging, install_dir)

        self.assertFalse(entry.is_symlink() and not entry.exists(), f"{entry} dangles")
        self.assertEqual(os.readlink(entry), "../base-python/bin/python3.11")
        self.assertTrue((staging / "base-python" / "bin" / "python3.11").is_file())
        home = Path(
            bootstrap._home_of(bootstrap._read_lines(staging / "pyvenv.cfg", "pyvenv.cfg"))
        )
        self.assertTrue(home.is_relative_to(staging / "base-python"), home)

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

    def test_a_materialized_runtime_carries_its_own_interpreter(self) -> None:
        # The runtime is mounted into containers that do not have the host's
        # interpreter, and a uv venv points at one outside the tree: bin/python
        # is an absolute symlink, Scripts/python.exe is a stub with the path
        # compiled in, and pyvenv.cfg names it too. Every one of those has to
        # name something inside the tree, or the runtime only runs on the
        # machine that built it.
        contract = resolve_runtime(self.runtime_root)
        root = Path(contract["runtime_root"]).resolve()
        base = (root / BASE_PYTHON_NAME).resolve()
        self.assertTrue(base.is_dir())
        cfg_home = Path(
            bootstrap._home_of(
                bootstrap._read_lines(root / "pyvenv.cfg", "pyvenv.cfg")
            )
        ).resolve()
        # home names the directory holding the interpreter, which is
        # base-python/bin on POSIX and base-python itself on Windows, so the
        # contract is containment, not the last path component.
        self.assertTrue(cfg_home.is_relative_to(base), f"{cfg_home} is not inside {base}")
        for current, dirs, files in os.walk(root):
            for name in dirs + files:
                path = Path(current) / name
                if not bootstrap._is_link(path):
                    continue
                target = Path(bootstrap._link_target(path))
                resolved = (
                    path.parent.joinpath(target).resolve()
                    if not target.is_absolute()
                    else target.resolve()
                )
                self.assertTrue(
                    resolved.is_relative_to(root),
                    f"{path} -> {target} escapes the tree",
                )

    def test_the_entry_point_is_the_tree_s_own_interpreter(self) -> None:
        # The entry point a container executes must be the tree's own binary,
        # not a launcher that reaches out to wherever the tree was built.
        contract = resolve_runtime(self.runtime_root)
        root = Path(contract["runtime_root"]).resolve()
        python = root / runtime_python_relative()
        base = root / BASE_PYTHON_NAME
        if os.name == "nt":
            self.assertTrue((base / "python.exe").is_file())
            self.assertEqual(
                (python).read_bytes(),
                (base / "python.exe").read_bytes(),
            )
        else:
            self.assertTrue(bootstrap._is_link(python))
            target = Path(bootstrap._link_target(python))
            self.assertFalse(target.is_absolute())
            self.assertTrue((python.parent / target).resolve().is_relative_to(root))

    @unittest.skipIf(
        os.name == "nt",
        "the container surface runs a Linux runtime",
    )
    def test_a_runtime_runs_with_nothing_but_the_interpreter_it_carries(self) -> None:
        # The container mounts the tree at the path it was materialized at and
        # has none of the host's Pythons, so the interpreter has to start from
        # the tree alone. pyvenv.cfg home is absolute by design (a relative one
        # is read against the process CWD and breaks the tree), so what has to
        # hold is that home names the in-tree copy and that the uv install
        # directory it was built from is gone.
        contract = resolve_runtime(self.runtime_root)
        root = Path(contract["runtime_root"])
        base = (root / BASE_PYTHON_NAME).resolve()
        home = Path(
            bootstrap._home_of(
                bootstrap._read_lines(root / "pyvenv.cfg", "pyvenv.cfg")
            )
        ).resolve()
        self.assertTrue(home.is_relative_to(base), f"pyvenv.cfg home {home} escapes {base}")
        install_dirs = [
            entry for entry in self.runtime_root.iterdir()
            if bootstrap.INSTALL_DIR_NAME in entry.name
        ]
        self.assertEqual(install_dirs, [], "the uv install directory outlived the relocation")
        python = root / runtime_python_relative()
        completed = subprocess.run(
            [str(python), "-s", "-c", "import sys;print(sys.prefix);print(sys.base_prefix)"],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            cwd=str(root),
            env=child_environment(),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        prefix, base_prefix = completed.stdout.split()
        self.assertEqual(Path(prefix).resolve(), root.resolve())
        self.assertEqual(Path(base_prefix).resolve(), base)

    def test_the_manifest_hash_describes_the_interpreter_in_the_tree(self) -> None:
        # pyvenv.cfg home is absolute, so the tree only runs where it stands;
        # what has to hold is that the hash in the manifest is the hash of the
        # interpreter the tree carries, which is what _verified_contract reads
        # back on every resolve.
        contract = resolve_runtime(self.runtime_root)
        root = Path(contract["runtime_root"])
        identity = bootstrap.probe_python(
            root / runtime_python_relative(), error=HarnessRuntimeError
        )
        self.assertEqual(identity["base_python_sha256"], contract["base_python_sha256"])
        self.assertTrue(
            Path(identity["base_python"]).resolve().is_relative_to((root / BASE_PYTHON_NAME).resolve()),
            identity["base_python"],
        )

    def test_a_materialized_windows_runtime_runs_on_its_own_tree(self) -> None:
        # The entry-point byte-equality above proves the launcher stub was
        # replaced, but not that the result runs. On Windows the trampoline stub
        # and the junction alias are the two things the relocation exists to
        # replace, so this is where actually starting the interpreter matters.
        if os.name != "nt":
            self.skipTest("the Windows launcher surface is Windows-only")
        contract = resolve_runtime(self.runtime_root)
        root = Path(contract["runtime_root"])
        python = root / runtime_python_relative()
        completed = subprocess.run(
            [str(python), "-s", "-c", "import sys;print(sys.prefix);print(sys.base_prefix)"],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            cwd=str(root),
            env=child_environment(),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        prefix, base_prefix = completed.stdout.split()
        self.assertEqual(Path(prefix).resolve(), root.resolve())
        self.assertEqual(Path(base_prefix).resolve(), (root / BASE_PYTHON_NAME).resolve())

    def test_a_runtime_that_reaches_outside_its_tree_is_refused(self) -> None:
        # Verification is what stands between a tree that works here and a tree
        # that works in a container, so a tree with one absolute link back to
        # the host must not be reported as ready.
        contract = resolve_runtime(self.runtime_root)
        root = Path(contract["runtime_root"])
        if os.name == "nt":
            self.skipTest("a Windows runtime carries no symlinks to break")
        link = root / "bin" / "escaped"
        link.symlink_to(Path(sys.executable).resolve())
        self.addCleanup(link.unlink)
        spec, _lock_path, lock_sha256, runtime_id = _load_spec()
        with self.assertRaisesRegex(HarnessRuntimeError, "not self-contained"):
            bootstrap._verified_contract(root, spec, lock_sha256, runtime_id)

    def test_a_manifest_base_python_hash_is_checked_against_the_interpreter(self) -> None:
        # The hash was recorded and never read back, so a runtime whose base
        # interpreter had been swapped under it was reused on every resolve.
        first = resolve_runtime(self.runtime_root)
        manifest_path = Path(first["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        genuine = manifest["base_python_sha256"]
        manifest["base_python_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(HarnessRuntimeError, "base_python_sha256"):
            resolve_runtime(self.runtime_root, repair=False)

        repaired = json.loads(
            Path(resolve_runtime(self.runtime_root)["manifest_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(repaired["base_python_sha256"], genuine)

    def _superseded_runtime(self) -> Path:
        """A runtime left behind by an earlier runtime_id, plus what must survive."""
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        superseded = self.runtime_root / "sure-harness-v0-m1-py311-000000000000"
        superseded.mkdir()
        (superseded / "runtime-manifest.json").write_text(
            json.dumps({"schema": "sure.harness.runtime.manifest.v1", "runtime_id": superseded.name}),
            encoding="utf-8",
        )
        (superseded / "payload").write_text("", encoding="utf-8")
        self.foreign = self.runtime_root / "not-a-runtime"
        self.foreign.mkdir()
        (self.foreign / "keep-me").write_text("", encoding="utf-8")
        self.quarantine = self.runtime_root / f".{superseded.name}.invalid-19700101-000000-1"
        self.quarantine.mkdir()
        return superseded

    def test_materializing_reclaims_a_superseded_runtime(self) -> None:
        # runtime_id carries the harness version, the materialization version and
        # the lock hash, so every bump of any of them leaves a whole venv behind
        # under the same root and nothing ever collected it.
        superseded = self._superseded_runtime()

        contract = resolve_runtime(self.runtime_root)

        self.assertFalse(superseded.exists())
        self.assertTrue(Path(contract["python_executable"]).is_file())
        # Only directories carrying this manifest schema are in scope.
        self.assertTrue(self.foreign.is_dir())
        self.assertTrue(self.quarantine.is_dir())

    def test_superseded_runtimes_can_be_kept(self) -> None:
        superseded = self._superseded_runtime()

        resolve_runtime(self.runtime_root, prune=False)

        self.assertTrue(superseded.is_dir())


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
                    # _load_spec raises the same type from four earlier branches,
                    # so name the field or a reorder passes this test for the
                    # wrong reason.
                    with self.assertRaisesRegex(HarnessRuntimeError, "materialization_version"):
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
