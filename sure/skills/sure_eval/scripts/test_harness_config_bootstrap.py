#!/usr/bin/env python3
"""Tests for the writable dataset projection root contract.

Run directly:
    cd sure/skills/sure_eval/scripts && python test_harness_config_bootstrap.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

import resolve_eval_input  # noqa: E402


class WriteHarnessConfigBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        base_config = self.root / "sure" / "external" / "sure-evaluation" / "config" / "default.yaml"
        base_config.parent.mkdir(parents=True)
        base_config.write_text("data: {}\n", encoding="utf-8")
        self.run_dir = self.root / "run"
        self.site_policy = {
            "policy": {
                "storage": {
                    "forbidden_output_roots": [str(self.root / "forbidden")],
                },
                "datasets": {
                    # key -> path, the shape sure.site.loader emits (a legacy one-item list is
                    # normalised to {"default": path} before any caller sees it)
                    "allowed_source_roots": {"default": str(self.root / "sources")},
                },
            }
        }
        self._repo_root = mock.patch.object(
            resolve_eval_input, "_repo_root_from_script", return_value=self.root
        )
        self._repo_root.start()
        self._env = mock.patch.dict(os.environ)
        self._env.start()
        os.environ.pop("SURE_EVAL_DATASETS_ROOT", None)
        os.environ.pop("SURE_EVAL_CONFIG", None)

    def tearDown(self) -> None:
        self._env.stop()
        self._repo_root.stop()
        self._tmp.cleanup()

    def test_default_root_is_created_on_fresh_checkout(self) -> None:
        config_path, projection = resolve_eval_input._materialize_harness_config(
            run_dir=self.run_dir,
            config_path=None,
            site_policy=self.site_policy,
        )
        jsonl_dir = self.root / "data" / "datasets" / "sure_benchmark" / "jsonl"
        self.assertTrue(jsonl_dir.is_dir())
        config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        self.assertEqual(config["data"]["datasets"], str(self.root / "data" / "datasets"))
        self.assertEqual(projection["source"], "development_default")
        self.assertEqual(projection["raw_data_policy"], "reference_only_no_copy_or_move")

    def test_explicit_env_root_is_created_on_first_use(self) -> None:
        missing = self.root / "elsewhere"
        os.environ["SURE_EVAL_DATASETS_ROOT"] = str(missing)
        _, projection = resolve_eval_input._materialize_harness_config(
            run_dir=self.run_dir,
            config_path=None,
            site_policy=self.site_policy,
        )
        self.assertTrue((missing / "sure_benchmark" / "jsonl").is_dir())
        self.assertEqual(projection["source"], "environment")

    def test_command_root_wins_over_environment_and_site_policy(self) -> None:
        command_root = self.root / "command-projection"
        os.environ["SURE_EVAL_DATASETS_ROOT"] = str(self.root / "environment-projection")
        self.site_policy["policy"]["datasets"]["projection_root"] = str(
            self.root / "policy-projection"
        )
        _, projection = resolve_eval_input._materialize_harness_config(
            run_dir=self.run_dir,
            config_path=None,
            datasets_root=str(command_root),
            site_policy=self.site_policy,
        )
        self.assertEqual(projection["host_root"], str(command_root))
        self.assertEqual(projection["source"], "command")

    def test_site_policy_root_is_used_without_an_override(self) -> None:
        policy_root = self.root / "policy-projection"
        self.site_policy["policy"]["datasets"]["projection_root"] = str(policy_root)
        _, projection = resolve_eval_input._materialize_harness_config(
            run_dir=self.run_dir,
            config_path=None,
            site_policy=self.site_policy,
        )
        self.assertEqual(projection["host_root"], str(policy_root))
        self.assertEqual(projection["source"], "site_policy")

    def test_custom_config_root_is_the_compatibility_fallback(self) -> None:
        configured_root = self.root / "configured-projection"
        custom = self.root / "configured.yaml"
        custom.write_text(
            f"data:\n  datasets: {configured_root}\n",
            encoding="utf-8",
        )
        _, projection = resolve_eval_input._materialize_harness_config(
            run_dir=self.run_dir,
            config_path=str(custom),
            site_policy=self.site_policy,
        )
        self.assertEqual(projection["host_root"], str(configured_root))
        self.assertEqual(projection["source"], "config")

    def test_rejects_projection_under_forbidden_output_root(self) -> None:
        forbidden_projection = self.root / "forbidden" / "projection"
        with self.assertRaisesRegex(resolve_eval_input.EvalInputError, "forbidden output root"):
            resolve_eval_input._materialize_harness_config(
                run_dir=self.run_dir,
                config_path=None,
                datasets_root=str(forbidden_projection),
                site_policy=self.site_policy,
            )
        self.assertFalse(forbidden_projection.exists())

    def test_rejects_projection_overlapping_source_root(self) -> None:
        source_projection = self.root / "sources" / "projection"
        with self.assertRaisesRegex(resolve_eval_input.EvalInputError, "must not overlap"):
            resolve_eval_input._materialize_harness_config(
                run_dir=self.run_dir,
                config_path=None,
                datasets_root=str(source_projection),
                site_policy=self.site_policy,
            )
        self.assertFalse(source_projection.exists())

    def test_rejects_projection_overlapping_run_output(self) -> None:
        with self.assertRaisesRegex(resolve_eval_input.EvalInputError, "evaluation output"):
            resolve_eval_input._materialize_harness_config(
                run_dir=self.run_dir,
                config_path=None,
                datasets_root=str(self.run_dir / "datasets"),
                site_policy=self.site_policy,
            )
        self.assertFalse((self.run_dir / "datasets").exists())

    def test_custom_config_is_copied_and_only_dataset_root_is_overridden(self) -> None:
        custom = self.root / "custom.yaml"
        custom.write_text(
            "data:\n  cache: /custom/cache\n  datasets: /custom/projection\n",
            encoding="utf-8",
        )
        projection_root = self.root / "explicit-projection"
        config_path, _ = resolve_eval_input._materialize_harness_config(
            run_dir=self.run_dir,
            config_path=str(custom),
            datasets_root=str(projection_root),
            site_policy=self.site_policy,
        )
        materialized = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.assertEqual(materialized["data"]["cache"], "/custom/cache")
        self.assertEqual(materialized["data"]["datasets"], str(projection_root))
        self.assertEqual(
            yaml.safe_load(custom.read_text(encoding="utf-8"))["data"]["datasets"],
            "/custom/projection",
        )


if __name__ == "__main__":
    unittest.main()
