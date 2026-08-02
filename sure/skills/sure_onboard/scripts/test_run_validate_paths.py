#!/usr/bin/env python3
"""Regression tests for run_validate.normalize_repo_relative_text.

On Windows, str(repo_root / "sure" / "models") contains backslashes (e.g.
"C:\\src\\sure-test\\sure\\models"). re.sub treats backslashes in a *string*
replacement argument as escape sequences (\\s, \\1, ...), so passing that
string straight to re.sub raises re.error: bad escape \\s instead of
performing the substitution.

Run directly:
    cd sure/skills/sure_onboard/scripts && python test_run_validate_paths.py
"""
from __future__ import annotations

import re
import sys
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


if __name__ == "__main__":
    unittest.main()
