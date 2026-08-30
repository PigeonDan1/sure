#!/usr/bin/env python3
"""Unit tests for the SURE-TRANS vc execution helpers.

All vc and docker subprocesses are mocked; these tests never submit a real
queue job or push a real image.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))


# The partition name and registry host are site data; tests pin their own site
# policy so no real site value appears in this file.
_SITE_POLICY_DIR: tempfile.TemporaryDirectory | None = None
_SITE_POLICY_PREVIOUS: str | None = None
TEST_PARTITION = "gpu-test"
TEST_REGISTRY = "registry.example"


def setUpModule() -> None:
    global _SITE_POLICY_DIR, _SITE_POLICY_PREVIOUS
    _SITE_POLICY_DIR = tempfile.TemporaryDirectory()
    root = "/srv"
    document = "\n".join([
        "schema: sure.site.policy.v1",
        "site_id: test-site",
        "policy_version: 1",
        "storage:",
        f"  approved_models_roots: [{root}/models]",
        f"  approved_results_roots: [{root}/results]",
        f"  forbidden_output_roots: [{root}]",
        f"  runtime_root: {root}/runtime",
        "datasets:",
        f"  allowed_source_roots: [{root}/datasets]",
        "execution:",
        "  surfaces: [vc]",
        f"  vc_partitions: [{TEST_PARTITION}]",
        f"  vc_default_partition: {TEST_PARTITION}",
        "network:",
        f"  container_registry: {TEST_REGISTRY}",
        "container_delivery:",
        '  repository_template: "{registry}/hpc/ai_{task}-{model_name}"',
        "",
    ])
    path = Path(_SITE_POLICY_DIR.name) / "site.yaml"
    path.write_text(document, encoding="utf-8")
    _SITE_POLICY_PREVIOUS = os.environ.get("SURE_SITE_POLICY")
    os.environ["SURE_SITE_POLICY"] = str(path.resolve())


def tearDownModule() -> None:
    if _SITE_POLICY_PREVIOUS is None:
        os.environ.pop("SURE_SITE_POLICY", None)
    else:
        os.environ["SURE_SITE_POLICY"] = _SITE_POLICY_PREVIOUS
    if _SITE_POLICY_DIR is not None:
        _SITE_POLICY_DIR.cleanup()


import run_execution_compat
import run_trans_validate
import vc_exec
from vc_exec import (
    VcJobResult,
    VcSpec,
    diagnose_oom,
    docker_run_to_vc,
    ensure_registry_image,
    normalize_job_name,
    parse_job_id,
    registry_image,
    run_vc_job,
    safe_image_component,
    user_partitions,
    vc_available,
)

REGISTRY = TEST_REGISTRY
PROBE_OUT = json.dumps(
    {
        "python_ok": True,
        "torch": "2.9.1",
        "cuda_available": True,
        "bf16_supported": True,
        "transformers": "4.57.6",
    }
)

CPU_PROBE_OUT = json.dumps(
    {
        "python_ok": True,
        "torch": "2.9.1",
        "cuda_available": False,
        "bf16_supported": False,
        "transformers": "4.57.6",
    }
)


def completed(args: list[str], returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


class DockerRunToVcTest(unittest.TestCase):
    def test_translates_supported_flags(self) -> None:
        spec = docker_run_to_vc(
            [
                "docker", "run", "--rm", "-i", "--gpus", "all", "--name", "smoke",
                "-e", "A=1", "--env=B=2", "--env", "C", "-v", "/host:/cont:ro",
                "--volume=/h2:/c2", "--entrypoint", "python", "-w", "/work",
                "--shm-size", "8g", "img", "-c", "print(1)",
            ]
        )
        self.assertEqual(spec.image, "img")
        self.assertEqual(spec.mounts, ["/host:/cont:ro", "/h2:/c2"])
        self.assertEqual(spec.env["A"], "1")
        self.assertEqual(spec.env["B"], "2")
        self.assertEqual(spec.env["C"], os.environ.get("C", ""))
        self.assertEqual(spec.command, ["python", "-c", "print(1)"])
        self.assertEqual(spec.workdir, "/work")

    def test_accepts_shell_string(self) -> None:
        spec = docker_run_to_vc("docker run --entrypoint=python img -c 'print(1)'")
        self.assertEqual(spec.image, "img")
        self.assertEqual(spec.command, ["python", "-c", "print(1)"])

    def test_resolves_image_entrypoint_when_flag_absent(self) -> None:
        resolver = lambda image: (["python", "/opt/code/infer.py"], ["bash"])
        spec = docker_run_to_vc(
            ["docker", "run", "-v", "/h:/c:ro", "img", "/wav", "--model-dir", "/models"],
            resolve_entrypoint=resolver,
        )
        self.assertEqual(spec.image, "img")
        self.assertEqual(
            spec.command,
            ["python", "/opt/code/infer.py", "/wav", "--model-dir", "/models"],
        )

    def test_uses_image_cmd_when_no_command_given(self) -> None:
        resolver = lambda image: (None, ["bash"])
        spec = docker_run_to_vc(["docker", "run", "img"], resolve_entrypoint=resolver)
        self.assertEqual(spec.command, ["bash"])

    def test_explicit_entrypoint_skips_resolver(self) -> None:
        def failing_resolver(image: str):
            raise AssertionError("resolver must not run when --entrypoint is given")

        spec = docker_run_to_vc(
            ["docker", "run", "--entrypoint", "python", "img", "-c", "print(1)"],
            resolve_entrypoint=failing_resolver,
        )
        self.assertEqual(spec.command, ["python", "-c", "print(1)"])

    def test_default_resolver_reads_docker_inspect(self) -> None:
        inspected = completed(
            ["docker", "image", "inspect", "img", "--format", "{{json .Config}}"],
            stdout=json.dumps({"Entrypoint": ["python", "/opt/infer.py"], "Cmd": ["--help"]}),
        )
        with mock.patch.object(vc_exec, "run_command", return_value=inspected) as run:
            entrypoint, cmd = vc_exec._docker_image_entrypoint("img")
            self.assertEqual(entrypoint, ["python", "/opt/infer.py"])
            self.assertEqual(cmd, ["--help"])
            run.assert_called_once()
            self.assertEqual(
                run.call_args.args[0],
                ["docker", "image", "inspect", "img", "--format", "{{json .Config}}"],
            )
            self.assertEqual(run.call_args.kwargs["timeout"], 60)
            # Asserted on PATH alone: comparing whole environments dumps every
            # variable into the failure message, secrets included.
            resolved = run.call_args.kwargs["env"]["PATH"].split(os.pathsep)
            self.assertNotIn(str(vc_exec.agent_bin_dir()), resolved)

    def test_default_resolver_accepts_null_entrypoint_and_cmd(self) -> None:
        inspected = completed(
            ["docker", "image", "inspect", "img", "--format", "{{json .Config}}"],
            stdout=json.dumps({"Entrypoint": ["python", "/opt/infer.py"], "Cmd": None}),
        )
        with mock.patch.object(vc_exec, "run_command", return_value=inspected):
            entrypoint, cmd = vc_exec._docker_image_entrypoint("img")
            self.assertEqual(entrypoint, ["python", "/opt/infer.py"])
            self.assertIsNone(cmd)

    def test_default_resolver_accepts_list_shaped_inspect_output(self) -> None:
        inspected = completed(
            ["docker", "image", "inspect", "img", "--format", "{{json .Config}}"],
            stdout=json.dumps([{"Entrypoint": None, "Cmd": ["bash"]}]),
        )
        with mock.patch.object(vc_exec, "run_command", return_value=inspected):
            entrypoint, cmd = vc_exec._docker_image_entrypoint("img")
            self.assertIsNone(entrypoint)
            self.assertEqual(cmd, ["bash"])

    def test_rejects_unsupported_shapes(self) -> None:
        cases = [
            ["docker", "run", "--mount", "type=bind,src=/a,dst=/b", "img", "true"],
            ["docker", "run", "--weird", "img", "true"],
            ["docker", "run", "img"],
            ["docker", "run", "-v", "relative:/cont", "img", "true"],
            ["docker", "run", "-v", "/host:/cont:bad", "img", "true"],
            ["docker", "run", "-v", "/host"],
            ["bash", "-c", "docker run img true"],
        ]
        for case in cases:
            with self.subTest(command=case):
                with self.assertRaises(ValueError):
                    docker_run_to_vc(case)


class MountHostPathTest(unittest.TestCase):
    def test_creates_missing_rw_dir_as_submitter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "out"
            vc_exec.ensure_mount_host_paths([f"{target}:/work/output:rw"])
            self.assertTrue(target.is_dir())
            self.assertTrue(os.access(target, os.W_OK))
            self.assertEqual(target.stat().st_uid, os.getuid())

    def test_creates_missing_plain_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "out"
            vc_exec.ensure_mount_host_paths([f"{target}:/work/output"])
            self.assertTrue(target.is_dir())

    def test_rejects_missing_ro_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError) as raised:
                vc_exec.ensure_mount_host_paths([f"{Path(temporary) / 'nope'}:/models:ro"])
            self.assertIn("read-only mount source does not exist", str(raised.exception))

    def test_rejects_existing_unwritable_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "locked"
            target.mkdir()
            target.chmod(0o555)
            try:
                with self.assertRaises(ValueError) as raised:
                    vc_exec.ensure_mount_host_paths([f"{target}:/work/output:rw"])
                self.assertIn("recreate it as your user", str(raised.exception))
            finally:
                target.chmod(0o755)

    def test_allows_existing_writable_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "out"
            target.mkdir()
            vc_exec.ensure_mount_host_paths([f"{target}:/work/output:rw"])
            self.assertTrue(target.is_dir())

    def test_allows_existing_ro_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "model"
            source.write_text("x", encoding="utf-8")
            vc_exec.ensure_mount_host_paths([f"{source}:/models:ro"])


class AgentBinShadowingTest(unittest.TestCase):
    """The coding agent puts its own bin dir first on PATH so its bundled fd and
    rg win. Anything else dropped in there shadows the system binary of the same
    name, which is how a stray docker took over every push in this pipeline."""

    def _layout(self, root: Path) -> tuple[Path, Path]:
        agent_bin = root / "agent" / "bin"
        system_bin = root / "usr" / "bin"
        agent_bin.mkdir(parents=True)
        system_bin.mkdir(parents=True)
        return agent_bin, system_bin

    def test_drops_configured_agent_bin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent_bin, system_bin = self._layout(root)
            environment = {
                "PATH": os.pathsep.join([str(agent_bin), str(system_bin)]),
                "PI_CODING_AGENT_DIR": str(root / "agent"),
            }
            with mock.patch.dict(os.environ, environment, clear=True):
                entries = vc_exec.agent_bin_cleared_env()["PATH"].split(os.pathsep)
        self.assertNotIn(str(agent_bin), entries)
        self.assertIn(str(system_bin), entries)

    def test_drops_default_agent_bin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent_bin = root / ".pi" / "agent" / "bin"
            system_bin = root / "usr" / "bin"
            agent_bin.mkdir(parents=True)
            system_bin.mkdir(parents=True)
            environment = {"PATH": os.pathsep.join([str(agent_bin), str(system_bin)])}
            with mock.patch.dict(os.environ, environment, clear=True):
                with mock.patch.object(vc_exec.Path, "home", staticmethod(lambda: root)):
                    entries = vc_exec.agent_bin_cleared_env()["PATH"].split(os.pathsep)
        self.assertNotIn(str(agent_bin), entries)
        self.assertIn(str(system_bin), entries)

    def test_keeps_every_other_prepended_dir(self) -> None:
        # The script tests drive docker by putting a fake one first on PATH;
        # only the agent's own bin dir may be dropped.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent_bin, system_bin = self._layout(root)
            fake_bin = root / "fake" / "bin"
            fake_bin.mkdir(parents=True)
            environment = {
                "PATH": os.pathsep.join([str(fake_bin), str(agent_bin), str(system_bin)]),
                "PI_CODING_AGENT_DIR": str(root / "agent"),
            }
            with mock.patch.dict(os.environ, environment, clear=True):
                entries = vc_exec.agent_bin_cleared_env()["PATH"].split(os.pathsep)
        self.assertEqual(entries, [str(fake_bin), str(system_bin)])

    def test_survives_an_unset_path(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            environment = vc_exec.proxy_cleared_env()
        self.assertNotIn("PATH", environment)


class ExecutionProbeEnvTest(unittest.TestCase):
    """The compatibility probe shells out to `docker run`, so it needs the same
    PATH treatment as the push and the build."""

    def test_probe_drops_the_agent_bin_dir(self) -> None:
        probe = completed(["docker"], stdout=CPU_PROBE_OUT)
        with mock.patch("subprocess.run", return_value=probe) as runner:
            run_execution_compat.run_probe("img", use_gpu=False)
        entries = runner.call_args.kwargs["env"]["PATH"].split(os.pathsep)
        self.assertNotIn(str(vc_exec.agent_bin_dir()), entries)


class RegistryNamingTest(unittest.TestCase):
    def test_registry_image_naming(self) -> None:
        self.assertEqual(
            registry_image("demo14.1", "0.1.0"),
            f"{REGISTRY}/hpc/ai_asr-demo14.1:0.1.0",
        )
        self.assertEqual(
            registry_image("demo14.1", "0.1.0", "source"),
            f"{REGISTRY}/hpc/ai_asr-demo14.1-source:0.1.0",
        )

    def test_registry_image_rejects_bad_values(self) -> None:
        with self.assertRaises(ValueError):
            registry_image("demo14.1", "a b")
        with self.assertRaises(ValueError):
            registry_image("", "0.1.0")

    def test_safe_image_component(self) -> None:
        self.assertEqual(safe_image_component("My Model/2"), "my-model-2")
        with self.assertRaises(ValueError):
            safe_image_component("!!!")

    def test_normalize_job_name(self) -> None:
        self.assertEqual(normalize_job_name("SURE Trans Demo 1"), "sure-trans-demo-1")
        self.assertEqual(normalize_job_name("!!!"), "sure-trans-job")


class JobIdParsingTest(unittest.TestCase):
    def test_parse_job_id(self) -> None:
        self.assertEqual(parse_job_id("submitted\njob-abc123\n"), "job-abc123")

    def test_parse_job_id_rejects_garbage(self) -> None:
        with self.assertRaises(ValueError):
            parse_job_id("!!!")


class DiagnoseOomTest(unittest.TestCase):
    def test_exit_137_maps_to_ram_repair(self) -> None:
        hint = diagnose_oom(137, "Killed\n")
        self.assertIsNotNone(hint)
        self.assertIn("vc_memory_gb", hint)
        self.assertIn("vc_gpus=2 vc_memory_gb=64", hint)

    def test_cuda_oom_maps_to_gpu_repair(self) -> None:
        hint = diagnose_oom(1, "RuntimeError: CUDA out of memory. Tried to allocate 64.00 MiB")
        self.assertIsNotNone(hint)
        self.assertIn("24 GiB", hint)
        self.assertNotIn("vc_gpus=2", hint)

    def test_alloc_failure_maps_to_ram_repair(self) -> None:
        hint = diagnose_oom(1, "terminate called after throwing an instance of 'std::bad_alloc'")
        self.assertIsNotNone(hint)
        self.assertIn("vc_memory_gb", hint)

    def test_unknown_failure_has_no_hint(self) -> None:
        self.assertIsNone(diagnose_oom(1, "SyntaxError: invalid syntax\n"))


class AvailabilityTest(unittest.TestCase):
    def test_vc_available(self) -> None:
        with mock.patch.object(vc_exec.shutil, "which", return_value="/usr/bin/vc"), mock.patch.object(
            vc_exec, "run_command", return_value=completed(["vc", "info"])
        ):
            self.assertTrue(vc_available())
        with mock.patch.object(vc_exec.shutil, "which", return_value=None):
            self.assertFalse(vc_available())
        with mock.patch.object(vc_exec.shutil, "which", return_value="/usr/bin/vc"), mock.patch.object(
            vc_exec, "run_command", return_value=completed(["vc", "info"], returncode=1)
        ):
            self.assertFalse(vc_available())

    def test_user_partitions_reads_only_the_partition_block(self) -> None:
        stdout = (
            "User: example" + chr(10)
            + "[Partition]" + chr(10)
            + "------------------------------" + chr(10)
            + "gpu-test" + chr(10)
            + "gpu-2" + chr(10)
            + "[是否允许资源配比超配]" + chr(10)
            + "NO" + chr(10)
            + "[Quota]" + chr(10)
            + "GPU: 32" + chr(10)
        )
        with mock.patch.object(vc_exec.shutil, "which", return_value="/usr/bin/vc"), mock.patch.object(
            vc_exec, "run_command", return_value=completed(["vc", "info", "-u"], stdout=stdout)
        ):
            self.assertEqual(user_partitions(), {"gpu-test", "gpu-2"})

    def test_user_partitions_is_empty_without_a_partition_block(self) -> None:
        with mock.patch.object(vc_exec.shutil, "which", return_value="/usr/bin/vc"), mock.patch.object(
            vc_exec, "run_command", return_value=completed(["vc", "info", "-u"], stdout="User: example" + chr(10))
        ):
            self.assertEqual(user_partitions(), set())


class EnsureRegistryImageTest(unittest.TestCase):
    def test_push_records_commands_and_parses_digest(self) -> None:
        digest = "sha256:" + "c" * 64
        ref = registry_image("demo", "0.1.0")
        calls: list[dict[str, str]] = []

        def fake_run(args: list[str], *, timeout: float | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
            calls.append({"args": " ".join(args), "env": env or {}})
            if args[:2] == ["docker", "tag"]:
                return completed(args)
            return completed(args, stdout=f"latest: digest: {digest} size: 123\n")

        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "nested" / "push.log"
            with mock.patch.object(vc_exec, "run_command", side_effect=fake_run):
                with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://proxy", "https_proxy": "http://proxy"}):
                    self.assertEqual(ensure_registry_image("local-image", ref, log), digest)
            self.assertEqual(calls[0]["args"], f"docker tag local-image {ref}")
            self.assertEqual(calls[1]["args"], f"docker push {ref}")
            for recorded_env in (calls[0]["env"], calls[1]["env"]):
                self.assertNotIn("HTTP_PROXY", recorded_env)
                self.assertNotIn("https_proxy", recorded_env)
            content = log.read_text(encoding="utf-8")
            self.assertIn("$ docker tag", content)
            self.assertIn("$ docker push", content)

    def test_push_log_records_exit_code_for_each_command(self) -> None:
        ref = registry_image("demo", "0.1.0")

        def fake_run(args: list[str], *, timeout: float | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
            return completed(args, stdout=f"latest: digest: sha256:{chr(99) * 64} size: 1\n")

        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "push.log"
            with mock.patch.object(vc_exec, "run_command", side_effect=fake_run):
                ensure_registry_image("local-image", ref, log)
            self.assertEqual(log.read_text(encoding="utf-8").count("exit_code=0"), 2)

    def test_push_log_separates_repeated_invocations(self) -> None:
        ref = registry_image("demo", "0.1.0")

        def fake_run(args: list[str], *, timeout: float | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
            return completed(args, stdout=f"latest: digest: sha256:{'c' * 64} size: 1\n")

        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "push.log"
            with mock.patch.object(vc_exec, "run_command", side_effect=fake_run):
                ensure_registry_image("local-image", ref, log)
                ensure_registry_image("local-image", ref, log)
            headers = [line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("=== ")]
            self.assertEqual(len(headers), 2)
            self.assertIn(f"tag+push {ref}", headers[0])
            self.assertIn(f"pid={os.getpid()}", headers[0])

    def test_the_log_is_readable_while_the_push_is_still_running(self) -> None:
        ref = registry_image("demo", "0.1.0")
        seen: list[str] = []

        def fake_run(args, *, timeout=None, env=None):
            seen.append(log.read_text(encoding="utf-8") if log.is_file() else "")
            return completed(args, stdout=f"latest: digest: sha256:{chr(99) * 64} size: 1" + chr(10))

        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "push.log"
            with mock.patch.object(vc_exec, "run_command", side_effect=fake_run):
                ensure_registry_image("local-image", ref, log)
        self.assertIn("tag+push", seen[0])
        self.assertIn("$ docker tag", seen[1])
        self.assertIn("exit_code=0", seen[1])

    def test_a_rerun_keeps_the_digest_the_first_push_earned(self) -> None:
        ref = registry_image("demo", "0.1.0")
        digest = "sha256:" + "c" * 64
        rejection = "镜像已存在，请更新tag"

        def fake_run(args, *, timeout=None, env=None):
            if args[:2] == ["docker", "tag"]:
                return completed(args)
            return completed(args, stdout=rejection + chr(10))

        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "push.log"
            with mock.patch.object(vc_exec, "run_command", side_effect=fake_run):
                self.assertEqual(
                    ensure_registry_image("local-image", ref, log, known_digest=digest), digest
                )

    def test_a_recorded_digest_is_only_reused_for_the_same_reference(self) -> None:
        ref = registry_image("demo", "0.1.0")
        digest = "sha256:" + "c" * 64
        artifact = {"registry_ref": ref, "registry_push": {"digest": digest}}
        self.assertEqual(vc_exec.recorded_push_digest(artifact, ref), digest)
        self.assertEqual(vc_exec.recorded_push_digest(artifact, registry_image("demo", "0.2.0")), "")
        self.assertEqual(vc_exec.recorded_push_digest({}, ref), "")
        self.assertEqual(
            vc_exec.recorded_push_digest({"registry_ref": ref, "registry_push": {"digest": None}}, ref), ""
        )

    def test_push_without_digest_is_a_failure(self) -> None:
        ref = registry_image("demo", "0.1.0")
        rejection = "镜像已存在，请更新tag"

        def fake_run(args, *, timeout=None, env=None):
            if args[:2] == ["docker", "tag"]:
                return completed(args)
            return completed(args, stdout=rejection + "\n")

        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "push.log"
            with mock.patch.object(vc_exec, "run_command", side_effect=fake_run):
                with self.assertRaises(ValueError) as raised:
                    ensure_registry_image("local-image", ref, log)
            message = str(raised.exception)
            self.assertIn(rejection, message)
            self.assertIn("image_version", message)

    def test_push_failure_mentions_site_target(self) -> None:
        ref = registry_image("demo", "0.1.0")

        def fake_run(args: list[str], *, timeout: float | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
            if args[:2] == ["docker", "tag"]:
                return completed(args)
            return completed(args, returncode=4, stderr="denied: image name does not conform\n")

        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "push.log"
            with mock.patch.object(vc_exec, "run_command", side_effect=fake_run):
                with self.assertRaises(ValueError) as raised:
                    ensure_registry_image("local-image", ref, log)
            message = str(raised.exception)
            self.assertIn("site-resolved target", message)
            self.assertIn("image_version", message)


class InnerCommandQuotingTest(unittest.TestCase):
    def _one_remote_word(self, log_dir: PurePosixPath) -> None:
        command = vc_exec.inner_script_command(log_dir)
        self.assertEqual(shlex.split(command), ["bash", str(log_dir / "inner.sh")])

    def test_a_log_dir_with_a_space_stays_one_remote_argument(self) -> None:
        self._one_remote_word(PurePosixPath("/shared/run dir"))

    def test_a_log_dir_carrying_a_semicolon_cannot_start_a_second_command(self) -> None:
        self._one_remote_word(PurePosixPath("/shared/run;touch /shared/injected"))

    def test_a_plain_log_dir_is_unchanged(self) -> None:
        self.assertEqual(
            vc_exec.inner_script_command(PurePosixPath("/shared/run")),
            "bash /shared/run/inner.sh",
        )


class BoundedSecondsTest(unittest.TestCase):
    def test_a_finite_positive_value_is_accepted(self) -> None:
        self.assertEqual(vc_exec.bounded_seconds("1800"), 1800.0)

    def test_infinity_is_rejected(self) -> None:
        for value in ("inf", "-inf", "nan", "Infinity"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                vc_exec.bounded_seconds(value)

    def test_zero_and_negative_are_rejected(self) -> None:
        for value in ("0", "-1"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                vc_exec.bounded_seconds(value)


class DiagnosticsFailureTest(unittest.TestCase):
    def test_a_diagnostics_failure_is_reported_instead_of_raised(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary)

            def fake_run(args, *, timeout=None, env=None):
                raise subprocess.TimeoutExpired(args, timeout or 0)

            with mock.patch.object(vc_exec, "run_command", side_effect=fake_run):
                rendered = vc_exec.collect_diagnostics("job-abc123", log_dir)
            self.assertIn("job-abc123", rendered)
            self.assertIn("diagnostics unavailable", rendered)
            self.assertEqual((log_dir / "vc_job.log").read_text(encoding="utf-8"), rendered)


class InnerScriptWorkdirTest(unittest.TestCase):
    def _render(self, workdir: str) -> str:
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary)
            vc_exec.render_inner_script(log_dir, "python train.py", {}, None, workdir=workdir)
            return (log_dir / "inner.sh").read_text(encoding="utf-8")

    def test_a_declared_workdir_becomes_a_cd_in_the_remote_script(self) -> None:
        self.assertIn("cd /opt/app", self._render("/opt/app"))

    def test_a_workdir_with_a_space_is_quoted(self) -> None:
        self.assertIn("cd '/opt/my app'", self._render("/opt/my app"))

    def test_no_workdir_leaves_the_image_default(self) -> None:
        self.assertNotIn("cd ", self._render(""))


class CancelVcJobTest(unittest.TestCase):
    def test_a_job_we_stopped_waiting_for_is_deleted(self) -> None:
        calls: list[list[str]] = []

        def fake_run(args, *, timeout=None, env=None):
            calls.append(args)
            return completed(args, stdout="deleted" + chr(10))

        with mock.patch.object(vc_exec, "run_command", side_effect=fake_run):
            rendered = vc_exec.cancel_vc_job("job-abc123")
        self.assertEqual(calls, [["vc", "delete", "--job", "job-abc123"]])
        self.assertIn("job-abc123", rendered)
        self.assertIn("exit_code=0", rendered)

    def test_a_failed_cancel_is_reported_not_raised(self) -> None:
        def fake_run(args, *, timeout=None, env=None):
            raise subprocess.TimeoutExpired(args, timeout or 0)

        with mock.patch.object(vc_exec, "run_command", side_effect=fake_run):
            rendered = vc_exec.cancel_vc_job("job-abc123")
        self.assertIn("job-abc123", rendered)
        self.assertIn("could not be cancelled", rendered)


class StaleResultTest(unittest.TestCase):
    def test_a_previous_exit_code_is_cleared_before_a_new_submission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary)
            (log_dir / "exit_code").write_text("0" + chr(10), encoding="utf-8")
            (log_dir / "stdout.log").write_text("previous attempt" + chr(10), encoding="utf-8")
            vc_exec.clear_previous_result(log_dir)
            self.assertFalse((log_dir / "exit_code").exists())
            self.assertEqual(
                (log_dir / "stdout.log").read_text(encoding="utf-8"),
                "previous attempt" + chr(10),
            )

    def test_clearing_a_fresh_log_dir_is_harmless(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vc_exec.clear_previous_result(Path(temporary) / "missing")


class RunVcJobTest(unittest.TestCase):
    def _submit_side_effect(self) -> tuple[object, list[list[str]]]:
        recorded: list[list[str]] = []

        def fake_run(args: list[str], *, timeout: float | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
            recorded.append(list(args))
            if args[:2] == ["vc", "info"]:
                return completed(args, stdout="cluster ok\n")
            if args[:2] == ["vc", "submit"]:
                inner = Path(args[args.index("--cmd") + 1].split(" ", 1)[1])
                inner.parent.mkdir(parents=True, exist_ok=True)
                (inner.parent / "exit_code").write_text("0\n", encoding="utf-8")
                (inner.parent / "stdout.log").write_text("probe ok\n", encoding="utf-8")
                (inner.parent / "stderr.log").write_text("", encoding="utf-8")
                return completed(args, stdout="job-abc-123\n")
            if args[:2] == ["vc", "logs"]:
                return completed(args, stdout="job logs\n")
            return completed(args, stdout="job info\n")

        return mock.patch.object(vc_exec, "run_command", side_effect=fake_run), recorded

    def test_success_polls_exit_code_and_builds_submit_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary) / "logs"
            patch, recorded = self._submit_side_effect()
            with patch, mock.patch.object(vc_exec, "vc_available", return_value=True), mock.patch.object(
                vc_exec, "user_partitions", return_value={"gpu-test"}
            ):
                result = run_vc_job(image="registry/demo:0.1.0", command="python -c 'print(1)'", log_dir=log_dir)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.job_id, "job-abc-123")
            self.assertFalse(result.timed_out)
            self.assertEqual(result.stdout, "probe ok\n")
            submit = recorded[0]
            self.assertEqual(submit[:6], ["vc", "submit", "-i", "registry/demo:0.1.0", "-p", "gpu-test"])
            self.assertEqual(submit[6:12], ["-g", "1", "-m", "32G", "-c", "8"])
            self.assertEqual(submit[12:14], ["-n", "1"])
            job_index = submit.index("-j") + 1
            self.assertTrue(submit[job_index].startswith("sure-trans-logs-"))
            self.assertEqual(submit[job_index + 1 : job_index + 3], ["--project", "hpc"])
            self.assertIn("-v", submit)
            self.assertTrue(submit[-2].startswith("--cmd"))
            self.assertTrue(submit[-1].startswith("bash ") and submit[-1].endswith("inner.sh"))
            self.assertTrue((log_dir / "inner.sh").is_file())
            inner = (log_dir / "inner.sh").read_text(encoding="utf-8")
            self.assertIn("timeout --kill-after=15 1200 python -c 'print(1)'", inner)

    def test_command_timeout_wrapper_is_configurable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary) / "logs"
            patch, _ = self._submit_side_effect()
            with patch, mock.patch.object(vc_exec, "vc_available", return_value=True), mock.patch.object(
                vc_exec, "user_partitions", return_value={"gpu-test"}
            ):
                run_vc_job(image="registry/demo:0.1.0", command="true", log_dir=log_dir, command_timeout_seconds=60)
            inner = (log_dir / "inner.sh").read_text(encoding="utf-8")
            self.assertIn("timeout --kill-after=15 60 true", inner)

            patch, _ = self._submit_side_effect()
            with patch, mock.patch.object(vc_exec, "vc_available", return_value=True), mock.patch.object(
                vc_exec, "user_partitions", return_value={"gpu-test"}
            ):
                run_vc_job(image="registry/demo:0.1.0", command="true", log_dir=log_dir, command_timeout_seconds=0)
            inner = (log_dir / "inner.sh").read_text(encoding="utf-8")
            self.assertNotIn("timeout ", inner)

    def test_adds_log_dir_mount_when_uncovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary) / "logs"
            patch, recorded = self._submit_side_effect()
            host_source = Path(temporary) / "host"
            host_source.write_text("x", encoding="utf-8")
            ro_mount = f"{host_source}:/cont:ro"
            with patch, mock.patch.object(vc_exec, "vc_available", return_value=True), mock.patch.object(
                vc_exec, "user_partitions", return_value={"gpu-test"}
            ):
                run_vc_job(
                    image="registry/demo:0.1.0",
                    command="true",
                    log_dir=log_dir,
                    mounts=[ro_mount],
                )
            volume_index = recorded[0].index("-v") + 1
            mounts = recorded[0][volume_index].split(",")
            self.assertIn(ro_mount, mounts)
            self.assertIn(f"{log_dir}:{log_dir}", mounts)

    def test_timeout_when_exit_code_never_appears(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary) / "logs"

            def fake_run(args: list[str], *, timeout: float | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
                if args[:2] == ["vc", "info"]:
                    return completed(args, stdout="cluster ok\n")
                if args[:2] == ["vc", "submit"]:
                    return completed(args, stdout="job-slow-123\n")
                if args[:2] == ["vc", "logs"]:
                    return completed(args, stdout="pending\n")
                return completed(args, stdout="info\n")

            with mock.patch.object(vc_exec, "run_command", side_effect=fake_run), mock.patch.object(
                vc_exec, "vc_available", return_value=True
            ), mock.patch.object(vc_exec, "user_partitions", return_value={"gpu-test"}):
                result = run_vc_job(
                    image="registry/demo:0.1.0",
                    command="sleep 1000",
                    log_dir=log_dir,
                    timeout_seconds=0.2,
                    poll_interval=0.01,
                )
            self.assertTrue(result.timed_out)
            self.assertIsNone(result.exit_code)

    def test_submit_failure_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary) / "logs"

            def fake_run(args: list[str], *, timeout: float | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
                if args[:2] == ["vc", "info"]:
                    return completed(args, stdout="cluster ok\n")
                return completed(args, returncode=1, stderr="quota exceeded\n")

            with mock.patch.object(vc_exec, "run_command", side_effect=fake_run), mock.patch.object(
                vc_exec, "vc_available", return_value=True
            ), mock.patch.object(vc_exec, "user_partitions", return_value={"gpu-test"}):
                with self.assertRaises(ValueError) as raised:
                    run_vc_job(image="registry/demo:0.1.0", command="true", log_dir=log_dir)
            self.assertIn("vc submit failed", str(raised.exception))

    def test_unavailable_partition_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(vc_exec, "vc_available", return_value=True), mock.patch.object(
                vc_exec, "user_partitions", return_value={"other-queue"}
            ):
                with self.assertRaises(ValueError) as raised:
                    run_vc_job(
                        image="registry/demo:0.1.0",
                        command="true",
                        log_dir=Path(temporary) / "logs",
                        partition="gpu-test",
                    )
            self.assertIn("gpu-test", str(raised.exception))

    def test_missing_vc_binary_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(vc_exec, "vc_available", return_value=False):
                with self.assertRaises(ValueError) as raised:
                    run_vc_job(image="registry/demo:0.1.0", command="true", log_dir=Path(temporary) / "logs")
            self.assertIn("vc is required for GPU validation", str(raised.exception))


def write_artifact(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


class ExecutionCompatVcTest(unittest.TestCase):
    def test_cuda_device_probes_via_vc_and_records_source_push(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            digest = "sha256:" + "c" * 64
            write_artifact(
                artifacts / "trans_input_resolved.json",
                {
                    "device": "cuda",
                    "model_framework": "transformers",
                    "gpu_required": True,
                    "bf16_required": False,
                    "model_name": "demo",
                    "image_version": "0.1.0",
                    "vc_partition": "gpu-test",
                    "vc_memory_gb": 48,
                    "vc_gpus": 1,
                },
            )
            write_artifact(
                artifacts / "source_image_result.json",
                {"schema": "sure.trans.source_image_result.v1", "status": "passed", "image": "demo-source", "image_id": "sha256:" + "b" * 64},
            )
            result = VcJobResult(
                exit_code=0,
                stdout=PROBE_OUT + "\n",
                stderr="",
                job_id="job-compat-123",
                partition="gpu-test",
                submit_command=["vc", "submit", "-i", "ref", "-p", "gpu-test", "--cmd", "bash inner.sh"],
                duration_ms=1.0,
                timed_out=False,
                log_dir=artifacts / "vc_logs" / "compat",
                vc_diagnostics="",
            )
            output = artifacts / "execution_compat.json"
            with mock.patch.object(run_execution_compat, "ensure_registry_image", return_value=digest), mock.patch.object(
                run_execution_compat, "run_vc_job", return_value=result
            ), mock.patch.object(
                sys, "argv", ["run_execution_compat.py", "--run-dir", str(run_dir), "--produces", str(output)]
            ):
                self.assertEqual(run_execution_compat.main(), 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["execution_surface"], "vc")
            self.assertEqual(payload["selected_device"], "cuda")
            self.assertEqual(payload["vc_job_id"], "job-compat-123")
            self.assertEqual(payload["vc_partition"], "gpu-test")
            self.assertEqual(payload["source_registry_ref"], f"{REGISTRY}/hpc/ai_asr-demo-source:0.1.0")
            source = json.loads((artifacts / "source_image_result.json").read_text(encoding="utf-8"))
            self.assertEqual(source["registry_ref"], f"{REGISTRY}/hpc/ai_asr-demo-source:0.1.0")
            self.assertEqual(source["registry_push"]["digest"], digest)

    def test_non_transformer_model_does_not_require_transformers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            write_artifact(
                artifacts / "trans_input_resolved.json",
                {
                    "device": "cpu",
                    "model_framework": "rnn",
                    "gpu_required": False,
                    "bf16_required": False,
                    "model_name": "demo",
                    "image_version": "0.1.0",
                },
            )
            write_artifact(artifacts / "source_image_result.json", {"image": "demo-source", "image_id": "sha256:" + "b" * 64})
            probe_output = json.dumps(
                {
                    "python_ok": True,
                    "torch": "2.9.1",
                    "cuda_available": False,
                    "bf16_supported": False,
                    "transformers_error": "No module named 'transformers'",
                }
            )
            output = artifacts / "execution_compat.json"
            with mock.patch.object(
                run_execution_compat,
                "run_probe",
                return_value=(
                    ["docker", "run", "--rm", "demo-source", "python", "-c", "<probe>"],
                    completed(["docker"], stdout=probe_output),
                    1.0,
                ),
            ), mock.patch.object(
                sys, "argv", ["run_execution_compat.py", "--run-dir", str(run_dir), "--produces", str(output)]
            ):
                self.assertEqual(run_execution_compat.main(), 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "ready")
            self.assertTrue(payload["compat_ok"])
            self.assertEqual(payload["model_framework"], "rnn")
            self.assertFalse(payload["transformers_required"])
            self.assertNotIn("Transformers import failed in the source image", payload["incompatibilities"])

    def test_transformer_model_still_requires_transformers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            write_artifact(
                artifacts / "trans_input_resolved.json",
                {
                    "device": "cpu",
                    "model_framework": "transformers",
                    "gpu_required": False,
                    "bf16_required": False,
                    "model_name": "demo",
                    "image_version": "0.1.0",
                },
            )
            write_artifact(artifacts / "source_image_result.json", {"image": "demo-source", "image_id": "sha256:" + "b" * 64})
            probe_output = json.dumps(
                {
                    "python_ok": True,
                    "torch": "2.9.1",
                    "cuda_available": False,
                    "bf16_supported": False,
                    "transformers_error": "No module named 'transformers'",
                }
            )
            output = artifacts / "execution_compat.json"
            with mock.patch.object(
                run_execution_compat,
                "run_probe",
                return_value=(
                    ["docker", "run", "--rm", "demo-source", "python", "-c", "<probe>"],
                    completed(["docker"], stdout=probe_output),
                    1.0,
                ),
            ), mock.patch.object(
                sys, "argv", ["run_execution_compat.py", "--run-dir", str(run_dir), "--produces", str(output)]
            ):
                with self.assertRaises(ValueError) as raised:
                    run_execution_compat.main()
            self.assertIn("Transformers import failed", str(raised.exception))
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "blocked")
            self.assertTrue(payload["transformers_required"])

    def test_auto_falls_back_to_local_cpu_after_vc_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            artifacts = run_dir / "artifacts"
            binaries = root / "bin"
            binaries.mkdir(parents=True)
            fake_docker = binaries / "docker"
            fake_docker.write_text(f"#!/bin/sh\nprintf '%s\\n' '{CPU_PROBE_OUT}'\n", encoding="utf-8")
            fake_docker.chmod(0o755)
            write_artifact(
                artifacts / "trans_input_resolved.json",
                {"device": "auto", "model_framework": "transformers", "gpu_required": False, "bf16_required": False, "model_name": "demo", "image_version": "0.1.0"},
            )
            write_artifact(artifacts / "source_image_result.json", {"image": "demo-source", "image_id": "sha256:" + "b" * 64})
            failed = VcJobResult(
                exit_code=1,
                stdout="",
                stderr="CUDA out of memory",
                job_id="job-compat-fail",
                partition="gpu-test",
                submit_command=["vc", "submit", "-i", "ref", "-p", "gpu-test", "--cmd", "bash inner.sh"],
                duration_ms=1.0,
                timed_out=False,
                log_dir=artifacts / "vc_logs" / "compat",
                vc_diagnostics="",
            )
            output = artifacts / "execution_compat.json"
            environment = dict(os.environ)
            environment["PATH"] = f"{binaries}:{environment['PATH']}"
            with mock.patch.object(run_execution_compat, "ensure_registry_image", return_value="sha256:" + "c" * 64), mock.patch.object(
                run_execution_compat, "run_vc_job", return_value=failed
            ), mock.patch.dict(os.environ, {"PATH": environment["PATH"]}), mock.patch.object(
                sys, "argv", ["run_execution_compat.py", "--run-dir", str(run_dir), "--produces", str(output)]
            ):
                self.assertEqual(run_execution_compat.main(), 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["selected_device"], "cpu")
            self.assertEqual(payload["execution_surface"], "vc")
            self.assertIsNotNone(payload["fallback"])
            self.assertEqual(payload["fallback"]["vc_job_id"], "job-compat-fail")


class TransValidateVcTest(unittest.TestCase):
    def test_import_kind_runs_on_vc_and_records_adapter_push(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            digest = "sha256:" + "e" * 64
            write_artifact(artifacts / "execution_compat.json", {"status": "ready", "compat_ok": True, "selected_device": "cuda"})
            write_artifact(
                artifacts / "trans_input_resolved.json",
                {"device": "cuda", "model_name": "demo", "image_version": "0.1.0", "vc_partition": "gpu-test"},
            )
            write_artifact(
                artifacts / "adapter_image_result.json",
                {"status": "passed", "image_id": "sha256:" + "d" * 64, "target_image": "demo-adapter"},
            )
            import_result = artifacts / "import_result.json"
            write_artifact(
                import_result,
                {
                    "status": "pending",
                    "run_command": ["docker", "run", "--rm", "--gpus", "all", "-v", "/host:/cont:ro", "demo-adapter", "python", "validate.py", "--stage", "import"],
                },
            )
            spec = VcSpec(image="demo-adapter", mounts=["/host:/cont:ro"], command=["python", "validate.py", "--stage", "import"], env={}, workdir="")
            vc_result = VcJobResult(
                exit_code=0,
                stdout="import_ok\n",
                stderr="",
                job_id="job-import-123",
                partition="gpu-test",
                submit_command=["vc", "submit", "-i", "ref", "-p", "gpu-test", "--cmd", "bash inner.sh"],
                duration_ms=1.0,
                timed_out=False,
                log_dir=artifacts / "vc_logs" / "import",
                vc_diagnostics="",
            )
            with mock.patch.object(run_trans_validate, "docker_run_to_vc", return_value=spec), mock.patch.object(
                run_trans_validate, "ensure_registry_image", return_value=digest
            ), mock.patch.object(run_trans_validate, "run_vc_job", return_value=vc_result), mock.patch.object(
                sys, "argv", ["run_trans_validate.py", "--run-dir", str(run_dir), "--produces", str(import_result), "--kind", "import"]
            ):
                self.assertEqual(run_trans_validate.main(), 0)
            payload = json.loads(import_result.read_text(encoding="utf-8"))
            self.assertEqual(payload["execution_surface"], "vc")
            self.assertEqual(payload["vc_partition"], "gpu-test")
            self.assertEqual(payload["registry_ref"], f"{REGISTRY}/hpc/ai_asr-demo:0.1.0")
            self.assertTrue(payload["import_passed"])
            adapter = json.loads((artifacts / "adapter_image_result.json").read_text(encoding="utf-8"))
            self.assertEqual(adapter["registry_ref"], f"{REGISTRY}/hpc/ai_asr-demo:0.1.0")
            self.assertEqual(adapter["registry_push"]["digest"], digest)

    def test_ram_oom_failure_reports_targeted_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            write_artifact(artifacts / "execution_compat.json", {"status": "ready", "compat_ok": True, "selected_device": "cuda"})
            write_artifact(
                artifacts / "trans_input_resolved.json",
                {"device": "cuda", "model_name": "demo", "image_version": "0.1.0", "vc_partition": "gpu-test"},
            )
            load_result = artifacts / "load_result.json"
            write_artifact(
                load_result,
                {"status": "pending", "run_command": ["docker", "run", "--rm", "demo-adapter", "python", "validate.py", "--stage", "load"]},
            )
            spec = VcSpec(image="demo-adapter", mounts=[], command=["python", "validate.py", "--stage", "load"], env={}, workdir="")
            oom_result = VcJobResult(
                exit_code=137,
                stdout="",
                stderr="Killed\n",
                job_id="job-load-oom",
                partition="gpu-test",
                submit_command=["vc", "submit", "-i", "ref", "-p", "gpu-test", "--cmd", "bash inner.sh"],
                duration_ms=1.0,
                timed_out=False,
                log_dir=artifacts / "vc_logs" / "load",
                vc_diagnostics="pod status: OOMKilled",
            )
            with mock.patch.object(run_trans_validate, "docker_run_to_vc", return_value=spec), mock.patch.object(
                run_trans_validate, "run_vc_job", return_value=oom_result
            ), mock.patch.object(
                sys, "argv", ["run_trans_validate.py", "--run-dir", str(run_dir), "--produces", str(load_result), "--kind", "load"]
            ):
                with self.assertRaises(ValueError) as raised:
                    run_trans_validate.main()
            message = str(raised.exception)
            self.assertIn("OOM", message)
            self.assertIn("vc_gpus=2 vc_memory_gb=64", message)
            payload = json.loads(load_result.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")
            self.assertIn("vc_gpus=2 vc_memory_gb=64", payload["error"])

    def test_gpu_oom_retries_with_fresh_vc_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            write_artifact(artifacts / "execution_compat.json", {"status": "ready", "compat_ok": True, "selected_device": "cuda"})
            write_artifact(
                artifacts / "trans_input_resolved.json",
                {"device": "cuda", "model_name": "demo", "image_version": "0.1.0", "vc_partition": "gpu-test"},
            )
            load_result = artifacts / "load_result.json"
            write_artifact(
                load_result,
                {"status": "pending", "run_command": ["docker", "run", "--rm", "demo-adapter", "python", "validate.py", "--stage", "load"]},
            )
            spec = VcSpec(image="demo-adapter", mounts=[], command=["python", "validate.py", "--stage", "load"], env={}, workdir="")
            def result(exit_code: int, stderr: str, job_id: str, log_dir: Path) -> VcJobResult:
                return VcJobResult(
                    exit_code=exit_code,
                    stdout="",
                    stderr=stderr,
                    job_id=job_id,
                    partition="gpu-test",
                    submit_command=["vc", "submit"],
                    duration_ms=1.0,
                    timed_out=False,
                    log_dir=log_dir,
                    vc_diagnostics="",
                )
            first = result(1, "torch.OutOfMemoryError: CUDA out of memory", "job-load-oom", artifacts / "vc_logs" / "load")
            second = result(0, "ok", "job-load-ok", artifacts / "vc_logs" / "load" / "oom-attempt-2")
            with mock.patch.object(run_trans_validate, "docker_run_to_vc", return_value=spec), mock.patch.object(
                run_trans_validate, "run_vc_job", side_effect=[first, second]
            ), mock.patch.object(
                sys, "argv", ["run_trans_validate.py", "--run-dir", str(run_dir), "--produces", str(load_result), "--kind", "load"]
            ):
                self.assertEqual(run_trans_validate.main(), 0)
            payload = json.loads(load_result.read_text(encoding="utf-8"))
            self.assertTrue(payload["load_passed"])
            self.assertEqual([entry["job_id"] for entry in payload["vc_attempts"]], ["job-load-oom", "job-load-ok"])
            self.assertEqual(payload["gpu_oom_attempts"], 1)
            self.assertFalse(payload["gpu_oom_retry_exhausted"])

    def test_a_passing_job_is_not_resubmitted_because_its_log_mentions_oom(self) -> None:
        """A recovered OOM leaves its traceback in the log of a job that exits 0."""
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            write_artifact(artifacts / "execution_compat.json", {"status": "ready", "compat_ok": True, "selected_device": "cuda"})
            write_artifact(
                artifacts / "trans_input_resolved.json",
                {"device": "cuda", "model_name": "demo", "image_version": "0.1.0", "vc_partition": "gpu-test"},
            )
            load_result = artifacts / "load_result.json"
            write_artifact(
                load_result,
                {"status": "pending", "run_command": ["docker", "run", "--rm", "demo-adapter", "python", "validate.py", "--stage", "load"]},
            )
            spec = VcSpec(image="demo-adapter", mounts=[], command=["python", "validate.py", "--stage", "load"], env={}, workdir="")
            passed = VcJobResult(
                exit_code=0,
                stdout="torch.OutOfMemoryError: CUDA out of memory; retrying with beam 1, then ok",
                stderr="",
                job_id="job-load-ok",
                partition="gpu-test",
                submit_command=["vc", "submit"],
                duration_ms=1.0,
                timed_out=False,
                log_dir=artifacts / "vc_logs" / "load",
                vc_diagnostics="",
            )
            extra = VcJobResult(
                exit_code=1, stdout="", stderr="unrelated failure", job_id="job-load-second",
                partition="gpu-test", submit_command=["vc", "submit"], duration_ms=1.0, timed_out=False,
                log_dir=artifacts / "vc_logs" / "load" / "oom-attempt-2", vc_diagnostics="",
            )
            with mock.patch.object(run_trans_validate, "docker_run_to_vc", return_value=spec), mock.patch.object(
                run_trans_validate, "run_vc_job", side_effect=[passed, extra]
            ), mock.patch.object(
                sys, "argv", ["run_trans_validate.py", "--run-dir", str(run_dir), "--produces", str(load_result), "--kind", "load"]
            ):
                self.assertEqual(run_trans_validate.main(), 0)
            payload = json.loads(load_result.read_text(encoding="utf-8"))
            self.assertTrue(payload["load_passed"])
            self.assertEqual([entry["job_id"] for entry in payload["vc_attempts"]], ["job-load-ok"])
            self.assertEqual(payload["gpu_oom_attempts"], 0)

    def test_oom_retry_stops_when_the_hook_budget_cannot_fit_another_attempt(self) -> None:
        """The hook kills the gate at its own timeout; a retry it cannot outlive loses the result."""
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            write_artifact(artifacts / "execution_compat.json", {"status": "ready", "compat_ok": True, "selected_device": "cuda"})
            write_artifact(
                artifacts / "trans_input_resolved.json",
                {"device": "cuda", "model_name": "demo", "image_version": "0.1.0", "vc_partition": "gpu-test"},
            )
            load_result = artifacts / "load_result.json"
            write_artifact(
                load_result,
                {
                    "status": "pending",
                    "timeout_seconds": 1800,
                    "run_command": ["docker", "run", "--rm", "demo-adapter", "python", "validate.py", "--stage", "load"],
                },
            )
            spec = VcSpec(image="demo-adapter", mounts=[], command=["python", "validate.py", "--stage", "load"], env={}, workdir="")
            oom = VcJobResult(
                exit_code=1, stdout="", stderr="torch.OutOfMemoryError: CUDA out of memory",
                job_id="job-load-oom", partition="gpu-test", submit_command=["vc", "submit"],
                duration_ms=1.0, timed_out=False, log_dir=artifacts / "vc_logs" / "load", vc_diagnostics="",
            )
            second = VcJobResult(
                exit_code=1, stdout="", stderr="torch.OutOfMemoryError: CUDA out of memory",
                job_id="job-load-oom-2", partition="gpu-test", submit_command=["vc", "submit"],
                duration_ms=1.0, timed_out=False,
                log_dir=artifacts / "vc_logs" / "load" / "oom-attempt-2", vc_diagnostics="",
            )
            # 1800s per attempt, 120s reserve: one attempt fits in 1900s, a second does not.
            with mock.patch.dict(os.environ, {"SURE_TRANS_GATE_BUDGET_SECONDS": "1900"}), \
                 mock.patch.object(run_trans_validate, "docker_run_to_vc", return_value=spec), \
                 mock.patch.object(run_trans_validate, "run_vc_job", side_effect=[oom, second]), \
                 mock.patch.object(
                     sys, "argv", ["run_trans_validate.py", "--run-dir", str(run_dir), "--produces", str(load_result), "--kind", "load"]
                 ):
                with self.assertRaises(ValueError):
                    run_trans_validate.main()
            payload = json.loads(load_result.read_text(encoding="utf-8"))
            self.assertEqual([entry["job_id"] for entry in payload["vc_attempts"]], ["job-load-oom"])
            self.assertTrue(payload["gpu_oom_retry_budget_exhausted"])

    def test_permission_denied_repair_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            write_artifact(artifacts / "execution_compat.json", {"status": "ready", "compat_ok": True, "selected_device": "cuda"})
            write_artifact(
                artifacts / "trans_input_resolved.json",
                {"device": "cuda", "model_name": "demo", "image_version": "0.1.0", "vc_partition": "gpu-test", "vc_memory_gb": 64},
            )
            infer_result = artifacts / "infer_result.json"
            write_artifact(
                infer_result,
                {"status": "pending", "run_command": ["docker", "run", "--rm", "demo-adapter", "python", "validate.py", "--stage", "infer"]},
            )
            spec = VcSpec(image="demo-adapter", mounts=[], command=["python", "validate.py", "--stage", "infer"], env={}, workdir="")
            denied = VcJobResult(
                exit_code=1,
                stdout="",
                stderr="PermissionError: [Errno 13] Permission denied: '/work/output/.smoke.txt.7.tmp'",
                job_id="job-load-denied",
                partition="gpu-test",
                submit_command=["vc", "submit", "-i", "ref", "-p", "gpu-test", "--cmd", "bash inner.sh"],
                duration_ms=1.0,
                timed_out=False,
                log_dir=artifacts / "vc_logs" / "infer",
                vc_diagnostics="",
            )
            with mock.patch.object(run_trans_validate, "docker_run_to_vc", return_value=spec), mock.patch.object(
                run_trans_validate, "run_vc_job", return_value=denied
            ), mock.patch.object(
                sys, "argv", ["run_trans_validate.py", "--run-dir", str(run_dir), "--produces", str(infer_result), "--kind", "infer"]
            ):
                with self.assertRaises(ValueError) as raised:
                    run_trans_validate.main()
            self.assertIn("Permission denied", str(raised.exception))
            self.assertIn("user-owned directory", str(raised.exception))
            payload = json.loads(infer_result.read_text(encoding="utf-8"))
            self.assertIn("user-owned directory", payload["error"])

    def test_mcp_gate_rejects_placeholder_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            write_artifact(artifacts / "execution_compat.json", {"status": "ready", "compat_ok": True, "selected_device": "cuda"})
            write_artifact(
                artifacts / "trans_input_resolved.json",
                {"device": "cuda", "model_name": "demo", "image_version": "0.1.0", "vc_partition": "gpu-test", "vc_memory_gb": 64},
            )
            mcp_result = artifacts / "mcp_result.json"
            write_artifact(
                mcp_result,
                {"status": "pending", "tool_name": "transcribe_audio", "run_command": ["docker", "run", "--rm", "demo-adapter", "python", "-c", "print('mcp placeholder ok')"]},
            )
            spec = VcSpec(image="demo-adapter", mounts=[], command=["python", "-c", "print('mcp placeholder ok')"], env={}, workdir="")
            passed_job = VcJobResult(
                exit_code=0, stdout="", stderr="", job_id="job-mcp-ph", partition="gpu-test",
                submit_command=["vc", "submit"], duration_ms=1.0, timed_out=False,
                log_dir=artifacts / "vc_logs" / "mcp", vc_diagnostics="",
            )
            with mock.patch.object(run_trans_validate, "docker_run_to_vc", return_value=spec), mock.patch.object(
                run_trans_validate, "run_vc_job", return_value=passed_job
            ), mock.patch.object(
                sys, "argv", ["run_trans_validate.py", "--run-dir", str(run_dir), "--produces", str(mcp_result), "--kind", "mcp"]
            ):
                with self.assertRaises(ValueError) as raised:
                    run_trans_validate.main()
            self.assertIn("mcp_smoke.py", str(raised.exception))
            self.assertIn("placeholder commands are rejected", str(raised.exception))

    def test_mcp_gate_passes_with_protocol_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            write_artifact(artifacts / "execution_compat.json", {"status": "ready", "compat_ok": True, "selected_device": "cuda"})
            write_artifact(
                artifacts / "trans_input_resolved.json",
                {"device": "cuda", "model_name": "demo", "image_version": "0.1.0", "vc_partition": "gpu-test", "vc_memory_gb": 64},
            )
            mcp_result = artifacts / "mcp_result.json"
            write_artifact(
                mcp_result,
                {"status": "pending", "tool_name": "transcribe_audio", "run_command": ["docker", "run", "--rm", "demo-adapter", "python", "/opt/sure_trans/mcp_smoke.py", "--audio", "/fixture/smoke.wav", "--produces", str(artifacts / "vc_logs" / "mcp" / "mcp_smoke.json")]},
            )
            evidence_dir = artifacts / "vc_logs" / "mcp"
            evidence_dir.mkdir(parents=True)
            write_artifact(
                evidence_dir / "mcp_smoke.json",
                {
                    "schema": "sure.trans.mcp_smoke.v1",
                    "status": "passed",
                    "tool": "transcribe_audio",
                    "initialize": {"ok": True, "protocolVersion": "2024-11-05"},
                    "tools_list": {"ok": True, "tools": ["transcribe_audio"]},
                    "tools_call": {"ok": True, "text_nonempty": True, "text": "ok"},
                    "shutdown": {"ok": True},
                    "error": None,
                },
            )
            spec = VcSpec(image="demo-adapter", mounts=[], command=["python", "/opt/sure_trans/mcp_smoke.py"], env={}, workdir="")
            passed_job = VcJobResult(
                exit_code=0, stdout="", stderr="", job_id="job-mcp-ok", partition="gpu-test",
                submit_command=["vc", "submit"], duration_ms=1.0, timed_out=False,
                log_dir=evidence_dir, vc_diagnostics="",
            )
            with mock.patch.object(run_trans_validate, "docker_run_to_vc", return_value=spec), mock.patch.object(
                run_trans_validate, "run_vc_job", return_value=passed_job
            ), mock.patch.object(
                sys, "argv", ["run_trans_validate.py", "--run-dir", str(run_dir), "--produces", str(mcp_result), "--kind", "mcp"]
            ):
                run_trans_validate.main()
            payload = json.loads(mcp_result.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["protocol"]["status"], "passed")

    def test_payload_budget_blocks_before_submit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            write_artifact(artifacts / "execution_compat.json", {"status": "ready", "compat_ok": True, "selected_device": "cuda"})
            write_artifact(
                artifacts / "trans_input_resolved.json",
                {"device": "cuda", "model_name": "demo", "image_version": "0.1.0", "vc_partition": "gpu-test", "vc_memory_gb": 32},
            )
            infer_result = artifacts / "infer_result.json"
            write_artifact(
                infer_result,
                {"status": "pending", "run_command": ["docker", "run", "--rm", "demo-adapter", "python", "validate.py", "--stage", "infer"]},
            )
            spec = VcSpec(image="demo-adapter", mounts=[], command=["python", "validate.py", "--stage", "infer"], env={}, workdir="")
            with mock.patch.object(run_trans_validate, "docker_run_to_vc", return_value=spec), mock.patch.object(
                run_trans_validate, "model_payload_bytes", return_value=40 * 1024 ** 3
            ), mock.patch.object(
                sys, "argv", ["run_trans_validate.py", "--run-dir", str(run_dir), "--produces", str(infer_result), "--kind", "infer"]
            ):
                with self.assertRaises(ValueError) as raised:
                    run_trans_validate.main()
            message = str(raised.exception)
            self.assertIn("40.0 GiB", message)
            self.assertIn("vc_gpus=2 vc_memory_gb=64", message)
            self.assertIn("32 GiB per GPU", message)

    def test_import_kind_skips_payload_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_dir = root / "model"
            model_dir.mkdir()
            (model_dir / "weights.bin").write_bytes(b"x" * 4096)
            resolved = {"model_path": str(model_dir)}
            self.assertEqual(run_trans_validate.model_payload_bytes(root, resolved, "import"), 0)
            self.assertEqual(run_trans_validate.model_payload_bytes(root, resolved, "load"), 4096)


class VcExecCliTest(unittest.TestCase):
    def test_cli_records_passed_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log_dir = root / "logs"
            produces = root / "smoke.json"
            patch, _ = RunVcJobTest()._submit_side_effect()
            with patch, mock.patch.object(vc_exec, "vc_available", return_value=True), mock.patch.object(
                vc_exec, "user_partitions", return_value={"gpu-test"}
            ), mock.patch.object(
                sys, "argv",
                ["vc_exec.py", "--image", "registry/demo:0.1.0", "--command", "true", "--log-dir", str(log_dir), "--produces", str(produces)],
            ):
                self.assertEqual(vc_exec.main(), 0)
            payload = json.loads(produces.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["vc_partition"], "gpu-test")
            self.assertEqual(payload["exit_code"], 0)

    def test_cli_returns_nonzero_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def fake_run(args: list[str], *, timeout: float | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
                if args[:2] == ["vc", "info"]:
                    return completed(args, stdout="cluster ok\n")
                return completed(args, returncode=1, stderr="quota exceeded\n")

            with mock.patch.object(vc_exec, "run_command", side_effect=fake_run), mock.patch.object(
                vc_exec, "vc_available", return_value=True
            ), mock.patch.object(vc_exec, "user_partitions", return_value={"gpu-test"}), mock.patch.object(
                sys, "argv",
                ["vc_exec.py", "--image", "registry/demo:0.1.0", "--command", "true", "--log-dir", str(root / "logs"), "--produces", str(root / "smoke.json")],
            ):
                self.assertEqual(vc_exec.main(), 1)



class SitePolicySourcedDefaultsTest(unittest.TestCase):
    """The partition name and registry host are site data, not public core constants."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = "/srv"
        policy = {
            "schema": "sure.site.policy.v1",
            "site_id": "test-site",
            "policy_version": 1,
            "storage": {
                "approved_models_roots": [f"{root}/models"],
                "approved_results_roots": [f"{root}/results"],
                "forbidden_output_roots": [root],
                "runtime_root": f"{root}/runtime",
            },
            "datasets": {"allowed_source_roots": [f"{root}/datasets"]},
            "execution": {"surfaces": ["vc"], "vc_partitions": ["gpu-a"], "vc_default_partition": "gpu-a"},
            "network": {"container_registry": "registry.example"},
            "container_delivery": {
                "repository_template": "{registry}/hpc/ai_{task}-{model_name}"
            },
        }
        path = Path(self._tmp.name) / "site.yaml"
        path.write_text(yaml.safe_dump(policy), encoding="utf-8")
        self._env = mock.patch.dict(os.environ, {"SURE_SITE_POLICY": str(path.resolve())})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_default_partition_comes_from_the_site_policy(self) -> None:
        self.assertEqual(vc_exec.default_partition(), "gpu-a")

    def test_registry_image_uses_the_site_container_registry(self) -> None:
        self.assertEqual(vc_exec.registry_image("demo", "0.1.0"), "registry.example/hpc/ai_asr-demo:0.1.0")


if __name__ == "__main__":
    unittest.main()
