from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory import launcher, paths


class MemoryWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "workspace"
        (self.repo / "sure" / "canonical").mkdir(parents=True)
        (self.repo / "sure" / "skills").mkdir(parents=True)
        self.memory = self.root / "outputs" / "memory"
        self.legacy = self.root / "outputs" / "skills"
        self.canonical = self.root / "outputs" / "canonical"
        self.reference = self.root / "production-reference"
        self.reference.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_launcher(self, *argv: str) -> tuple[int, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = launcher.main(argv)
        rows = [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(rows), 1, output.getvalue())
        return code, rows[0]

    def base_args(self) -> list[str]:
        return [
            "--repo-root",
            str(self.repo),
            "--memory-root",
            str(self.memory),
            "--canonical-root",
            str(self.canonical),
            "--legacy-skills-root",
            str(self.legacy),
            "--reference-root",
            str(self.reference),
        ]

    def test_index_uses_explicit_output_roots_and_leaves_checkout_default_empty(self) -> None:
        code, receipt = self.run_launcher(*self.base_args(), "index", "--rebuild")
        self.assertEqual(code, 0)
        self.assertEqual(receipt["status"], "SUCCEEDED")
        self.assertTrue(receipt["advisory"])
        self.assertTrue((self.memory / "index.json").is_file())
        self.assertFalse((self.repo / "sure" / "memory").exists())
        self.assertEqual(list((self.repo / "sure" / "skills").iterdir()), [])
        self.assertIsNone(paths.current_memory_workspace())

    def test_reference_root_admission_is_fail_closed(self) -> None:
        code, receipt = self.run_launcher(
            "--repo-root",
            str(self.repo),
            "--memory-root",
            str(self.reference / "memory"),
            "--reference-root",
            str(self.reference),
            "index",
            "--rebuild",
        )
        self.assertEqual(code, 2)
        self.assertEqual(receipt["status"], "NOT_EXECUTED")
        self.assertEqual(receipt["reason_code"], "CAPABILITY_MISSING")
        self.assertFalse((self.reference / "memory").exists())

    def test_publish_failure_is_advisory_and_does_not_create_workflow_state(self) -> None:
        code, receipt = self.run_launcher(
            *self.base_args(),
            "publish",
            "--run-dir",
            str(self.root / "missing-run"),
        )
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "FAILED")
        self.assertTrue(receipt["advisory"])
        self.assertEqual(receipt["workflow_disposition"], "NO_EFFECT")
        self.assertTrue((self.memory / "digests").exists())
        self.assertFalse((self.repo / "state.json").exists())

    def test_reference_aliases_are_taken_from_the_explicit_workspace(self) -> None:
        path = self.canonical / "skills" / "sure-infer" / "references" / "memory" / "bad_cases" / "known.md"
        path.parent.mkdir(parents=True)
        path.write_text("# known\n", encoding="utf-8")
        workspace = paths.make_memory_workspace(
            self.repo,
            memory_root_override=self.memory,
            canonical_root=self.canonical,
            legacy_skills_root=self.legacy,
        )
        with paths.memory_workspace(workspace):
            registry = paths.reference_registry(self.repo)
            self.assertEqual(registry.resolve("sure_infer/known", "bad_case"), path)
            self.assertEqual(registry.memory_root, self.memory)
        self.assertIsNone(paths.current_memory_workspace())


if __name__ == "__main__":
    unittest.main()
