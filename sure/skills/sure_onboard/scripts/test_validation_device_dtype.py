from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from run_validate import env_for, rewrite_container_env, run_validation_command, validation_device_for, validate_artifact_device


class ValidationDeviceDtypeTests(unittest.TestCase):
    def test_missing_container_env_is_before_image_with_or_without_entrypoint(self):
        for engine in ("docker", "podman"):
            for options in ([], ["--entrypoint", "python"], ["--entrypoint=python"]):
                with self.subTest(engine=engine, options=options):
                    command = [engine, "run", "--rm", *options, "myimage", "python", "validate.py"]
                    result = rewrite_container_env(command, {"SURE_MODEL_DTYPE": "float32"})
                    self.assertEqual(result[2:4], ["-e", "SURE_MODEL_DTYPE=float32"])
                    self.assertEqual(result[result.index("myimage"):], ["myimage", "python", "validate.py"])
                    self.assertNotIn("SURE_MODEL_DTYPE=float32", command)

    def test_rewrites_all_env_forms_and_preserves_container_arguments(self):
        for option in (["-e", "DEVICE=cpu"], ["--env", "DEVICE=cpu"],
                       ["--env=DEVICE=cpu"], ["-eDEVICE=cpu"], ["-e", "DEVICE"]):
            with self.subTest(option=option):
                command = ["docker", "run", "-it", "-v", "/tmp:/data", *option,
                           "--gpus", "all", "image", "--env", "DEVICE=cpu"]
                result = rewrite_container_env(command, {"DEVICE": "cuda"})
                self.assertEqual(result[result.index("image"):], ["image", "--env", "DEVICE=cpu"])
                self.assertTrue("DEVICE=cuda" in result or "--env=DEVICE=cuda" in result)

    def test_double_dash_and_non_container_commands(self):
        result = rewrite_container_env(["podman", "run", "--", "image"], {"DEVICE": "cuda"})
        self.assertEqual(result, ["podman", "run", "-e", "DEVICE=cuda", "--", "image"])
        for command in (["python", "validate.py"], "python validate.py"):
            self.assertEqual(rewrite_container_env(command, {"DEVICE": "cuda"}), command)
        with self.assertRaises(ValueError):
            rewrite_container_env(["docker", "run", "--unknown", "value", "image"], {"DEVICE": "cuda"})

    def test_legacy_and_concrete_devices_reach_real_local_process(self):
        with tempfile.TemporaryDirectory() as root:
            run_dir = Path(root)
            artifacts = run_dir / "artifacts"
            artifacts.mkdir()
            for device in ("cuda", "cuda:0", "cuda:2", "cpu", "mps"):
                with self.subTest(device=device):
                    (artifacts / "env_compat_result.json").write_text(json.dumps({"device": "cuda", "resolved_device": device}))
                    target = validation_device_for(run_dir)
                    self.assertEqual(target, device)
                    command = [sys.executable, "-c", "import os; print(os.environ['DEVICE'])"]
                    result = run_validation_command({"run_command": command}, "load", run_dir, target)
                    self.assertEqual(result[0], 0, result[3])
                    self.assertEqual(result[3], device)

    def test_container_and_local_commands_use_same_bare_device(self):
        with tempfile.TemporaryDirectory() as root:
            command = ["docker", "run", "--rm", "image", "python", "validate.py"]
            with patch("run_validate.subprocess.run", return_value=subprocess.CompletedProcess(command, 0, "", "")) as run:
                run_validation_command({"run_command": command}, "load", Path(root), "cuda")
            actual = run.call_args
            self.assertIn("DEVICE=cuda", actual.args[0])
            self.assertNotIn("DEVICE=cuda:0", actual.args[0])
            self.assertEqual(actual.kwargs["env"]["DEVICE"], "cuda")

    def test_environment_and_conflicting_artifacts(self):
        environment = env_for({"env": {"DEVICE": "cpu", "SURE_MODEL_DTYPE": "float16"}}, "cuda:2", "float32")
        self.assertEqual(environment["DEVICE"], "cuda:2")
        self.assertEqual(environment["SURE_MODEL_DTYPE"], "float32")
        self.assertIsNone(validate_artifact_device({"device": "cuda"}, "cuda:0"))
        self.assertIsNotNone(validate_artifact_device({"device": "cuda:2"}, "cuda:0"))
        self.assertIsNotNone(validate_artifact_device({"device": "cuda", "env": {"SURE_DEVICE": "cpu"}}, "cuda"))


if __name__ == "__main__":
    unittest.main()
