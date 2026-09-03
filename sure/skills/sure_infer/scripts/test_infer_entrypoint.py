#!/usr/bin/env python3
"""End-to-end tests for infer_entrypoint.py with fake bundled scripts.

The real entrypoint runs under this interpreter against a fake skill root whose
scripts/ directory holds stand-ins for the deterministic scripts. Each fake
records its argv so the tests can assert what the entrypoint asked for.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parent / "infer_entrypoint.py"
STAGE_MARKER = "INFER_STAGE_FAILED"

FAKE_PREPARE = """
import json, sys
from pathlib import Path
args = sys.argv[1:]
datasets = args[args.index("--dataset") + 1 : args.index("--output")]
Path(args[args.index("--output") + 1]).write_text(
    json.dumps({"prepared": [{"dataset": f"{name}__v1"} for name in datasets]}), encoding="utf-8"
)
"""

FAKE_MATERIALIZE = """
import json, sys
from pathlib import Path
args = sys.argv[1:]
out = Path(args[args.index("--output-dir") + 1])
out.mkdir(parents=True, exist_ok=True)
(out / "manifest.json").write_text(json.dumps({"datasets": []}), encoding="utf-8")
"""

FAKE_GENERATE = """
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
run_dir = Path(args[args.index("--run-dir") + 1])
dataset = args[args.index("--dataset") + 1]
rows = int(args[args.index("--max-samples") + 1]) if "--max-samples" in args else 3
with (run_dir / "fake_calls.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
predictions = run_dir / "predictions"
predictions.mkdir(parents=True, exist_ok=True)
empty = os.environ.get("FAKE_EMPTY_PREDICTIONS") == "1"
(predictions / f"{dataset}.txt").write_text(
    "".join(f"key{i}\\t{'' if empty else f'pred{i}'}\\n" for i in range(rows)), encoding="utf-8"
)
status_path = run_dir / "prediction_generation_status.json"
status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {"datasets": []}
status["datasets"] = [row for row in status["datasets"] if row["dataset"] != dataset]
status["datasets"].append({"dataset": dataset, "status": "completed", "num_expected_samples": rows, "num_generated_samples": rows})
status_path.write_text(json.dumps(status), encoding="utf-8")
"""

FAKE_VALIDATE = """
import json, sys
from pathlib import Path
args = sys.argv[1:]
datasets = args[args.index("--dataset") + 1 : args.index("--pred-dir")]
Path(args[args.index("--output") + 1]).write_text(
    json.dumps({"is_valid": True, "results": [{"dataset": name, "is_valid": True} for name in datasets]}), encoding="utf-8"
)
"""

FAKE_PROTOCOL = """
import sys
from pathlib import Path
args = sys.argv[1:]
results_dir = Path(args[args.index("--results-dir") + 1])
tool = args[args.index("--tool-name") + 1]
(results_dir / "protocol.yaml").write_text(f"schema: sure.eval.inference_protocol.v1\\ntool: {tool}\\n", encoding="utf-8")
"""

FAKE_FINALIZE = """
import json, sys
from pathlib import Path
args = sys.argv[1:]
run_dir = Path(args[args.index("--run-dir") + 1])
(run_dir / "artifact_path_localization.json").write_text(json.dumps({"changed_artifacts": []}), encoding="utf-8")
"""

FAKES = {
    "prepare_sure_dataset.py": FAKE_PREPARE,
    "materialize_predictions_template.py": FAKE_MATERIALIZE,
    "generate_predictions_via_server.py": FAKE_GENERATE,
    "validate_prediction_files.py": FAKE_VALIDATE,
    "protocol_writer.py": FAKE_PROTOCOL,
    "finalize_result_bundle.py": FAKE_FINALIZE,
}


class InferEntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo_root = self.root / "sure" / "skills" / "sure_infer"
        scripts = self.repo_root / "scripts"
        scripts.mkdir(parents=True)
        for name, body in FAKES.items():
            (scripts / name).write_text(body.lstrip(), encoding="utf-8")
        self.model_dir = self.root / "models" / "demo"
        self.model_dir.mkdir(parents=True)
        (self.model_dir / "config.yaml").write_text(
            "model:\n  name: demo\n  task: ASR\ntools:\n  - name: transcribe_audio\n", encoding="utf-8"
        )
        self.datasets_root = self.root / "data" / "datasets"
        jsonl_dir = self.datasets_root / "sure_benchmark" / "jsonl"
        jsonl_dir.mkdir(parents=True)
        for name in ("ds_a__v1", "ds_b__v1"):
            (jsonl_dir / f"{name}.jsonl").write_text(json.dumps({"key": "k1", "path": "/audio/k1.wav"}) + "\n", encoding="utf-8")
        self.config = self.root / "harness_config.yaml"
        self.config.write_text(f"data:\n  datasets: {self.datasets_root.as_posix()}\n", encoding="utf-8")
        self.run_dir = self.root / "sure" / "results" / "demo" / "standard_system" / "run-1"
        self.run_dir.mkdir(parents=True)
        self.input_resolved = self.root / "eval_input_resolved.json"
        self.input_resolved.write_text(
            json.dumps(
                {
                    "datasets": [
                        {"name": "ds_a__v1", "language": "zh"},
                        {"name": "ds_b__v1", "language": "en"},
                    ]
                }
            ),
            encoding="utf-8",
        )

    def env(self, **overrides: str | None) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG", "LC_ALL", "PYTHONIOENCODING"}
        }
        env.update(
            {
                "REPO_ROOT": str(self.repo_root),
                "MODEL_NAME": "demo",
                "MODEL_DIR": str(self.model_dir),
                "SURE_EVAL_APPROVED_MODEL_DIR": str(self.model_dir),
                "RUN_DIR": str(self.run_dir),
                "RUN_ID": "run-1",
                "PROTOCOL_ID": "standard_system",
                "DATASETS": "ds_a ds_b",
                "MAX_SAMPLES": "0",
                "SURE_EVAL_CONFIG": str(self.config),
                "SURE_EVAL_DATASETS_ROOT": str(self.datasets_root),
                "SURE_EVAL_INPUT_RESOLVED": str(self.input_resolved),
                "MODEL_PYTHON": str(self.root / "model-python-that-does-not-exist"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        for key, value in overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return env

    def run_entrypoint(self, **overrides: str | None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ENTRYPOINT)],
            env=self.env(**overrides),
            capture_output=True,
            text=True,
            check=False,
            cwd=str(self.root),
        )

    def generate_calls(self) -> list[list[str]]:
        path = self.run_dir / "fake_calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    @staticmethod
    def failed_stage(completed: subprocess.CompletedProcess[str]) -> str:
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        last = lines[-1] if lines else ""
        return last.split(" ", 1)[1] if last.startswith(STAGE_MARKER) else ""

    def test_full_pass_writes_the_product_tree(self) -> None:
        completed = self.run_entrypoint()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        for relative in (
            "prepare_summary.json",
            "predictions/manifest.json",
            "predictions/ds_a__v1.txt",
            "predictions/ds_b__v1.txt",
            "validation_payload.json",
            "protocol.yaml",
            "references/sure_benchmark/jsonl/ds_a__v1.jsonl",
            "references/sure_benchmark/jsonl/ds_b__v1.jsonl",
            "artifact_path_localization.json",
        ):
            self.assertTrue((self.run_dir / relative).is_file(), relative)
        # The tool name came from the model config because TOOL_NAME was not injected.
        self.assertIn("tool: transcribe_audio", (self.run_dir / "protocol.yaml").read_text(encoding="utf-8"))
        calls = self.generate_calls()
        self.assertEqual([call[call.index("--dataset") + 1] for call in calls], ["ds_a__v1", "ds_a__v1", "ds_b__v1"])
        for call in calls:
            self.assertEqual(call[call.index("--tool-name") + 1], "transcribe_audio")
            self.assertIn("--resume", call)

    def test_smoke_runs_first_and_bounds_the_sample_count(self) -> None:
        completed = self.run_entrypoint()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        smoke, full_a, full_b = self.generate_calls()
        self.assertEqual(smoke[smoke.index("--max-samples") + 1], "10")
        self.assertNotIn("--max-samples", full_a)
        self.assertNotIn("--max-samples", full_b)

    def test_max_samples_below_ten_bounds_the_smoke_pass(self) -> None:
        completed = self.run_entrypoint(MAX_SAMPLES="5")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        smoke, full_a, _ = self.generate_calls()
        self.assertEqual(smoke[smoke.index("--max-samples") + 1], "5")
        self.assertEqual(full_a[full_a.index("--max-samples") + 1], "5")

    def test_max_samples_above_ten_keeps_the_ten_sample_smoke(self) -> None:
        completed = self.run_entrypoint(MAX_SAMPLES="50")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        smoke, full_a, _ = self.generate_calls()
        self.assertEqual(smoke[smoke.index("--max-samples") + 1], "10")
        self.assertEqual(full_a[full_a.index("--max-samples") + 1], "50")

    def test_no_resume_drops_the_resume_flag(self) -> None:
        completed = self.run_entrypoint(NO_RESUME="1")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for call in self.generate_calls():
            self.assertNotIn("--resume", call)

    def test_each_dataset_gets_its_own_language(self) -> None:
        completed = self.run_entrypoint()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        languages = {call[call.index("--dataset") + 1]: call[call.index("--language") + 1] for call in self.generate_calls()}
        self.assertEqual(languages, {"ds_a__v1": "zh", "ds_b__v1": "en"})

    def test_a_failing_smoke_stops_before_the_full_pass(self) -> None:
        completed = self.run_entrypoint(FAKE_EMPTY_PREDICTIONS="1")
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(self.failed_stage(completed), "smoke")
        self.assertEqual(len(self.generate_calls()), 1)
        self.assertFalse((self.run_dir / "validation_payload.json").exists())

    def test_a_model_dir_outside_the_approved_model_is_refused(self) -> None:
        other = self.root / "models" / "other"
        other.mkdir()
        completed = self.run_entrypoint(MODEL_DIR=str(other))
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(self.failed_stage(completed), "guards")
        self.assertIn("approved model directory", completed.stderr)
        self.assertEqual(self.generate_calls(), [])

    def test_a_run_dir_outside_the_staging_root_is_refused(self) -> None:
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        completed = self.run_entrypoint(RUN_DIR=str(elsewhere))
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(self.failed_stage(completed), "guards")
        self.assertIn("RUN_DIR", completed.stderr)

    def test_the_launchers_result_directory_is_an_allowed_run_dir(self) -> None:
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.run_dir = elsewhere
        completed = self.run_entrypoint(RUN_DIR=str(elsewhere), SURE_EVAL_PUBLISHED_RUN_DIR=str(elsewhere))
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_a_missing_dataset_projection_fails_the_references_stage(self) -> None:
        (self.datasets_root / "sure_benchmark" / "jsonl" / "ds_b__v1.jsonl").unlink()
        completed = self.run_entrypoint()
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(self.failed_stage(completed), "references")
        self.assertIn("ds_b__v1.jsonl", completed.stderr)

    def test_an_unknown_protocol_is_refused(self) -> None:
        completed = self.run_entrypoint(PROTOCOL_ID="loose")
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(self.failed_stage(completed), "guards")


if __name__ == "__main__":
    unittest.main()
