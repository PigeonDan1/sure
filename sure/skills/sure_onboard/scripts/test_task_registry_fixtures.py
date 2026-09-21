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
    def test_lid_uses_its_task_specific_playbook(self) -> None:
        self.assertEqual(task_playbooks_for("lid"), ["references/task_playbooks/LID.md"])

    def test_speech_understanding_loads_every_available_atomic_playbook(self) -> None:
        self.assertEqual(
            task_playbooks_for("speech_understanding"),
            [
                "references/task_playbooks/SPEECH_UNDERSTANDING.md",
                "references/task_playbooks/ASR.md",
                "references/task_playbooks/LID.md",
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
            self.assertEqual(len(manifest["suite_members"]), 16)
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

    def test_custom_fixture_resolution_is_preserved_with_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run"
            artifacts = run_dir / "artifacts"
            model_dir = root / "model"
            source_dir = root / "custom-fixture"
            artifacts.mkdir(parents=True)
            model_dir.mkdir()
            source_dir.mkdir()
            (source_dir / "sample.wav").write_bytes(b"RIFF-custom")
            (source_dir / "gt.jsonl").write_text(
                json.dumps({"audio": "sample.wav", "text": "custom"}) + "\n",
                encoding="utf-8",
            )
            resolved = {
                "model_id": "test/custom",
                "model_name": "test__custom",
                "model_dir": str(model_dir),
                "task_type": "asr",
                "normalized_model_input": {
                    "fixture": {
                        "fixture_status": "needs_input",
                        "fixture_source": "unresolved",
                    }
                },
            }
            (artifacts / "model_input_resolved.json").write_text(json.dumps(resolved), encoding="utf-8")
            manifest_path = artifacts / "fixture_manifest.json"
            prepared = subprocess.run(
                [
                    sys.executable,
                    str(PREPARE),
                    "--run-dir", str(run_dir),
                    "--produces", str(manifest_path),
                    "--source-dir", str(source_dir),
                    "--fixture-source", "model_specific",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(prepared.returncode, 0, msg=prepared.stderr)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["fixture_source"], "model_specific")
            self.assertFalse(manifest["official"])
            self.assertEqual(manifest["provenance"]["source_sha256"], manifest["fixture_sha256"])
            checked = subprocess.run(
                [sys.executable, str(CHECK), "--run-dir", str(run_dir), "--produces", str(manifest_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(checked.returncode, 0, msg=checked.stderr)

    def test_restaging_for_another_task_leaves_only_the_declared_fixture(self) -> None:
        # validate.py takes the first gt.jsonl under <model>/fixture, so a set left
        # behind by an earlier task would be validated instead of the declared one.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_dir = root / "model"
            model_dir.mkdir()
            manifest: dict = {}
            for task in ("asr", "lid"):
                run_dir = root / f"run-{task}"
                artifacts = run_dir / "artifacts"
                artifacts.mkdir(parents=True)
                (artifacts / "model_input_resolved.json").write_text(
                    json.dumps({
                        "model_id": "test/retask",
                        "model_name": "test__retask",
                        "model_dir": str(model_dir),
                        "task_type": task,
                    }),
                    encoding="utf-8",
                )
                manifest_path = artifacts / "fixture_manifest.json"
                prepared = subprocess.run(
                    [sys.executable, str(PREPARE), "--run-dir", str(run_dir), "--produces", str(manifest_path)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(prepared.returncode, 0, msg=prepared.stderr)
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            staged = sorted(path.resolve() for path in (model_dir / "fixture").glob("**/gt.jsonl"))
            self.assertEqual(staged, [Path(manifest["gt_jsonl"]).resolve()])


if __name__ == "__main__":
    unittest.main()
