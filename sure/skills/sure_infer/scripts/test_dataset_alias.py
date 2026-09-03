#!/usr/bin/env python3
"""Regression tests for the eval/reval dataset identity boundary.

/sure_infer expands a short alias such as "aishell1" to the fully qualified,
versioned dataset id it actually writes artifacts under (e.g.
"aishell1__v1.0.2__asr") via
DatasetManager._existing_jsonl_for_dataset -> normalize_dataset_name.
/sure_reval deliberately has a stricter public identity: callers must provide
the exact ``<dataset>__<version>`` value approved in NFS. It does not accept a
short alias or the historical ``__task`` report suffix.

These tests exercise:
  - the shared resolve_dataset_alias() rule directly,
  - /sure_infer's own DatasetManager._existing_jsonl_for_dataset using that
    rule (proves the eval side keeps resolving exactly as before), and
  - /sure_reval's canonical request validator (proves aliases and historical
    report suffixes cannot weaken exact dataset-set equality).

Run directly:
    cd sure/skills/sure_infer/scripts && python test_dataset_alias.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dataset_alias  # noqa: E402
import resolve_prediction_source  # noqa: E402
from sure_eval.datasets.dataset_manager import DatasetManager  # noqa: E402


class ResolveDatasetAliasTests(unittest.TestCase):
    """Unit tests for the shared resolve_dataset_alias() rule."""

    def test_short_name_resolves_to_unique_versioned_match(self) -> None:
        result = dataset_alias.resolve_dataset_alias(
            "aishell1", ["aishell1__v1.0.2__asr", "librispeech_test_clean"]
        )
        self.assertEqual(result, "aishell1__v1.0.2__asr")

    def test_fully_qualified_name_is_unchanged(self) -> None:
        result = dataset_alias.resolve_dataset_alias("aishell1__v1.0.2__asr", ["aishell1__v1.0.2__asr"])
        self.assertEqual(result, "aishell1__v1.0.2__asr")

    def test_ambiguous_short_name_is_not_resolved(self) -> None:
        result = dataset_alias.resolve_dataset_alias(
            "aishell1", ["aishell1__v1.0.2__asr", "aishell1__v2.0.0__asr"]
        )
        self.assertIsNone(result)

    def test_unknown_name_is_not_resolved(self) -> None:
        result = dataset_alias.resolve_dataset_alias("no_such_dataset", ["aishell1__v1.0.2__asr"])
        self.assertIsNone(result)

    def test_two_segment_source_id_resolves_from_short_name(self) -> None:
        result = dataset_alias.resolve_dataset_alias(
            "demo_speech_zh_test",
            ["demo_speech_zh_test__v1.0.2", "other_ds__v1.0.1"],
        )
        self.assertEqual(result, "demo_speech_zh_test__v1.0.2")

    def test_two_segment_id_ambiguous_versions_fail_closed(self) -> None:
        result = dataset_alias.resolve_dataset_alias(
            "demo_ds", ["demo_ds__v1.0.1", "demo_ds__v1.0.2"]
        )
        self.assertIsNone(result)


class _FakeDatasetManager:
    """Minimal stand-in exposing only the attribute _existing_jsonl_for_dataset reads.

    Lets the test call the real, unmodified DatasetManager method without
    constructing a full DatasetManager (which needs Config.from_env() and a
    real harness environment).
    """

    def __init__(self, jsonl_dir: Path) -> None:
        self.jsonl_dir = jsonl_dir


class EvalResolutionUnchangedTests(unittest.TestCase):
    """/sure_infer's own resolver must keep resolving exactly as it did before this fix."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.jsonl_dir = Path(self._tmp.name)
        (self.jsonl_dir / "aishell1__v1.0.2__asr.jsonl").write_text("{}\n", encoding="utf-8")
        self.manager = _FakeDatasetManager(self.jsonl_dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_short_name_expands_to_versioned_projection(self) -> None:
        result = DatasetManager._existing_jsonl_for_dataset(self.manager, "aishell1")
        self.assertEqual(result, self.jsonl_dir / "aishell1__v1.0.2__asr.jsonl")

    def test_fully_qualified_name_resolves_to_itself(self) -> None:
        result = DatasetManager._existing_jsonl_for_dataset(self.manager, "aishell1__v1.0.2__asr")
        self.assertEqual(result, self.jsonl_dir / "aishell1__v1.0.2__asr.jsonl")

    def test_ambiguous_short_name_is_left_unresolved(self) -> None:
        (self.jsonl_dir / "aishell1__v2.0.0__asr.jsonl").write_text("{}\n", encoding="utf-8")
        result = DatasetManager._existing_jsonl_for_dataset(self.manager, "aishell1")
        self.assertIsNone(result)


class RevalRequiresCanonicalDatasetIdTests(unittest.TestCase):
    def test_exact_dataset_and_version_is_unchanged(self) -> None:
        self.assertEqual(
            resolve_prediction_source._requested_dataset_id("aishell1__v1.0.2"),
            "aishell1__v1.0.2",
        )

    def test_short_alias_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not canonical"):
            resolve_prediction_source._requested_dataset_id("aishell1")

    def test_historical_task_suffix_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not canonical"):
            resolve_prediction_source._requested_dataset_id("aishell1__v1.0.2__asr")

    def test_legacy_single_underscore_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not canonical"):
            resolve_prediction_source._requested_dataset_id("aishell1_v1.0.2")


if __name__ == "__main__":
    unittest.main()
