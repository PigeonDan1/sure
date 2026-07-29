#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess as real_subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sure_eval.agent import vc_precheck  # noqa: E402


class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def make_runner(responses):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        for prefix, reply in responses:
            if args[: len(prefix)] == prefix:
                return reply
        return FakeCompleted(1, "", "unexpected command")

    run.calls = calls
    return run


IMG = "docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_asr_nemo_stt_zh_conformer_transducer:v1.1"
LOCAL_IMAGES = (
    "docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_asr_kyutai_stt_1b:v1.0\n"
    + IMG
    + "\nubuntu:22.04\n"
)


class ProbeTests(unittest.TestCase):
    def test_partition_error_means_image_exists(self):
        run = make_runner([(["vc", "submit"], FakeCompleted(1, "", "partition not found: sure-precheck-nonexistent-queue"))])
        self.assertEqual(vc_precheck.probe_registry_image(IMG, run), "exists")

    def test_image_error_means_image_missing(self):
        run = make_runner([(["vc", "submit"], FakeCompleted(1, "镜像不存在!", ""))])
        self.assertEqual(vc_precheck.probe_registry_image(IMG, run), "missing")

    def test_unrecognized_output_is_unknown(self):
        run = make_runner([(["vc", "submit"], FakeCompleted(1, "server upgraded, new wording", ""))])
        self.assertEqual(vc_precheck.probe_registry_image(IMG, run), "unknown")

    def test_probe_never_names_a_real_queue(self):
        run = make_runner([(["vc", "submit"], FakeCompleted(1, "", "partition not found"))])
        vc_precheck.probe_registry_image(IMG, run)
        submit_args = run.calls[0]
        queue = submit_args[submit_args.index("-p") + 1]
        self.assertNotIn(queue, {"pdcpu", "pdgpu-3090", "pdgpu-4090", "pdgpu-5090"})


class ImageCheckTests(unittest.TestCase):
    def test_local_image_passes_without_probe(self):
        run = make_runner([(["docker", "image", "inspect"], FakeCompleted(0, "[]", ""))])
        result = vc_precheck.check_image(IMG, "auto_select_best_image", run)
        self.assertEqual(result.status, "pass")
        self.assertEqual(len(run.calls), 1)

    def test_registry_hit_passes_with_note(self):
        run = make_runner([
            (["docker", "image", "inspect"], FakeCompleted(1, "", "No such image")),
            (["vc", "submit"], FakeCompleted(1, "", "partition not found")),
        ])
        result = vc_precheck.check_image(IMG, "cli_override", run)
        self.assertEqual(result.status, "pass")
        self.assertIn("not local", result.detail)

    def test_fabricated_missing_image_fails_with_candidates(self):
        run = make_runner([
            (["docker", "image", "inspect"], FakeCompleted(1, "", "No such image")),
            (["vc", "submit"], FakeCompleted(1, "镜像不存在!", "")),
            (["docker", "images"], FakeCompleted(0, LOCAL_IMAGES, "")),
        ])
        result = vc_precheck.check_image(IMG, "auto_select_best_image", run)
        self.assertEqual(result.status, "fail")
        self.assertEqual(len(result.candidates), 2)

    def test_human_missing_image_warns_but_passes(self):
        run = make_runner([
            (["docker", "image", "inspect"], FakeCompleted(1, "", "No such image")),
            (["vc", "submit"], FakeCompleted(1, "镜像不存在!", "")),
            (["docker", "images"], FakeCompleted(0, LOCAL_IMAGES, "")),
        ])
        result = vc_precheck.check_image(IMG, "cli_override", run)
        self.assertEqual(result.status, "warn")

    def test_unknown_probe_fails_fabricated_and_warns_human(self):
        responses = [
            (["docker", "image", "inspect"], FakeCompleted(1, "", "No such image")),
            (["vc", "submit"], FakeCompleted(1, "new wording", "")),
            (["docker", "images"], FakeCompleted(0, LOCAL_IMAGES, "")),
        ]
        self.assertEqual(vc_precheck.check_image(IMG, "auto_select_best_image", make_runner(responses)).status, "fail")
        self.assertEqual(vc_precheck.check_image(IMG, "model_metadata", make_runner(responses)).status, "warn")


