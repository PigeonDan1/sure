#!/usr/bin/env python3
"""Tests for source-root -> SURE JSONL conversion.

Run directly:
    cd sure/skills/sure_infer/scripts && python test_source_conversion.py
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
    manager.dataset_source_key = "default"  # __init__'s default; normalize/convert read it since 19b17fc
    return manager


def make_source_tree(
    root: Path, name: str, version: str, supported_tasks: list[str] | None = None
) -> Path:
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
    ds_meta: dict = {"audio": {"speech": {"language": "zh"}}}
    if supported_tasks is not None:
        ds_meta["supported_tasks"] = list(supported_tasks)
    (version_dir / "ds.jsonl").write_text(
        json.dumps(ds_meta, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return dataset_root


def make_flat_source_tree(root: Path, name: str) -> Path:
    """A source directory with sample.jsonl and the audio side by side: no ds.jsonl, no raws/."""
    dataset_root = root / name
    dataset_root.mkdir(parents=True)
    audio = dataset_root / "utt1.wav"
    audio.write_bytes(b"RIFFxxxx")
    (dataset_root / "sample.jsonl").write_text(
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
    return dataset_root


def make_vad_source_tree(root: Path, name: str, version: str) -> Path:
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
                    "path": str(audio),
                    "size": audio.stat().st_size,
                    "sample_rate": 16000,
                    "duration": 1500,
                    "raw_data_format": "wav",
                    "channels": 1,
                },
                "annotation": [
                    {"seg_id": "0", "timestamp": {"begin_time": 0.1, "end_time": 0.4}},
                    {"seg_id": "1", "timestamp": {"begin_time": 0.8, "end_time": 1.2}},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (version_dir / "ds.jsonl").write_text(
        json.dumps(
            {
                "supported_tasks": ["other"],
                "audio": {"speech": {"language": "zh"}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return dataset_root


def make_lid_source_tree(root: Path, name: str, version: str) -> Path:
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
                "task": "LID",
                "language": "en",
                "attribute": {
                    "path": "utt1.wav",
                    "size": audio.stat().st_size,
                    "sample_rate": 16000,
                    "duration": 1000,
                },
            }
        )
        + "\n",
        encoding="utf-8",
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
        self.assertEqual(jsonl_path.name, "demo_ds__v1.0.2__asr.jsonl")
        row = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["dataset"], "demo_ds__v1.0.2__asr")
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
        source_payload = json.loads((package_dir / "source" / "source.json").read_text(encoding="utf-8"))
        self.assertEqual(source_payload["source"], "site_dataset_pool")
        self.assertEqual(source_payload["source_dataset_name"], "demo_ds")
        self.assertEqual(source_payload["version_id"], "v1.0.2")
        report = json.loads(
            (package_dir / "projections" / "asr_transcription_v1" / "conversion_report.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(report["dataset"], "demo_ds__v1.0.2__asr")
        manifest = json.loads((package_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["dataset"], "demo_ds")
        self.assertEqual(
            manifest["projections"]["asr_transcription_v1"]["dataset"], "demo_ds__v1.0.2__asr"
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

    def test_flat_source_converts_with_unversioned_id_and_absolute_audio(self) -> None:
        flat_root = make_flat_source_tree(self.source_root, "flat_ds")
        ref = source_resolver.resolve_site_source_entry(str(flat_root))
        jsonl_path = self.manager._convert_source_root_to_jsonl(ref)
        self.assertEqual(jsonl_path.name, "flat_ds__unversioned__asr.jsonl")
        row = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["dataset"], "flat_ds__unversioned__asr")
        self.assertEqual(row["task"], "ASR")
        self.assertEqual(row["target"], "你好")
        self.assertEqual(row["language"], "auto")
        self.assertEqual(row["path"], str(flat_root / "utt1.wav"))
        self.assertTrue(Path(row["path"]).is_absolute())
        self.assertEqual(row["metadata"]["version_id"], "unversioned")
        self.assertEqual(row["metadata"]["source_dataset_root"], str(flat_root))

    def test_converts_timestamp_annotations_to_vad_projection(self) -> None:
        vad_root = make_vad_source_tree(self.source_root, "vad_ds", "v0.0.1")
        ref = source_resolver.resolve_site_source_entry(str(vad_root))
        self.assertEqual(source_resolver.read_source_task(ref), "VAD")

        jsonl_path = self.manager.download_and_convert(str(vad_root))
        row = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(jsonl_path.name, "vad_ds__v0.0.1__vad.jsonl")
        self.assertEqual(row["task"], "VAD")
        self.assertEqual(row["duration"], 1.5)
        self.assertEqual(
            row["speech_segments"],
            [{"start": 0.1, "end": 0.4}, {"start": 0.8, "end": 1.2}],
        )
        self.assertNotIn("target", row)

        projection_dir = self.manager.sure_dir / "vad_ds" / "projections" / "vad_segments_v1"
        contract = json.loads((projection_dir / "io_contract.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["task"], "VAD")
        self.assertEqual(contract["reference"]["primary_field"], "speech_segments")
        manifest = json.loads(
            (self.manager.sure_dir / "vad_ds" / "dataset_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["default_projection"], "vad_segments_v1")

    def test_converts_lid_language_labels_to_label_projection(self) -> None:
        lid_root = make_lid_source_tree(self.source_root, "lid_ds", "v1.0.0")
        ref = source_resolver.resolve_site_source_entry(str(lid_root))
        self.assertEqual(source_resolver.read_source_task(ref), "LID")
        jsonl_path = self.manager.download_and_convert(str(lid_root))
        row = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["task"], "LID")
        self.assertEqual(row["label"], "en")
        self.assertEqual(row["target"], "en")
        contract = json.loads(
            (self.manager.sure_dir / "lid_ds" / "projections" / "lid_labels_v1" / "io_contract.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(contract["reference"]["primary_field"], "label")

    def test_rebuilds_stale_asr_projection_for_vad_source(self) -> None:
        # Bare legacy cache must not block the per-task VAD projection.
        vad_root = make_vad_source_tree(self.source_root, "vad_ds", "v0.0.1")
        stale_path = self.manager.jsonl_dir / "vad_ds__v0.0.1.jsonl"
        stale_path.write_text(
            json.dumps({"key": "utt1", "task": "ASR", "target": "stale"}) + "\n",
            encoding="utf-8",
        )

        jsonl_path = self.manager.download_and_convert(str(vad_root))
        row = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(jsonl_path.name, "vad_ds__v0.0.1__vad.jsonl")
        self.assertEqual(row["task"], "VAD")
        self.assertIn("speech_segments", row)
        self.assertNotIn("target", row)

    # ---- supported_tasks-driven multi-task sources ----

    def test_source_resolver_reads_supported_tasks(self) -> None:
        multi_root = make_source_tree(self.source_root, "multi_ds", "v1.0.0", supported_tasks=["ASR", "TTS"])
        ref = source_resolver.resolve_site_source_entry(str(multi_root))
        self.assertEqual(ref.supported_tasks, ("ASR", "TTS"))
        self.assertEqual(ref.dataset_id, "multi_ds__v1.0.0")

    def test_source_resolver_supported_tasks_tolerant(self) -> None:
        nested_root = make_source_tree(self.source_root, "nested_ds", "v1.0.0", supported_tasks=None)
        ds_jsonl = nested_root / "sample_files" / "v1.0.0" / "ds.jsonl"
        ds_jsonl.write_text(
            json.dumps({"audio": {"speech": {"supported_tasks": ["tts"]}}}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        ref = source_resolver.resolve_site_source_entry(str(nested_root))
        self.assertEqual(ref.supported_tasks, ("TTS",))
        ds_jsonl.write_text("{not json", encoding="utf-8")
        ref = source_resolver.resolve_site_source_entry(str(nested_root))
        self.assertEqual(ref.supported_tasks, ())

    def test_source_default_task_branches(self) -> None:
        self.assertEqual(
            source_resolver.source_default_task(
                source_resolver.resolve_site_source_entry(str(self.dataset_root))
            ),
            "ASR",
        )
        with self.assertRaises(ValueError):
            source_resolver.source_default_task(
                source_resolver.resolve_site_source_entry(str(self.dataset_root)), "TTS"
            )
        multi_root = make_source_tree(self.source_root, "intent_ds", "v1.0.0", supported_tasks=["ASR", "TTS"])
        multi_ref = source_resolver.resolve_site_source_entry(str(multi_root))
        self.assertEqual(source_resolver.source_default_task(multi_ref, "tts"), "TTS")
        self.assertEqual(source_resolver.source_default_task(multi_ref), "ASR")
        tts_root = make_source_tree(self.source_root, "tts_ds", "v1.0.0", supported_tasks=["TTS"])
        self.assertEqual(
            source_resolver.source_default_task(source_resolver.resolve_site_source_entry(str(tts_root))),
            "TTS",
        )
        synth_root = make_source_tree(self.source_root, "synth_ds", "v1.0.0", supported_tasks=["TTS", "VC"])
        with self.assertRaises(ValueError):
            source_resolver.source_default_task(source_resolver.resolve_site_source_entry(str(synth_root)))

    def test_multi_task_source_projects_each_task_independently(self) -> None:
        multi_root = make_source_tree(self.source_root, "duo_ds", "v1.0.0", supported_tasks=["ASR", "TTS"])
        tts_path = self.manager.download_and_convert(str(multi_root), task="TTS")
        self.assertEqual(tts_path.name, "duo_ds__v1.0.0__tts.jsonl")
        tts_row = json.loads(tts_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(tts_row["task"], "TTS")
        self.assertEqual(tts_row["dataset"], "duo_ds__v1.0.0__tts")
        self.assertEqual(tts_row["target"], "你好")
        self.assertTrue(Path(tts_row["path"]).is_file())
        self.assertEqual(tts_row["metadata"]["source"], "site_dataset_pool")

        asr_path = self.manager.download_and_convert(str(multi_root))
        self.assertEqual(asr_path.name, "duo_ds__v1.0.0__asr.jsonl")
        self.assertNotEqual(asr_path, tts_path)
        self.assertEqual(
            json.loads(asr_path.read_text(encoding="utf-8").splitlines()[0])["task"], "ASR"
        )
        self.assertEqual(self.manager.download_and_convert(str(multi_root), task="TTS"), tts_path)
        self.assertEqual(self.manager.download_and_convert(str(multi_root)), asr_path)

        package_dir = self.manager.sure_dir / "duo_ds"
        manifest = json.loads((package_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["projections"]), {"asr_transcription_v1", "tts_readback_v1"})
        self.assertEqual(
            manifest["projections"]["tts_readback_v1"]["dataset"], "duo_ds__v1.0.0__tts"
        )
        # ASR wins once present, regardless of which task was prepared first.
        # Covers both orderings:
        #   TTS first → default sticks at TTS until ASR lands, then flips to ASR.
        #   ASR first → default is ASR from the start and stays.
        self.assertEqual(manifest["default_projection"], "asr_transcription_v1")

    def test_default_projection_prefers_asr_in_either_order(self) -> None:
        """Default flips to ASR whichever task lands first.

        This is the regression guard for ``dataset_manager._convert_source_root_to_jsonl``:
        a TTS-first prepare must not leave the package's default at TTS once ASR
        arrives, and an ASR-first prepare must keep ASR even after TTS arrives.
        """
        # ASR-first ordering: ASR sets the default, then TTS must not bump it off.
        asr_first_root = make_source_tree(self.source_root, "asr_first", "v1.0.0", supported_tasks=["ASR", "TTS"])
        self.manager.download_and_convert(str(asr_first_root))
        self.manager.download_and_convert(str(asr_first_root), task="TTS")
        manifest = json.loads(
            (self.manager.sure_dir / "asr_first" / "dataset_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(manifest["projections"]), {"asr_transcription_v1", "tts_readback_v1"})
        self.assertEqual(manifest["default_projection"], "asr_transcription_v1")

    def test_legacy_source_cannot_be_readback_projected(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.download_and_convert(str(self.dataset_root), task="TTS")

    def test_per_task_projection_wins_over_legacy_bare_jsonl(self) -> None:
        multi_root = make_source_tree(self.source_root, "duo_ds", "v1.0.0", supported_tasks=["ASR", "TTS"])
        tts_path = self.manager.download_and_convert(str(multi_root), task="TTS")
        legacy = self.manager.jsonl_dir / "duo_ds__v1.0.0.jsonl"
        legacy.write_text(
            json.dumps({"task": "ASR", "dataset": "duo_ds__v1.0.0", "key": "old"}) + "\n",
            encoding="utf-8",
        )
        resolved = self.manager.get_jsonl_path("duo_ds__v1.0.0")
        self.assertEqual(resolved, tts_path)
        self.assertEqual(resolved.name, "duo_ds__v1.0.0__tts.jsonl")

    def test_ambiguous_multi_task_projections_do_not_guess(self) -> None:
        multi_root = make_source_tree(self.source_root, "duo_ds", "v1.0.0", supported_tasks=["ASR", "TTS"])
        self.manager.download_and_convert(str(multi_root), task="TTS")
        self.manager.download_and_convert(str(multi_root), task="ASR")
        self.assertIsNone(self.manager._existing_jsonl_for_dataset("duo_ds__v1.0.0"))
        self.assertEqual(
            self.manager.get_jsonl_path("duo_ds__v1.0.0__tts").name,
            "duo_ds__v1.0.0__tts.jsonl",
        )

def make_s2tt_source_tree(root: Path, name: str, ds_jsonl_text: str) -> Path:
    """A flat speech-translation source: one utterance, transcription + translation."""
    dataset_root = root / name
    dataset_root.mkdir(parents=True)
    audio = dataset_root / "utt1.wav"
    audio.write_bytes(b"RIFFxxxx")
    (dataset_root / "sample.jsonl").write_text(
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
                "annotation": [
                    {"transcription": {"text": ["在此次申办冬奥会的过程中"]}},
                    {"translation": {"text": ["In the process of bidding for the Winter Olympics"]}},
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (dataset_root / "ds.jsonl").write_text(ds_jsonl_text + "\n", encoding="utf-8")
    return dataset_root


class SourceConversionS2TTTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source_root = self.tmp / "src"
        self._env = mock.patch.dict(
            os.environ, {source_resolver.SOURCE_ROOT_ENV: str(self.source_root)}
        )
        self._env.start()
        self.manager = make_manager(self.tmp)

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_translation_language_declares_s2tt(self) -> None:
        dataset_root = make_s2tt_source_tree(
            self.source_root,
            "mini_s2tt",
            '{"audio": {"speech": {"language": "zh", "translation_language": "en"}}}',
        )
        ref = source_resolver.resolve_site_source_entry(str(dataset_root))
        jsonl_path = self.manager._convert_source_root_to_jsonl(ref)
        row = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["task"], "S2TT")
        self.assertEqual(row["language"], "zh")
        self.assertEqual(row["target"], "In the process of bidding for the Winter Olympics")
        self.assertEqual(row["source"], "在此次申办冬奥会的过程中")
        manifest = json.loads(
            (self.manager.sure_dir / "mini_s2tt" / "dataset_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["default_projection"], "s2tt_translation_v1")
        self.assertIn("s2tt_translation_v1", manifest["projections"])
        report = json.loads(
            (self.manager.sure_dir / "mini_s2tt" / "projections" / "s2tt_translation_v1" / "conversion_report.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(report["task"], "S2TT")
        self.assertEqual(report["translation_language"], "en")
        self.assertEqual(report["field_mapping"]["target"], "annotation[0].translation.text[0]")
        self.assertEqual(report["field_mapping"]["source"], "annotation[0].transcription.text[0]")

    def test_explicit_task_declares_s2tt_without_translation_language(self) -> None:
        dataset_root = make_s2tt_source_tree(
            self.source_root,
            "explicit_s2tt",
            '{"task": "S2TT", "audio": {"speech": {"language": "zh"}}}',
        )
        ref = source_resolver.resolve_site_source_entry(str(dataset_root))
        jsonl_path = self.manager._convert_source_root_to_jsonl(ref)
        row = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["task"], "S2TT")
        self.assertEqual(row["target"], "In the process of bidding for the Winter Olympics")

    def test_missing_translation_text_skips_the_record(self) -> None:
        dataset_root = make_s2tt_source_tree(
            self.source_root,
            "no_translation",
            '{"audio": {"speech": {"language": "zh", "translation_language": "en"}}}',
        )
        (dataset_root / "sample.jsonl").write_text(
            json.dumps(
                {
                    "sample_id": "utt1",
                    "attribute": {"path": "utt1.wav"},
                    "annotation": [{"transcription": {"text": ["你好"]}}],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        ref = source_resolver.resolve_site_source_entry(str(dataset_root))
        with self.assertRaises(ValueError) as ctx:
            self.manager._convert_source_root_to_jsonl(ref)
        self.assertIn("missing translation text", str(ctx.exception))

    def test_missing_transcription_text_skips_the_record(self) -> None:
        """An empty source line would reach triangle metrics as a valid source."""
        dataset_root = make_s2tt_source_tree(
            self.source_root,
            "no_transcription",
            '{"audio": {"speech": {"language": "zh", "translation_language": "en"}}}',
        )
        (dataset_root / "sample.jsonl").write_text(
            json.dumps(
                {
                    "sample_id": "utt1",
                    "attribute": {"path": "utt1.wav"},
                    "annotation": [{"translation": {"text": ["hello"]}}],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        ref = source_resolver.resolve_site_source_entry(str(dataset_root))
        with self.assertRaises(ValueError) as ctx:
            self.manager._convert_source_root_to_jsonl(ref)
        self.assertIn("missing transcription text", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
