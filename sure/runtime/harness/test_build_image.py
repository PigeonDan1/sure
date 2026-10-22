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
        root.mkdir(parents=True)
        (root / "runtime-manifest.json").write_text(
            json.dumps({
                "runtime_id": "sure-harness-v1-py311-abc123",
                "lock_sha256": lock_sha256,
                "python_version": "3.11.13",
            }),
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


    def test_the_build_names_the_runtime_id_so_the_image_lands_at_its_final_path(self) -> None:
        """A uv venv is path-bound, so the image must build its own at the destination."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._runtime_root(tmp)
            output = Path(tmp) / "runtime-image.json"
            inspect = _completed(json.dumps({"RepoDigests": ["registry.example/sure-harness@sha256:" + "a" * 64]}))
            recorded: list[list[str]] = []

            def record(command: list[str]):
                recorded.append(command)
                return _completed() if command[:2] == ["docker", "build"] else inspect

            with mock.patch.object(build_image, "run", side_effect=record), \
                 mock.patch.object(sys, "argv", [
                     "build_image.py", "--runtime-root", str(root),
                     "--image", "registry.example/sure-harness:v1", "--output", str(output),
                 ]):
                self.assertEqual(build_image.main(), 0)

            build = recorded[0]
            self.assertIn("RUNTIME_ID=sure-harness-v1-py311-abc123", build)
            self.assertIn("harness_runtime_spec=" + str(build_image.SPEC_PATH.parent.parent), build)
            self.assertNotIn(str(root), " ".join(build))

    def test_the_build_pins_the_interpreter_the_host_manifest_records(self) -> None:
        """Otherwise the image ships one build of Python and claims another."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._runtime_root(tmp)
            inspect = _completed(json.dumps({"RepoDigests": []}))
            recorded: list[list[str]] = []

            def record(command: list[str]):
                recorded.append(command)
                return _completed() if command[:2] == ["docker", "build"] else inspect

            with mock.patch.object(build_image, "run", side_effect=record), \
                 mock.patch.object(sys, "argv", [
                     "build_image.py", "--runtime-root", str(root),
                     "--image", "registry.example/sure-harness:v1",
                 ]):
                self.assertEqual(build_image.main(), 0)
            self.assertIn("PYTHON_FULL_VERSION=3.11.13", recorded[0])

    def test_a_manifest_without_an_interpreter_version_cannot_be_sealed(self) -> None:
        """The exact version is a build input now, so a manifest missing it must stop."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._runtime_root(tmp)
            manifest = json.loads((root / "runtime-manifest.json").read_text(encoding="utf-8"))
            del manifest["python_version"]
            (root / "runtime-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with mock.patch.object(sys, "argv", [
                "build_image.py", "--runtime-root", str(root),
                "--image", "registry.example/sure-harness:v1",
            ]):
                with self.assertRaises(ValueError) as caught:
                    build_image.main()
            self.assertIn("python_version", str(caught.exception))


class DockerfileTest(unittest.TestCase):
    """The Dockerfile cannot be run here, so pin the parts the gates depend on."""

    def setUp(self) -> None:
        self.text = (build_image.SPEC_PATH.parent / "Dockerfile").read_text(encoding="utf-8")

    def test_the_image_builds_the_runtime_instead_of_copying_the_host(self) -> None:
        """Copying a uv venv into an image ships an interpreter that cannot start."""
        self.assertNotIn("harness_runtime_source", self.text)
        self.assertIn("bootstrap.py", self.text)
        # The bootstrap is the single source of the relocation recipe: the
        # Dockerfile must run it and must not hand-roll the move/sed/symlink
        # sequence, or the two copies of the recipe would drift apart again.
        self.assertIn("$base\" \"$bootstrap\" --runtime-root /opt/sure-harness", self.text)
        self.assertNotIn("mv /opt/harness-base-python", self.text)
        self.assertNotIn("sed -i", self.text)
        self.assertNotIn("ln -sfn", self.text)
        self.assertIn("COPY --from=build /opt/sure-harness/${RUNTIME_ID}/ /", self.text)
        # The runtime the image built must be the runtime the host manifest names.
        self.assertIn('[ -d "$dest" ]', self.text)

    def test_the_self_contained_runtime_is_verified_before_the_check(self) -> None:
        """bootstrap's own verification must run; `--check` is the image's re-proof."""
        self.assertIn('"$dest/bin/python" "$bootstrap" --check --runtime-root /opt/sure-harness', self.text)
        check = self.text.index("--check --runtime-root")
        # bootstrap.py materializes and verifies the tree before the image
        # re-checks it with the tree's own interpreter.
        self.assertLess(self.text.index("$base\" \"$bootstrap\" --runtime-root /opt/sure-harness"), check)

    def test_no_shell_pipeline_swallows_a_failing_scan(self) -> None:
        """The relocation proof now lives in bootstrap.py, so the Dockerfile must
        not contain a `find | while` scan whose failure dash could swallow."""
        self.assertNotIn("| while", self.text)
        self.assertNotIn("-xtype l", self.text)
        self.assertNotIn("grep -rlI", self.text)

    def test_the_interpreter_is_pinned_to_the_one_the_host_manifest_records(self) -> None:
        """"3.11" would let two builds of one lock ship different interpreter bytes."""
        self.assertIn('uv python install "${PYTHON_FULL_VERSION}"', self.text)
        # The bootstrap asks uv for "3.11"; without this it could fetch a newer patch.
        self.assertIn("uv python find \"${PYTHON_FULL_VERSION}\"", self.text)
        self.assertIn('[ "$built" = "${PYTHON_FULL_VERSION}" ]', self.text)


if __name__ == "__main__":
    unittest.main()