VC_INFO_U = "User: ruichen.sun\n[Partition]\npdcpu\npdgpu-3090\npdgpu-4090\n[Quota]\nGPU: 32\n"


class PartitionCheckTests(unittest.TestCase):
    def test_allowed_partition_passes(self):
        run = make_runner([(["vc", "info", "-u"], FakeCompleted(0, VC_INFO_U, ""))])
        self.assertEqual(vc_precheck.check_partition("pdcpu", run).status, "pass")

    def test_unknown_partition_fails_with_candidates(self):
        run = make_runner([(["vc", "info", "-u"], FakeCompleted(0, VC_INFO_U, ""))])
        result = vc_precheck.check_partition("mlops", run)
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.candidates, ["pdcpu", "pdgpu-3090", "pdgpu-4090"])

    def test_unlistable_partitions_warn(self):
        run = make_runner([(["vc", "info", "-u"], FakeCompleted(1, "", "boom"))])
        self.assertEqual(vc_precheck.check_partition("pdcpu", run).status, "warn")


class PathCheckTests(unittest.TestCase):
    def test_paths_under_mount_pass(self):
        result = vc_precheck.check_paths(
            ["/hpc_stor03/sjtu_home/ruichen.sun/WorkSpace/sure-harness/sure"],
            "/hpc_stor03/sjtu_home:/hpc_stor03/sjtu_home",
        )
        self.assertEqual(result.status, "pass")

    def test_path_outside_mount_fails_and_names_it(self):
        result = vc_precheck.check_paths(
            ["/hpc_stor03/sjtu_home/ruichen.sun/x", "/tmp/leak"],
            "/hpc_stor03/sjtu_home:/hpc_stor03/sjtu_home",
        )
        self.assertEqual(result.status, "fail")
        self.assertIn("/tmp/leak", result.detail)


class VenvCheckTests(unittest.TestCase):
    def test_missing_local_image_skips(self):
        run = make_runner([(["docker", "image", "inspect"], FakeCompleted(1, "", "No such image"))])
        self.assertEqual(vc_precheck.check_venv(IMG, "/opt/x_venv", run).status, "skip")

    def test_expected_venv_found_passes(self):
        run = make_runner([
            (["docker", "image", "inspect"], FakeCompleted(0, "[]", "")),
            (["docker", "run"], FakeCompleted(0, "/opt/asr_nemo_stt_zh_conformer_transducer_venv\n/opt/conda\n", "")),
        ])
        result = vc_precheck.check_venv(IMG, "/opt/asr_nemo_stt_zh_conformer_transducer_venv", run)
        self.assertEqual(result.status, "pass")

    def test_expected_venv_missing_fails_with_opt_listing(self):
        run = make_runner([
            (["docker", "image", "inspect"], FakeCompleted(0, "[]", "")),
            (["docker", "run"], FakeCompleted(0, "/opt/conda\n", "")),
        ])
        result = vc_precheck.check_venv(IMG, "/opt/x_venv", run)
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.candidates, ["/opt/conda"])


class ResourceCheckTests(unittest.TestCase):
    def test_valid_resources_pass(self):
        self.assertEqual(vc_precheck.check_resources("16G", 1, 4).status, "pass")

    def test_bare_memory_number_fails(self):
        result = vc_precheck.check_resources("16", 1, 4)
        self.assertEqual(result.status, "fail")
        self.assertIn("16", result.detail)

    def test_gpu_over_quota_fails(self):
        self.assertEqual(vc_precheck.check_resources("16G", 33, 4).status, "fail")


