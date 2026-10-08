from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class SeOnboardValidationTests(unittest.TestCase):
    def test_se_noisy_input_aliases_pass_fixture_producer_and_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "noisy.wav").write_bytes(b"RIFFnoisy")
            (source / "other.wav").write_bytes(b"RIFFother")
            (source / "clean.wav").write_bytes(b"RIFFclean")
            rows = [
                {"key": field, field: "noisy.wav", "reference_audio": "clean.wav"}
                for field in ("audio", "noisy_audio", "wav")
            ]
            rows.extend([
                {"key": "audio_first", "audio": "noisy.wav", "noisy_audio": "other.wav", "reference_audio": "clean.wav"},
                {"key": "noisy_before_wav", "noisy_audio": "noisy.wav", "wav": "other.wav", "reference_audio": "clean.wav"},
            ])
            source_gt = source / "gt.jsonl"
            source_gt.write_text("".join(json.dumps(row) + "\n" for row in rows))
            model_dir = root / "sure" / "models" / "example"
            model_dir.mkdir(parents=True)
            run_dir = root / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            (artifacts / "model_input_resolved.json").write_text(
                json.dumps({"model_id": "example/model", "model_name": "example", "model_dir": str(model_dir), "task_type": "se"})
            )
            manifest = artifacts / "fixture_manifest.json"
            scripts = Path(__file__).parent
            prepare_command = [
                sys.executable, str(scripts / "prepare_fixture.py"), "--run-dir", str(run_dir),
                "--produces", str(manifest), "--source-dir", str(source), "--fixture-source", "model_specific",
            ]
            prepared = subprocess.run(prepare_command, capture_output=True, text=True)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            staged_manifest = json.loads(manifest.read_text())
            self.assertEqual(staged_manifest["sample_count"], len(rows))
            for sample in staged_manifest["samples"]:
                with self.subTest(key=sample["key"]):
                    self.assertEqual(sample["audio"], "noisy.wav")
                    self.assertEqual(sample["audio_path"], str(Path(staged_manifest["staged_dir"]) / "noisy.wav"))
                    self.assertIn("reference_audio", sample["annotation_fields"])
                    self.assertEqual(sample["audio_roles"]["reference_audio"], str(Path(staged_manifest["staged_dir"]) / "clean.wav"))
            check_command = [
                sys.executable, str(scripts / "check_fixture.py"), "--run-dir", str(run_dir),
                "--produces", str(manifest),
            ]
            checked = subprocess.run(check_command, capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            staged = Path(staged_manifest["gt_jsonl"])
            # Reach semantic validation rather than the content hash check for these mutations.
            staged_manifest.pop("fixture_sha256")
            staged_manifest["samples"] = staged_manifest["samples"][:1]
            staged_manifest["sample_count"] = 1
            manifest.write_text(json.dumps(staged_manifest))
            invalid_rows = [
                ({"reference_audio": "clean.wav"}, "non-empty"),
                ({"noisy_audio": "noisy.wav", "reference_text": "speech"}, "distinct clean reference_audio"),
                ({"noisy_audio": "clean.wav", "reference_audio": "clean.wav"}, "distinct clean reference_audio"),
            ]
            for row, error in invalid_rows:
                with self.subTest(invalid=row):
                    source_gt.write_text(json.dumps(row) + "\n")
                    rejected = subprocess.run(prepare_command, capture_output=True, text=True)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn(error, rejected.stderr)
                    staged.write_text(json.dumps(row) + "\n")
                    rejected = subprocess.run(check_command, capture_output=True, text=True)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn(error, rejected.stderr)

    def test_se_fixture_and_all_generated_files_are_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = Path(__file__).parent / "templates/validate.py"
            script = root / "validate.py"
            script.write_text(source.read_text().replace("__TASK_TYPE__", "SE").replace(
                "__IO_CONTRACT_JSON__", json.dumps({"primary_field": "audio_path"})
            ))
            spec = importlib.util.spec_from_file_location("onboard_se_validate", script)
            validator = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(validator)
            fixture = root / "fixture/se"
            fixture.mkdir(parents=True)
            gt = fixture / "gt.jsonl"
            gt.write_text("\n".join(json.dumps({"audio": f"noisy_{i}.wav", "reference_audio": "clean.wav"}) for i in range(2)))
            with patch.dict(os.environ, {"SURE_VALIDATE_INPUT_JSON": ""}):
                payloads = validator.fixture_payloads()
                self.assertTrue(all(set(row["input"]) == {"audio_path"} for row in payloads))

                class Wrapper:
                    def predict(self, payload):
                        path = Path(payload["output_path"])
                        path.write_bytes(b"generated audio")
                        return {"audio_path": str(path)}

                with patch.object(validator, "load_wrapper", return_value=Wrapper()):
                    self.assertTrue(validator.stage_infer())
                rows = [json.loads(line) for line in validator.SAMPLE_OUTPUTS.read_text().splitlines()]
                self.assertEqual(len({row["output"]["audio_path"] for row in rows}), 2)
                self.assertTrue(validator.stage_contract())
                Path(rows[1]["output"]["audio_path"]).unlink()
                self.assertFalse(validator.stage_contract())
                # A rerun that returns an old destination without writing must fail.
                class NoWriteWrapper:
                    def predict(self, payload):
                        return {"audio_path": payload["output_path"]}

                with patch.object(validator, "load_wrapper", return_value=NoWriteWrapper()):
                    self.assertFalse(validator.stage_infer())
                gt.write_text(json.dumps({"reference_audio": "clean.wav"}))
                with self.assertRaisesRegex(ValueError, "scoring-only"):
                    validator.fixture_payloads()


if __name__ == "__main__":
    unittest.main()
