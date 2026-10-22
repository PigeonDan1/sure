"""Worker-count boundaries without GPUs, model loading or cluster submission."""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import infer_entrypoint
import parallel_predictions
import resolve_eval_input


class WorkerLimitTests(unittest.TestCase):
    def _plan(self, args, manager, rows, *, available: int = 8):
        with patch.object(resolve_eval_input, "_local_gpu_count", return_value=available):
            return resolve_eval_input._plan_workers(args, manager, rows)

    def test_automatic_thresholds_actual_projection_and_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "task__asr.jsonl"
            manager = Mock()
            manager.download_and_convert.return_value = path
            args = resolve_eval_input._build_parser().parse_args(["--model", "demo", "--datasets", "demo"])
            self.assertIsNone(args.num_workers)
            for total, expected in ((5, 1), (999, 1), (1000, 2), (1999, 2), (2000, 4), (3999, 4), (4000, 8), (8000, 8)):
                with self.subTest(total=total):
                    path.write_text('{}\n' * total + '\n', encoding="utf-8")
                    rows = [{"name": "demo", "requested_name": "/source/demo@v1", "task": "ASR"}]
                    self.assertEqual(self._plan(args, manager, rows), (expected, "auto"))
                    self.assertEqual(rows[0]["num_samples"], total)
                    self.assertEqual(rows[0]["selected_samples"], total)
                    self.assertEqual(rows[0]["jsonl_path"], str(path))
                    manager.download_and_convert.assert_called_with("/source/demo@v1", task="ASR")
            args.max_samples = 32
            self.assertEqual(self._plan(args, manager, rows), (1, "auto"))
            self.assertEqual(rows[0]["selected_samples"], 32)
            args.num_workers = 8
            self.assertEqual(self._plan(args, manager, rows), (8, "explicit"))
            args.max_samples = -1
            with self.assertRaisesRegex(resolve_eval_input.EvalInputError, "non-negative"):
                self._plan(args, manager, rows)
            args.max_samples = 0
            for content in ("", "\n\n"):
                path.write_text(content)
                with self.assertRaisesRegex(resolve_eval_input.EvalInputError, "no samples"):
                    self._plan(args, manager, rows)
            path.unlink()
            with self.assertRaisesRegex(resolve_eval_input.EvalInputError, "no samples"):
                self._plan(args, manager, rows)

    def test_automatic_selection_never_exceeds_the_gpus_the_node_provides(self) -> None:
        """A large dataset on a small node plans what the node can run."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "task__asr.jsonl"
            path.write_text('{}\n' * 5000, encoding="utf-8")
            manager = Mock()
            manager.download_and_convert.return_value = path
            args = resolve_eval_input._build_parser().parse_args(["--model", "demo", "--datasets", "demo"])
            for available, expected in ((0, 1), (1, 1), (2, 2), (3, 3), (4, 4), (8, 8)):
                with self.subTest(available=available):
                    rows = [{"name": "demo", "requested_name": "/source/demo@v1", "task": "ASR"}]
                    self.assertEqual(self._plan(args, manager, rows, available=available), (expected, "auto"))
                    self.assertEqual(rows[0]["planned_workers"], expected)

    def test_an_explicit_count_above_the_node_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "task__asr.jsonl"
            path.write_text('{}\n' * 5000, encoding="utf-8")
            manager = Mock()
            manager.download_and_convert.return_value = path
            args = resolve_eval_input._build_parser().parse_args(
                ["--model", "demo", "--datasets", "demo", "--num-workers", "8"]
            )
            rows = [{"name": "demo", "requested_name": "/source/demo@v1", "task": "ASR"}]
            with self.assertRaisesRegex(resolve_eval_input.EvalInputError, "this node provides 2"):
                self._plan(args, manager, rows, available=2)
            self.assertEqual(self._plan(args, manager, rows, available=8), (8, "explicit"))

    def test_mixed_datasets_allocate_maximum_before_submission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {"X": root / "X.jsonl", "Y": root / "Y.jsonl"}
            paths["X"].write_text('{}\n' * 5)
            paths["Y"].write_text('{}\n' * 8000)
            manager = Mock()
            manager.download_and_convert.side_effect = lambda name, task: paths[name]
            args = resolve_eval_input._build_parser().parse_args(["--model", "demo", "--datasets", "X,Y"])
            for names in (("X", "Y"), ("Y", "X")):
                rows = [{"name": name, "task": "ASR"} for name in names]
                maximum, selection = self._plan(args, manager, rows)
                self.assertEqual((maximum, selection), (8, "auto"))
                self.assertEqual({row["name"]: row["planned_workers"] for row in rows}, {"X": 1, "Y": 8})
                self.assertEqual(resolve_eval_input._parallel_request(args, maximum), 8)
            args.device = "cpu"
            with self.assertRaisesRegex(resolve_eval_input.EvalInputError, "device"):
                resolve_eval_input._parallel_request(args, maximum)

    def test_new_and_cached_task_projections_use_the_same_plan(self) -> None:
        from test_source_conversion import make_manager, make_source_tree
        from sure_eval.datasets import source_resolver

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            dataset = make_source_tree(source, "demo", "v1", supported_tasks=["ASR", "TTS"])
            manager = make_manager(root)
            args = resolve_eval_input._build_parser().parse_args(["--model", "demo", "--datasets", str(dataset)])
            with patch.dict(os.environ, {source_resolver.SOURCE_ROOT_ENV: str(source)}), \
                    patch.object(source_resolver, "DEFAULT_SOURCE_ROOTS", {"default": str(source)}):
                for _ in range(2):
                    rows = resolve_eval_input._dataset_details(manager, [str(dataset)], [], None, model_task="ASR")
                    self.assertEqual(self._plan(args, manager, rows), (1, "auto"))
                    self.assertEqual(rows[0]["num_samples"], 1)
                    self.assertTrue(rows[0]["jsonl_path"].endswith("__asr.jsonl"))
                tts = manager.download_and_convert(str(dataset), task="TTS")
                self.assertNotEqual(str(tts), rows[0]["jsonl_path"])

    def test_input_bounds_and_default(self) -> None:
        parser = resolve_eval_input._build_parser()
        base = ["--model", "demo", "--datasets", "/data/demo"]
        self.assertEqual(resolve_eval_input._parallel_request(parser.parse_args(base)), 1)
        for workers in (1, 8):
            with self.subTest(workers=workers):
                args = parser.parse_args(base + ["--num-workers", str(workers)])
                self.assertEqual(resolve_eval_input._parallel_request(args), workers)
        for workers in (0, -1, 9, 100, True, 1.5, "8"):
            with self.subTest(workers=workers):
                args = parser.parse_args(base)
                args.num_workers = workers
                with self.assertRaisesRegex(resolve_eval_input.EvalInputError, "between 1 and 8"):
                    resolve_eval_input._parallel_request(args)
        for value in ("1.5", "abc", "true"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parser.parse_args(base + ["--num-workers", value])

    def test_local_gpu_count_reads_the_allocation_then_the_machine(self) -> None:
        with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "0,1,2"}, clear=False):
            self.assertEqual(resolve_eval_input._local_gpu_count(), 3)
        with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": ""}, clear=False):
            self.assertEqual(resolve_eval_input._local_gpu_count(), 0)
        with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "-1"}, clear=False):
            self.assertEqual(resolve_eval_input._local_gpu_count(), 0)
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(resolve_eval_input, "_nvidia_smi_available", return_value=True), \
                patch.object(resolve_eval_input.subprocess, "run") as probe:
            probe.return_value = subprocess.CompletedProcess([], 0, "0\n1\n", "")
            self.assertEqual(resolve_eval_input._local_gpu_count(), 2)
            probe.return_value = subprocess.CompletedProcess([], 1, "", "boom")
            self.assertEqual(resolve_eval_input._local_gpu_count(), 0)

    def test_execution_entrypoint_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            model = root / "model"
            model.mkdir()
            output = root / "output"
            env = {
                "REPO_ROOT": str(root), "MODEL_NAME": "demo",
                "SURE_EVAL_APPROVED_MODEL_DIR": str(model), "MODEL_DIR": str(model),
                "RUN_DIR": str(output), "SURE_EVAL_PUBLISHED_RUN_DIR": str(output),
                "MODEL_PYTHON": "/model/bin/python", "DATASETS": "/data/demo", "DEVICE": "cuda",
            }
            for value in ("0", "-1", "9", "100", "1.5", "abc"):
                with self.subTest(value=value), patch.dict(os.environ, {**env, "SURE_INFER_NUM_WORKERS": value}, clear=True):
                    with self.assertRaisesRegex(infer_entrypoint.StageError, "between 1 and 8"):
                        infer_entrypoint.stage_guards()
            for value in ("1", "8"):
                with self.subTest(value=value), patch.dict(os.environ, {**env, "SURE_INFER_NUM_WORKERS": value}, clear=True):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(infer_entrypoint.stage_guards().num_workers, int(value))

    def test_gpu_discovery_rejects_invalid_counts_before_probing(self) -> None:
        with patch.object(parallel_predictions.subprocess, "run") as probe:
            for workers in (0, -1, 1, 9, True, 2.5):
                with self.subTest(workers=workers), self.assertRaisesRegex(ValueError, "between 2 and 8"):
                    parallel_predictions.discover_gpus(workers, {})
            probe.assert_not_called()
        with patch.object(parallel_predictions.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, '{"count":8}\n', ""),
        ]):
            self.assertEqual(parallel_predictions.discover_gpus(8, {"MODEL_PYTHON": "/model/bin/python"}),
                             [str(index) for index in range(8)])


if __name__ == "__main__":
    unittest.main()