class RunPrecheckTests(unittest.TestCase):
    def test_no_vc_marks_cluster_checks_skipped(self):
        run = make_runner([(["docker", "image", "inspect"], FakeCompleted(1, "", "No such image"))])
        results = vc_precheck.run_precheck(
            image=IMG, image_source="cli_override", partition="pdcpu", memory="16G",
            gpus=1, cpus=4, volume_mount="/hpc_stor03/sjtu_home:/hpc_stor03/sjtu_home",
            paths=["/hpc_stor03/sjtu_home/x"], expected_venv="/opt/x_venv",
            run=run, which=lambda name: None,
        )
        by_name = {r.name: r for r in results}
        self.assertEqual(by_name["image"].status, "skip")
        self.assertEqual(by_name["partition"].status, "skip")
        self.assertTrue(vc_precheck.precheck_passed(results))

    def test_failed_check_fails_the_precheck_and_report_names_it(self):
        run = make_runner([
            (["docker", "image", "inspect"], FakeCompleted(1, "", "No such image")),
            (["vc", "submit"], FakeCompleted(1, "镜像不存在!", "")),
            (["docker", "images"], FakeCompleted(0, LOCAL_IMAGES, "")),
            (["vc", "info", "-u"], FakeCompleted(0, VC_INFO_U, "")),
        ])
        results = vc_precheck.run_precheck(
            image=IMG, image_source="auto_select_best_image", partition="pdcpu", memory="16G",
            gpus=1, cpus=4, volume_mount="/hpc_stor03/sjtu_home:/hpc_stor03/sjtu_home",
            paths=["/hpc_stor03/sjtu_home/x"], expected_venv="/opt/x_venv",
            run=run, which=lambda name: "/usr/bin/vc",
        )
        self.assertFalse(vc_precheck.precheck_passed(results))
        report = vc_precheck.format_report(results)
        self.assertIn("FAIL", report)
        self.assertIn("image", report)
        payload = vc_precheck.as_payload(results)
        self.assertFalse(payload["passed"])
        self.assertEqual(len(payload["checks"]), 5)


class SelectBestImageTests(unittest.TestCase):
    def setUp(self):
        from sure_eval.agent import vc_submitter
        self.vc_submitter = vc_submitter
        self.original = vc_submitter._run_cmd
        self.addCleanup(setattr, vc_submitter, "_run_cmd", self.original)

    def fake_docker(self, stdout):
        def _run_cmd(args, check=True):
            return FakeCompleted(0, stdout, "")
        self.vc_submitter._run_cmd = _run_cmd

    def test_no_local_image_raises_instead_of_guessing(self):
        self.fake_docker(LOCAL_IMAGES)
        with self.assertRaises(RuntimeError) as ctx:
            self.vc_submitter.select_best_image("whisper_tiny")
        message = str(ctx.exception)
        self.assertIn("refusing to guess", message)
        self.assertIn("sure_asr_kyutai_stt_1b:v1.0", message)

    def test_local_image_still_selected(self):
        self.fake_docker(LOCAL_IMAGES)
        selected = self.vc_submitter.select_best_image("asr_nemo_stt_zh_conformer_transducer")
        self.assertEqual(selected, IMG)


class CliTests(unittest.TestCase):
    SCRIPT = str(Path(__file__).resolve().parent / "check_vc_submit_readiness.py")

    def run_cli(self, *extra):
        return real_subprocess.run(
            [sys.executable, self.SCRIPT,
             "--image", IMG, "--image-source", "cli_override",
             "--partition", "pdcpu", "--memory", "16G",
             "--volume", "/hpc_stor03/sjtu_home:/hpc_stor03/sjtu_home",
             "--path", "/hpc_stor03/sjtu_home/x",
             "--expected-venv", "/opt/x_venv", *extra],
            capture_output=True, text=True,
        )

    def test_cli_writes_payload_and_exit_code_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "vc_precheck.json"
            completed = self.run_cli("--output", str(out))
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["checks"]), 5)
            self.assertEqual(completed.returncode, 0 if payload["passed"] else 1, completed.stderr)

    def test_cli_fails_on_bad_memory(self):
        completed = self.run_cli("--memory-override-for-test")
        self.assertNotEqual(completed.returncode, 0)

    def test_cli_reports_failure_on_stderr(self):
        completed = real_subprocess.run(
            [sys.executable, self.SCRIPT,
             "--image", IMG, "--image-source", "cli_override",
             "--partition", "pdcpu", "--memory", "16",
             "--volume", "/hpc_stor03/sjtu_home:/hpc_stor03/sjtu_home",
             "--path", "/hpc_stor03/sjtu_home/x",
             "--expected-venv", "/opt/x_venv"],
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("resources", completed.stderr)


if __name__ == "__main__":
    unittest.main()
