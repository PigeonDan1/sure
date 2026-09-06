from __future__ import annotations

import json
import os
import subprocess
import sys
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

    def test_subprocess_crash_matrix_recovers_from_durable_journal(self) -> None:
        child_program = r"""
import os
import sys
from pathlib import Path

from evaluation_commit import prepare_evaluation_commit, publish_evaluation_commit

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
fault_point = sys.argv[3]

def crash(point: str) -> None:
    if point == fault_point:
        os._exit(73)

prepared = prepare_evaluation_commit(destination, source=source, fault=crash)
(prepared.candidate_root / "report.jsonl").write_text("candidate\n", encoding="utf-8")
publish_evaluation_commit(prepared, fault=crash)
raise SystemExit(0)
"""
        fault_points = (
            "initializing_journal",
            "candidate_materialized",
            "prepared_journal",
            "publish_intent",
            "backup_renamed",
            "backup_journal",
            "candidate_renamed",
            "published_journal",
        )
        runtime_dir = Path(evaluation_commit.__file__).resolve().parent
        child_environment = dict(os.environ)
        existing_python_path = child_environment.get("PYTHONPATH")
        child_environment["PYTHONPATH"] = (
            str(runtime_dir)
            if not existing_python_path
            else str(runtime_dir) + os.pathsep + existing_python_path
        )

        for destination_existed in (True, False):
            for fault_point in fault_points:
                with self.subTest(destination_existed=destination_existed, fault_point=fault_point):
                    case_root = self.root / ("existing" if destination_existed else "new") / fault_point
                    source = case_root / "source"
                    destination = case_root / "destination"
                    source.mkdir(parents=True)
                    if destination_existed:
                        destination.mkdir()
                    (source / "report.jsonl").write_text("source\n", encoding="utf-8")
                    if destination_existed:
                        (destination / "report.jsonl").write_text("before\n", encoding="utf-8")

                    completed = subprocess.run(
                        [sys.executable, "-c", child_program, str(source), str(destination), fault_point],
                        cwd=runtime_dir,
                        env=child_environment,
                        capture_output=True,
                        text=True,
                        timeout=15,
                        check=False,
                    )

                    self.assertEqual(
                        completed.returncode,
                        73,
                        msg=f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}",
                    )
                    recovered = recover_pending(destination)
                    self.assertEqual(len(recovered), 1)
                    if destination_existed:
                        self.assertEqual(
                            destination.joinpath("report.jsonl").read_text(encoding="utf-8"),
                            "before\n",
                        )
                    else:
                        self.assertFalse(destination.exists())
                    self.assertEqual(source.joinpath("report.jsonl").read_text(encoding="utf-8"), "source\n")
                    self.assertEqual(list(case_root.glob(".sure-eval-txn-*")), [])

    def test_subprocess_crash_before_finalize_rolls_back_published_tree(self) -> None:
        child_program = r"""
import os
import sys
from pathlib import Path

from evaluation_commit import finalize_evaluation_commit, prepare_evaluation_commit, publish_evaluation_commit

source = Path(sys.argv[1])
destination = Path(sys.argv[2])

def crash(point: str) -> None:
    if point == "before_finalize_cleanup":
        os._exit(73)

prepared = prepare_evaluation_commit(destination, source=source)
(prepared.candidate_root / "report.jsonl").write_text("candidate\n", encoding="utf-8")
publish_evaluation_commit(prepared)
finalize_evaluation_commit(prepared, fault=crash)
raise SystemExit(0)
"""
        runtime_dir = Path(evaluation_commit.__file__).resolve().parent
        child_environment = dict(os.environ)
        existing_python_path = child_environment.get("PYTHONPATH")
        child_environment["PYTHONPATH"] = (
            str(runtime_dir)
            if not existing_python_path
            else str(runtime_dir) + os.pathsep + existing_python_path
        )
        case_root = self.root / "finalize-crash"
        source = case_root / "source"
        destination = case_root / "destination"
        source.mkdir(parents=True)
        destination.mkdir()
        (source / "report.jsonl").write_text("source\n", encoding="utf-8")
        (destination / "report.jsonl").write_text("before\n", encoding="utf-8")

        completed = subprocess.run(
            [sys.executable, "-c", child_program, str(source), str(destination)],
            cwd=runtime_dir,
            env=child_environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            73,
            msg=f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}",
        )
        self.assertEqual(len(recover_pending(destination)), 1)
        self.assertEqual(destination.joinpath("report.jsonl").read_text(encoding="utf-8"), "before\n")
        self.assertEqual(list(case_root.glob(".sure-eval-txn-*")), [])

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
