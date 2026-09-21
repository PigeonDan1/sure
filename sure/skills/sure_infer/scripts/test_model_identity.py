"""canonical_model_name: the sealed inventory names the model; config and directory are fallbacks."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_identity import canonical_model_name  # noqa: E402


class CanonicalModelNameTests(unittest.TestCase):
    def _model(self, root: Path, *, inventory: dict | None = None, config: dict | None = None) -> Path:
        # The directory is deliberately not the model's name: inside the container
        # the bundle is mounted under a policy-defined alias.
        model = root / "mounted-alias"
        (model / "artifacts").mkdir(parents=True)
        if inventory is not None:
            (model / "artifacts" / "runtime_inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
        if config is not None:
            (model / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        return model

    def test_inventory_name_wins_over_config_and_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model = self._model(Path(directory), inventory={"model": {"name": "owner__demo"}}, config={"name": "config-name"})
            self.assertEqual(canonical_model_name(model), "owner__demo")

    def test_config_name_when_there_is_no_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model = self._model(Path(directory), config={"name": "config-name"})
            self.assertEqual(canonical_model_name(model), "config-name")

    def test_nested_model_name_when_config_has_no_top_level_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model = self._model(Path(directory), config={"model": {"name": "nested-name", "id": "org/x"}})
            self.assertEqual(canonical_model_name(model), "nested-name")

    def test_directory_name_when_nothing_declares_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model = self._model(Path(directory), inventory={"model": {}}, config={"task": "ASR"})
            self.assertEqual(canonical_model_name(model), "mounted-alias")

    def test_a_config_already_in_hand_is_used_instead_of_rereading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model = self._model(Path(directory), config={"name": "on-disk"})
            self.assertEqual(canonical_model_name(model, {"name": "in-hand"}), "in-hand")


if __name__ == "__main__":
    unittest.main()
