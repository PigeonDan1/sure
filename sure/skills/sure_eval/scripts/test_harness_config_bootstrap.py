#!/usr/bin/env python3
"""Tests: default datasets root bootstraps itself; explicit env root still fails fast.

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
        config_path = resolve_eval_input._write_harness_config(run_dir=self.run_dir, config_path=None)
        jsonl_dir = self.root / "data" / "datasets" / "sure_benchmark" / "jsonl"
        self.assertTrue(jsonl_dir.is_dir())
        config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        self.assertEqual(config["data"]["datasets"], str(self.root / "data" / "datasets"))

    def test_explicit_env_root_must_exist(self) -> None:
        missing = self.root / "elsewhere"
        os.environ["SURE_EVAL_DATASETS_ROOT"] = str(missing)
        with self.assertRaises(FileNotFoundError):
            resolve_eval_input._write_harness_config(run_dir=self.run_dir, config_path=None)
        self.assertFalse((missing / "sure_benchmark" / "jsonl").exists())


if __name__ == "__main__":
    unittest.main()
