#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sure_eval.models.registry import ModelRegistry  # noqa: E402


def _plant(root: Path, dir_name: str, declared_name: str, task: str = "ASR") -> Path:
    model_dir = root / dir_name
    model_dir.mkdir(parents=True)
    (model_dir / "config.yaml").write_text(
        f"name: {declared_name}\ntask: {task}\n", encoding="utf-8"
    )
    return model_dir


class RegistryLookupTests(unittest.TestCase):
    def test_model_is_findable_by_the_directory_it_was_mounted_as(self) -> None:
        """The container mounts the bundle at /workspace/model, so callers look it
        up by the basename "model" while config.yaml declares the canonical name."""
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            _plant(root, "model", "CohereLabs__cohere-transcribe-03-2026")
            registry = ModelRegistry(root)

            self.assertIsNotNone(registry.get_model("CohereLabs__cohere-transcribe-03-2026"))
            found = registry.get_model("model")
            self.assertIsNotNone(found)
            self.assertEqual(found.name, "CohereLabs__cohere-transcribe-03-2026")

    def test_a_declared_name_is_never_shadowed_by_another_directorys_basename(self) -> None:
        """Whichever order the directories are walked in, the name config.yaml
        declares must win over a directory that merely happens to be called that."""
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            _plant(root, "spare", "aispeech14.1")
            _plant(root, "aispeech14.1", "aispeech14.1-v2")
            registry = ModelRegistry(root)

            found = registry.get_model("aispeech14.1")
            self.assertIsNotNone(found)
            self.assertEqual(found.path.name, "spare")
            self.assertIsNotNone(registry.get_model("aispeech14.1-v2"))

    def test_unknown_name_still_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            _plant(root, "model", "CohereLabs__cohere-transcribe-03-2026")
            registry = ModelRegistry(root)

            self.assertIsNone(registry.get_model("no-such-model"))


if __name__ == "__main__":
    unittest.main()
