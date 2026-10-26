"""GPU-free regressions for sharding, real stdio generation and recovery."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import parallel_predictions as parallel
import infer_entrypoint
import resolve_eval_input
import run_infer
import test_deployment_binding as binding_tests
import check_execution_result
from container_execution import build_local_container_command
from deployment_binding import load_deployment_binding
from finalize_result_bundle import finalize_bundle


FAKE_WORKER = r'''
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
value = lambda name: args[args.index(name) + 1]
root = Path(value("--run-dir"))
attempt = json.loads((root / "attempt.json").read_text())
keys = Path(value("--keys-file")).read_text().splitlines()
rank = int(os.environ["SURE_INFER_WORKER_RANK"])
with Path(os.environ["CALLS"]).open("a") as calls:
    calls.write(json.dumps({"rank":rank,"round":attempt["round"],"keys":keys,
                           "dataset":value("--dataset"),
                           "gpu":os.environ["CUDA_VISIBLE_DEVICES"],"time":time.monotonic()}) + "\n")
time.sleep(float(os.environ.get("WORK_SECONDS", "1.0")))
mode = os.environ.get("FAIL_MODE", "")
with (root / "completed.jsonl").open("a") as journal:
    for key in keys:
        if rank == 0 and ((mode == "once" and attempt["round"] == 0 and key == keys[-1]) or mode == "always"):
            continue
        row = {"key":key,"dataset":value("--dataset"),"task":"ASR",
               "prediction":{"text":key},"normalized_prediction":key,"raw_response":{"text":key}}
        journal.write(json.dumps(row) + "\n")
        journal.flush()
if mode and rank == 0 and (mode == "always" or attempt["round"] == 0):
    print("RuntimeError: injected Worker failure")
    sys.exit(1)
'''

FAKE_SERVER = r'''
import json, os, sys, time
from pathlib import Path
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "tools/call":
        key = Path(request["params"]["arguments"]["audio_path"]).stem
        if key == "k1" and os.environ.get("WAIT_SAMPLE") == "1":
            time.sleep(2)
        if key == "k1" and os.environ.get("FAIL_SAMPLE") == "1":
            response = {"id":request["id"],"error":{"message":"injected sample failure"}}
        else:
            gpu = os.environ["CUDA_VISIBLE_DEVICES"]
            response = {"id":request["id"],"result":{"content":[{"text":json.dumps({"text":key})}]}}
            # One file per server: Windows does not make a shared append atomic
            # across processes, so a single log would drop concurrent writes.
            root = Path(os.environ["SERVER_CALLS"])
            root.mkdir(exist_ok=True)
            with (root / f"{os.getpid()}.jsonl").open("a") as calls:
                calls.write(json.dumps({"key":key,"gpu":gpu,"device":os.environ["DEVICE"]}) + "\n")
    else:
        response = {"id":request["id"],"result":{}}
    print(json.dumps(response), flush=True)
    if method == "shutdown":
        break
'''

FAKE_JSONL_SERVER = r'''
import json, sys
from pathlib import Path
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({"text":Path(request["audio_path"]).stem}), flush=True)
'''

# Windows loads its socket and event machinery from SystemRoot; a scrubbed
# environment that drops it makes every child die on `import asyncio`.
ESSENTIAL_ENV = {"PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR",
                 "TEMP", "TMP", "USERPROFILE", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS"}

# How long each fake Worker holds its turn. Overlapping start times are only
# observable while a peer is still running, so the window is this long.
WORK_SECONDS = 1.0


class ParallelTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.model = self.root / "model"
        (self.model / "artifacts").mkdir(parents=True)
        self.script_root = self.root / "skill"
        (self.script_root / "scripts").mkdir(parents=True)
        (self.script_root / "scripts" / "generate_predictions_via_server.py").write_text(FAKE_WORKER)
        (self.model / "server.py").write_text(FAKE_SERVER)
        config = {
            "model": {"name": "demo", "task": "ASR"},
            # A model that ships its own server declares it here; without a
            # declared command the harness starts its generic adapter instead.
            "server": {"command": [sys.executable, str(self.model / "server.py")],
                       "protocol": "mcp", "env": {"CUDA_VISIBLE_DEVICES": "all", "DEVICE": "cpu"}},
            "tools": [{"name": "transcribe_audio", "input_schema": {
                "properties": {"audio_path": {"type": "string"}}, "required": ["audio_path"]}}],
        }
        (self.model / "config.yaml").write_text(yaml.safe_dump(config))
        inventory = {
            "schema": "sure.onboard.runtime_inventory.v2", "status": "ready",
            "model": {"name": "demo"},
            "policy": {"eval_runtime": "python", "host_python_fallback": False, "image_override_allowed": False},
            "model_runtime": {"required": True, "runtime_id": "test-model", "lock_sha256": "a" * 64,
                              "server_command": [sys.executable, str(self.model / "server.py")],
                              "tool_names": ["transcribe_audio"]},
        }
        (self.model / "artifacts" / "runtime_inventory.json").write_text(json.dumps(inventory))
        self.datasets_root = self.root / "data"
        jsonl = self.datasets_root / "sure_benchmark" / "jsonl"
        jsonl.mkdir(parents=True)
        self.samples = [{"key": f"k{i}", "path": str(self.root / f"k{i}.wav"), "task": "ASR", "language": "zh"}
                        for i in range(10)]
        for sample in self.samples:
            Path(sample["path"]).write_bytes(b"fake audio: server does not read it")
        (jsonl / "demo__v1.jsonl").write_text("".join(json.dumps(sample) + "\n" for sample in self.samples))
        self.config = self.root / "harness.yaml"
        self.config.write_text(yaml.safe_dump({"data": {"datasets": str(self.datasets_root)}}))
        self.product = self.root / "product"
        self.product.mkdir()
        env = {key: value for key, value in os.environ.items() if key in ESSENTIAL_ENV}
        env.update({"MODEL_PYTHON": sys.executable, "SURE_EVAL_MODEL_RUNTIME_ID": "test-model",
                    "SURE_EVAL_MODEL_WORKING_DIR": str(self.model), "SURE_EVAL_CONFIG": str(self.config),
                    "CALLS": str(self.root / "calls.jsonl"), "SERVER_CALLS": str(self.root / "server_calls.jsonl"),
                    "WORK_SECONDS": str(WORK_SECONDS), "PYTHONDONTWRITEBYTECODE": "1"})
        ambient = patch.dict(os.environ, env)
        ambient.start()
        self.addCleanup(ambient.stop)
        self.ctx = infer_entrypoint.Ctx(
            repo_root=self.script_root, harness_root=self.root, model_name="demo", model_dir=self.model,
            run_dir=self.product, run_id="test", protocol_id="standard_system", requested_datasets=["demo__v1"],
            max_samples=0, no_resume=True, device="cuda", metrics="", smoke_samples=None,
            require_nonempty_predictions=False, child_env=env,
            tool_name="transcribe_audio", datasets_root=self.datasets_root, datasets=["demo__v1"],
            num_workers=3, gpu_devices=["2", "4", "6"],
        )

    def calls(self) -> list[dict]:
        path = Path(self.ctx.child_env["CALLS"])
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_mixed_automatic_workers_use_separate_processes_and_preserve_order(self) -> None:
        counts = {"small__v1": 5, "large__v1": 4000}
        self.ctx.datasets = list(counts)
        self.ctx.num_workers = 8
        self.ctx.gpu_devices = [str(index) for index in range(8)]
        self.ctx.worker_selection = "auto"
        for dataset, count in counts.items():
            samples = [{**self.samples[0], "key": f"k{index}"} for index in range(count)]
            path = self.datasets_root / "sure_benchmark" / "jsonl" / f"{dataset}.jsonl"
            path.write_text("".join(json.dumps(sample) + "\n" for sample in samples))
            self.ctx.worker_plans[dataset] = {"worker_selection": "auto", "planned_workers": 1 if count == 5 else 8,
                                              "total_samples": count, "selected_samples": count}
        infer_entrypoint.stage_generate(self.ctx)
        calls = self.calls()
        self.assertEqual([call["dataset"] for call in calls], ["small__v1"] + ["large__v1"] * 8)
        small_report = parallel.read_json(self.product / "parallel_inference/small__v1/merge_report.json")
        large_report = parallel.read_json(self.product / "parallel_inference/large__v1/merge_report.json")
        self.assertEqual(small_report["requested_workers"], 1)
        self.assertEqual(large_report["requested_workers"], 8)
        self.assertEqual(min(call["time"] for call in calls[1:]) > calls[0]["time"] + 0.15, True)
        for dataset, count in counts.items():
            rows = parallel.read_json(self.product / "parallel_inference" / dataset / "plan.json")
            self.assertEqual(rows["selected_samples"], count)
            self.assertEqual(rows["worker_selection"], "auto")
            lines = (self.product / "predictions" / f"{dataset}.txt").read_text().splitlines()
            self.assertEqual([line.split("\t")[0] for line in lines], [f"k{index}" for index in range(count)])
        self.ctx.no_resume = False
        infer_entrypoint.stage_generate(self.ctx)
        self.assertEqual(len(self.calls()), 9)
        summary = run_infer._parallel_summary(self.product, 8)
        self.assertEqual(summary["worker_selection"], "auto")
        self.assertEqual([row["planned_workers"] for row in summary["datasets"]], [1, 8])

    def test_dataset_count_drift_fails_before_starting_workers(self) -> None:
        self.ctx.worker_plans["demo__v1"] = {"worker_selection": "explicit", "planned_workers": 3,
                                           "total_samples": 11, "selected_samples": 11}
        with self.assertRaisesRegex(ValueError, "changed after planning"):
            parallel.run_dataset(self.ctx, "demo__v1")
        self.assertEqual(self.calls(), [])

    def test_even_split_and_small_or_empty_dataset(self) -> None:
        for count, workers, lengths in ((10, 3, [4, 3, 3]), (2, 4, [1, 1]), (0, 4, [])):
            with self.subTest(count=count):
                shards = parallel.split_keys([str(i) for i in range(count)], workers)
                self.assertEqual([len(shard) for shard in shards], lengths)
                self.assertEqual(sum(shards, []), [str(i) for i in range(count)])

    def test_concurrent_workers_merge_in_source_order_and_record_gpus(self) -> None:
        report = parallel.run_dataset(self.ctx, "demo__v1")
        self.assertEqual(report["status"], "completed")
        calls = self.calls()
        self.assertEqual(sorted(row["gpu"] for row in calls), ["2", "4", "6"])
        # Every Worker logged its start before the first one could finish, so
        # none of them was queued behind a peer. Process spawn is slow enough on
        # a loaded host that the fixed window this used to use was a coin flip.
        self.assertLess(max(row["time"] for row in calls), min(row["time"] for row in calls) + WORK_SECONDS)
        text = (self.product / "predictions" / "demo__v1.txt").read_text()
        self.assertEqual(text, "".join(f"k{i}\tk{i}\n" for i in range(10)))
        status = parallel.read_json(self.product / "prediction_generation_status.json")
        self.assertEqual(status["datasets"][0]["num_generated_samples"], 10)
        summary = run_infer._parallel_summary(self.product, 3)
        self.assertEqual(summary["gpu_devices"], ["2", "4", "6"])

    def test_one_worker_retry_does_not_repeat_successes(self) -> None:
        self.ctx.child_env["FAIL_MODE"] = "once"
        report = parallel.run_dataset(self.ctx, "demo__v1")
        self.assertEqual(report["status"], "completed")
        self.assertEqual(len(self.calls()), 4)
        retry = self.calls()[-1]
        self.assertEqual(retry["keys"], ["k3"])
        self.assertEqual(retry["gpu"], "2")

    def test_failure_preserves_successes_and_resume_only_loads_missing_keys(self) -> None:
        self.ctx.child_env["FAIL_MODE"] = "always"
        report = parallel.run_dataset(self.ctx, "demo__v1")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["generated"], 6)
        self.assertFalse((self.product / "predictions" / "manifest.json").exists())
        self.assertEqual(len(self.calls()), 5)
        self.ctx.no_resume = False
        self.ctx.child_env.pop("FAIL_MODE")
        report = parallel.run_dataset(self.ctx, "demo__v1")
        self.assertEqual(report["reused"], 6)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(set(sum([call["keys"] for call in self.calls()[5:]], [])), {"k0", "k1", "k2", "k3"})
        before = len(self.calls())
        report = parallel.run_dataset(self.ctx, "demo__v1")
        self.assertEqual(report["effective_workers"], 0)
        self.assertEqual(len(self.calls()), before)

    def test_scope_cap_precedes_split_and_changed_identity_is_rejected(self) -> None:
        self.ctx.max_samples = 2
        report = parallel.run_dataset(self.ctx, "demo__v1")
        self.assertEqual(report["effective_workers"], 2)
        self.assertEqual(report["expected"], 2)
        self.assertCountEqual(sum([call["keys"] for call in self.calls()], []), ["k0", "k1"])
        self.ctx.no_resume = False
        self.ctx.max_samples = 3
        with self.assertRaisesRegex(ValueError, "identity differs"):
            parallel.run_dataset(self.ctx, "demo__v1")

    def test_bad_worker_keys_fail_without_publishing(self) -> None:
        rows = [{"key": "unknown", "dataset": "demo__v1"}]
        with self.assertRaisesRegex(ValueError, "Unknown"):
            parallel._valid_records(rows, self.samples, "demo__v1", self.product)
        row = {"key": "k0", "dataset": "demo__v1", "task": "ASR", "normalized_prediction": "ok", "prediction": {"text": "ok"}}
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parallel._valid_records([row, row], self.samples, "demo__v1", self.product)

    def test_empty_prediction_follows_the_run_and_task_policy(self) -> None:
        row = {"key": "k0", "dataset": "demo__v1", "task": "ASR",
               "normalized_prediction": "", "prediction": {"text": ""}}
        # ASR scores an empty answer unless the run demands non-empty values.
        self.assertEqual(list(parallel._valid_records([row], self.samples, "demo__v1", self.product)), ["k0"])
        os.environ["SURE_EVAL_REQUIRE_NONEMPTY_PREDICTIONS"] = "1"
        self.addCleanup(os.environ.pop, "SURE_EVAL_REQUIRE_NONEMPTY_PREDICTIONS", None)
        self.assertEqual(parallel._valid_records([row], self.samples, "demo__v1", self.product), {})
        # A task that does not score empty answers drops the row either way.
        os.environ.pop("SURE_EVAL_REQUIRE_NONEMPTY_PREDICTIONS", None)
        classification = [{**self.samples[0], "task": "classification"}]
        self.assertEqual(parallel._valid_records([{**row, "task": "classification"}],
                                                 classification, "demo__v1", self.product), {})

    def test_killed_worker_journal_keeps_complete_utf8_rows(self) -> None:
        path = self.root / "journal.jsonl"
        path.write_bytes(b'{"key":"ok"}\n{"key":"' + b'\xe4\xb8')
        self.assertEqual(parallel._journal_records(path), [{"key": "ok"}])
        for text, code, kind in (("CUDA out of memory", 1, "gpu_oom"), ("OOMKilled", 137, "host_oom"),
                                 ("", 137, "killed_unknown"), ("ValueError: bad input", 1, "input_or_config")):
            self.assertEqual(parallel._error_kind(text, code), kind)

    def test_actual_generator_enforces_gpu_and_continues_a_sample_error(self) -> None:
        self.ctx.repo_root = Path(__file__).resolve().parents[1]
        self.ctx.num_workers = 2
        self.ctx.gpu_devices = ["2", "4"]
        self.ctx.max_samples = 4
        self.ctx.child_env["FAIL_SAMPLE"] = "1"
        report = parallel.run_dataset(self.ctx, "demo__v1")
        self.assertEqual(report["status"], "failed")
        diagnostics = str([parallel.read_json(path).get("datasets") for path in self.product.glob("parallel_inference/**/prediction_generation_status.json")])
        self.assertEqual(report["missing_keys"], ["k1"], diagnostics)
        calls = [json.loads(line) for path in Path(self.ctx.child_env["SERVER_CALLS"]).glob("*.jsonl")
                 for line in path.read_text().splitlines()]
        self.assertEqual({row["key"] for row in calls}, {"k0", "k2", "k3"})
        self.assertTrue(all(row["device"] == "cuda:0" and row["gpu"] in {"2", "4"} for row in calls))

    def test_actual_jsonl_server_uses_the_same_parallel_controller(self) -> None:
        config = yaml.safe_load((self.model / "config.yaml").read_text())
        config["server"]["protocol"] = "jsonl"
        (self.model / "config.yaml").write_text(yaml.safe_dump(config))
        (self.model / "server.py").write_text(FAKE_JSONL_SERVER)
        self.ctx.repo_root = Path(__file__).resolve().parents[1]
        self.ctx.max_samples = 5
        report = parallel.run_dataset(self.ctx, "demo__v1")
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["generated"], 5)

    def test_unresponsive_worker_preserves_peers_and_bounds_retries(self) -> None:
        config = yaml.safe_load((self.model / "config.yaml").read_text())
        config["server"].update({"timeout": 0.1, "startup_timeout_sec": 5})
        (self.model / "config.yaml").write_text(yaml.safe_dump(config))
        self.ctx.repo_root = Path(__file__).resolve().parents[1]
        self.ctx.max_samples = 4
        self.ctx.num_workers = 2
        self.ctx.gpu_devices = ["2", "4"]
        self.ctx.child_env["WAIT_SAMPLE"] = "1"
        report = parallel.run_dataset(self.ctx, "demo__v1")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["missing_keys"], ["k1"])
        self.assertEqual(len(report["attempts"]), 4)
        self.assertEqual(report["generated"], 3)

    def test_datasets_remain_serial_even_when_first_is_incomplete(self) -> None:
        self.ctx.datasets = ["first", "second"]
        visited = []
        def run(ctx, dataset):
            visited.append(dataset)
            return {"status": "failed" if dataset == "first" else "completed"}
        with patch.object(parallel, "run_dataset", side_effect=run):
            with self.assertRaisesRegex(infer_entrypoint.StageError, "first"):
                infer_entrypoint.stage_generate(self.ctx)
        self.assertEqual(visited, ["first", "second"])

    def test_gpu_probe_honors_visibility_and_refuses_short_allocation(self) -> None:
        env = {"MODEL_PYTHON": "/model-python", "CUDA_VISIBLE_DEVICES": "2,4,6"}
        def run(command, **_kwargs):
            if command[0] == "nvidia-smi":
                return subprocess.CompletedProcess(command, 0, "0, GPU-a\n2, GPU-b\n4, GPU-c\n6, GPU-d\n", "")
            return subprocess.CompletedProcess(command, 0, '{"count":3}\n', "")
        with patch.object(parallel.subprocess, "run", side_effect=run):
            self.assertEqual(parallel.discover_gpus(2, env), ["2", "4"])
            with self.assertRaisesRegex(ValueError, "only 3"):
                parallel.discover_gpus(4, env)
            # A narrowed allocation is still the truth: two Workers cannot share one GPU.
            single = {**env, "CUDA_VISIBLE_DEVICES": "2"}
            results = [subprocess.CompletedProcess([], 0, "0, GPU-a\n2, GPU-b\n", ""),
                       subprocess.CompletedProcess([], 0, '{"count":1}\n', "")]
            with patch.object(parallel.subprocess, "run", side_effect=results):
                with self.assertRaisesRegex(ValueError, "only 1"):
                    parallel.discover_gpus(2, single)

    def test_worker_count_parameters_are_validated_without_a_cluster(self) -> None:
        parser = resolve_eval_input._build_parser()
        base = ["--model", "demo", "--datasets", "/data/demo"]
        self.assertEqual(resolve_eval_input._parallel_request(parser.parse_args(base)), 1)
        self.assertEqual(resolve_eval_input._parallel_request(parser.parse_args(base + ["--num-workers", "4"])), 4)
        # An omitted count is the automatic path, not an error.
        self.assertIsNone(parser.parse_args(base).num_workers)
        for flags in (("--num-workers", "0"), ("--num-workers", "9"),
                      ("--num-workers", "2", "--device", "cpu"),
                      ("--num-workers", "2", "--device", "cuda:0")):
            with self.subTest(flags=flags), self.assertRaises(resolve_eval_input.EvalInputError):
                resolve_eval_input._parallel_request(parser.parse_args(base + list(flags)))
        for stale in ("--vc-gpus", "--vc-cpus", "--vc-partition"):
            with self.subTest(flag=stale), self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(base + [stale, "1"])

    def test_container_gpu_uuid_discovery_does_not_use_host_indices(self) -> None:
        env = {"MODEL_PYTHON": "/model-python"}
        results = [subprocess.CompletedProcess([], 0, "2, GPU-b\n4, GPU-c\n", ""),
                   subprocess.CompletedProcess([], 0, '{"count":2}\n', "")]
        with patch.object(parallel.subprocess, "run", side_effect=results):
            self.assertEqual(parallel.discover_gpus(2, env), ["GPU-b", "GPU-c"])

    def test_new_fresh_session_does_not_reuse_old_successes(self) -> None:
        parallel.run_dataset(self.ctx, "demo__v1")
        self.ctx.child_env["FAIL_MODE"] = "always"
        parallel.run_dataset(self.ctx, "demo__v1")
        self.ctx.child_env.pop("FAIL_MODE")
        self.ctx.no_resume = False
        report = parallel.run_dataset(self.ctx, "demo__v1")
        self.assertEqual(report["reused"], 6)
        self.assertEqual(report["generated"], 10)

    def test_shared_interruption_cleans_started_workers(self) -> None:
        started = []
        popen = subprocess.Popen
        def launch(*args, **kwargs):
            if started:
                raise InterruptedError("injected cancellation")
            process = popen(*args, **kwargs)
            started.append(process)
            return process
        with patch.object(parallel.subprocess, "Popen", side_effect=launch):
            with self.assertRaisesRegex(InterruptedError, "cancellation"):
                parallel.run_dataset(self.ctx, "demo__v1")
        self.assertIsNotNone(started[0].poll())
        report = parallel.read_json(self.product / "parallel_inference/demo__v1/merge_report.json")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["attempts"][0]["status"], "failed")
        self.assertEqual(report["attempts"][0]["error_kind"], "shared_failure")

    def test_resume_records_the_current_execution_job(self) -> None:
        self.ctx.child_env["SURE_EVAL_EXECUTION_JOB_ID"] = "job-first"
        parallel.run_dataset(self.ctx, "demo__v1")
        self.ctx.no_resume = False
        self.ctx.child_env["SURE_EVAL_EXECUTION_JOB_ID"] = "job-resumed"
        report = parallel.run_dataset(self.ctx, "demo__v1")
        self.assertEqual(report["reused"], 10)
        status = parallel.read_json(self.product / "prediction_generation_status.json")
        plan = parallel.read_json(self.product / "parallel_inference/demo__v1/plan.json")
        self.assertEqual(status["execution_job_id"], "job-resumed")
        self.assertEqual(plan["execution"]["job_id"], "job-resumed")

    def test_local_container_limits_gpus_and_drops_host_cuda_ordinals(self) -> None:
        fixture = binding_tests.DeploymentBindingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        binding = load_deployment_binding(fixture.model, "demo")
        arguments = dict(surface={"env": {"SURE_INFER_NUM_WORKERS": "2"}},
            eval_input={"model": {"deployment_binding": binding}, "runtime": {
                "num_workers": 2, "run_dir": str(fixture.output), "harness_runtime": fixture._runtime_binding(),
                "dataset_projection": {"host_root": str(fixture.projection)}}, "datasets": []},
            control_run_dir=fixture.control, entrypoint=fixture.entrypoint, repo_root=fixture.repo,
            device_request="cuda", extra_env={"CUDA_VISIBLE_DEVICES": "2,4", "NO_RESUME": "0"})
        with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "2,4"}):
            command, _ = build_local_container_command(**arguments)
        self.assertEqual(command[command.index("--gpus") + 1], '"device=2,4"')
        self.assertFalse(any(argument.startswith("CUDA_VISIBLE_DEVICES=") for argument in command))
        self.assertIn("NO_RESUME=0", command)
        with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "2"}):
            with self.assertRaisesRegex(ValueError, "enough"):
                build_local_container_command(**arguments)

    def test_report_and_gate_use_the_recorded_task_projection(self) -> None:
        self.ctx.worker_selection = "auto"
        self.ctx.worker_plans["demo__v1"] = {"worker_selection": "auto", "planned_workers": 3,
                                           "total_samples": 10, "selected_samples": 10}
        parallel.run_dataset(self.ctx, "demo__v1")
        (self.product / "validation_payload.json").write_text(json.dumps({"is_valid": True,
            "results": [{"dataset": "demo__v1", "is_valid": True, "expected_samples": 10, "provided_predictions": 10}]}))
        canonical = "demo_pool__v1"
        details = [{"name": canonical, "jsonl_path": "/data/demo__v1.jsonl", "planned_workers": 3,
                    "num_samples": 10, "selected_samples": 10}]
        rows = run_infer._dataset_rows(self.product, [canonical], run_infer.projection_stems(details))
        self.assertEqual(rows, [{"dataset": canonical, "expected": 10, "generated": 10, "valid": 10}])
        artifacts = self.root / "control/artifacts"
        artifacts.mkdir(parents=True)
        binding = {"schema": "sure.deployment.binding.v2", "runtime_kind": "python", "model_dir": str(self.model),
                   "python": {}, "evidence": {}}
        surface = {"execution": {"path_planned": "local_python"},
                   "deployment_binding": check_execution_result.compliance.expected_binding_summary(binding)}
        (artifacts / "execution_surface.json").write_text(json.dumps(surface))
        (artifacts / "eval_input_resolved.json").write_text(json.dumps({"model": {"deployment_binding": binding},
                "datasets": details, "runtime": {"num_workers": 3, "worker_selection": "auto"}}))
        result = {"job_status": "succeeded", "exit_code": 0, "execution_path": "local_python",
                  "product_dir": str(self.product), "datasets": rows,
                  "parallelism": run_infer._parallel_summary(self.product, 3)}
        result_path = artifacts / "execution_result.json"
        result_path.write_text(json.dumps(result))
        (self.product / "protocol.yaml").write_text("schema: test\n")
        references = self.product / "references/sure_benchmark/jsonl"
        references.mkdir(parents=True)
        (references / "demo__v1.jsonl").write_text("{}\n")
        self.assertEqual(check_execution_result.gate_errors(artifacts.parent, result_path), [])
        for key, value in (("planned_workers", 8), ("total_samples", 11), ("selected_samples", 9), ("effective_workers", 4)):
            with self.subTest(key=key):
                old = result["parallelism"]["datasets"][0][key]
                result["parallelism"]["datasets"][0][key] = value
                result_path.write_text(json.dumps(result))
                self.assertTrue(check_execution_result.gate_errors(artifacts.parent, result_path))
                result["parallelism"]["datasets"][0][key] = old
        result_path.write_text(json.dumps(result))
        (self.product / "predictions/demo__v1.jsonl").write_text("{}\n")
        self.assertTrue(any("digest changed" in error for error in check_execution_result.gate_errors(artifacts.parent, result_path)))

    def test_audio_paths_localize_without_changing_asr_text_or_raw_evidence(self) -> None:
        prediction = self.product / "predictions"
        prediction.mkdir()
        audio = self.product / "parallel_inference/demo/workers/worker_0/attempt_0000/audio/k.wav"
        audio.parent.mkdir(parents=True)
        audio.write_bytes(b"test audio")
        row = {"key": "k", "task": "SE", "prediction": {"audio_path": str(audio)},
               "normalized_prediction": str(audio), "raw_response": {"path": str(audio)}}
        text_row = {"key": "text", "task": "ASR", "prediction": {"text": str(audio)},
                    "normalized_prediction": str(audio), "raw_response": {"text": str(audio)}}
        (prediction / "demo.jsonl").write_text(json.dumps(row) + "\n" + json.dumps(text_row) + "\n")
        (prediction / "demo.txt").write_text(f"k\t{audio}\ntext\t{audio}\n")
        journal = audio.parent.parent / "completed.jsonl"
        journal.write_bytes((json.dumps(row) + "\n").encode() + b'{"key":"\xe4\xb8')
        published = self.root / "host-product"
        finalize_bundle(self.product, published)
        localized = [json.loads(line) for line in (prediction / "demo.jsonl").read_text().splitlines()]
        self.assertTrue(localized[0]["normalized_prediction"].startswith(str(published)))
        self.assertEqual(localized[0]["raw_response"], row["raw_response"])
        self.assertEqual(localized[1], text_row)
        restored = parallel.localize_prediction_row(localized[0], str(published), str(self.product))
        self.assertEqual(restored, row)
        self.assertEqual(parallel._journal_records(journal), [localized[0]])

    def test_relative_worker_audio_is_published_as_a_stable_absolute_path(self) -> None:
        attempt = self.product / "parallel_inference/demo/workers/worker_0/attempt_0000"
        audio = attempt / "audio/k.wav"
        audio.parent.mkdir(parents=True)
        audio.write_bytes(b"test audio")
        row = {"key": "k", "dataset": "demo", "task": "SE",
               "prediction": {"audio_path": "audio/k.wav"},
               "normalized_prediction": "audio/k.wav", "raw_response": {"path": "audio/k.wav"}}
        valid = parallel._valid_records([row], [{"key": "k", "task": "SE"}], "demo", attempt)
        self.assertEqual(valid["k"]["prediction"]["audio_path"], str(audio))
        self.assertEqual(valid["k"]["normalized_prediction"], str(audio))
        self.assertEqual(valid["k"]["raw_response"], row["raw_response"])

    def test_blocking_stdio_timeout_is_bounded(self) -> None:
        # A server that never answers must not hold the Worker for the run. On
        # Windows there is no interval timer to interrupt a blocking read, so
        # the deadline is proved against a real wedged pipe: the kill turns the
        # read into the timeout it actually was.
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                   start_new_session=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "did not respond"):
            parallel.generation._worker_request(parallel.generation._send_request, process,
                                                {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, 0.3)
        self.assertLess(time.monotonic() - started, 10)
        self.assertIsNotNone(process.wait(timeout=10))


if __name__ == "__main__":
    unittest.main()
