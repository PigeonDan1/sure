#!/usr/bin/env python3
"""Regression tests: /sure_reval must accept the short dataset aliases that
/sure_eval already accepts (FIX-502 / Task 18).

/sure_eval expands a short alias such as "aishell1" to the fully qualified,
versioned dataset id it actually writes artifacts under (e.g.
"aishell1__v1.0.2__asr") via
DatasetManager._existing_jsonl_for_dataset -> normalize_dataset_name.
/sure_reval's resolve_prediction_source.py required the caller to already
know and pass that fully qualified id, so the two-step eval-then-reval
workflow documented by both skills broke at step two: `/sure_eval
datasets=aishell1` works, `/sure_reval datasets=aishell1` did not.

These tests exercise:
  - the shared resolve_dataset_alias() rule directly,
  - /sure_eval's own DatasetManager._existing_jsonl_for_dataset using that
    rule (proves the eval side keeps resolving exactly as before), and
  - /sure_reval's resolve_prediction_source.build_payload() using the same
    rule against its predictions directory (proves reval now accepts the
    short name, and resolves it to the identical qualified name eval would).

Run directly:
    cd sure/skills/sure_eval/scripts && python test_dataset_alias.py
"""
from __future__ import annotations

import argparse
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


class _FakeDatasetManager:
    """Minimal stand-in exposing only the attribute _existing_jsonl_for_dataset reads.

    Lets the test call the real, unmodified DatasetManager method without
    constructing a full DatasetManager (which needs Config.from_env() and a
    real harness environment).
    """

    def __init__(self, jsonl_dir: Path) -> None:
        self.jsonl_dir = jsonl_dir


class EvalResolutionUnchangedTests(unittest.TestCase):
    """/sure_eval's own resolver must keep resolving exactly as it did before this fix."""

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


class RevalAcceptsShortDatasetNameTests(unittest.TestCase):
    """/sure_reval must accept the same short name /sure_eval accepts, and
    resolve it to the identical qualified dataset id."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.predictions_dir = root / "predictions"
        self.predictions_dir.mkdir()
        (self.predictions_dir / "aishell1__v1.0.2__asr.txt").write_text("hello\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _args(self, datasets: list[str] | None) -> argparse.Namespace:
        return argparse.Namespace(
            source=str(self.predictions_dir),
            model=None,
            model_dir=None,
            datasets=datasets,
            protocol_id=None,
        )

    def test_short_dataset_name_now_resolves(self) -> None:
        payload = resolve_prediction_source.build_payload(self._args(["aishell1"]))
        self.assertEqual(payload["datasets"], ["aishell1__v1.0.2__asr"])
        self.assertEqual(payload["predictions"][0]["dataset"], "aishell1__v1.0.2__asr")

    def test_short_name_resolves_to_the_same_value_eval_gives_it(self) -> None:
        # A directory laid out the way /sure_eval's dataset jsonl root would
        # be for this dataset: eval writes predictions using exactly the
        # canonical name it resolves, so give the eval-side resolver the
        # same file stem (as a .jsonl) that reval's predictions dir has (as
        # a .txt), and confirm both land on the identical qualified name.
        eval_jsonl_dir = Path(self._tmp.name) / "eval_jsonl_dir"
        eval_jsonl_dir.mkdir()
        (eval_jsonl_dir / "aishell1__v1.0.2__asr.jsonl").write_text("{}\n", encoding="utf-8")
        eval_manager = _FakeDatasetManager(eval_jsonl_dir)
        eval_resolved = DatasetManager._existing_jsonl_for_dataset(eval_manager, "aishell1").stem

        payload = resolve_prediction_source.build_payload(self._args(["aishell1"]))

        self.assertEqual(payload["datasets"], [eval_resolved])

    def test_fully_qualified_dataset_name_is_unchanged(self) -> None:
        payload = resolve_prediction_source.build_payload(self._args(["aishell1__v1.0.2__asr"]))
        self.assertEqual(payload["datasets"], ["aishell1__v1.0.2__asr"])

    def test_ambiguous_short_name_still_fails_closed(self) -> None:
        (self.predictions_dir / "aishell1__v2.0.0__asr.txt").write_text("hello\n", encoding="utf-8")
        with self.assertRaises(FileNotFoundError):
            resolve_prediction_source.build_payload(self._args(["aishell1"]))

    def test_unresolvable_short_name_still_fails_closed(self) -> None:
        with self.assertRaises(FileNotFoundError):
            resolve_prediction_source.build_payload(self._args(["no_such_dataset"]))


if __name__ == "__main__":
    unittest.main()
