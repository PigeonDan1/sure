#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import check_artifact  # noqa: E402
import materialize_trans_inputs  # noqa: E402
import run_execution_compat  # noqa: E402
import run_trans_validate  # noqa: E402
from vc_exec import VcSpec  # noqa: E402


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def local_site_policy(root: Path) -> dict:
    return {
        "path": str(root / "site.local.yaml"),
        "sha256": "a" * 64,
        "policy": {
            "execution": {"surfaces": ["local"], "local_runtimes": ["container"]},
        },
    }


class LocalGpuExecutionTests(unittest.TestCase):
    def test_materialize_selects_site_approved_local_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = root / "source"
            context.mkdir()
            dockerfile = context / "Dockerfile"
            dockerfile.write_text("FROM scratch\n", encoding="utf-8")
            model = root / "model"
            model.mkdir()
            entrypoint = context / "infer.py"
            entrypoint.write_text(
                "import torch\nassert torch.cuda.is_available()\nassert torch.cuda.is_bf16_supported()\n",
                encoding="utf-8",
            )
            run_dir = root / ".sure" / "runs" / "run-local"
            arguments = [
                "materialize_trans_inputs.py",
                "--dockerfile",
                str(dockerfile),
                "--model",
                str(model),
                "--inference-entrypoint",
                str(entrypoint),
                "--framework",
                "pytorch",
                "--model-framework",
                "transformers",
                "--model-name",
                "demo__model",
                "--task-type",
                "asr",
                "--device",
                "cuda",
                "--execution",
                "local",
                "--image-version",
                "0.1.0",
                "--run-dir",
                str(run_dir),
                "--repo-root",
                str(root),
            ]
            with (
                mock.patch.object(materialize_trans_inputs, "load_site_policy", return_value=local_site_policy(root)),
                mock.patch.object(materialize_trans_inputs, "resolve_container_repository", side_effect=["registry/source", "registry/target"]),
                mock.patch.object(materialize_trans_inputs, "resolve_container_image", side_effect=["registry/target:0.1.0", "registry/source:0.1.0"]),
                mock.patch.object(materialize_trans_inputs, "resolve_image_version", return_value=("0.1.0", {"mode": "explicit", "repositories": [], "existing_tags": []})),
                mock.patch.object(sys, "argv", arguments),
            ):
                self.assertEqual(materialize_trans_inputs.main(), 0)
            payload = json.loads((run_dir / "artifacts" / "trans_input_resolved.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["execution_request"], "local")
            self.assertEqual(payload["execution_surface"], "local_docker")
            self.assertNotIn("vc_partition", payload)

    def test_materialize_rejects_vc_for_cpu_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = root / "source"
            context.mkdir()
            dockerfile = context / "Dockerfile"
            dockerfile.write_text("FROM scratch\n", encoding="utf-8")
            model = root / "model"
            model.mkdir()
            entrypoint = context / "infer.py"
            entrypoint.write_text("print('ok')\n", encoding="utf-8")
            arguments = [
                "materialize_trans_inputs.py",
                "--dockerfile",
                str(dockerfile),
                "--model",
                str(model),
                "--inference-entrypoint",
                str(entrypoint),
                "--framework",
                "pytorch",
                "--model-framework",
                "transformers",
                "--model-name",
                "demo__model",
                "--task-type",
                "asr",
                "--device",
                "cpu",
                "--execution",
                "vc",
                "--image-version",
                "0.1.0",
                "--run-dir",
                str(root / ".sure" / "runs" / "run-cpu-vc"),
                "--repo-root",
                str(root),
            ]
            with (
                mock.patch.object(materialize_trans_inputs, "load_site_policy", return_value=local_site_policy(root)),
                mock.patch.object(materialize_trans_inputs, "resolve_container_repository", side_effect=["registry/source", "registry/target"]),
                mock.patch.object(materialize_trans_inputs, "resolve_container_image", side_effect=["registry/target:0.1.0", "registry/source:0.1.0"]),
                mock.patch.object(materialize_trans_inputs, "resolve_image_version", return_value=("0.1.0", {"mode": "explicit", "repositories": [], "existing_tags": []})),
                mock.patch.object(sys, "argv", arguments),
            ):
                with self.assertRaisesRegex(ValueError, "execution=vc requires device=auto or cuda"):
                    materialize_trans_inputs.main()

    def test_compat_probe_uses_local_docker_gpu_without_vc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            write_json(
                artifacts / "trans_input_resolved.json",
                {
                    "source_kind": "docker",
                    "model_framework": "transformers",
                    "model_name": "demo__model",
                    "task_type": "asr",
                    "device": "cuda",
                    "execution_surface": "local_docker",
                    "gpu_required": True,
                    "bf16_required": True,
                    "image_version": "0.1.0",
                },
            )
            write_json(artifacts / "source_image_result.json", {"image_id": "sha256:source"})
            probe = subprocess.CompletedProcess(
                ["docker"],
                0,
                stdout=json.dumps(
                    {
                        "python_ok": True,
                        "torch": "2.9.1+cu128",
                        "transformers": "4.57.6",
                        "cuda_available": True,
                        "bf16_supported": True,
                        "gpu_name": "Example GPU",
                    }
                )
                + "\n",
                stderr="",
            )
            output = artifacts / "execution_compat.json"
            with (
                mock.patch.object(
                    run_execution_compat,
                    "run_probe",
                    return_value=(["docker", "run", "--rm", "--gpus", "all"], probe, 12.0),
                ) as run_probe,
                mock.patch.object(run_execution_compat, "run_vc_job", side_effect=AssertionError("VC called")),
                mock.patch.object(
                    sys,
                    "argv",
                    ["run_execution_compat.py", "--run-dir", str(run_dir), "--produces", str(output)],
                ),
            ):
                self.assertEqual(run_execution_compat.main(), 0)
            run_probe.assert_called_once_with("sha256:source", True)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["execution_surface"], "local_docker")
            self.assertEqual(payload["selected_device"], "cuda")
            self.assertEqual(payload["probe"]["gpu_name"], "Example GPU")
            self.assertNotIn("vc_job_id", payload)

    def test_validation_executes_local_gpu_docker_command_without_vc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            output = artifacts / "import_result.json"
            validation_dir = artifacts / "adapter_validation"
            command = [
                "docker", "run", "--rm", "--gpus", "all",
                "-v", f"{validation_dir}:/validation:rw",
                "-e", "SURE_VALIDATE_ARTIFACTS_DIR=/validation",
                "image", "true",
            ]
            write_json(output, {"status": "pending", "run_command": command})
            write_json(
                artifacts / "execution_compat.json",
                {"selected_device": "cuda", "execution_surface": "local_docker"},
            )
            write_json(artifacts / "trans_input_resolved.json", {"source_kind": "docker"})
            completed = subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

            def run_local(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
                validation_dir.mkdir(parents=True, exist_ok=True)
                write_json(validation_dir / "import_result.json", {"import_passed": True})
                return completed

            with (
                mock.patch.object(
                    run_trans_validate,
                    "docker_run_to_vc",
                    return_value=VcSpec(
                        image="image",
                        mounts=[f"{validation_dir}:/validation:rw"],
                        command=["true"],
                        env={"SURE_VALIDATE_ARTIFACTS_DIR": "/validation"},
                    ),
                ),
                mock.patch.object(run_trans_validate.subprocess, "run", side_effect=run_local) as run,
                mock.patch.object(run_trans_validate, "run_vc_validation", side_effect=AssertionError("VC called")),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "run_trans_validate.py",
                        "--run-dir",
                        str(run_dir),
                        "--produces",
                        str(output),
                        "--kind",
                        "import",
                    ],
                ),
            ):
                self.assertEqual(run_trans_validate.main(), 0)
            run.assert_called_once()
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["execution_surface"], "local_docker")
            self.assertNotIn("vc_job_id", payload)

    def test_validation_rejects_local_gpu_without_adapter_artifact_mount(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            output = artifacts / "import_result.json"
            command = ["docker", "run", "--rm", "--gpus", "all", "image", "true"]
            write_json(output, {"status": "pending", "run_command": command})
            write_json(
                artifacts / "execution_compat.json",
                {"selected_device": "cuda", "execution_surface": "local_docker"},
            )
            write_json(artifacts / "trans_input_resolved.json", {"source_kind": "docker"})
            with (
                mock.patch.object(
                    run_trans_validate,
                    "docker_run_to_vc",
                    return_value=VcSpec(image="image", mounts=[], command=["true"], env={}),
                ),
                mock.patch.object(run_trans_validate.subprocess, "run") as run,
                mock.patch.object(run_trans_validate, "run_vc_validation", side_effect=AssertionError("VC called")),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "run_trans_validate.py",
                        "--run-dir",
                        str(run_dir),
                        "--produces",
                        str(output),
                        "--kind",
                        "import",
                    ],
                ),
            ):
                with self.assertRaisesRegex(ValueError, "must set SURE_VALIDATE_ARTIFACTS_DIR"):
                    run_trans_validate.main()
            run.assert_not_called()

    def test_validation_rejects_local_gpu_without_stage_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            validation_dir = artifacts / "adapter_validation"
            output = artifacts / "import_result.json"
            command = [
                "docker", "run", "--rm", "--gpus", "all",
                "-v", f"{validation_dir}:/validation:rw",
                "-e", "SURE_VALIDATE_ARTIFACTS_DIR=/validation",
                "image", "true",
            ]
            write_json(output, {"status": "pending", "run_command": command})
            write_json(
                artifacts / "execution_compat.json",
                {"selected_device": "cuda", "execution_surface": "local_docker"},
            )
            write_json(artifacts / "trans_input_resolved.json", {"source_kind": "docker"})
            completed = subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")
            with (
                mock.patch.object(
                    run_trans_validate,
                    "docker_run_to_vc",
                    return_value=VcSpec(
                        image="image",
                        mounts=[f"{validation_dir}:/validation:rw"],
                        command=["true"],
                        env={"SURE_VALIDATE_ARTIFACTS_DIR": "/validation"},
                    ),
                ),
                mock.patch.object(run_trans_validate.subprocess, "run", return_value=completed) as run,
                mock.patch.object(run_trans_validate, "run_vc_validation", side_effect=AssertionError("VC called")),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "run_trans_validate.py",
                        "--run-dir",
                        str(run_dir),
                        "--produces",
                        str(output),
                        "--kind",
                        "import",
                    ],
                ),
            ):
                with self.assertRaisesRegex(ValueError, "evidence is missing or invalid"):
                    run_trans_validate.main()
            run.assert_called_once()
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")

    def test_original_inference_rejects_missing_declared_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            output = artifacts / "original_inference_result.json"
            command = ["docker", "run", "--rm", "--gpus", "all", "image", "true"]
            missing = artifacts / "original_outputs" / "missing.json"
            write_json(
                output,
                {
                    "status": "pending",
                    "input": "fixture.wav",
                    "output": str(missing),
                    "run_command": command,
                },
            )
            write_json(
                artifacts / "execution_compat.json",
                {"selected_device": "cuda", "execution_surface": "local_docker"},
            )
            write_json(artifacts / "trans_input_resolved.json", {"source_kind": "docker"})
            completed = subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")
            with (
                mock.patch.object(
                    run_trans_validate,
                    "docker_run_to_vc",
                    return_value=VcSpec(image="image", mounts=[], command=["true"], env={}),
                ),
                mock.patch.object(run_trans_validate.subprocess, "run", return_value=completed),
                mock.patch.object(run_trans_validate, "run_vc_validation", side_effect=AssertionError("VC called")),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "run_trans_validate.py",
                        "--run-dir",
                        str(run_dir),
                        "--produces",
                        str(output),
                        "--kind",
                        "original_inference",
                    ],
                ),
            ):
                with self.assertRaisesRegex(ValueError, "did not create a non-empty artifact"):
                    run_trans_validate.main()
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")
            self.assertFalse(payload["inference_passed"])
            self.assertFalse(payload["model_loaded"])

    def test_registry_accepts_local_digest_pinned_post_pull_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            write_json(
                artifacts / "execution_compat.json",
                {"selected_device": "cuda", "execution_surface": "local_docker"},
            )
            log_dir = artifacts / "local_gpu" / "post_pull_smoke"
            log_dir.mkdir(parents=True)
            digest = "sha256:" + "b" * 64
            image_ref = f"registry/demo@{digest}"
            write_json(
                log_dir / "mcp_smoke.json",
                {
                    "status": "passed",
                    "initialize": {"ok": True},
                    "tools_list": {"ok": True},
                    "tools_call": {"ok": True, "output_nonempty": True},
                },
            )
            output = artifacts / "docker_registry_result.json"
            write_json(
                output,
                {
                    "schema": "sure.trans.docker_registry_result.v1",
                    "status": "passed",
                    "target_image": "registry/demo:0.1.0",
                    "target_image_digest": digest,
                    "target_image_ref": image_ref,
                    "pull_verified": True,
                    "post_pull_smoke": {
                        "execution_surface": "local_docker",
                        "image_ref": image_ref,
                        "resolved_digest": digest,
                        "exit_code": 0,
                        "log_path": str(log_dir),
                    },
                },
            )
            with mock.patch.object(
                sys,
                "argv",
                ["check_artifact.py", "--run-dir", str(run_dir), "--produces", str(output), "--kind", "registry"],
            ):
                self.assertEqual(check_artifact.main(), 0)


if __name__ == "__main__":
    unittest.main()
