from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import bootstrap  # noqa: F401  # imported for its side effect: repository root onto sys.path
from sure.runtime import uvenv

# "bin" on POSIX, "Scripts" on Windows.
BIN_DIR = uvenv.runtime_python_relative().split("/")[0]


class PublishRelocationTests(unittest.TestCase):
    def test_publish_moves_a_venv_without_repointing_the_paths_baked_into_it(self) -> None:
        """A published runtime's console scripts still name the staging directory.

        A uv virtual environment is not relocatable, and publish() is a rename:
        every console script uv wrote carries an absolute path to the staging
        interpreter, which no longer exists once the rename is done. Nothing
        executes a console script today -- every caller runs
        `<runtime>/<runtime_python_relative()>`, which is a real interpreter and
        survives the move -- so this pins the gap rather than a wanted property.
        The day publish() repoints what it moves, `stale` goes empty and this
        test turns red: replace it with `assertEqual(stale, [])`, which is the
        invariant callers of a console script would need.

        The scan reads bytes, so it covers the Windows launcher too: uv writes
        the same absolute path into a binary .exe stub. It does not cover
        `pyvenv.cfg`, symlink targets, or anything outside the script directory.
        """
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        staging = root / "staging"
        (staging / BIN_DIR).mkdir(parents=True)
        # The shape uv gives a console script: a shebang naming the interpreter of
        # the environment it installed into.
        (staging / BIN_DIR / "console-script").write_bytes(
            b"#!" + str(staging / BIN_DIR / "python").encode() + b"\nfrom tool import main\n"
        )
        destination = root / "runtime"

        uvenv.publish(staging, destination)

        needle = str(staging).encode()
        stale = sorted(
            path.name for path in (destination / BIN_DIR).iterdir() if needle in path.read_bytes()
        )
        self.assertEqual(stale, ["console-script"])


if __name__ == "__main__":
    unittest.main()
