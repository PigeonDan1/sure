#!/usr/bin/env python3
"""Tests: /sure_infer input resolution policy.

Run directly:
    cd sure/skills/sure_infer/scripts && python test_eval_input_policy.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import resolve_eval_input  # noqa: E402
from sure_eval.datasets import source_resolver  # noqa: E402
from test_source_conversion import (  # noqa: E402
    make_flat_source_tree,
    make_lid_source_tree,
    make_manager,
    make_s2tt_source_tree,
    make_source_tree,
    make_vad_source_tree,
)


class MainFlowRemovalTests(unittest.TestCase):
    def test_the_resolver_no_longer_exposes_the_dataset_input_policy_check(self) -> None:
        self.assertFalse(hasattr(resolve_eval_input, "_check_dataset_input_policy"))
        self.assertFalse(hasattr(resolve_eval_input, "MAIN_FLOW_SCRIPTS"))

    def test_the_parser_no_longer_accepts_strict_main_flow(self) -> None:
        parser = resolve_eval_input._build_parser()
        for flag in ("--strict-main-flow", "--no-strict-main-flow"):
            with self.subTest(flag=flag):
                args, rest = parser.parse_known_args(["--model", "demo", "--datasets", "/x", flag])
                self.assertEqual(rest, [flag])
                self.assertFalse(hasattr(args, "strict_main_flow"))


class RunIdPolicyTests(unittest.TestCase):
    def test_safe_single_segment_passes(self) -> None:
        self.assertEqual(
            resolve_eval_input._validate_run_id("eval_Qwen3-v1.2=final"),
            "eval_Qwen3-v1.2=final",
        )

    def test_path_and_shell_like_run_ids_are_rejected(self) -> None:
        for value in ("../nfs/results", "/tmp/escape", "nested/run", "run id", "$(touch_x)"):
            with self.subTest(value=value):
                with self.assertRaises(resolve_eval_input.EvalInputError):
                    resolve_eval_input._validate_run_id(value)


class ExecutionSurfacePolicyTests(unittest.TestCase):
    def test_auto_lands_on_the_approved_local_container_runtime(self) -> None:
        execution = resolve_eval_input._normalize_execution("auto", "auto")
        self.assertEqual(execution["requested"], "auto")
        self.assertEqual(execution["planned"], "local")
        self.assertEqual(execution["path_planned"], "local_docker")
        self.assertEqual(execution["reason"], "auto_selected_local")
        self.assertNotIn("vc_available_at_resolve", execution)

    def test_python_runtime_lands_on_local_python(self) -> None:
        execution = resolve_eval_input._normalize_execution("auto", "auto", "python", ["container", "python"])
        self.assertEqual(execution["path_planned"], "local_python")
        self.assertEqual(execution["reason"], "auto_selected_local_runtime_only")

    def test_vc_is_no_longer_an_execution_surface(self) -> None:
        with self.assertRaisesRegex(ValueError, "execution=vc is no longer supported"):
            resolve_eval_input._normalize_execution("vc", "auto")
        with self.assertRaisesRegex(ValueError, "execution=vc is no longer supported"):
            resolve_eval_input._normalize_execution(None, "vc_submit")

    def test_runtime_not_enabled_by_site_policy_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "local_runtimes"):
            resolve_eval_input._normalize_execution("auto", "auto", "python", ["container"])

    def test_legacy_local_bash_maps_to_the_approved_local_path(self) -> None:
        execution = resolve_eval_input._normalize_execution(None, "local_bash")
        self.assertEqual(execution["requested"], "local")
        self.assertEqual(execution["path_planned"], "local_docker")
        self.assertEqual(execution["reason"], "user_requested_local")

    def test_device_resolution_no_longer_takes_an_execution_plan(self) -> None:
        with mock.patch.object(resolve_eval_input, "_nvidia_smi_available", return_value=False):
            device = resolve_eval_input._resolve_device("auto")
        self.assertEqual(device["resolved"], "cpu")
        self.assertEqual(device["execution_device_source"], "local_nvidia_smi")
        self.assertEqual(device["notes"], [])

    def test_unexecutable_nvidia_smi_counts_as_unavailable(self) -> None:
        with (
            mock.patch.object(resolve_eval_input.shutil, "which", return_value="/usr/bin/nvidia-smi"),
            mock.patch.object(resolve_eval_input.subprocess, "run", side_effect=OSError(8, "Exec format error")),
        ):
            self.assertFalse(resolve_eval_input._nvidia_smi_available())


class OutputDirPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.staged = self.tmp / "sure" / "results" / "demo" / "standard_system" / "main_agent_demo"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_without_an_override_the_staged_path_is_kept(self) -> None:
        self.assertEqual(resolve_eval_input._resolve_output_dir(None, self.staged), self.staged)

    def test_absolute_override_becomes_the_product_directory(self) -> None:
        target = self.tmp / "job-1234"
        resolved = resolve_eval_input._resolve_output_dir(str(target), self.staged)
        self.assertEqual(resolved, target.resolve())
        self.assertTrue(resolved.is_dir())

    def test_relative_override_is_rejected(self) -> None:
        with self.assertRaises(resolve_eval_input.EvalInputError) as ctx:
            resolve_eval_input._resolve_output_dir("jobs/job-1234", self.staged)
        self.assertIn("absolute", str(ctx.exception))

    def test_override_under_nfs_is_rejected(self) -> None:
        target = resolve_eval_input.NFS_ROOT.resolve() / "results" / "demo"
        with self.assertRaises(resolve_eval_input.EvalInputError) as ctx:
            resolve_eval_input._resolve_output_dir(str(target), self.staged)
        self.assertIn(str(resolve_eval_input.NFS_ROOT), str(ctx.exception))

    def test_uncreatable_override_is_rejected_before_the_run(self) -> None:
        blocker = self.tmp / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        with self.assertRaises(resolve_eval_input.EvalInputError) as ctx:
            resolve_eval_input._resolve_output_dir(str(blocker / "job-1234"), self.staged)
        self.assertIn("blocker", str(ctx.exception))

    def test_unwritable_override_is_rejected_before_the_run(self) -> None:
        target = self.tmp / "readonly"
        target.mkdir()
        with mock.patch.object(resolve_eval_input.os, "access", return_value=False):
            with self.assertRaises(resolve_eval_input.EvalInputError) as ctx:
                resolve_eval_input._resolve_output_dir(str(target), self.staged)
        self.assertIn(str(target), str(ctx.exception))


class OutputDirArgumentTests(unittest.TestCase):
    def test_output_dir_defaults_to_empty(self) -> None:
        args = resolve_eval_input._build_parser().parse_args(["--model", "demo", "--datasets", "/x"])
        self.assertEqual(args.output_dir, "")

    def test_output_dir_flag_is_captured(self) -> None:
        args = resolve_eval_input._build_parser().parse_args(
            ["--model", "demo", "--datasets", "/x", "--output-dir", "/srv/jobs/job-1234"]
        )
        self.assertEqual(args.output_dir, "/srv/jobs/job-1234")


class StagingPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_layout_is_model_protocol_run_id(self) -> None:
        staged = resolve_eval_input._stage_output_dir(
            self.root, "Qwen__Qwen3-ASR-1.7B", "standard_system", "main_agent_demo"
        )
        self.assertEqual(
            staged, self.root / "Qwen__Qwen3-ASR-1.7B" / "standard_system" / "main_agent_demo"
        )

    def test_model_escaping_the_root_is_rejected(self) -> None:
        with self.assertRaises(resolve_eval_input.EvalInputError) as ctx:
            resolve_eval_input._stage_output_dir(
                self.root, "../escape", "standard_system", "main_agent_demo"
            )
        self.assertIn("escapes", str(ctx.exception))


class DatasetDetailsSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source_root = self.tmp / "src"
        self._env = mock.patch.dict(
            os.environ, {source_resolver.SOURCE_ROOT_ENV: str(self.source_root)}
        )
        self._env.start()
        self.dataset_root = make_source_tree(self.source_root, "demo_ds", "v1.0.2")
        self.manager = make_manager(self.tmp)

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_unconverted_source_entry_yields_asr_detail_with_source_fields(self) -> None:
        details = resolve_eval_input._dataset_details(
            self.manager, [str(self.dataset_root)], [], None
        )
        self.assertEqual(len(details), 1)
        detail = details[0]
        self.assertEqual(detail["name"], "demo_ds__v1.0.2")
        self.assertEqual(detail["requested_name"], str(self.dataset_root))
        self.assertEqual(detail["task"], "ASR")
        self.assertEqual(detail["language"], "zh")
        self.assertEqual(detail["source_root"], str(self.dataset_root))
        self.assertEqual(detail["source_dataset_name"], "demo_ds")
        self.assertEqual(detail["version_id"], "v1.0.2")

    def test_converted_source_entry_reads_jsonl_metadata(self) -> None:
        self.manager.download_and_convert(str(self.dataset_root))
        details = resolve_eval_input._dataset_details(
            self.manager, [str(self.dataset_root)], [], None
        )
        detail = details[0]
        self.assertTrue(detail["jsonl_exists"])
        self.assertEqual(detail["source_root"], str(self.dataset_root))
        self.assertEqual(detail["version_id"], "v1.0.2")

    def test_flat_source_entry_yields_asr_detail_with_unversioned_id(self) -> None:
        flat_root = make_flat_source_tree(self.source_root, "flat_ds")
        details = resolve_eval_input._dataset_details(self.manager, [str(flat_root)], [], None)
        self.assertEqual(len(details), 1)
        detail = details[0]
        self.assertEqual(detail["name"], "flat_ds__unversioned")
        self.assertEqual(detail["requested_name"], str(flat_root))
        self.assertEqual(detail["task"], "ASR")
        self.assertEqual(detail["language"], "auto")
        self.assertEqual(detail["source_root"], str(flat_root))
        self.assertEqual(detail["source_dataset_name"], "flat_ds")
        self.assertEqual(detail["version_id"], "unversioned")

    def test_unconverted_vad_source_entry_yields_vad_detail(self) -> None:
        vad_root = make_vad_source_tree(self.source_root, "vad_ds", "v0.0.1")
        details = resolve_eval_input._dataset_details(self.manager, [str(vad_root)], [], None)
        self.assertEqual(len(details), 1)
        detail = details[0]
        self.assertEqual(detail["name"], "vad_ds__v0.0.1")
        self.assertEqual(detail["task"], "VAD")
        self.assertEqual(detail["language"], "zh")
        self.assertEqual(detail["default_metrics"], ["f1"])

    def test_unconverted_s2tt_source_entry_yields_s2tt_detail(self) -> None:
        """The ds.jsonl declaration wins: the sample's transcription must not report ASR."""
        for name, ds_jsonl in (
            ("implicit_s2tt", '{"audio": {"speech": {"language": "zh", "translation_language": "en"}}}'),
            ("explicit_s2tt", '{"task": "S2TT", "audio": {"speech": {"language": "zh"}}}'),
        ):
            with self.subTest(name=name):
                s2tt_root = make_s2tt_source_tree(self.source_root, name, ds_jsonl)
                details = resolve_eval_input._dataset_details(
                    self.manager, [str(s2tt_root)], [], None
                )
                self.assertEqual(len(details), 1)
                detail = details[0]
                self.assertEqual(detail["task"], "S2TT")
                self.assertEqual(detail["default_metrics"], ["bleu"])

    def test_unconverted_lid_source_entry_yields_accuracy_detail(self) -> None:
        lid_root = make_lid_source_tree(self.source_root, "lid_ds", "v1.0.0")
        details = resolve_eval_input._dataset_details(self.manager, [str(lid_root)], [], None)
        self.assertEqual(len(details), 1)
        detail = details[0]
        self.assertEqual(detail["name"], "lid_ds__v1.0.0")
        self.assertEqual(detail["task"], "LID")
        self.assertEqual(detail["default_metrics"], ["accuracy"])

    def test_multi_task_source_detail_follows_model_intent(self) -> None:
        multi_root = make_source_tree(
            self.source_root, "duo_ds", "v1.0.0", supported_tasks=["ASR", "TTS"]
        )
        details = resolve_eval_input._dataset_details(
            self.manager, [str(multi_root)], [], None, model_task="TTS"
        )
        detail = details[0]
        self.assertEqual(detail["name"], "duo_ds__v1.0.0")
        self.assertEqual(detail["task"], "TTS")
        self.assertEqual(detail["supported_tasks"], ["ASR", "TTS"])
        self.assertEqual(detail["language"], "zh")
        self.assertEqual(Path(detail["jsonl_path"]).name, "duo_ds__v1.0.0__tts.jsonl")

    def test_kws_model_selects_kws_projection_from_multi_task_source(self) -> None:
        multi_root = make_source_tree(
            self.source_root, "kws_ds", "v1.0.0", supported_tasks=["ASR", "LID", "KWS"]
        )
        stale_lid_projection = self.manager.jsonl_dir / "kws_ds__v1.0.0__lid.jsonl"
        stale_lid_projection.write_text(
            json.dumps({"task": "LID", "dataset": "kws_ds__v1.0.0__lid"}) + "\n",
            encoding="utf-8",
        )
        details = resolve_eval_input._dataset_details(
            self.manager, [str(multi_root)], ["accuracy"], None, model_task="KWS"
        )
        detail = details[0]
        self.assertEqual(detail["name"], "kws_ds__v1.0.0")
        self.assertEqual(detail["display_name"], "kws_ds__v1.0.0")
        self.assertIsNone(detail["num_samples"])
        self.assertEqual(detail["task"], "KWS")
        self.assertEqual(detail["supported_tasks"], ["ASR", "LID", "KWS"])
        self.assertEqual(Path(detail["jsonl_path"]).name, "kws_ds__v1.0.0__kws.jsonl")
        resolve_eval_input._check_task_compatibility(
            {"name": "wake-model", "declared_task": "KWS"}, details
        )

    def test_plain_asr_model_on_multi_task_source_stays_asr(self) -> None:
        multi_root = make_source_tree(
            self.source_root, "duo_ds", "v1.0.0", supported_tasks=["ASR", "TTS"]
        )
        details = resolve_eval_input._dataset_details(
            self.manager, [str(multi_root)], [], None, model_task="ASR"
        )
        self.assertEqual(details[0]["task"], "ASR")
        self.assertNotIn("task_source", details[0])

    def test_legacy_task_agnostic_cache_does_not_lock_source_to_asr(self) -> None:
        multi_root = make_source_tree(
            self.source_root, "duo_ds", "v1.0.0", supported_tasks=["ASR", "TTS"]
        )
        legacy = self.manager.jsonl_dir / "duo_ds__v1.0.0.jsonl"
        legacy.write_text(
            json.dumps(
                {
                    "task": "ASR",
                    "language": "zh",
                    "dataset": "duo_ds__v1.0.0",
                    "metadata": {
                        "source": "site_dataset_pool",
                        "source_dataset_root": str(multi_root),
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        details = resolve_eval_input._dataset_details(
            self.manager, [str(multi_root)], [], None, model_task="TTS"
        )
        detail = details[0]
        self.assertEqual(detail["task"], "TTS")
        self.assertEqual(Path(detail["jsonl_path"]).name, "duo_ds__v1.0.0__tts.jsonl")
        self.assertFalse(detail["jsonl_exists"])

    def test_multi_task_source_detail_points_at_the_per_task_projection(self) -> None:
        multi_root = make_source_tree(
            self.source_root, "duo_ds", "v1.0.0", supported_tasks=["ASR", "TTS"]
        )
        self.manager.download_and_convert(str(multi_root), task="TTS")
        details = resolve_eval_input._dataset_details(
            self.manager, [str(multi_root)], [], None, model_task="TTS"
        )
        detail = details[0]
        self.assertEqual(detail["task"], "TTS")
        self.assertEqual(Path(detail["jsonl_path"]).name, "duo_ds__v1.0.0__tts.jsonl")
        self.assertTrue(detail["jsonl_exists"])
        self.assertEqual(detail["num_samples"], 1)

    def test_tts_model_multi_task_source_passes_task_compatibility(self) -> None:
        multi_root = make_source_tree(
            self.source_root, "duo_ds", "v1.0.0", supported_tasks=["ASR", "TTS"]
        )
        details = resolve_eval_input._dataset_details(
            self.manager, [str(multi_root)], ["utmos"], None, model_task="TTS"
        )
        model = {"name": "xs__M40-IndexTTS", "declared_task": "TTS"}
        # Must not raise Task mismatch.
        resolve_eval_input._check_task_compatibility(model, details)
        self.assertEqual(details[0]["task"], "TTS")

    def test_tts_only_source_rejects_asr_model_in_guard(self) -> None:
        tts_root = make_source_tree(
            self.source_root, "tts_ds", "v1.0.0", supported_tasks=["TTS"]
        )
        details = resolve_eval_input._dataset_details(
            self.manager, [str(tts_root)], [], None, model_task="ASR"
        )
        model = {"name": "some_asr_model", "declared_task": "ASR"}
        with self.assertRaises(resolve_eval_input.EvalInputError) as ctx:
            resolve_eval_input._check_task_compatibility(model, details)
        self.assertIn("Task mismatch", str(ctx.exception))


class MainErrorHandlingTests(unittest.TestCase):
    def test_source_resolution_error_exits_2_with_clean_message(self) -> None:
        boom = resolve_eval_input.SourceResolutionError("multiple versions under sample_files")
        with mock.patch.object(resolve_eval_input, "build_payload", side_effect=boom):
            with mock.patch.object(
                sys, "argv",
                ["resolve_eval_input.py", "--model", "demo", "--datasets", "/srv/datasets/x"],
            ):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    rc = resolve_eval_input.main()
        self.assertEqual(rc, 2)
        self.assertIn("multiple versions", stderr.getvalue())

    def test_engine_probe_failure_exits_2_and_names_the_probe(self) -> None:
        """The engine probe failure joins the exit-2 shape, still distinguishable."""

        def broken(*args, **kwargs):
            raise RuntimeError("sure-evaluation engine at /e could not describe task 'asr'")

        with mock.patch.object(
            resolve_eval_input, "default_metrics_for_task_language", side_effect=broken
        ):
            with self.assertRaises(resolve_eval_input.EvalInputError) as ctx:
                resolve_eval_input._default_metrics("ASR", "zh", Path("/e"))

        with mock.patch.object(resolve_eval_input, "build_payload", side_effect=ctx.exception):
            with mock.patch.object(
                sys, "argv",
                ["resolve_eval_input.py", "--model", "demo", "--datasets", "/srv/datasets/x"],
            ):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    rc = resolve_eval_input.main()
        self.assertEqual(rc, 2)
        message = stderr.getvalue()
        self.assertIn("engine probe", message)
        self.assertIn("could not describe task", message)


class DatasetProjectionRootTests(unittest.TestCase):
    """An unusable projection root has to name its source and an override."""

    def _policy(self, projection_root: str) -> dict:
        return {
            "policy": {
                "storage": {"forbidden_output_roots": []},
                "datasets": {"allowed_source_roots": {}, "projection_root": projection_root},
            }
        }

    def test_uncreatable_policy_root_names_the_policy_and_the_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            blocker = Path(temporary) / "a-file"
            blocker.write_text("not a directory\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SURE_EVAL_DATASETS_ROOT", None)
                with self.assertRaises(resolve_eval_input.EvalInputError) as ctx:
                    resolve_eval_input._resolve_dataset_projection(
                        explicit_root=None,
                        configured_root=None,
                        harness_root=Path(temporary) / "harness",
                        site_policy=self._policy(str(blocker / "projections")),
                    )
        message = str(ctx.exception)
        self.assertIn("site_policy", message)
        self.assertIn("SURE_EVAL_DATASETS_ROOT", message)


class DefaultMetricsProbeTests(unittest.TestCase):
    """_default_metrics may guess only when there is no engine to ask."""

    def _probe(self, side_effect, task="ASR", language="zh", engine_root=Path("engine")):
        with mock.patch.object(
            resolve_eval_input, "default_metrics_for_task_language", side_effect=side_effect
        ), mock.patch.object(
            resolve_eval_input, "supported_metrics_for_task_language", side_effect=side_effect
        ):
            return resolve_eval_input._default_metrics(task, language, engine_root)

    def test_a_broken_engine_probe_does_not_become_a_guess(self) -> None:
        """A RuntimeError here means the engine could not answer at all.

        Falling back to the hardcoded table would publish a guessed metric as
        the dataset's default_metrics, indistinguishable from one the engine
        actually chose. It surfaces as EvalInputError so main() renders it in
        the same exit-2 shape as every other fatal error in this script.
        """

        def broken(*args, **kwargs):
            raise RuntimeError("sure-evaluation engine at engine could not describe task 'asr'")

        with self.assertRaises(resolve_eval_input.EvalInputError) as ctx:
            self._probe(broken)
        self.assertIn("engine probe", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)

    def test_a_task_the_engine_does_not_cover_still_falls_back(self) -> None:
        """ValueError is how the engine says 'not my task', e.g. UNKNOWN or a suite."""

        def unsupported(*args, **kwargs):
            raise ValueError("Unsupported evaluation task for sure-evaluation: 'UNKNOWN'")

        self.assertEqual(self._probe(unsupported, task="UNKNOWN"), ["accuracy"])

    def test_no_engine_root_uses_the_table_without_probing(self) -> None:
        def never(*args, **kwargs):
            raise AssertionError("must not probe when there is no engine root")

        with mock.patch.object(
            resolve_eval_input, "default_metrics_for_task_language", side_effect=never
        ):
            self.assertEqual(resolve_eval_input._default_metrics("ASR", "en", None), ["wer"])


if __name__ == "__main__":
    unittest.main()
