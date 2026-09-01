from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import materialize_onboard_inputs


SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parents[3]
VAD_FIXTURE = REPO_ROOT / "fixtures" / "tasks" / "vad" / "librispeech_vad_smoke"


class VadOnboardingTest(unittest.TestCase):
    @staticmethod
    def _render_validator(root: Path) -> Path:
        contract = {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "speech_segments",
            "required_fields": ["speech_segments"],
            "nonempty_fields": ["speech_segments"],
            "json_serializable": True,
        }
        source = (SCRIPTS_DIR / "templates" / "validate.py").read_text(encoding="utf-8")
        source = source.replace("__TASK_TYPE__", "vad")
        source = source.replace("__IO_CONTRACT_JSON__", json.dumps(contract))
        validator = root / "validate.py"
        validator.write_text(source, encoding="utf-8")
        fixture_dir = root / "fixture" / "vad" / "smoke"
        fixture_dir.mkdir(parents=True)
        (fixture_dir / "silence.wav").write_bytes(b"RIFF-test")
        (fixture_dir / "gt.jsonl").write_text(
            json.dumps({"audio": "silence.wav", "duration": 3.35}) + "\n",
            encoding="utf-8",
        )
        return validator

    def test_vad_routes_to_its_own_playbook(self) -> None:
        self.assertEqual(
            materialize_onboard_inputs.task_playbooks_for("vad"),
            ["references/task_playbooks/VAD.md"],
        )

    def test_model_input_gate_accepts_vad(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / ".sure" / "runs" / "vad-run"
            artifacts = run_dir / "artifacts"
            model_dir = root / "sure" / "models" / "example__vad"
            artifacts.mkdir(parents=True)
            model_dir.mkdir(parents=True)
            resolved = artifacts / "model_input_resolved.json"
            resolved.write_text(
                json.dumps(
                    {
                        "model_id": "example/vad",
                        "model_name": "example__vad",
                        "model_dir": str(model_dir),
                        "repo_url": "https://example.invalid/vad",
                        "task_type": "vad",
                        "deployment_type": "local",
                        "package_profile": "none",
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "check_model_input.py"),
                    "--run-dir",
                    str(run_dir),
                    "--produces",
                    str(resolved),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_vad_fixture_is_staged_with_seconds_timebase_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / ".sure" / "runs" / "vad-run"
            artifacts = run_dir / "artifacts"
            model_dir = root / "sure" / "models" / "example__vad"
            artifacts.mkdir(parents=True)
            (artifacts / "model_input_resolved.json").write_text(
                json.dumps(
                    {
                        "model_id": "example/vad",
                        "model_name": "example__vad",
                        "model_dir": str(model_dir),
                        "task_type": "vad",
                    }
                ),
                encoding="utf-8",
            )
            produces = artifacts / "fixture_manifest.json"
            staged = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "prepare_fixture.py"),
                    "--run-dir",
                    str(run_dir),
                    "--produces",
                    str(produces),
                    "--source-dir",
                    str(VAD_FIXTURE),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(staged.returncode, 0, staged.stderr)
            manifest = json.loads(produces.read_text(encoding="utf-8"))
            self.assertEqual(manifest["task_type"], "vad")
            self.assertEqual(manifest["samples"][0]["duration"], 3.35)
            self.assertEqual(manifest["samples"][0]["key"], "librispeech-vad-001")
            self.assertIn("speech_segments", manifest["samples"][0]["annotation_fields"])

            checked = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "check_fixture.py"),
                    "--run-dir",
                    str(run_dir),
                    "--produces",
                    str(produces),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)

            gt_path = Path(manifest["gt_jsonl"])
            bad_row = json.loads(gt_path.read_text(encoding="utf-8"))
            bad_row["segments"] = bad_row.pop("speech_segments")
            gt_path.write_text(json.dumps(bad_row) + "\n", encoding="utf-8")
            rejected = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "check_fixture.py"),
                    "--run-dir",
                    str(run_dir),
                    "--produces",
                    str(produces),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("speech_segments", rejected.stderr)

    def test_vad_contract_rejects_speaker_segments_and_accepts_speech_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            validator = self._render_validator(root)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            sample = artifacts / "sample_output.json"
            sample.write_text(
                json.dumps({"segments": [{"speaker": "spk1", "start": 0.5, "end": 2.85}]}) + "\n",
                encoding="utf-8",
            )
            rejected = subprocess.run(
                [sys.executable, str(validator), "--stage", "contract"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            failure = json.loads((artifacts / "contract_result.json").read_text(encoding="utf-8"))
            self.assertIn("speech_segments", failure["error"])

            sample.write_text(
                json.dumps(
                    {
                        "speech_segments": [{"start": 0.551687, "end": 2.553813}],
                        "frame_scores": [{"start": 0.0, "end": 0.01, "score": 0.0}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            accepted = subprocess.run(
                [sys.executable, str(validator), "--stage", "contract"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)

            sample.write_text(
                json.dumps(
                    {
                        "speech_segments": [{"start": 0.5, "end": 4.0}],
                        "frame_scores": "invalid",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            rejected = subprocess.run(
                [sys.executable, str(validator), "--stage", "contract"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            failure = json.loads((artifacts / "contract_result.json").read_text(encoding="utf-8"))
            self.assertIn("duration", failure["error"])
            self.assertIn("frame_scores", failure["error"])


if __name__ == "__main__":
    unittest.main()
