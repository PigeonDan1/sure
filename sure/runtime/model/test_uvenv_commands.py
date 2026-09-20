from __future__ import annotations

import unittest
from pathlib import Path

import bootstrap  # noqa: F401  # imported for its side effect: repository root onto sys.path
from sure.runtime import uvenv

# Relative paths only: str(Path(...)) has to read the same on POSIX and Windows.
STAGE = Path("stage")
PYTHON = Path("stage/bin/python")
LOCK = Path("requirements.lock.txt")


class UvCommandTests(unittest.TestCase):
    """The three runtimes are built by these builders, so pin their whole argv.

    `--require-hashes` and `--strict` are the integrity gate: without an
    assertion here both could be deleted and every runtime suite would stay
    green, because nothing else inspects the commands uv is given.
    """

    def test_sync_command_argv(self) -> None:
        self.assertEqual(
            uvenv.sync_command("uv", PYTHON, LOCK, allow_python_downloads=True),
            ["uv", "pip", "sync", "--python", str(PYTHON), "--require-hashes", "--strict", str(LOCK)],
        )

    def test_sync_command_always_requires_hashes_and_strict(self) -> None:
        for allow_empty in (False, True):
            for downloads in (False, True):
                command = uvenv.sync_command(
                    "uv", PYTHON, LOCK,
                    allow_python_downloads=downloads,
                    allow_empty=allow_empty,
                    extra=["--break-system-packages"],
                )
                with self.subTest(allow_empty=allow_empty, downloads=downloads):
                    self.assertIn("--require-hashes", command)
                    self.assertIn("--strict", command)

    def test_sync_command_optional_flags_follow_their_arguments(self) -> None:
        plain = uvenv.sync_command("uv", PYTHON, LOCK, allow_python_downloads=True)
        self.assertNotIn("--allow-empty-requirements", plain)
        self.assertNotIn("--no-python-downloads", plain)
        self.assertIn(
            "--allow-empty-requirements",
            uvenv.sync_command("uv", PYTHON, LOCK, allow_python_downloads=True, allow_empty=True),
        )
        self.assertIn(
            "--no-python-downloads",
            uvenv.sync_command("uv", PYTHON, LOCK, allow_python_downloads=False),
        )

    def test_sync_command_puts_extra_before_the_lock_path(self) -> None:
        """uv reads the trailing argument as the requirement file."""
        command = uvenv.sync_command("uv", PYTHON, LOCK, allow_python_downloads=True, extra=["--index", "off"])
        self.assertEqual(command[-1], str(LOCK))
        self.assertLess(command.index("--index"), command.index(str(LOCK)))

    def test_venv_command_argv(self) -> None:
        self.assertEqual(
            uvenv.venv_command("uv", STAGE, python="3.11", allow_python_downloads=True),
            ["uv", "venv", "--no-project", "--python", "3.11", str(STAGE)],
        )
        self.assertIn(
            "--no-python-downloads",
            uvenv.venv_command("uv", STAGE, python="3.11", allow_python_downloads=False),
        )

    def test_freeze_command_argv(self) -> None:
        self.assertEqual(
            uvenv.freeze_command("uv", PYTHON, allow_python_downloads=True),
            ["uv", "pip", "freeze", "--python", str(PYTHON), "--strict"],
        )
        self.assertIn(
            "--no-python-downloads",
            uvenv.freeze_command("uv", PYTHON, allow_python_downloads=False),
        )


if __name__ == "__main__":
    unittest.main()
