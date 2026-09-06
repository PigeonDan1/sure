from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import evaluation_commit
from evaluation_commit import (
    EvaluationCommitError,
    finalize_evaluation_commit,
    prepare_evaluation_commit,
    publish_evaluation_commit,
    recover_pending,
    rollback_evaluation_commit,
)


class EvaluationCommitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source"
        self.destination = self.root / "destination"
        self.source.mkdir()
        (self.source / "report.jsonl").write_text('{"record_id":"old"}\n', encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_prepare_publish_finalize_is_atomic_and_no_clobber(self) -> None:
        prepared = prepare_evaluation_commit(self.destination, source=self.source)
        self.assertFalse(self.destination.exists())
        (prepared.candidate_root / "report.jsonl").write_text('{"record_id":"new"}\n', encoding="utf-8")
        publish_evaluation_commit(prepared)
        self.assertEqual(self.destination.joinpath("report.jsonl").read_text(encoding="utf-8"), '{"record_id":"new"}\n')
        self.assertTrue(prepared.journal_path.is_file())
        finalize_evaluation_commit(prepared)
        self.assertFalse(prepared.transaction_root.exists())

    def test_new_nested_destination_creates_its_parent_before_recovery(self) -> None:
        destination = self.root / "nested" / "results" / "bundle"
        prepared = prepare_evaluation_commit(destination, source=self.source)
        publish_evaluation_commit(prepared)
        finalize_evaluation_commit(prepared)
        self.assertEqual(
            destination.joinpath("report.jsonl").read_text(encoding="utf-8"),
            '{"record_id":"old"}\n',
        )

    def test_source_change_after_prepare_is_rejected(self) -> None:
        prepared = prepare_evaluation_commit(self.destination, source=self.source)
        (self.source / "report.jsonl").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(EvaluationCommitError):
            publish_evaluation_commit(prepared)
        self.assertFalse(self.destination.exists())
        rollback_evaluation_commit(prepared)

    def test_destination_change_after_prepare_is_rejected(self) -> None:
        self.destination.mkdir()
        (self.destination / "report.jsonl").write_text("before\n", encoding="utf-8")
        prepared = prepare_evaluation_commit(self.destination, source=self.source)
        (self.destination / "report.jsonl").write_text("concurrent\n", encoding="utf-8")
        with self.assertRaises(EvaluationCommitError):
            publish_evaluation_commit(prepared)
        self.assertEqual(self.destination.joinpath("report.jsonl").read_text(encoding="utf-8"), "concurrent\n")
        rollback_evaluation_commit(prepared)

    def test_rollback_restores_published_destination(self) -> None:
        self.destination.mkdir()
        (self.destination / "report.jsonl").write_text("before\n", encoding="utf-8")
        prepared = prepare_evaluation_commit(self.destination, source=self.source)
        (prepared.candidate_root / "report.jsonl").write_text("candidate\n", encoding="utf-8")
        publish_evaluation_commit(prepared)
        rollback_evaluation_commit(prepared)
        self.assertEqual(self.destination.joinpath("report.jsonl").read_text(encoding="utf-8"), "before\n")
        self.assertFalse(prepared.transaction_root.exists())

    def test_recover_pending_rolls_back_an_unfinished_publish(self) -> None:
        self.destination.mkdir()
        (self.destination / "report.jsonl").write_text("before\n", encoding="utf-8")
        prepared = prepare_evaluation_commit(self.destination, source=self.source)
        (prepared.candidate_root / "report.jsonl").write_text("candidate\n", encoding="utf-8")
        publish_evaluation_commit(prepared)
        # Simulate a process dying after publish and before finalize by dropping
        # the in-memory object; the journal is the recovery authority.
        recovered = recover_pending(self.destination)
        self.assertEqual(recovered, [str(prepared.transaction_root)])
        self.assertEqual(self.destination.joinpath("report.jsonl").read_text(encoding="utf-8"), "before\n")

    def test_second_rename_failure_can_be_rolled_back_without_losing_original(self) -> None:
        self.destination.mkdir()
        (self.destination / "report.jsonl").write_text("before\n", encoding="utf-8")
        prepared = prepare_evaluation_commit(self.destination, source=self.source)
        (prepared.candidate_root / "report.jsonl").write_text("candidate\n", encoding="utf-8")
        real_replace = evaluation_commit.os.replace

        def fail_candidate_rename(source: str | bytes, target: str | bytes) -> None:
            if Path(source) == prepared.candidate_root and Path(target) == self.destination:
                raise OSError("simulated candidate rename failure")
            real_replace(source, target)

        with mock.patch.object(evaluation_commit.os, "replace", side_effect=fail_candidate_rename):
            with self.assertRaisesRegex(OSError, "candidate rename"):
                publish_evaluation_commit(prepared)
        rollback_evaluation_commit(prepared)
        self.assertEqual(self.destination.joinpath("report.jsonl").read_text(encoding="utf-8"), "before\n")

    def test_recovery_handles_backup_moved_phase(self) -> None:
        self.destination.mkdir()
        (self.destination / "report.jsonl").write_text("before\n", encoding="utf-8")
        prepared = prepare_evaluation_commit(self.destination, source=self.source)
        # Model the process dying after the first directory rename and before
        # the candidate rename.  The journal is the only in-memory state left.
        evaluation_commit.os.replace(self.destination, prepared.transaction_root / "backup")
        payload = prepared.journal()
        payload["backup_root"] = str(prepared.transaction_root / "backup")
        payload["phase"] = "backup_moved"
        prepared.journal_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        recovered = recover_pending(self.destination)
        self.assertEqual(recovered, [str(prepared.transaction_root)])
        self.assertEqual(self.destination.joinpath("report.jsonl").read_text(encoding="utf-8"), "before\n")

    def test_tampered_journal_cannot_delete_an_unrelated_transaction_root(self) -> None:
        prepared = prepare_evaluation_commit(self.destination, source=self.source)
        payload = prepared.journal()
        payload["transaction_root"] = str(self.source)
        prepared.journal_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        self.assertEqual(recover_pending(self.destination), [])
        self.assertTrue(prepared.transaction_root.exists())
        self.assertTrue(self.source.exists())

    def test_symlinked_artifact_tree_is_rejected(self) -> None:
        (self.source / "escape").symlink_to(Path(os.devnull))
        with self.assertRaises(EvaluationCommitError):
            prepare_evaluation_commit(self.destination, source=self.source)


if __name__ == "__main__":
    unittest.main()
