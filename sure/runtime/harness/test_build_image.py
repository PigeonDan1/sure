from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_image  # noqa: E402


def _completed(stdout: str = "", returncode: int = 0):
    return mock.Mock(returncode=returncode, stdout=stdout, stderr="")


class BuildImageLockTest(unittest.TestCase):
    def _runtime_root(self, tmp: str) -> Path:
        spec = build_image.read_json(build_image.SPEC_PATH)
        lock_sha256 = build_image.sha256_file(build_image.SPEC_PATH.parent / str(spec["lock_file"]))
        root = Path(tmp) / "sure-harness-v1-py311-abc123"
        (root / "bin").mkdir(parents=True)
        (root / "bin" / "python").write_text("", encoding="utf-8")
        (root / "runtime-manifest.json").write_text(
            json.dumps({"runtime_id": "sure-harness-v1-py311-abc123", "lock_sha256": lock_sha256}),
            encoding="utf-8",
        )
        return root

    def test_lock_file_records_no_host_filesystem_path(self) -> None:
        """The JSON lock is committed into public core, so it must carry no host paths."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._runtime_root(tmp)
            output = Path(tmp) / "runtime-image.json"
            inspect = _completed(json.dumps({"RepoDigests": ["registry.example/sure-harness@sha256:" + "a" * 64]}))
            with mock.patch.object(build_image, "run", side_effect=[_completed(), inspect]), \
                 mock.patch.object(sys, "argv", [
                     "build_image.py", "--runtime-root", str(root),
                     "--image", "registry.example/sure-harness:v1", "--output", str(output),
                 ]):
                self.assertEqual(build_image.main(), 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            blob = json.dumps(payload)
            self.assertNotIn(str(root), blob)
            self.assertNotIn(str(build_image.SPEC_PATH.parent), blob)
            self.assertNotIn("build_command", payload)


    def test_digest_pin_keeps_the_repository_when_the_image_has_a_port_and_no_tag(self) -> None:
        """rsplit(':') on a registry port would fabricate a wrong but well-formed pin."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._runtime_root(tmp)
            output = Path(tmp) / "runtime-image.json"
            digest = "sha256:" + "b" * 64
            push = _completed(f"latest: digest: {digest} size: 1234")
            inspect = _completed(json.dumps({"RepoDigests": []}))
            with mock.patch.object(build_image, "run", side_effect=[_completed(), push, inspect]), \
                 mock.patch.object(sys, "argv", [
                     "build_image.py", "--runtime-root", str(root),
                     "--image", "registry.example:5000/hpc/sure-harness", "--push", "--output", str(output),
                 ]):
                self.assertEqual(build_image.main(), 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["image_ref"], f"registry.example:5000/hpc/sure-harness@{digest}")


if __name__ == "__main__":
    unittest.main()
