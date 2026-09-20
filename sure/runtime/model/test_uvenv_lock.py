from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import bootstrap  # noqa: F401  # imported for its side effect: repository root onto sys.path
from sure.runtime import uvenv

REPO_ROOT = Path(uvenv.__file__).resolve().parents[2]

# Longer than the ~9s that msvcrt.locking(LK_LOCK) waits before giving up with
# EDEADLOCK, so a Windows lock that does not retry fails this test instead of
# silently reappearing as a cold-start crash in the second process.
HOLD_SECONDS = 12.0

CHILD = """
import sys, time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from sure.runtime.uvenv import exclusive_lock

with exclusive_lock(Path(sys.argv[2])):
    print("held", flush=True)
    time.sleep(float(sys.argv[3]))
"""


class ExclusiveLockTests(unittest.TestCase):
    def test_second_process_waits_for_a_long_held_lock(self) -> None:
        """A cold start downloads CPython and every wheel; the loser must wait, not crash."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        lock_path = Path(temporary.name) / "runtime.lock"

        child = subprocess.Popen(
            [sys.executable, "-c", CHILD, str(REPO_ROOT), str(lock_path), str(HOLD_SECONDS)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def stop() -> None:
            child.kill()
            child.wait(timeout=30)
            child.stdout.close()
            child.stderr.close()

        # Registered after the directory, so it runs before it: Windows cannot
        # remove a file the holder still has open.
        self.addCleanup(stop)
        # Never read stderr here: unittest evaluates a failure message eagerly,
        # and reading it blocks until the holder exits, which frees the lock
        # this test is trying to contend for.
        self.assertEqual(child.stdout.readline().strip(), "held", "the holder never took the lock")

        start = time.monotonic()
        with uvenv.exclusive_lock(lock_path):
            waited = time.monotonic() - start

        self.assertGreater(waited, 10.5, "the lock was handed over before the holder released it")
        self.assertEqual(child.wait(timeout=30), 0, "the lock holder did not exit cleanly")


if __name__ == "__main__":
    unittest.main()
