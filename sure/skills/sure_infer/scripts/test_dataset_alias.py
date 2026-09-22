#!/usr/bin/env python3
"""Regression tests for the infer/eval dataset identity boundary.

/sure_infer expands a short alias such as "aishell1" to the fully qualified
projection id it writes artifacts under (e.g. ``aishell1__v1.0.2__asr``) via
DatasetManager._existing_jsonl_for_dataset -> normalize_dataset_name.

/sure_eval local_infer_run accepts 2-seg or 3-seg ids. A 2-seg request expands
to the unique completed projection stem in the bundle (e.g. ``…__tts``); ambiguous
multi-task bundles still require the full 3-seg id. Approved NFS reval still
requests 2-seg ``source__version`` and peels optional task suffixes when reading
report rows (GitLab reval surface).

These tests exercise:
  - the shared resolve_dataset_alias() rule directly,
  - DatasetManager._existing_jsonl_for_dataset,
  - local vs approved request validators.

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

    def test_approved_nfs_rejects_task_suffix_on_request(self) -> None:
        # Approved reval set matching is still 2-seg source__version.
        with self.assertRaisesRegex(ValueError, "not canonical"):
            resolve_prediction_source._requested_dataset_id("aishell1__v1.0.2__asr")

    def test_legacy_single_underscore_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not canonical"):
            resolve_prediction_source._requested_dataset_id("aishell1_v1.0.2")


class LocalInferDatasetIdTests(unittest.TestCase):
    def test_two_seg_local_id(self) -> None:
        self.assertEqual(
            resolve_prediction_source._local_dataset_id("aishell1__v1.0.2"),
            "aishell1__v1.0.2",
        )

    def test_three_seg_projection_id(self) -> None:
        self.assertEqual(
            resolve_prediction_source._local_dataset_id("aishell1__v1.0.2__asr"),
            "aishell1__v1.0.2__asr",
        )
        self.assertEqual(
            resolve_prediction_source._local_dataset_id("duo_ds__v1.0.0__tts"),
            "duo_ds__v1.0.0__tts",
        )

    def test_unversioned_and_task(self) -> None:
        self.assertEqual(
            resolve_prediction_source._local_dataset_id("flat_ds__unversioned"),
            "flat_ds__unversioned",
        )
        self.assertEqual(
            resolve_prediction_source._local_dataset_id("flat_ds__unversioned__asr"),
            "flat_ds__unversioned__asr",
        )

    def test_short_alias_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not "):
            resolve_prediction_source._local_dataset_id("aishell1")


class ExpandLocalDatasetIdsTests(unittest.TestCase):
    def test_exact_three_seg_passthrough(self) -> None:
        self.assertEqual(
            resolve_prediction_source._expand_local_dataset_ids(
                ["duo_ds__v1.0.0__tts"],
                ["duo_ds__v1.0.0__tts"],
            ),
            ["duo_ds__v1.0.0__tts"],
        )

    def test_two_seg_expands_to_unique_projection(self) -> None:
        self.assertEqual(
            resolve_prediction_source._expand_local_dataset_ids(
                ["aispeech_phy_ar_common_fleurs_v251217__v1.0.2"],
                ["aispeech_phy_ar_common_fleurs_v251217__v1.0.2__tts"],
            ),
            ["aispeech_phy_ar_common_fleurs_v251217__v1.0.2__tts"],
        )

    def test_exact_two_seg_when_bundle_is_two_seg(self) -> None:
        self.assertEqual(
            resolve_prediction_source._expand_local_dataset_ids(
                ["legacy__v1.0.0"],
                ["legacy__v1.0.0"],
            ),
            ["legacy__v1.0.0"],
        )

    def test_ambiguous_multi_task_requires_full_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            resolve_prediction_source._expand_local_dataset_ids(
                ["duo_ds__v1.0.0"],
                ["duo_ds__v1.0.0__asr", "duo_ds__v1.0.0__tts"],
            )

    def test_missing_id_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "not among completed"):
            resolve_prediction_source._expand_local_dataset_ids(
                ["other__v1.0.0"],
                ["duo_ds__v1.0.0__tts"],
            )

    def test_subset_rejected_even_after_expand(self) -> None:
        with self.assertRaisesRegex(ValueError, "do not exactly match"):
            resolve_prediction_source._expand_local_dataset_ids(
                ["a__v1__tts"],
                ["a__v1__tts", "b__v1__tts"],
            )

    def test_duplicate_expand_targets_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            resolve_prediction_source._expand_local_dataset_ids(
                ["duo_ds__v1.0.0", "duo_ds__v1.0.0__tts"],
                ["duo_ds__v1.0.0__tts"],
            )


if __name__ == "__main__":
    unittest.main()