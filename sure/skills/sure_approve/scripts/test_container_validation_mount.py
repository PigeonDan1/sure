#!/usr/bin/env python3
from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import approval_core


class ContainerValidationMountTests(unittest.TestCase):
    def _verify(self, *, model_target: str = "/workspace/model", ship_artifacts: bool = True, ship_outputs: bool = False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            candidate = root / "candidate"
            artifacts = candidate / "artifacts"
            if ship_artifacts:
                artifacts.mkdir(parents=True)
                (artifacts / "artifact_manifest.json").write_text("{}\n", encoding="utf-8")
                (artifacts / "fixture_manifest.json").write_text("{}\n", encoding="utf-8")
                if ship_outputs:
                    outputs = artifacts / "outputs"
                    outputs.mkdir()
                    outputs.chmod(0o750)
                    sample = outputs / "sample.wav"
                    sample.write_text("audio\n", encoding="utf-8")
                    sample.chmod(0o640)
            else:
                candidate.mkdir(parents=True)
            (run_dir / "artifacts").mkdir(parents=True)
            (run_dir / "artifacts" / "approval_manifest.json").write_text(
                json.dumps({"candidate_dir": str(candidate), "candidate_digest": "digest", "model_name": "demo"}),
                encoding="utf-8",
            )
            (run_dir / "artifacts" / "approve_input_resolved.json").write_text(
                json.dumps({"site_policy": {"path": str(root / "site.yaml")}}),
                encoding="utf-8",
            )

            binding = {
                "runtime_kind": "container",
                "target_image_ref": "registry.example/demo@sha256:" + "a" * 64,
                "container": {
                    "model_mount": {"target": model_target},
                    "working_dir": "/workspace/model",
                    "python_executable": "/usr/local/bin/python",
                    "gpu_required": False,
                },
            }
            observed: dict[str, object] = {}

            def run(command: list[str], **_: object) -> mock.Mock:
                if command[1] == "pull":
                    return mock.Mock(returncode=0, stdout="", stderr="")
                observed["command"] = command
                mounts = [value for value in command if value.startswith("type=bind")]
                observed["mounts"] = mounts
                validation_mount = next(value for value in mounts if value.endswith(",dst=/workspace/model/artifacts"))
                validation_source = Path(
                    validation_mount.removeprefix("type=bind,src=").removesuffix(",dst=/workspace/model/artifacts")
                )
                observed["copied_files"] = sorted(path.name for path in validation_source.iterdir())
                observed["modes"] = {
                    str(path.relative_to(validation_source)): stat.S_IMODE(path.stat().st_mode)
                    for path in validation_source.rglob("*")
                }
                return mock.Mock(returncode=0, stdout="", stderr="")

            with (
                mock.patch.object(approval_core, "tree_digest", return_value=("digest", [], [])),
                mock.patch.object(approval_core, "load_deployment_binding", return_value=binding),
                mock.patch.object(approval_core, "verify_server_contract", return_value={"status": "passed"}),
                mock.patch.object(approval_core.subprocess, "run", side_effect=run),
            ):
                result = approval_core.verify_runtime(run_dir)
            return candidate, result, observed

    def test_validate_smoke_overlays_writable_copy_of_artifacts(self) -> None:
        candidate, result, observed = self._verify()

        mounts = observed["mounts"]
        assert isinstance(mounts, list)
        # verify_runtime resolves the manifest's candidate_dir; Windows temp paths
        # come back in their 8.3 spelling, so compare the resolved form.
        self.assertIn(f"type=bind,src={candidate.resolve()},dst=/workspace/model,readonly", mounts)
        self.assertTrue(any(value.endswith(",dst=/workspace/model/artifacts") for value in mounts))
        self.assertEqual(observed["copied_files"], ["artifact_manifest.json", "fixture_manifest.json"])
        self.assertEqual(result["status"], "passed")

    def test_validation_mount_target_normalizes_a_trailing_slash(self) -> None:
        _, _, observed = self._verify(model_target="/workspace/model/")

        mounts = observed["mounts"]
        assert isinstance(mounts, list)
        self.assertTrue(any(value.endswith(",dst=/workspace/model/artifacts") for value in mounts))
        self.assertFalse(any("//artifacts" in value for value in mounts))

    def test_validate_smoke_refuses_a_candidate_without_artifacts(self) -> None:
        with self.assertRaisesRegex(approval_core.ApprovalError, "no artifacts/ directory"):
            self._verify(ship_artifacts=False)

    def test_overlay_opens_nested_artifacts_for_non_root_images(self) -> None:
        _, result, observed = self._verify(ship_outputs=True)

        self.assertEqual(result["status"], "passed")
        modes = observed["modes"]
        assert isinstance(modes, dict)
        # copytree preserves the restrictive host bits; the overlay must reopen
        # every directory and file so a non-root container USER can write.
        self.assertEqual(modes["outputs"], 0o777)
        self.assertEqual(modes["outputs/sample.wav"], 0o666)


if __name__ == "__main__":
    unittest.main()
