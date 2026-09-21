#!/usr/bin/env python3
"""The legacy evaluation database must not touch the filesystem until it writes.

Constructing it used to create the database directory, so every process that
built an `RPSManager` left an empty `results/` behind in whatever directory it
happened to be started from.

Run directly:
    cd sure/skills/sure_infer/scripts && python test_evaluation_database_dir.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sure_eval.evaluation import rps  # noqa: E402


class EvaluationDatabaseDirTests(unittest.TestCase):
    def test_construction_does_not_create_the_database_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "results" / "evaluations.json"
            rps.EvaluationDatabase(db_path)
            self.assertFalse(db_path.parent.exists())

    def test_saving_a_record_creates_the_database_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "results" / "evaluations.json"
            database = rps.EvaluationDatabase(db_path)
            record = rps.EvaluationRecord(
                tool_name="demo_tool",
                model_name="demo_model",
                dataset="demo_dataset",
                metric="cer",
                score=1.0,
                rps=None,
            )
            with mock.patch.object(rps, "logger"):
                database.add_record(record)
            self.assertTrue(db_path.is_file())


if __name__ == "__main__":
    unittest.main()
