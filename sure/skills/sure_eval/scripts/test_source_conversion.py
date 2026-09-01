#!/usr/bin/env python3
"""Tests for source-root -> SURE JSONL conversion.

Run directly:
    cd sure/skills/sure_eval/scripts && python test_source_conversion.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sure_eval.datasets import source_resolver  # noqa: E402
from sure_eval.datasets.dataset_manager import DatasetManager  # noqa: E402


def make_manager(tmp: Path) -> DatasetManager:
    manager = object.__new__(DatasetManager)
    manager.config = SimpleNamespace(
        datasets=SimpleNamespace(definitions={}),
        get_dataset=lambda name: None,
    )
    manager.data_dir = tmp / "data"
    manager.sure_dir = manager.data_dir / "sure_benchmark"
    manager.jsonl_dir = manager.sure_dir / "jsonl"
    manager.jsonl_dir.mkdir(parents=True, exist_ok=True)
    manager._oref_config = {"datasets": {}, "fallbacks": {}}
    manager.oref_local_datasets = {}
    manager.dataset_fallbacks = {}
    manager.dataset_source_key = "default"  # __init__'s default; normalize/convert read it since 19b17fc
    return manager


def make_source_tree(root: Path, name: str, version: str) -> Path:
    dataset_root = root / "g001" / "store002" / "ds_pool" / name
    version_dir = dataset_root / "sample_files" / version
    version_dir.mkdir(parents=True)
    raw_dir = dataset_root / "raws" / "sample"
    raw_dir.mkdir(parents=True, exist_ok=True)
    audio = raw_dir / "utt1.wav"
    audio.write_bytes(b"RIFFxxxx")
    (version_dir / "sample.jsonl").write_text(
        json.dumps(
            {
                "sample_id": "utt1",
                "attribute": {
                    "path": "utt1.wav",
                    "size": audio.stat().st_size,
                    "sample_rate": 16000,
                    "duration": 1000,
                    "raw_data_format": "wav",
                    "channels": 1,
                },
                "annotation": [{"transcription": {"text": ["你好"]}}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (version_dir / "ds.jsonl").write_text(
        '{"audio": {"speech": {"language": "zh"}}}\n', encoding="utf-8"
    )
    return dataset_root


class SourceConversionTests(unittest.TestCase):
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

    def test_converts_to_two_segment_jsonl_with_source_metadata(self) -> None:
        ref = source_resolver.resolve_site_source_entry(str(self.dataset_root))
        jsonl_path = self.manager._convert_source_root_to_jsonl(ref)
        self.assertEqual(jsonl_path.name, "demo_ds__v1.0.2.jsonl")
        row = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["dataset"], "demo_ds__v1.0.2")
        self.assertEqual(row["task"], "ASR")
        self.assertEqual(row["language"], "zh")
        self.assertEqual(row["target"], "你好")
        expected_audio = self.dataset_root / "raws" / "sample" / "utt1.wav"
        self.assertEqual(row["path"], str(expected_audio))
        self.assertTrue(expected_audio.is_file())
        self.assertFalse((self.manager.sure_dir / "demo_ds" / "raws").exists())
        meta = row["metadata"]
        self.assertEqual(meta["source"], "site_dataset_pool")
        self.assertEqual(meta["source_dataset_root"], str(self.dataset_root))
        self.assertEqual(meta["source_dataset_name"], "demo_ds")
        self.assertEqual(meta["version_id"], "v1.0.2")
        self.assertEqual(meta["sample_id"], "utt1")

    def test_writes_package_side_artifacts(self) -> None:
        ref = source_resolver.resolve_site_source_entry(str(self.dataset_root))
        self.manager._convert_source_root_to_jsonl(ref)
        package_dir = self.manager.sure_dir / "demo_ds"
        source_payload = json.loads((package_dir / "oref" / "source.json").read_text(encoding="utf-8"))
        self.assertEqual(source_payload["source"], "site_dataset_pool")
        self.assertEqual(source_payload["source_dataset_name"], "demo_ds")
        self.assertEqual(source_payload["version_id"], "v1.0.2")
        report = json.loads(
            (package_dir / "projections" / "asr_transcription_v1" / "conversion_report.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(report["dataset"], "demo_ds__v1.0.2")
        manifest = json.loads((package_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["dataset"], "demo_ds")
        self.assertEqual(
            manifest["projections"]["asr_transcription_v1"]["dataset"], "demo_ds__v1.0.2"
        )
        self.assertEqual(
            manifest["projections"]["asr_transcription_v1"]["sure_jsonl"],
            "projections/asr_transcription_v1/sure.jsonl",
        )

    def test_conversion_is_idempotent(self) -> None:
        ref = source_resolver.resolve_site_source_entry(str(self.dataset_root))
        first = self.manager._convert_source_root_to_jsonl(ref)
        before = first.read_text(encoding="utf-8")
        second = self.manager._convert_source_root_to_jsonl(ref)
        self.assertEqual(first, second)
        self.assertEqual(before, second.read_text(encoding="utf-8"))

    def test_missing_source_after_resolve_raises_friendly_error(self) -> None:
        ref = source_resolver.resolve_site_source_entry(str(self.dataset_root))
        Path(ref.sample_jsonl).unlink()
        with self.assertRaises(FileNotFoundError) as ctx:
            self.manager._convert_source_root_to_jsonl(ref)
        self.assertIn("sample.jsonl", str(ctx.exception))

    def test_legacy_oref_platform_conversion_unchanged(self) -> None:
        # The extracted row-projection helper must keep the legacy path working.
        self.manager.oref_local_datasets = {
            "demo_legacy": {
                "source": "oref_platform",
                "config_name": "demo_legacy",
                "version_id": "v1.0.2",
                "task": "ASR",
                "dataset_root": str(self.dataset_root),
            }
        }
        jsonl_path = self.manager._convert_oref_platform_to_jsonl("demo_legacy")
        self.assertEqual(jsonl_path.name, "demo_legacy__v1.0.2__asr.jsonl")
        row = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["metadata"]["source"], "oref_platform")
        self.assertEqual(row["metadata"]["version_id"], "v1.0.2")


if __name__ == "__main__":
    unittest.main()
