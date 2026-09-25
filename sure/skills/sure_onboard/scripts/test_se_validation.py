from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class SeOnboardValidationTests(unittest.TestCase):
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
