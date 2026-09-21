#!/usr/bin/env python3
from __future__ import annotations

import builtins
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402,F401  (imported here so the patch below never wraps an import)
from sure_eval.models.registry import ModelRegistry  # noqa: E402


def _host_encoding(encoding: str):
    """Pretend the host code page is `encoding` for every open() that names none.

    That is what open() does on a non-UTF-8 Windows box, and forcing it here
    keeps the regression provable on a UTF-8 host too.
    """
    real_open = builtins.open

    def opener(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        if "b" not in mode and not args and "encoding" not in kwargs:
            kwargs["encoding"] = encoding
        return real_open(file, mode, *args, **kwargs)

    return mock.patch("builtins.open", opener)


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
            _plant(root, "spare", "canonical-model")
            _plant(root, "canonical-model", "canonical-model-v2")
            registry = ModelRegistry(root)

            found = registry.get_model("canonical-model")
            self.assertIsNotNone(found)
            self.assertEqual(found.path.name, "spare")
            self.assertIsNotNone(registry.get_model("canonical-model-v2"))

    def test_unknown_name_still_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            _plant(root, "model", "CohereLabs__cohere-transcribe-03-2026")
            registry = ModelRegistry(root)

            self.assertIsNone(registry.get_model("no-such-model"))


class RegistryEncodingTests(unittest.TestCase):
    """A model's own files are UTF-8; reading them as the host code page loses the model."""

    def test_a_non_ascii_config_still_registers_the_model(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            model_dir = root / "model"
            model_dir.mkdir(parents=True)
            (model_dir / "config.yaml").write_text(
                'name: zh-asr\ntask: ASR\ndescription: "中文语音识别"\n',
                encoding="utf-8",
            )

            with _host_encoding("ascii"):
                registry = ModelRegistry(root)

            found = registry.get_model("zh-asr")
            self.assertIsNotNone(found)
            self.assertEqual(found.description, "中文语音识别")

    def test_a_non_ascii_result_file_is_still_readable(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            model_dir = _plant(root, "model", "zh-asr")
            results_dir = model_dir / "results"
            results_dir.mkdir()
            (results_dir / "aishell_v1.json").write_text(
                '{"note": "中文"}', encoding="utf-8"
            )

            registry = ModelRegistry(root)
            with _host_encoding("ascii"):
                results = registry.get_model("zh-asr").get_test_results()

            self.assertEqual(results.get("aishell", {}).get("note"), "中文")


if __name__ == "__main__":
    unittest.main()
