#!/usr/bin/env python3
"""Docker-gated regression: a mounted Harness Runtime starts without the host Python.

PR #139's blocking finding was that the active runtime's ``bin/python`` was an
absolute symlink to the host interpreter, so ``docker run --entrypoint
/active/bin/python`` died with ``/bin/sh: 1: /active/bin/python: not found``.
This test materializes a real runtime on the host, constructs the exact local
container command for a Qwen-like approved binding, and then runs it against a
Linux image. The host interpreter is unreachable by construction: the image is a
glibc base with no CPython of the host's, and the entrypoint is the mounted
runtime's own interpreter.

Skipped wherever Docker (or uv, needed for the bootstrap) is unavailable; Public
CI provides both, and the bootstrap step is what makes the test slow, not the
container run.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from container_execution import build_local_container_command, container_path  # noqa: E402
from docker_runtime import resolve_docker_binary  # noqa: E402

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "runtime" / "harness" / "bootstrap.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.runtime.harness.bootstrap import resolve_runtime  # noqa: E402

BASE_IMAGE = "debian:bookworm-slim"
REQUIRED_IMPORTS = ("yaml", "structlog", "pydantic", "pydantic_settings", "rich", "typer")


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _probe_entrypoint() -> str:
    """Run by the mounted runtime's own Python inside the container.

    Imports the six runtime libraries the spec requires, parses its own source
    (so a script mounted into the container demonstrably runs), and reports the
    interpreter identity back as JSON. ``sys.prefix``/``sys.base_prefix`` are
    how the test proves the interpreter is the tree's own and not a host Python
    that leaked through.
    """
    names = list(REQUIRED_IMPORTS)
    return (
        "import ast,json,sys;"
        f"[__import__(name) for name in {names!r}];"
        "ast.parse(open(__file__, encoding='utf-8').read());"
        "print(json.dumps({'version':sys.version.split()[0],"
        "'prefix':sys.prefix,'base_prefix':sys.base_prefix,"
        "'entrypoint_parsed':True}))"
    )


@unittest.skipUnless(_docker_available(), "docker is not installed")
class ContainerHarnessRuntimeRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.repo = root / "repo"
        self.repo.mkdir()
        self.runtime_root = self.repo / "runtime"
        self.model = root / "models" / "qwen-like"
        self.model.mkdir(parents=True)
        self.run_dir = root / "out"
        self.run_dir.mkdir()
        self.control = root / "control"
        self.control.mkdir()
        self.entrypoint = self.control / "probe_entrypoint.py"
        self.entrypoint.write_text(_probe_entrypoint(), encoding="utf-8")

    def _pull_digest(self) -> str:
        subprocess.run(
            [resolve_docker_binary(), "pull", "-q", BASE_IMAGE],
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
        completed = subprocess.run(
            [
                resolve_docker_binary(),
                "inspect",
                "--format",
                "{{index .RepoDigests 0}}",
                BASE_IMAGE,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        image_ref = completed.stdout.strip()
        if "@sha256:" not in image_ref:
            self.skipTest(f"docker could not resolve a digest-pinned ref for {BASE_IMAGE}")
        return image_ref

    def test_a_mounted_runtime_starts_in_a_container_without_the_host_python(self) -> None:
        if shutil.which("uv") is None and not os.environ.get("SURE_UV_BIN", "").strip():
            self.skipTest("uv is not installed")
        contract = resolve_runtime(self.runtime_root)
        self.assertTrue(Path(contract["python_executable"]).is_file())

        image_ref = self._pull_digest()
        image = image_ref.split("@", 1)[0]
        digest = image_ref.rsplit("@", 1)[1]
        binding = {
            "schema": "sure.eval.deployment_binding.v2",
            "runtime_kind": "container",
            "model_name": "qwen-like",
            "model_dir": str(self.model),
            "source": "approved_nfs_models",
            "package_profile": "docker-registry",
            "target_image": image,
            "target_image_digest": digest,
            "target_image_ref": image_ref,
            "container": {
                "python_executable": "python",
                "working_dir": "/workspace/model",
                "server_command": ["python", "server.py"],
                "tool_names": ["transcribe_audio"],
                "gpu_required": False,
                "model_mount": {"source": str(self.model), "target": "/workspace/model", "read_only": True},
                "result_mount": {"target": "/sure-output", "read_only": False},
            },
            "policy": {
                "execution_mode": "container_only",
                "host_python_fallback": False,
                "image_override_allowed": False,
                "nfs_models_read_only": True,
            },
            "evidence": {
                "harness_runtime_source": "legacy_approval_evidence",
                "harness_runtime_participates_in_execution": False,
            },
        }
        eval_input = {
            "model": {"deployment_binding": binding},
            "runtime": {
                "run_dir": str(self.run_dir),
                "harness_runtime": {
                    "schema": "sure.harness.runtime.binding.v1",
                    "runtime_id": contract["runtime_id"],
                    "runtime_type": "harness_python",
                    "python_executable": contract["python_executable"],
                    "lock_sha256": contract["lock_sha256"],
                    "manifest_path": contract["manifest_path"],
                    "runtime_root": contract["runtime_root"],
                },
            },
            "datasets": [],
        }
        command, launch = build_local_container_command(
            surface={"env": {}},
            eval_input=eval_input,
            control_run_dir=self.control,
            entrypoint=self.entrypoint,
            repo_root=self.repo,
            device_request="cpu",
        )
        self.assertEqual(launch["harness_runtime"]["execution_source"], "active_injected_runtime")
        entry_index = command.index("--entrypoint")
        self.assertEqual(command[entry_index + 1], container_path(Path(contract["python_executable"])))
        self.assertIn(
            f"HARNESS_PYTHON_BIN={container_path(Path(contract['python_executable']))}",
            command,
        )

        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=1800,
            env={key: value for key, value in os.environ.items() if not key.startswith("PYTHON")},
        )
        if completed.returncode != 0:
            self.fail(
                f"container probe failed with exit {completed.returncode}\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        answer = json.loads(completed.stdout.strip().splitlines()[-1])
        mounted_root = container_path(Path(contract["runtime_root"]).resolve())
        self.assertTrue(answer["prefix"].startswith(mounted_root), answer)
        self.assertTrue(answer["base_prefix"].startswith(mounted_root), answer)
        self.assertTrue(answer["entrypoint_parsed"])


if __name__ == "__main__":
    unittest.main()
