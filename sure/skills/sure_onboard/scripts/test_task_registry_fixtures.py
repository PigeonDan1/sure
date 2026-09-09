#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
PREPARE = SCRIPTS_DIR / "prepare_fixture.py"
CHECK = SCRIPTS_DIR / "check_fixture.py"
sys.path.insert(0, str(SCRIPTS_DIR))

from materialize_onboard_inputs import task_playbooks_for


class TaskRegistryFixtureTests(unittest.TestCase):
    def test_speech_understanding_loads_every_available_atomic_playbook(self) -> None:
        self.assertEqual(
            task_playbooks_for("speech_understanding"),
            [
                "references/task_playbooks/SPEECH_UNDERSTANDING.md",
                "references/task_playbooks/ASR.md",
                "references/task_playbooks/KWS.md",
                "references/task_playbooks/TTS.md",
                "references/task_playbooks/VC.md",
            ],
        )

    def test_speech_understanding_stages_complete_engine_suite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run"
            artifacts = run_dir / "artifacts"
            model_dir = root / "model"
            artifacts.mkdir(parents=True)
            model_dir.mkdir()
            resolved = {
                "model_id": "test/speech-understanding",
                "model_name": "test__speech-understanding",
                "model_dir": str(model_dir),
                "task_type": "speech_understanding",
            }
            (artifacts / "model_input_resolved.json").write_text(
                json.dumps(resolved),
                encoding="utf-8",
            )
            manifest_path = artifacts / "fixture_manifest.json"

            prepared = subprocess.run(
                [
                    sys.executable,
                    str(PREPARE),
                    "--run-dir",
                    str(run_dir),
                    "--produces",
                    str(manifest_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(prepared.returncode, 0, msg=prepared.stderr)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["suite_members"]), 15)
            self.assertEqual(
                {entry["task_type"] for entry in manifest["subtask_fixtures"]},
                set(manifest["suite_members"]),
            )
            for entry in manifest["subtask_fixtures"]:
                self.assertTrue(Path(entry["gt_jsonl"]).is_file())
                self.assertGreaterEqual(entry["sample_count"], 1)
                self.assertLessEqual(entry["sample_count"], 5)

            checked = subprocess.run(
                [
                    sys.executable,
                    str(CHECK),
                    "--run-dir",
                    str(run_dir),
                    "--produces",
                    str(manifest_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(checked.returncode, 0, msg=checked.stderr)


if __name__ == "__main__":
    unittest.main()
