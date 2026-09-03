#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import json

from evaluation_runtime import (
    EvaluationIdentityUnavailable,
    EvaluationRuntimeError,
    _approved_harness_runtime,
    _engine_commit,
    _engine_has_repository,
    _expected_binding,
    _sha256,
    _make_group_writable,
    _wrapper,
    ensure_evaluation_runtime,
    evaluation_child_environment,
    evaluation_runtime_from_eval_input,
    main,
)


class EvaluationRuntimeTests(unittest.TestCase):
    def test_materialized_runtime_is_group_writable_without_inventing_execute_bits(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            data = root / "data.json"
            executable = root / "bin" / "python"
            executable.parent.mkdir()
            data.write_text("{}\n", encoding="utf-8")
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            root.chmod(0o700)
            executable.parent.chmod(0o700)
            data.chmod(0o600)
            executable.chmod(0o700)

            _make_group_writable(root)

            self.assertEqual(stat.S_IMODE(root.stat().st_mode) & 0o070, 0o070)
            self.assertEqual(stat.S_IMODE(executable.parent.stat().st_mode) & 0o070, 0o070)
            self.assertEqual(stat.S_IMODE(data.stat().st_mode) & 0o070, 0o060)
            self.assertEqual(stat.S_IMODE(executable.stat().st_mode) & 0o070, 0o070)

            inherited = root / "created-after-finalize.txt"
            inherited.write_text("ok\n", encoding="utf-8")
            self.assertEqual(stat.S_IMODE(inherited.stat().st_mode) & 0o060, 0o060)

    def test_wrapper_does_not_leak_parent_pythonhome(self) -> None:
        text = _wrapper(
            {
                "runtime_root": "/repo/sure/.runtime/evaluation/demo",
                "harness_runtime_root": "/repo/sure/.runtime/harness/demo",
                "engine_root": "/repo/sure/external/sure-evaluation",
                "dynamic_loader": "/lib64/ld-linux-x86-64.so.2",
            }
        )
        self.assertIn("unset PYTHONHOME PYTHONEXECUTABLE", text)
        self.assertIn('"$_sure_eval_root/site-packages', text)
        self.assertIn("--library-path", text)
        self.assertNotIn("export LD_LIBRARY_PATH='/repo", text)
        self.assertIn("_sure_eval_parent_ld", text)

    def test_group_permissions_leave_another_owners_files_alone(self) -> None:
        # The runtime cache is shared, and its log directory keeps every
        # bootstrap log anyone has written. Only an owner may change a file's
        # ACL, so re-ACLing the whole directory means the first person to
        # materialize a runtime is the last: everyone after them dies on
        # "Operation not permitted" with the packages already installed.
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            (root / "bootstrap-old.log").write_text("", encoding="utf-8")
            touched: list[Path] = []

            def record(setfacl: str, entries: str, paths: list[Path]) -> None:
                touched.extend(paths)

            with mock.patch("evaluation_runtime.shutil.which", return_value="/usr/bin/setfacl"), \
                mock.patch("evaluation_runtime._apply_acl", record), \
                mock.patch("evaluation_runtime.os.getuid", return_value=999999):
                _make_group_writable(root)

            self.assertEqual(touched, [])

    def test_the_wrapper_is_identical_wherever_the_runtime_is_reached_from(self) -> None:
        # One cache entry is reached through several names: the same storage
        # carries two mount paths, and the Harness Runtime sits in the repo on
        # the host but in /opt inside the evaluation image. _verify compares
        # this text byte for byte, so any of those names baked into it turns a
        # perfectly good runtime into "wrapper differs from the contract".
        loader = "/lib64/ld-linux-x86-64.so.2"
        host = _wrapper(
            {
                "runtime_root": "/storage/one/checkout/sure/.runtime/evaluation/demo",
                "harness_runtime_root": "/storage/one/checkout/sure/.runtime/harness/demo",
                "engine_root": "/storage/one/checkout/sure/external/sure-evaluation",
                "dynamic_loader": loader,
            }
        )
        container = _wrapper(
            {
                "runtime_root": "/storage/two/checkout/sure/.runtime/evaluation/demo",
                "harness_runtime_root": "/opt/sure-harness/demo",
                "engine_root": "/storage/two/checkout/sure/external/sure-evaluation",
                "dynamic_loader": loader,
            }
        )
        self.assertEqual(host, container)
        for name in ("/storage/one", "/storage/two", "/opt/sure-harness"):
            self.assertNotIn(name, host)

    def test_child_environment_removes_only_harness_library_path(self) -> None:
        harness_root = "/repo/sure/.runtime/harness/demo"
        env = evaluation_child_environment(
            {
                "SURE_HARNESS_RUNTIME_ROOT": harness_root,
                "LD_LIBRARY_PATH": f"{harness_root}/base/lib:/usr/local/cuda/lib64:/opt/model/lib",
            }
        )
        self.assertEqual(env["LD_LIBRARY_PATH"], "/usr/local/cuda/lib64:/opt/model/lib")

    def test_non_external_input_has_no_evaluation_runtime(self) -> None:
        self.assertIsNone(
            evaluation_runtime_from_eval_input(
                {"evaluation": {"backend": "legacy"}},
                prepare=False,
            )
        )

    def test_external_backend_without_an_engine_is_refused_before_launch(self) -> None:
        with self.assertRaises(EvaluationRuntimeError) as caught:
            evaluation_runtime_from_eval_input(
                {"evaluation": {"backend": "external", "engine": None}},
                prepare=False,
            )
        self.assertIn("engine", str(caught.exception))


class EngineCommitTests(unittest.TestCase):
    def test_absent_git_binary_is_reported_as_an_evaluation_runtime_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            empty_bin = root / "bin"
            empty_bin.mkdir()
            engine_root = root / "engine"
            engine_root.mkdir()
            with mock.patch.dict(os.environ, {"PATH": str(empty_bin)}):
                with self.assertRaises(EvaluationRuntimeError) as caught:
                    _engine_commit(engine_root)
        self.assertIn("git", str(caught.exception))


class EngineIdentityAbsenceTests(unittest.TestCase):
    """A checkout that cannot answer is not the same as one that answers wrong."""

    def test_a_directory_that_is_not_a_repository_cannot_be_asked(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            engine = Path(raw_root) / "engine"
            engine.mkdir()
            self.assertFalse(_engine_has_repository(engine))

    def test_a_repository_can_be_asked(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            engine = Path(raw_root) / "engine"
            engine.mkdir()
            subprocess.run(["git", "init", "--quiet", str(engine)], check=False, capture_output=True)
            self.assertTrue(_engine_has_repository(engine))

    def test_a_repository_with_no_readable_head_is_an_error_not_an_absence(self) -> None:
        # git ran, the repository is there, and it still would not say. That is
        # an answer we did not get, not a boundary the injected identity should
        # cross -- falling back there would hand the drift check to whoever can
        # make git fail.
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            engine = root / "engine"
            engine.mkdir()
            subprocess.run(["git", "init", "--quiet", str(engine)], check=False, capture_output=True)
            (engine / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            lock = root / "requirements.lock.txt"
            lock.write_text("demo==1.0\n", encoding="utf-8")
            spec = {"lock_file": "requirements.lock.txt", "engine_commit": "c" * 40}
            with mock.patch("evaluation_runtime.SPEC_ROOT", root):
                with mock.patch("evaluation_runtime._load_json", return_value=spec):
                    with mock.patch("evaluation_runtime._engine_commit", return_value=""):
                        with self.assertRaises(EvaluationRuntimeError) as caught:
                            _expected_binding(engine)

        self.assertNotIsInstance(caught.exception, EvaluationIdentityUnavailable)


class ReadinessArtifactTests(unittest.TestCase):
    """[2.6/5] redirects this program's stdout into evaluation_readiness.json."""

    def _run(self, engine_root: Path, output: Path) -> tuple[int, str]:
        argv = ["evaluation_runtime.py", "--engine-root", str(engine_root), "--output", str(output)]
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(stdout):
                with contextlib.redirect_stderr(io.StringIO()):
                    status = main()
        return status, stdout.getvalue()

    def test_a_refused_runtime_leaves_a_readable_reason(self) -> None:
        # The gate redirected stdout into the artifact and the program exited
        # by raising, so the file the run left behind was zero bytes: a human
        # could read stderr, a reader of the run directory got nothing.
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            output = root / "evaluation_readiness.json"
            status, _ = self._run(root / "no-engine-here", output)
            record = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(status, 1)
        self.assertFalse(record["ok"])
        self.assertTrue(record["error"]["message"])

    def test_the_success_payload_is_still_the_binding_alone(self) -> None:
        # /sure_reval spawns this script and parses stdout against the binding
        # schema, so the success shape is a contract and stays untouched.
        binding = {"schema": "sure.evaluation.runtime.binding.v1", "runtime_id": "x"}
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            output = root / "evaluation_readiness.json"
            with mock.patch("evaluation_runtime.ensure_evaluation_runtime", return_value=binding):
                status, printed = self._run(root, output)
            written = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(status, 0)
        self.assertEqual(json.loads(printed), binding)
        self.assertEqual(written, binding)


class ApprovedHarnessRuntimeTests(unittest.TestCase):
    """The evaluation binding rests on this check, so it has to be the strict one."""

    def _runtime(self, root: Path, **overrides: object) -> dict[str, str]:
        python = root / "bin" / "python"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("#!/bin/sh\n", encoding="utf-8")
        python.chmod(0o755)
        manifest = {
            "schema": "sure.harness.runtime.manifest.v1",
            "runtime_id": "sure-harness-approved",
            "lock_sha256": "a" * 64,
            "python_version": "3.11.5",
            "python_abi": "cp311",
            "harness_version": "test",
        }
        manifest.update(overrides)
        (root / "runtime-manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        return {
            "HARNESS_PYTHON_BIN": str(python),
            "SURE_HARNESS_RUNTIME_ID": "sure-harness-approved",
            "SURE_HARNESS_LOCK_SHA256": "a" * 64,
            "SURE_HARNESS_MANIFEST_PATH": str(root / "runtime-manifest.json"),
            "SURE_HARNESS_RUNTIME_ROOT": str(root),
        }

    def test_a_runtime_that_disagrees_about_its_lock_is_refused(self) -> None:
        # The identity is two halves. Checking only the runtime_id let a
        # directory the run itself wrote answer for where the run came from.
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            env = self._runtime(root, lock_sha256="b" * 64)
            with mock.patch.dict(os.environ, env):
                with self.assertRaises(EvaluationRuntimeError):
                    _approved_harness_runtime()

    def test_a_python_outside_the_runtime_root_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            outside = root.parent / "outside-python"
            outside.write_text("#!/bin/sh\n", encoding="utf-8")
            outside.chmod(0o755)
            env = self._runtime(root)
            env["HARNESS_PYTHON_BIN"] = str(outside)
            with mock.patch.dict(os.environ, env):
                with self.assertRaises(EvaluationRuntimeError):
                    _approved_harness_runtime()

    def test_the_binding_carries_the_harness_runtime_it_checked(self) -> None:
        # Nothing else walks _expected_binding to its return: every other test
        # stops at one of the raises, because reaching the end needs a real
        # engine checkout on the locked commit. So the only path every live run
        # takes was the one path no test covered.
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            engine = root / "engine"
            engine.mkdir()
            (engine / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            loader = root / "ld.so"
            loader.write_text("", encoding="utf-8")
            lock = root / "requirements.lock.txt"
            lock.write_text("demo==1.0\n", encoding="utf-8")
            spec = {
                "lock_file": "requirements.lock.txt",
                "engine_commit": "c" * 40,
                "engine_pyproject_sha256": _sha256(engine / "pyproject.toml"),
                "dynamic_loader": str(loader),
            }
            with mock.patch.dict(os.environ, self._runtime(root)):
                with mock.patch("evaluation_runtime.SPEC_ROOT", root):
                    with mock.patch("evaluation_runtime._load_json", return_value=spec):
                        with mock.patch("evaluation_runtime._engine_commit", return_value="c" * 40):
                            binding = _expected_binding(engine)

        self.assertEqual(binding["harness_runtime_id"], "sure-harness-approved")
        self.assertEqual(binding["harness_runtime_root"], str(root))
        self.assertEqual(binding["engine_commit"], "c" * 40)

    def test_an_approved_runtime_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            with mock.patch.dict(os.environ, self._runtime(root)):
                self.assertEqual(_approved_harness_runtime()["runtime_id"], "sure-harness-approved")


class AttestedBindingTests(unittest.TestCase):
    """The host resolves the binding before it submits; the container reuses it."""

    def _write_manifest(self, root: Path, **overrides: object) -> Path:
        manifest_path = root / "runtime-manifest.json"
        payload = {
            "schema": "sure.evaluation.runtime.binding.v1",
            "runtime_id": "sure-evaluation-root-v1-m1-cb9267e9f887-py311-abcdef012345",
            "lock_sha256": "abcdef0123456789",
            "engine_commit": "cb9267e9f887b1619f8449e49da828c77960a52e",
            "python_executable": "/host/runtime/bin/python",
            "engine_root": "/host/engine",
            "required_imports": [],
        }
        payload.update(overrides)
        manifest_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return manifest_path

    def test_a_resolvable_binding_beats_whatever_the_environment_attests(self) -> None:
        # The host runs this too, from a shell the model agent controls, so an
        # `export SURE_EVALUATION_RUNTIME_ID=...` before scripts/run_local_execution.py
        # used to make the host adopt the agent's binding and then inject it into
        # the container as the run's provenance. Where the binding can be
        # computed, it is computed.
        local = {
            "schema": "sure.evaluation.runtime.binding.v1",
            "runtime_id": "sure-evaluation-locally-resolved",
            "lock_sha256": "1" * 16,
            "engine_commit": "cb9267e9f887b1619f8449e49da828c77960a52e",
            "python_executable": "/host/runtime/bin/python",
            "engine_root": "/host/engine",
            "manifest_path": "",
        }
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest_path = self._write_manifest(root, runtime_id="forged", lock_sha256="0" * 16)
            local["manifest_path"] = str(manifest_path)
            env = {
                "SURE_EVALUATION_RUNTIME_ID": "forged",
                "SURE_EVALUATION_LOCK_SHA256": "0" * 16,
                "SURE_EVALUATION_RUNTIME_MANIFEST": str(manifest_path),
            }
            with mock.patch.dict(os.environ, env):
                with mock.patch("evaluation_runtime._expected_binding", return_value=local):
                    with mock.patch("evaluation_runtime._verify", return_value=(True, "ok")):
                        binding = ensure_evaluation_runtime(Path(root / "engine"), prepare=False)

        self.assertEqual(binding["runtime_id"], "sure-evaluation-locally-resolved")
        self.assertNotIn("attested", binding["verification"])

    def test_an_unresolvable_binding_still_falls_back_to_the_attested_one(self) -> None:
        # The evaluation image has no git and no engine .git, so the container
        # cannot compute anything; the host's injected identity is all it has.
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest_path = self._write_manifest(root)
            env = {
                "SURE_EVALUATION_RUNTIME_ID": "sure-evaluation-root-v1-m1-cb9267e9f887-py311-abcdef012345",
                "SURE_EVALUATION_LOCK_SHA256": "abcdef0123456789",
                "SURE_EVALUATION_RUNTIME_MANIFEST": str(manifest_path),
            }
            failure = EvaluationIdentityUnavailable("git is unavailable")
            with mock.patch.dict(os.environ, env):
                with mock.patch("evaluation_runtime._expected_binding", side_effect=failure):
                    binding = ensure_evaluation_runtime(Path(root / "engine"), prepare=False)

        self.assertIn("attested", binding["verification"])

    def test_a_drifted_engine_does_not_fall_back_to_the_attested_binding(self) -> None:
        # Falling back on every EvaluationRuntimeError would hand the drift
        # check to the environment: the run breaks its own harness binding, or
        # moves the engine, and the injected identity answers instead. Only
        # "the identity cannot be computed here" is a boundary to cross.
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest_path = self._write_manifest(root)
            env = {
                "SURE_EVALUATION_RUNTIME_ID": "sure-evaluation-root-v1-m1-cb9267e9f887-py311-abcdef012345",
                "SURE_EVALUATION_LOCK_SHA256": "abcdef0123456789",
                "SURE_EVALUATION_RUNTIME_MANIFEST": str(manifest_path),
            }
            drift = EvaluationRuntimeError("evaluation engine commit differs from the locked runtime")
            with mock.patch.dict(os.environ, env):
                with mock.patch("evaluation_runtime._expected_binding", side_effect=drift):
                    with self.assertRaises(EvaluationRuntimeError) as caught:
                        ensure_evaluation_runtime(Path(root / "engine"), prepare=False)

        self.assertIn("differs from the locked runtime", str(caught.exception))

    def test_an_unresolvable_binding_with_nothing_attested_reports_why(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            failure = EvaluationIdentityUnavailable("git is unavailable")
            with mock.patch.dict(os.environ, {}, clear=False):
                for key in (
                    "SURE_EVALUATION_RUNTIME_ID",
                    "SURE_EVALUATION_LOCK_SHA256",
                    "SURE_EVALUATION_RUNTIME_MANIFEST",
                ):
                    os.environ.pop(key, None)
                with mock.patch("evaluation_runtime._expected_binding", side_effect=failure):
                    with self.assertRaises(EvaluationRuntimeError) as caught:
                        ensure_evaluation_runtime(Path(root / "engine"), prepare=False)

        self.assertIn("git is unavailable", str(caught.exception))

    def test_attested_binding_is_used_without_running_git(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            empty_bin = root / "bin"
            empty_bin.mkdir()
            manifest_path = self._write_manifest(root)
            env = {
                "PATH": str(empty_bin),
                "SURE_EVALUATION_RUNTIME_ID": "sure-evaluation-root-v1-m1-cb9267e9f887-py311-abcdef012345",
                "SURE_EVALUATION_LOCK_SHA256": "abcdef0123456789",
                "SURE_EVALUATION_RUNTIME_MANIFEST": str(manifest_path),
                "SURE_EVALUATION_PYTHON": "/sure-runtime/bin/python",
                "SURE_EVALUATION_HOME": "/sure-engine",
            }
            with mock.patch.dict(os.environ, env):
                binding = ensure_evaluation_runtime(Path(root / "engine"), prepare=False)

        self.assertEqual(binding["engine_commit"], "cb9267e9f887b1619f8449e49da828c77960a52e")
        # The container's own mount paths win over the host paths in the manifest.
        self.assertEqual(binding["python_executable"], "/sure-runtime/bin/python")
        self.assertEqual(binding["engine_root"], "/sure-engine")
        self.assertIn("attested", binding["verification"])

    def test_attested_identity_must_match_its_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest_path = self._write_manifest(root)
            env = {
                "SURE_EVALUATION_RUNTIME_ID": "sure-evaluation-root-v1-m1-deadbeefdead-py311-abcdef012345",
                "SURE_EVALUATION_LOCK_SHA256": "abcdef0123456789",
                "SURE_EVALUATION_RUNTIME_MANIFEST": str(manifest_path),
            }
            with mock.patch.dict(os.environ, env):
                with self.assertRaises(EvaluationRuntimeError) as caught:
                    ensure_evaluation_runtime(Path(root / "engine"), prepare=False)
        self.assertIn("runtime_id", str(caught.exception))

    def test_attested_lock_mismatch_names_the_lock_not_the_id(self) -> None:
        # The first version of this message printed only runtime_id, so a lock
        # mismatch reported two identical ids and told the operator nothing.
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest_path = self._write_manifest(root)
            env = {
                "SURE_EVALUATION_RUNTIME_ID": "sure-evaluation-root-v1-m1-cb9267e9f887-py311-abcdef012345",
                "SURE_EVALUATION_LOCK_SHA256": "0000000000000000",
                "SURE_EVALUATION_RUNTIME_MANIFEST": str(manifest_path),
            }
            with mock.patch.dict(os.environ, env):
                with self.assertRaises(EvaluationRuntimeError) as caught:
                    ensure_evaluation_runtime(Path(root / "engine"), prepare=False)
        message = str(caught.exception)
        self.assertIn("lock_sha256", message)
        self.assertIn("0000000000000000", message)
        self.assertNotIn("runtime_id", message)


if __name__ == "__main__":
    unittest.main()
