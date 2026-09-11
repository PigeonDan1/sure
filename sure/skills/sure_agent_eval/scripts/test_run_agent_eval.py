#!/usr/bin/env python3
"""Tests for run_agent_eval.py: the eval wrapper around the pinned engine backend.

The engine bridge itself is stubbed (no real evaluation runtime); what is
tested is the wrapper's contract: input requirements, report shape, and error
mapping.

Run directly (needs the Harness Python):
    cd sure/skills/sure_agent_eval/scripts && python3 -m unittest test_run_agent_eval.py
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_agent_eval  # noqa: E402

DATASET = "mini_s2tt__unversioned"


def make_spec(product_dir: Path) -> dict:
    return {
        "schema": "sure.agent_eval.spec_resolved.v1",
        "run_id": "run_test",
        "created_at": "2026-09-10T00:00:00Z",
        "agent": {
            "name": "demo_s2tt",
            "task": "s2tt",
            "input": "speech",
            "output": "text",
            "spec_path": "/tmp/agent.yaml",
            "spec_sha256": "a" * 64,
        },
        "stages": [
            {
                "id": "asr",
                "model": "asr_model",
                "mode": "mcp_tool",
                "task": "ASR",
                "model_dir": "/tmp/models/asr_model",
                "config_path": "/tmp/models/asr_model/config.yaml",
                "verdict_path": "/tmp/models/asr_model/verdict.json",
                "tool_names": ["asr_transcribe"],
                "server_command": ["python", "server.py"],
                "working_dir": "/tmp/models/asr_model",
                "api": None,
                "prompt_template": None,
                "deployment_bound": False,
                "deployment_error": None,
            }
        ],
        "datasets": [
            {
                "dataset": DATASET,
                "source_root": "/tmp/src/mini_s2tt",
                "source_dataset_name": "mini_s2tt",
                "version_id": "unversioned",
                "task": "S2TT",
                "language": "zh",
                "translation_language": "en",
                "sample_jsonl": "/tmp/src/mini_s2tt/sample.jsonl",
                "ds_jsonl": "/tmp/src/mini_s2tt/ds.jsonl",
                "raw_dir": "/tmp/src/mini_s2tt",
                "num_samples": 2,
            }
        ],
        "metrics": ["bleu", "chrf"],
        "runtime": {"product_dir": str(product_dir), "output_dir": None, "dataset_source_key": "default"},
    }


def write_bundle(product_dir: Path) -> None:
    (product_dir / "predictions").mkdir(parents=True, exist_ok=True)
    (product_dir / "references" / "sure_benchmark" / "jsonl").mkdir(parents=True, exist_ok=True)
    (product_dir / "predictions" / f"{DATASET}.txt").write_text("utt0\thello\nutt1\tworld\n", encoding="utf-8")
    (product_dir / "references" / "sure_benchmark" / "jsonl" / f"{DATASET}.jsonl").write_text(
        '{"key": "utt0", "target": "hello"}\n', encoding="utf-8"
    )


def fake_run(scratch: Path):
    def _run(command, *, cwd=None, env=None):
        if "--output" in command:
            output = Path(command[command.index("--output") + 1])
            if "validate_prediction_files" in command[1]:
                output.write_text('{"is_valid": true}\n', encoding="utf-8")
            else:
                output.write_text(
                    json.dumps(
                        {
                            "results": [
                                {
                                    "dataset": DATASET,
                                    "metric": "bleu",
                                    "pipeline_id": "s2tt.zh.bleu.sacrebleu_zh_v1",
                                    "result": {"score": 42.0},
                                },
                                {
                                    "dataset": DATASET,
                                    "metric": "chrf",
                                    "pipeline_id": "s2tt.zh.chrf.sacrebleu_zh_v1",
                                    "result": {"score": 55.5},
                                },
                            ]
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
        return None

    return _run


class RunAgentEvalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.run_dir = self.tmp / "run"
        self.artifacts = self.run_dir / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.product_dir = self.tmp / "product"
        write_bundle(self.product_dir)
        (self.artifacts / "agent_spec_resolved.json").write_text(
            json.dumps(make_spec(self.product_dir)), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_args(self) -> argparse.Namespace:
        return argparse.Namespace(run_dir=str(self.run_dir), run_id="run_test", device="cpu")

    def write_execution(self, job_status: str = "succeeded") -> None:
        (self.artifacts / "execution_result.json").write_text(
            json.dumps(
                {
                    "schema": "sure.agent_eval.execution_result.v1",
                    "job_status": job_status,
                    "exit_code": 0 if job_status == "succeeded" else 1,
                    "product_dir": str(self.product_dir),
                    "agent": {"name": "demo_s2tt", "task": "s2tt"},
                    "datasets": [{"dataset": DATASET, "expected": 2, "generated": 2}],
                    "created_at": "2026-09-10T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )

    def patch_backend(self):
        return (
            mock.patch.object(run_agent_eval.run_eval, "_harness_config", return_value=self.tmp / "config.yaml"),
            mock.patch.object(run_agent_eval.run_eval, "_run", side_effect=fake_run(self.run_dir)),
            mock.patch.object(
                run_agent_eval.run_eval,
                "_pipeline_ids_for_metrics",
                return_value=["s2tt.zh.bleu.sacrebleu_zh_v1", "s2tt.zh.chrf.sacrebleu_zh_v1"],
            ),
            mock.patch.object(
                run_agent_eval.run_eval,
                "_engine_info",
                return_value={"engine_root": "/engine", "commit": "abc123"},
            ),
        )

    def test_success_report(self) -> None:
        self.write_execution()
        config_mock, run_mock, pipelines_mock, engine_mock = self.patch_backend()
        with config_mock, run_mock, pipelines_mock, engine_mock:
            report = run_agent_eval.run_agent_eval(self.make_args())
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["schema"], "sure.agent_eval.eval_run_report.v1")
        self.assertTrue(report["evaluation_only"])
        self.assertTrue(report["batch_id"].startswith("agent_eval_"))
        self.assertEqual(report["agent"], {"name": "demo_s2tt", "spec_sha256": "a" * 64})
        (dataset,) = report["datasets"]
        self.assertEqual(dataset["dataset"], DATASET)
        self.assertEqual(dataset["task"], "S2TT")
        self.assertEqual(dataset["num_samples"], 2)
        self.assertEqual(
            dataset["metrics"],
            [
                {"metric": "bleu", "pipeline_id": "s2tt.zh.bleu.sacrebleu_zh_v1", "score": 42.0},
                {"metric": "chrf", "pipeline_id": "s2tt.zh.chrf.sacrebleu_zh_v1", "score": 55.5},
            ],
        )
        batch_dir = Path(report["evaluation_runs_dir"])
        self.assertEqual(batch_dir, self.product_dir / "evaluation_runs" / report["batch_id"])
        self.assertTrue((batch_dir / "evaluation_payload.json").is_file())
        persisted = json.loads((self.artifacts / "eval_run_report.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["status"], "success")

    def test_missing_execution_is_a_failed_report(self) -> None:
        report = run_agent_eval.run_agent_eval(self.make_args())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_code"], "AGENT_EXECUTION_MISSING")

    def test_failed_agent_run_is_a_failed_report(self) -> None:
        self.write_execution(job_status="failed")
        report = run_agent_eval.run_agent_eval(self.make_args())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_code"], "AGENT_EXECUTION_FAILED")

    def test_engine_failure_is_a_failed_report(self) -> None:
        self.write_execution()
        with (
            mock.patch.object(run_agent_eval.run_eval, "_harness_config", return_value=self.tmp / "config.yaml"),
            mock.patch.object(run_agent_eval.run_eval, "_run", side_effect=RuntimeError("engine exploded")),
            mock.patch.object(run_agent_eval.run_eval, "_pipeline_ids_for_metrics", return_value=["p"]),
        ):
            report = run_agent_eval.run_agent_eval(self.make_args())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_code"], "EVALUATION_FAILED")
        persisted = json.loads((self.artifacts / "eval_run_report.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["error_code"], "EVALUATION_FAILED")


if __name__ == "__main__":
    unittest.main()
