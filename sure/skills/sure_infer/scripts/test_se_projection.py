from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sure_eval.core.config import Config
from sure_eval.datasets import source_resolver
from sure_eval.datasets.dataset_manager import DatasetManager


class SEProjectionTests(unittest.TestCase):
    def project(self, root: Path, rows: list[dict]) -> tuple[Path, DatasetManager]:
        source = root / "paired_se"
        source.mkdir()
        (source / "noisy.wav").write_bytes(b"noisy audio")
        (source / "clean.wav").write_bytes(b"clean audio")
        (source / "ds.jsonl").write_text(json.dumps({"task": "SE", "audio": {"speech": {"language": "en"}}}), encoding="utf-8")
        (source / "sample.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        config = Config()
        config.data.datasets = str(root / "projection")
        manager = DatasetManager(config)
        with patch.object(source_resolver, "DEFAULT_SOURCE_ROOTS", {"default": str(root)}):
            return manager.download_and_convert(str(source)), manager

    def test_audio_pair_is_projected_without_a_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projected, manager = self.project(root, [{"key": "se_1", "noisy_audio": "noisy.wav", "reference_audio": "clean.wav"}])
            row = json.loads(projected.read_text(encoding="utf-8"))
            self.assertEqual(row["task"], "SE")
            self.assertEqual(row["path"], row["noisy_audio"])
            self.assertNotEqual(row["path"], row["reference_audio"])
            self.assertTrue(Path(row["reference_audio"]).is_file())
            self.assertEqual(manager.get_info(projected.stem)["task"], "SE")
            contract = manager.sure_dir / "paired_se" / "projections" / "se_audio_v1" / "io_contract.json"
            self.assertEqual(json.loads(contract.read_text(encoding="utf-8"))["reference"]["type"], "audio_path")

    def test_no_reference_is_allowed_for_no_reference_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            projected, _ = self.project(Path(directory), [{"sample_id": "se_1", "attribute": {"path": "noisy.wav"}}])
            self.assertNotIn("reference_audio", json.loads(projected.read_text(encoding="utf-8")))

    def test_invalid_audio_and_duplicate_keys_fail_before_caching(self) -> None:
        cases = [
            [{"key": "se_1", "noisy_audio": "missing.wav"}],
            [{"key": "se_1", "noisy_audio": "noisy.wav", "reference_audio": "missing.wav"}],
            [{"key": "se_1", "noisy_audio": "noisy.wav"}] * 2,
            [{"key": "se_1", "noisy_audio": "missing/noisy.wav"}],
        ]
        for rows in cases:
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, "skipped"):
                    self.project(Path(directory), rows)
                self.assertFalse(list((Path(directory) / "projection" / "sure_benchmark" / "jsonl").glob("*.jsonl")))


if __name__ == "__main__":
    unittest.main()
