from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory import index as index_module
from memory import paths
from memory.reference_registry import ReferenceRegistry


class ReferenceRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "sure" / "skills" / "sure_infer").mkdir(parents=True)
        (self.repo / "sure" / "skills" / "_shared").mkdir(parents=True)
        self.registry = ReferenceRegistry(self.repo)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_logical_uri_and_legacy_default(self) -> None:
        entry_id = "sure_infer/missing-gpu"
        self.assertEqual(
            self.registry.logical_uri(entry_id, "bad_case"),
            "memory://sure_infer/bad_case/missing-gpu",
        )
        self.assertEqual(
            self.registry.path_for(entry_id, "bad_case"),
            self.repo / "sure/skills/sure_infer/references/memory/bad_cases/missing-gpu.md",
        )
        self.assertEqual(self.registry.parse_uri("memory://sure_infer/bad_case/missing-gpu"), ("sure_infer", "bad_case", "missing-gpu"))

    def test_canonical_alias_is_explicit_and_legacy_first(self) -> None:
        entry_id = "sure_infer/missing-gpu"
        legacy = self.registry.path_for(entry_id, "bad_case", alias="legacy")
        canonical = self.registry.path_for(entry_id, "bad_case", alias="canonical")
        legacy.parent.mkdir(parents=True)
        canonical.parent.mkdir(parents=True)
        legacy.write_text("legacy\n", encoding="utf-8")
        canonical.write_text("canonical\n", encoding="utf-8")

        self.assertEqual(self.registry.resolve(entry_id, "bad_case"), legacy)
        canonical_first = ReferenceRegistry(self.repo, read_order=("canonical", "legacy"), write_root="canonical")
        self.assertEqual(canonical_first.resolve(entry_id, "bad_case"), canonical)
        self.assertEqual(canonical_first.path_for(entry_id, "bad_case"), canonical)

    def test_shared_fact_uses_checked_in_canonical_legacy_resource_tree(self) -> None:
        canonical = self.registry.path_for("_shared/site-gpu", "fact", alias="canonical")
        self.assertEqual(
            canonical,
            self.repo / "sure/canonical/shared/legacy-resources/memory/facts/site-gpu.md",
        )

    def test_shared_fact_and_reference_file_enumeration(self) -> None:
        fact = self.registry.path_for("_shared/site-gpu", "fact")
        bad = self.registry.path_for("sure_infer/partition", "bad_case")
        canonical_twin = self.registry.path_for("sure_infer/partition", "bad_case", alias="canonical")
        canonical_only = self.registry.path_for("sure_infer/canonical-only", "bad_case", alias="canonical")
        fact.parent.mkdir(parents=True)
        bad.parent.mkdir(parents=True)
        canonical_twin.parent.mkdir(parents=True)
        fact.write_text("# fact\n", encoding="utf-8")
        bad.write_text("# bad\n", encoding="utf-8")
        canonical_twin.write_text("# generated twin\n", encoding="utf-8")
        canonical_only.write_text("# canonical only\n", encoding="utf-8")
        (bad.parent / "README.md").write_text("# index\n", encoding="utf-8")
        self.assertEqual(
            list(self.registry.iter_reference_files()),
            [("_shared/site-gpu", fact), ("sure_infer/partition", bad), ("sure_infer/canonical-only", canonical_only)],
        )

    def test_rejects_bad_ids_and_symlink_escape(self) -> None:
        with self.assertRaises(ValueError):
            self.registry.path_for("../outside/x", "bad_case")
        outside = Path(self.tmp.name).parent / (Path(self.tmp.name).name + "-outside")
        outside.mkdir()
        try:
            target = self.repo / "sure" / "skills" / "sure_infer" / "references" / "memory" / "bad_cases"
            target.parent.mkdir(parents=True)
            target.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.registry.path_for("sure_infer/escape", "bad_case")
        finally:
            outside.rmdir()

    def test_rejects_kind_namespace_mismatch(self) -> None:
        with self.assertRaises(ValueError):
            self.registry.path_for("sure_infer/not-a-fact", "fact")
        with self.assertRaises(ValueError):
            self.registry.path_for("_shared/not-a-case", "bad_case")

    def test_ignores_a_reference_file_symlink_that_escapes_the_alias_root(self) -> None:
        outside = Path(self.tmp.name).parent / (Path(self.tmp.name).name + "-file-outside")
        outside.mkdir()
        try:
            outside_file = outside / "secret.md"
            outside_file.write_text("# secret\n", encoding="utf-8")
            link = self.registry.path_for("sure_infer/escape-file", "bad_case")
            link.parent.mkdir(parents=True)
            link.symlink_to(outside_file)
            self.assertIsNone(self.registry.resolve("sure_infer/escape-file", "bad_case"))
            self.assertNotIn(("sure_infer/escape-file", link), list(self.registry.iter_reference_files()))
        finally:
            outside_file.unlink(missing_ok=True)
            outside.rmdir()

    def test_index_prefers_the_first_alias_without_duplicate_entries(self) -> None:
        legacy = self.registry.path_for("sure_infer/twin", "bad_case", alias="legacy")
        canonical = self.registry.path_for("sure_infer/twin", "bad_case", alias="canonical")
        text = "\n".join(
            (
                "Trigger: runtime mismatch",
                "Cell: sure_infer/execute_inference x runtime",
                "Source: legacy",
                "Added: 2026-09-06",
                "Status: confirmed",
                "",
                "# Twin",
                "",
                "same logical entry",
                "",
            )
        )
        legacy.parent.mkdir(parents=True)
        canonical.parent.mkdir(parents=True)
        legacy.write_text(text, encoding="utf-8")
        canonical.write_text(text.replace("same logical entry", "canonical copy"), encoding="utf-8")

        built = index_module.build_index(self.repo, config=paths.load_config(), units=paths.load_units())
        rows = [row for row in built["entries"] if row.get("entry_id") == "sure_infer/twin"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["path"], legacy.relative_to(self.repo).as_posix())


if __name__ == "__main__":
    unittest.main()
