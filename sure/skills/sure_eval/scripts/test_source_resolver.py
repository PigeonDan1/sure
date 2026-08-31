#!/usr/bin/env python3
"""Tests for the aispeech source-root resolver.

Run directly:
    cd sure/skills/sure_eval/scripts && python test_source_resolver.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sure_eval.datasets import source_resolver  # noqa: E402


def make_source_tree(root: Path, name: str, versions: list[str]) -> Path:
    dataset_root = root / "g001" / "store002" / "ds_pool" / name
    for version in versions:
        version_dir = dataset_root / "sample_files" / version
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / "sample.jsonl").write_text('{"sample_id": "s1"}\n', encoding="utf-8")
        (version_dir / "ds.jsonl").write_text(
            '{"audio": {"speech": {"language": "zh"}}}\n', encoding="utf-8"
        )
    (dataset_root / "raws" / "sample").mkdir(parents=True, exist_ok=True)
    return dataset_root


class SplitSourceEntryTests(unittest.TestCase):
    def test_plain_path_has_no_version(self) -> None:
        self.assertEqual(
            source_resolver.split_source_entry("/a/ds_pool/x"), ("/a/ds_pool/x", None)
        )

    def test_at_suffix_splits(self) -> None:
        self.assertEqual(
            source_resolver.split_source_entry("/a/ds_pool/x@v1.0.2"),
            ("/a/ds_pool/x", "v1.0.2"),
        )

    def test_last_at_wins(self) -> None:
        self.assertEqual(
            source_resolver.split_source_entry("/a/ds@pool/x@v1"), ("/a/ds@pool/x", "v1")
        )

    def test_invalid_suffixes_are_not_split(self) -> None:
        self.assertEqual(source_resolver.split_source_entry("/a/x@"), ("/a/x@", None))
        self.assertEqual(source_resolver.split_source_entry("@v1"), ("@v1", None))
        self.assertEqual(
            source_resolver.split_source_entry("/a/x@v1/extra"), ("/a/x@v1/extra", None)
        )
        self.assertEqual(source_resolver.split_source_entry(""), ("", None))


class SourceResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = mock.patch.dict(
            os.environ, {source_resolver.SOURCE_ROOT_ENV: str(self.root)}
        )
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_override_with_backslashes_is_a_path_not_a_key(self) -> None:
        # a Windows path carries no "/"; only a value in the key grammar is looked up as a key
        with mock.patch.dict(os.environ, {source_resolver.SOURCE_ROOT_ENV: r"C:\data\src"}):
            self.assertEqual(source_resolver.accepted_source_root(), r"C:\data\src")

    def test_single_version_resolves_to_two_segment_id(self) -> None:
        dataset_root = make_source_tree(self.root, "aispeech_phy_aishell-1-test", ["v1.0.2"])
        ref = source_resolver.resolve_aispeech_source_entry(str(dataset_root))
        self.assertEqual(ref.dataset_id, "aispeech_phy_aishell-1-test__v1.0.2")
        self.assertEqual(ref.source_dataset_name, "aispeech_phy_aishell-1-test")
        self.assertEqual(ref.version_id, "v1.0.2")
        self.assertTrue(ref.sample_jsonl.endswith("sample.jsonl"))
        self.assertTrue(ref.ds_jsonl.endswith("ds.jsonl"))
        self.assertTrue(Path(ref.raw_dir).is_dir())

    def test_multiple_versions_without_explicit_version_fails(self) -> None:
        dataset_root = make_source_tree(self.root, "demo_ds", ["v1.0.1", "v1.0.2"])
        with self.assertRaises(source_resolver.SourceResolutionError) as ctx:
            source_resolver.resolve_aispeech_source_entry(str(dataset_root))
        self.assertIn("v1.0.1", str(ctx.exception))
        self.assertIn("v1.0.2", str(ctx.exception))

    def test_explicit_version_selects_among_multiple(self) -> None:
        dataset_root = make_source_tree(self.root, "demo_ds", ["v1.0.1", "v1.0.2"])
        ref = source_resolver.resolve_aispeech_source_entry(str(dataset_root), "v1.0.1")
        self.assertEqual(ref.dataset_id, "demo_ds__v1.0.1")

    def test_zero_versions_fails(self) -> None:
        dataset_root = self.root / "g001" / "store002" / "ds_pool" / "empty_ds"
        (dataset_root / "raws" / "sample").mkdir(parents=True)
        with self.assertRaises(source_resolver.SourceResolutionError):
            source_resolver.resolve_aispeech_source_entry(str(dataset_root))

    def test_path_outside_accepted_root_fails(self) -> None:
        with self.assertRaises(source_resolver.SourceResolutionError) as ctx:
            source_resolver.resolve_aispeech_source_entry("/srv/outside/ds_pool/x")
        self.assertIn(str(self.root), str(ctx.exception))

    def test_path_not_at_ds_pool_level_fails(self) -> None:
        dataset_root = make_source_tree(self.root, "demo_ds", ["v1.0.1"])
        for bad in (dataset_root / "sample_files", dataset_root.parent):
            with self.assertRaises(source_resolver.SourceResolutionError):
                source_resolver.resolve_aispeech_source_entry(str(bad))

    def test_missing_sample_jsonl_fails(self) -> None:
        dataset_root = make_source_tree(self.root, "demo_ds", ["v1.0.1"])
        (dataset_root / "sample_files" / "v1.0.1" / "sample.jsonl").unlink()
        with self.assertRaises(source_resolver.SourceResolutionError) as ctx:
            source_resolver.resolve_aispeech_source_entry(str(dataset_root))
        self.assertIn("sample.jsonl", str(ctx.exception))

    def test_missing_raws_sample_fails(self) -> None:
        dataset_root = make_source_tree(self.root, "demo_ds", ["v1.0.1"])
        import shutil

        shutil.rmtree(dataset_root / "raws")
        with self.assertRaises(source_resolver.SourceResolutionError):
            source_resolver.resolve_aispeech_source_entry(str(dataset_root))

    def test_is_source_entry(self) -> None:
        self.assertTrue(source_resolver.is_source_entry("/srv/sure/datasets/a/ds_pool/x"))
        self.assertTrue(source_resolver.is_source_entry(str(self.root / "x")))
        self.assertFalse(source_resolver.is_source_entry("aishell1"))
        self.assertFalse(source_resolver.is_source_entry("aishell1__v1.0.2__asr"))
        self.assertFalse(source_resolver.is_source_entry(""))

    def test_read_source_language(self) -> None:
        dataset_root = make_source_tree(self.root, "demo_ds", ["v1.0.1"])
        ref = source_resolver.resolve_aispeech_source_entry(str(dataset_root))
        self.assertEqual(source_resolver.read_source_language(ref), "zh")

    def test_at_suffix_selects_among_multiple_versions(self) -> None:
        dataset_root = make_source_tree(self.root, "demo_ds", ["v1.0.1", "v1.0.2"])
        ref = source_resolver.resolve_aispeech_source_entry(f"{dataset_root}@v1.0.1")
        self.assertEqual(ref.dataset_id, "demo_ds__v1.0.1")
        self.assertEqual(ref.source_root, str(dataset_root))

    def test_at_suffix_unknown_version_lists_available(self) -> None:
        dataset_root = make_source_tree(self.root, "demo_ds", ["v1.0.1"])
        with self.assertRaises(source_resolver.SourceResolutionError) as ctx:
            source_resolver.resolve_aispeech_source_entry(f"{dataset_root}@v9.9.9")
        self.assertIn("v1.0.1", str(ctx.exception))

    def test_at_suffix_conflicting_with_param_fails(self) -> None:
        dataset_root = make_source_tree(self.root, "demo_ds", ["v1.0.1", "v1.0.2"])
        with self.assertRaises(source_resolver.SourceResolutionError) as ctx:
            source_resolver.resolve_aispeech_source_entry(f"{dataset_root}@v1.0.1", "v1.0.2")
        message = str(ctx.exception)
        self.assertIn("v1.0.1", message)
        self.assertIn("v1.0.2", message)

    def test_is_source_entry_accepts_at_suffix(self) -> None:
        self.assertTrue(source_resolver.is_source_entry("/a/ds_pool/x@v1.0.2"))


class RejectedRootMessageTests(unittest.TestCase):
    """A rejected path has to say which configured key would have taken it."""

    def test_path_under_another_configured_key_names_that_key(self) -> None:
        roots = {"default": "/stor/external_ds/aispeech", "aiplatform": "/stor/ds/aispeech"}
        with mock.patch.object(source_resolver, "DEFAULT_SOURCE_ROOTS", roots):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(source_resolver.SOURCE_ROOT_ENV, None)
                with self.assertRaises(source_resolver.SourceResolutionError) as ctx:
                    source_resolver.resolve_aispeech_source_entry(
                        "/stor/ds/aispeech/g001/store002/ds_pool/demo_ds"
                    )
        message = str(ctx.exception)
        self.assertIn("aiplatform", message)
        self.assertIn("dataset_source_key", message)

    def test_path_under_no_configured_key_lists_them(self) -> None:
        roots = {"default": "/stor/external_ds/aispeech", "aiplatform": "/stor/ds/aispeech"}
        with mock.patch.object(source_resolver, "DEFAULT_SOURCE_ROOTS", roots):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(source_resolver.SOURCE_ROOT_ENV, None)
                with self.assertRaises(source_resolver.SourceResolutionError) as ctx:
                    source_resolver.resolve_aispeech_source_entry("/elsewhere/ds_pool/demo_ds")
        message = str(ctx.exception)
        self.assertIn("default", message)
        self.assertIn("aiplatform", message)


if __name__ == "__main__":
    unittest.main()
