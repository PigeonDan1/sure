#!/usr/bin/env python3
"""Regression tests for run_validate path handling.

normalize_repo_relative_text: on Windows, str(repo_root / "sure" / "models") contains backslashes (e.g.
"C:\\src\\sure-test\\sure\\models"). re.sub treats backslashes in a *string*
replacement argument as escape sequences (\\s, \\1, ...), so passing that
string straight to re.sub raises re.error: bad escape \\s instead of
performing the substitution.

Run directly:
    cd sure/skills/sure_onboard/scripts && python test_run_validate_paths.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_validate


class WindowsPathSubstitution(unittest.TestCase):
    def test_backslash_repo_root_does_not_raise(self) -> None:
        repo_root = Path(r"C:\src\sure-test")
        value = "sure/models/foo/config.yaml"

        result = run_validate.normalize_repo_relative_text(value, repo_root)

        # Expected output built by plain string concatenation (not by
        # re-running the regex under test), so this pins the actual
        # requirement: the "sure/models/" prefix becomes the absolute
        # repo_root path, and the rest of value is untouched.
        expected = str(repo_root / "sure" / "models") + "/" + "foo/config.yaml"
        self.assertEqual(result, expected)

    def test_posix_repo_root_output_is_unchanged(self) -> None:
        """The fix must be a no-op off Windows: prove the new function-based
        re.sub call produces byte-identical output to the old string-based
        re.sub call, for a repo_root with no backslashes. PurePosixPath is
        used (instead of Path) so this holds regardless of the host OS
        actually running this test.
        """
        repo_root = PurePosixPath("/home/user/sure-test")
        value = "sure/models/foo/config.yaml"
        pattern = r"(?<![A-Za-z0-9_./-])sure/models/"
        old_style_replacement = str(repo_root / "sure" / "models") + "/"

        # Re-run today's (pre-fix) string-based re.sub call directly. This is
        # safe here because a POSIX repo_root never contains a backslash, so
        # this call cannot raise re.error the way the Windows case does.
        pre_fix_output = re.sub(pattern, old_style_replacement, value)

        result = run_validate.normalize_repo_relative_text(value, repo_root)

        self.assertEqual(result, pre_fix_output)
        self.assertEqual(result, "/home/user/sure-test/sure/models/foo/config.yaml")


class CommandMustNameWrapperManifestEntrypoint(unittest.TestCase):
    """The validation command may only run the wrapper that generate_wrapper
    recorded in wrapper_manifest.json; an agent-supplied stub is refused."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.run_dir = root / "run"
        (self.run_dir / "artifacts").mkdir(parents=True)
        self.model_dir = root / "model"
        self.model_dir.mkdir()
        for name in ("validate.py", "server.py"):
            (self.model_dir / name).write_text("", encoding="utf-8")
        self.stub = root / "other" / "stub.py"
        self.stub.parent.mkdir()
        self.stub.write_text("print('ok')\n", encoding="utf-8")
        self.check = run_validate._check_command_against_wrapper_manifest

    def write_manifest(self, **fields: str) -> None:
        manifest = {"wrapper_path": str(self.model_dir), **fields}
        (self.run_dir / "artifacts" / "wrapper_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_validate_py_matching_manifest_passes(self) -> None:
        self.write_manifest(validate_py="validate.py", server_py="server.py")
        command = [sys.executable, str(self.model_dir / "validate.py"), "--stage", "import"]
        self.assertIsNone(self.check(command, self.model_dir, self.run_dir, allowed_keys=("validate_py",)))

    def test_validate_py_pointing_elsewhere_is_refused_with_both_paths(self) -> None:
        self.write_manifest(validate_py="validate.py", server_py="server.py")
        command = [sys.executable, str(self.stub)]
        problem = self.check(command, self.stub.parent, self.run_dir, allowed_keys=("validate_py",))
        self.assertIsNotNone(problem)
        self.assertIn(str(self.stub.resolve()), problem)
        self.assertIn(str((self.model_dir / "validate.py").resolve()), problem)

    def test_run_command_list_naming_server_py_passes(self) -> None:
        self.write_manifest(validate_py="validate.py", server_py="server.py")
        command = ["python", str(self.model_dir / "server.py"), "--smoke"]
        self.assertIsNone(self.check(command, self.model_dir, self.run_dir))

    def test_run_command_string_resolves_relative_to_cwd(self) -> None:
        self.write_manifest(validate_py="validate.py", server_py="server.py")
        self.assertIsNone(self.check("python validate.py --stage import", self.model_dir, self.run_dir))

    def test_run_command_naming_foreign_py_is_refused(self) -> None:
        self.write_manifest(validate_py="validate.py", server_py="server.py")
        command = ["python", "-c", "import x", str(self.stub)]
        problem = self.check(command, self.model_dir, self.run_dir)
        self.assertIsNotNone(problem)
        self.assertIn(str(self.stub.resolve()), problem)

    def test_missing_manifest_is_refused(self) -> None:
        command = [sys.executable, str(self.model_dir / "validate.py")]
        problem = self.check(command, self.model_dir, self.run_dir, allowed_keys=("validate_py",))
        self.assertIsNotNone(problem)
        self.assertIn("wrapper_manifest.json", problem)

    def test_manifest_without_validate_py_is_refused(self) -> None:
        self.write_manifest(server_py="server.py")
        command = ["python", str(self.model_dir / "server.py")]
        problem = self.check(command, self.model_dir, self.run_dir)
        self.assertIsNotNone(problem)
        self.assertIn("wrapper_manifest.json", problem)


if __name__ == "__main__":
    unittest.main()
