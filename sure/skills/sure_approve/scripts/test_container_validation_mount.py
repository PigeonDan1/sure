#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import approval_core


class ContainerValidationMountTests(unittest.TestCase):
    def test_validate_smoke_overlays_writable_copy_of_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            candidate = root / "candidate"
            artifacts = candidate / "artifacts"
            artifacts.mkdir(parents=True)
            (artifacts / "artifact_manifest.json").write_text("{}\n", encoding="utf-8")
            (artifacts / "fixture_manifest.json").write_text("{}\n", encoding="utf-8")
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
                    "model_mount": {"target": "/workspace/model"},
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
                validation_source = Path(validation_mount.removeprefix("type=bind,src=").removesuffix(",dst=/workspace/model/artifacts"))
                observed["copied_files"] = sorted(path.name for path in validation_source.iterdir())
                return mock.Mock(returncode=0, stdout="", stderr="")

            with (
                mock.patch.object(approval_core, "tree_digest", return_value=("digest", [], [])),
                mock.patch.object(approval_core, "load_deployment_binding", return_value=binding),
                mock.patch.object(approval_core, "verify_server_contract", return_value={"status": "passed"}),
                mock.patch.object(approval_core.subprocess, "run", side_effect=run),
            ):
                result = approval_core.verify_runtime(run_dir)

            mounts = observed["mounts"]
            assert isinstance(mounts, list)
            self.assertIn(f"type=bind,src={candidate},dst=/workspace/model,readonly", mounts)
            self.assertTrue(any(value.endswith(",dst=/workspace/model/artifacts") for value in mounts))
            self.assertEqual(observed["copied_files"], ["artifact_manifest.json", "fixture_manifest.json"])
            self.assertEqual(result["status"], "passed")


if __name__ == "__main__":
    unittest.main()
