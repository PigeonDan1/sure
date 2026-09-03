#!/usr/bin/env python3
"""Tests: /sure_eval input resolution policy.

Run directly:
    cd sure/skills/sure_eval/scripts && python test_eval_input_policy.py
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import resolve_eval_input  # noqa: E402
from sure_eval.datasets import source_resolver  # noqa: E402
from test_source_conversion import make_manager, make_source_tree  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
