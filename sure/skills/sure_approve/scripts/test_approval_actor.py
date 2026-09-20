#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from approval_core import _actor, _fsync_tree


class ApprovalActorTests(unittest.TestCase):
    def test_actor_records_the_os_user_everywhere(self) -> None:
        actor = _actor()
        self.assertTrue(actor["os_user"])

    def test_actor_omits_uid_where_the_platform_has_none(self) -> None:
        # Windows has no getuid/getgid. The approval_decision schema only
        # requires "actor" to be an object, so the keys are allowed to be absent
        # rather than faked with a sentinel that looks like a real uid.
        import approval_core

        with mock.patch.object(approval_core.os, "getuid", create=True, return_value=1000), \
             mock.patch.object(approval_core.os, "getgid", create=True, return_value=1000):
            self.assertEqual(_actor()["uid"], 1000)

        with mock.patch.object(approval_core, "os", _NoIdentityOs()):
            self.assertNotIn("uid", _actor())
            self.assertNotIn("gid", _actor())


class FsyncTreeTests(unittest.TestCase):
    def test_fsync_tree_flushes_a_staging_copy_on_this_platform(self) -> None:
        # Windows has no descriptor for a directory and refuses to flush a
        # read-only handle, so both halves of the walk are platform-specific.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "artifacts").mkdir()
            (root / "artifacts" / "publication_result.json").write_text("{}", encoding="utf-8")
            _fsync_tree(root)


class _NoIdentityOs:
    """Stands in for the os module on a platform without uid/gid."""

    name = "nt"

    def __getattr__(self, item: str):
        if item in ("getuid", "getgid"):
            raise AttributeError(item)
        import os

        return getattr(os, item)


if __name__ == "__main__":
    unittest.main()
